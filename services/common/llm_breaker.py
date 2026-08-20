"""A7 — circuit breaker and NAMED failure taxonomy for every LLM call.

Ollama (or whatever `model_roles` resolves a role to) is a hard external
dependency with no fallback: when it is down, every call in the process fails
the same way, at the same cost, forever. A course build is the worst place for
that — it is minutes deep, runs dozens of sequential calls, and each one pays
its full 90-600 s timeout before failing, so a host that died at concept 3 of
40 takes an hour to say so.

Two things are broken there and this module fixes both.

**1. Cost.** While the host is unreachable, the *right* answer is known before
the request is made. The breaker remembers consecutive transport failures and,
past a threshold, fast-fails without a socket — turning an hour of timeouts
into an immediate, actionable stop. It reopens itself: after a probe interval
one request is let through (HALF_OPEN); if it succeeds the circuit closes and
the pipeline resumes with no operator action.

**2. Naming.** This is the half that matters more here. Historically every
failure in this path collapsed to `""` or `None`:

    the host is unreachable          -> ""      (build writes stub content)
    the GPU gate shed the request    -> ""      (build writes stub content)
    the model answered with garbage  -> None    (build retries, then stubs)

Those are three different facts with three different fixes — start Ollama,
wait for load to drop, fix the prompt/schema — and the pipeline could not tell
them apart, so it treated all three as "content unavailable" and carried on.
This repo cares a great deal about that distinction (see the SearXNG post-mortem
in docs/MODE_A_STATUS.md §5: a container in a restart loop looked healthy from
every angle except the one nobody checked). So failures now carry a name:

    LLMUnavailable   — we never got an answer from the model service
      LLMCircuitOpen   ... because the breaker is open; we did not even try
      LLMTimeout       ... because it did not answer inside the deadline
      LLMTransportError... because the connection or the server itself failed
      LLMOverloaded    ... because OUR OWN admission gate shed the request
    LLMBadOutput     — the model answered, and the answer was unusable
      LLMBadJSON       ... unparseable even after repair_json()
      LLMSchemaMismatch... parsed, but not the shape the caller requires

`LLMOverloaded` sits under `LLMUnavailable` because the caller's recovery is the
same (wait and retry), but it is deliberately its own class: it means the host
is *fine* and we are busy, so it must never trip the breaker.

Callers opt into the names with `strict=True` on `llm_generate` /
`llm_generate_json` / `LLMClient.chat`, or globally with `LLM_STRICT_ERRORS=1`.
Callers that still want the old empty-string contract get it, and read the name
afterwards from `last_llm_failure()` — the failure is recorded either way, so
"which one happened" is never lost even when the return value cannot say it.

Configuration (all optional, defaults chosen for a single-host tutor):

    OLLAMA_BREAKER_ENABLED    1     0 disables the breaker entirely
    OLLAMA_BREAKER_TRIP       3     consecutive transport failures before OPEN
    OLLAMA_BREAKER_PROBE_S    15    seconds OPEN before a half-open probe
    OLLAMA_BREAKER_PROBE_MAX_S 300  cap on the backed-off probe interval
    LLM_STRICT_ERRORS         0     1 makes named exceptions the default
"""

import logging
import os
import threading
import time

logger = logging.getLogger(__name__)

CLOSED, OPEN, HALF_OPEN = "closed", "open", "half_open"


# ---------------------------------------------------------------------------
# The failure taxonomy.
#
# Every class carries a stable `reason` code and a `user_message`. The code is
# for logs, status events and tests (a string comparison that does not depend on
# exception identity across the two import paths this repo has — `gpu_gate` vs
# `services.core.gpu_gate` — which is a real hazard here). The message is what a
# learner or a build log should see: it must name the fact, not the traceback.

class LLMError(Exception):
    """Base for every named LLM failure."""

    reason = "llm_error"
    user_message = "The model call failed."

    def __init__(self, detail="", reason=None, user_message=None):
        self.detail = detail
        if reason:
            self.reason = reason
        if user_message:
            self.user_message = user_message
        super().__init__(f"[{self.reason}] {self.user_message}"
                         + (f" ({detail})" if detail else ""))


class LLMUnavailable(LLMError):
    """We never got an answer out of the model service.

    The distinguishing fact for a caller: retrying the same prompt later may
    well work, and nothing about the prompt needs to change.
    """

    reason = "llm_unavailable"
    user_message = "The model service is unreachable."


class LLMCircuitOpen(LLMUnavailable):
    """Fast-fail: the breaker is OPEN, so no request was made at all.

    Distinct from a timeout on purpose. "We did not try" and "we tried and it
    did not answer" have different remedies and very different latencies, and a
    log that conflates them makes the breaker itself invisible.
    """

    reason = "circuit_open"
    user_message = ("The model service is unreachable and calls are paused "
                    "while it recovers.")


class LLMTimeout(LLMUnavailable):
    reason = "timeout"
    user_message = "The model service did not answer in time."


class LLMTransportError(LLMUnavailable):
    reason = "transport"
    user_message = "Could not reach the model service."


class LLMOverloaded(LLMUnavailable):
    """OUR admission gate shed the request — the host is healthy, we are busy.

    Never counts toward the breaker: tripping on self-inflicted backpressure
    would fast-fail a perfectly good server for the next 15 seconds, converting
    a queue into an outage.
    """

    reason = "overloaded"
    user_message = "Helga is at capacity right now — try again in a moment."


class LLMBadOutput(LLMError):
    """The model answered; the answer was unusable.

    The distinguishing fact: retrying identically is mostly wasted work. The
    prompt, the schema or the token budget is what has to change.
    """

    reason = "bad_output"
    user_message = "The model returned an unusable response."


class LLMBadJSON(LLMBadOutput):
    reason = "bad_json"
    user_message = "The model returned unusable JSON."


class LLMSchemaMismatch(LLMBadOutput):
    reason = "schema_mismatch"
    user_message = "The model's JSON did not match the required shape."


class LLMEmptyResponse(LLMBadOutput):
    """Transport succeeded, `content` came back empty.

    This is not hypothetical: a reasoning model with thinking left on spends the
    whole token budget in its thinking block and returns zero characters
    (measured, see llm_utils). For months that was indistinguishable from a
    parse failure, and every affected concept quietly became a stub.
    """

    reason = "empty_response"
    user_message = "The model returned an empty response."


class LLMRequestRejected(LLMError):
    """A 4xx — the model service is healthy and refused what WE sent.

    Deliberately not under `LLMUnavailable`: it must never trip the breaker,
    because retrying elsewhere or later cannot help. The payload has to change.
    This repo has lost hours to exactly this hiding inside a generic failure —
    a 400 "exceeds the available context size (4096)" disabled the one-shot
    build path for five of six modules in every build and surfaced as nothing.
    """

    reason = "request_rejected"
    user_message = "The model service rejected the request."


# Kept as an alias: `OllamaUnavailable` was the original name and is part of the
# gpu_gate surface other modules import.
OllamaUnavailable = LLMUnavailable


def _env_int(name, default):
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_float(name, default):
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def strict_default():
    """Whether named exceptions are raised when the caller did not choose.

    Read at CALL time, not import time, so an operator can flip it in a shell
    and so tests can monkeypatch the environment without reimporting modules.
    """
    return os.getenv("LLM_STRICT_ERRORS", "0").strip().lower() in ("1", "true", "yes")


class CircuitBreaker:
    """CLOSED → OPEN after `trip_after` consecutive transport failures; while
    OPEN, `allow()` fast-fails; after the probe interval exactly one request is
    admitted (HALF_OPEN) — success closes the circuit, failure re-opens it.

    The probe interval BACKS OFF on each re-open (doubling, capped at
    `probe_max`). A host that is down for an hour should not be probed 240
    times: each failed probe costs a full client timeout on whichever unlucky
    caller draws it, and that caller is usually a student mid-lesson.
    """

    def __init__(self, trip_after=3, probe_interval=15.0, probe_max=300.0,
                 enabled=True):
        self.trip_after = max(1, int(trip_after))
        self.probe_interval = float(probe_interval)
        self.probe_max = float(probe_max)
        self.enabled = bool(enabled)
        self._state = CLOSED
        self._fails = 0
        self._opened_at = 0.0
        self._probing = False
        self._open_streak = 0
        self._lock = threading.RLock()
        self.state_changes = 0

    @property
    def state(self):
        return self._state

    def _effective_probe_interval(self):
        # Exponent is streak-1 so the FIRST open uses the configured interval;
        # only a host that keeps failing its probes gets backed off.
        exp = max(0, self._open_streak - 1)
        return min(self.probe_max, self.probe_interval * (2 ** exp))

    def allow(self):
        """True if a request may proceed (CLOSED always; OPEN only the single
        half-open probe once the probe interval has elapsed)."""
        if not self.enabled:
            return True
        with self._lock:
            if self._state == CLOSED:
                return True
            if self._state == OPEN:
                if time.time() - self._opened_at >= self._effective_probe_interval():
                    self._state = HALF_OPEN
                    self._probing = True
                    logger.info("LLM breaker HALF_OPEN — probing")
                    return True
                return False
            # HALF_OPEN: one probe in flight; everything else fast-fails so a
            # queue of callers cannot all pile onto a host that is still down.
            if not self._probing:
                self._probing = True
                return True
            return False

    def raise_if_open(self, detail=""):
        """`allow()` for callers that want the named exception instead of a bool."""
        if not self.allow():
            raise LLMCircuitOpen(detail or f"breaker open, retry in "
                                           f"{self.retry_after():.0f}s")

    def retry_after(self):
        """Seconds until the next probe would be admitted (0 when closed)."""
        with self._lock:
            if self._state != OPEN:
                return 0.0
            return max(0.0, self._effective_probe_interval()
                       - (time.time() - self._opened_at))

    def record_success(self):
        with self._lock:
            if self._state != CLOSED:
                logger.info("LLM breaker CLOSED (recovered)")
                self.state_changes += 1
            self._state = CLOSED
            self._fails = 0
            self._probing = False
            self._open_streak = 0

    def record_failure(self, exc=None):
        """Count a TRANSPORT failure. Callers must not pass output failures
        (bad JSON, schema mismatch) or `LLMOverloaded` here — the host answering
        badly, or our own gate shedding load, is not the host being down, and
        counting either would trip the circuit on a healthy server."""
        with self._lock:
            self._fails += 1
            self._probing = False
            if self._state == HALF_OPEN or self._fails >= self.trip_after:
                if self._state != OPEN:
                    logger.error(
                        "LLM breaker OPEN after %d consecutive transport "
                        "failures (%s) — fast-failing LLM calls for %.0fs",
                        self._fails,
                        getattr(exc, "reason", None) or type(exc).__name__
                        if exc else "transport",
                        self._effective_probe_interval())
                    self.state_changes += 1
                self._state = OPEN
                self._open_streak += 1
                self._opened_at = time.time()

    def reset(self):
        """Force back to CLOSED. For tests and for an operator who has just
        restarted Ollama and does not want to wait out the probe interval."""
        with self._lock:
            self._state = CLOSED
            self._fails = 0
            self._probing = False
            self._open_streak = 0
            self._opened_at = 0.0

    def stats(self):
        with self._lock:
            return {"state": self._state,
                    "consecutive_failures": self._fails,
                    "state_changes": self.state_changes,
                    "enabled": self.enabled,
                    "trip_after": self.trip_after,
                    "retry_after_s": round(self.retry_after(), 1)}


# `OllamaBreaker` is the historical name (B27.5) and is still imported by
# gpu_gate and its tests.
OllamaBreaker = CircuitBreaker


# ---------------------------------------------------------------------------
# ONE breaker per process, not one per endpoint.
#
# Keying by URL was considered and dropped: the build role and the tutor role
# almost always resolve to the same Ollama, so per-URL breakers would mostly
# duplicate state, and the one case they differ (mlx_lm serving the build role)
# is rare enough not to justify making `get_breaker()` take an argument that
# every existing call site would have to learn.

_breaker = None
_breaker_lock = threading.Lock()


def get_breaker() -> CircuitBreaker:
    global _breaker
    if _breaker is None:
        with _breaker_lock:
            if _breaker is None:
                _breaker = CircuitBreaker(
                    trip_after=_env_int("OLLAMA_BREAKER_TRIP", 3),
                    probe_interval=_env_float("OLLAMA_BREAKER_PROBE_S", 15.0),
                    probe_max=_env_float("OLLAMA_BREAKER_PROBE_MAX_S", 300.0),
                    enabled=os.getenv("OLLAMA_BREAKER_ENABLED", "1")
                            .strip().lower() not in ("0", "false", "no"))
    return _breaker


def reset_breaker():
    """Drop the singleton so the next `get_breaker()` re-reads the environment.

    The breaker is process-global, so a test that trips it open leaks into every
    later test in the same run — which has already bitten this repo once
    (see the setUp note in tests/core/test_reasoning_effort.py, which had to
    reach into private attributes to undo it). This is the supported way.
    """
    global _breaker
    with _breaker_lock:
        _breaker = None


# ---------------------------------------------------------------------------
# Last failure, per thread.
#
# The build pipeline runs in its own thread and the FSM serves turns in others;
# a module-level "last error" would hand one thread's failure to another and be
# worse than no answer at all. Thread-local keeps the record honest without
# changing any function's return type.

_local = threading.local()


def record_failure_reason(exc):
    """Remember the named failure for the current thread and return it, so call
    sites can `raise record_failure_reason(LLMTimeout(...))` in one line."""
    _local.last = exc
    return exc


def last_llm_failure():
    """The most recent named LLM failure on this thread, or None.

    For the callers that keep the old ""/None contract: the return value cannot
    say which failure happened, but this can.
    """
    return getattr(_local, "last", None)


def clear_llm_failure():
    _local.last = None
