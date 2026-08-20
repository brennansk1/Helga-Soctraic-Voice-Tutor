"""startup_preflight.py — can this machine run Helga right now?

WHY THIS EXISTS
---------------
Helga is an appliance: one Mac mini (Apple M4, 24 GB unified) holding a
~12.7 GB model resident while six containers and the user's own applications
share what is left. Nothing checked that arithmetic before work started. A
course build would simply begin, allocate, and take the machine down with it —
and because the failure surfaced as "the LLM consistently failed to generate 3
modules", the actual cause was two layers away from the message.

The measurement that makes this non-negotiable (docs/MEMORY_ALLOCATION_PLAN.md,
measured on the target machine, generation verified at each size):

    resident   free   decode        state
    13.18 GB    —     30.1 tok/s    comfortable
    14.82 GB   24%    31.0 tok/s    healthy
    15.75 GB   14%    30.9 tok/s    tight
    16.40 GB    8%    no output     thrashing, swap 3.97 GB
    17.72 GB    6%    no output     thrashing, swap 4.79 GB

Throughput is FLAT at ~31 tok/s right up to 15.75 GB and then generation stops
returning usable results at all. There is no gentle slope to trade against: you
are either under the ceiling at full speed or over it and broken. A preflight
that says "this will be slow" would be a lie — it says "this will not work".

WHAT IT IS NOT
--------------
- Not `memory_guard`. That module answers "may this background job run in the
  next few seconds"; it is the measurement layer and this module uses it.
  Preflight answers the larger question — is this machine, this disk and this
  Ollama configuration capable of the job at all.
- Not `scripts/mac_preflight.py`. That is a host-side deploy gate for things
  outside the repository (Docker's architecture, OLLAMA_KEEP_ALIVE, num_ctx).
  This one runs inside the product, is served over HTTP, and re-checks itself.

THE TWO FAILURE MODES ARE DIFFERENT AND MUST READ DIFFERENTLY
-------------------------------------------------------------
"This machine has 16 GB and the model needs 21" is not fixable by closing
Chrome, and telling someone to close Chrome wastes their afternoon. "You have
24 GB but 21 of it is in use" is fixable in ten seconds. Same shortfall in
gigabytes, opposite advice — so they are separate checks with separate wording,
and `installed_memory` never suggests closing anything.

NEVER RAISES
------------
Every check is individually guarded and `preflight()` has a final net. A
preflight that crashes is worse than one that reports `degraded`: the crash
takes down the caller that was trying to protect itself. An unreadable value
produces an `unknown` check, and `unknown` never blocks — we do not stop a user
from working on the strength of a measurement we could not take.
"""

import json
import logging
import os
import platform
import shutil
import time
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------- verdicts
OK = "ok"
DEGRADED = "degraded"
BLOCKED = "blocked"
UNKNOWN = "unknown"

# Ordering used to fold individual checks into one overall state.
_RANK = {OK: 0, UNKNOWN: 1, DEGRADED: 1, BLOCKED: 2}


def _gb(x):
    """One decimal is the actionable precision. '1.4 GB more' is a decision;
    '1.43829 GB more' is noise the reader has to round themselves."""
    return f"{x:.1f} GB"


# ------------------------------------------------------------- thresholds
#
# Every number here is measured on the target machine (docs/MEMORY_BUDGET.md,
# docs/MEMORY_ALLOCATION_PLAN.md) rather than chosen for feel. They are
# env-overridable because the appliance is not the only place this runs.

# What the machine costs before Ollama loads anything: kernel wired 1.68 +
# apps 1.20 + compressor 2.04 + Docker host-side 0.88 + the build subset of
# containers ~1.10. MEMORY_BUDGET §1–2.
NON_MODEL_FLOOR_GB = float(os.getenv("HELGA_NON_MODEL_FLOOR_GB", "7.6"))

# The OOM guard the budget reserves so the machine has somewhere to go when a
# hydration pass spikes. MEMORY_BUDGET §4.
OOM_GUARD_GB = float(os.getenv("HELGA_OOM_GUARD_GB", "2.0"))

# Runtime + KV on top of the weights file at the configured 16k context;
# measured at 0.44 GB, and 128k only takes it to ~2.4. Weights dominate.
MODEL_RUNTIME_OVERHEAD_GB = float(os.getenv("HELGA_MODEL_OVERHEAD_GB", "0.44"))

# Used only when Ollama cannot tell us the real weights size: IQ3_S nail-35b-a3b
# at 16k measured 13.18 GB resident.
DEFAULT_MODEL_RESIDENT_GB = float(os.getenv("HELGA_MODEL_RESIDENT_GB", "13.2"))

# Transient headroom. 1.5 GB is memory_guard's own floor — deliberately "about
# to fail" rather than a comfort margin, because an earlier comfort number
# blocked real work on a machine the kernel called healthy. Below 3.0 GB we say
# so without stopping anyone.
TRANSIENT_BLOCK_GB = float(os.getenv("HELGA_MIN_FREE_GB", "1.5"))
TRANSIENT_WARN_GB = float(os.getenv("HELGA_PREFLIGHT_WARN_FREE_GB", "3.0"))

# Loading weights needs room for the weights plus a little slack for the copy.
MODEL_LOAD_SLACK_GB = float(os.getenv("HELGA_MODEL_LOAD_SLACK_GB", "1.0"))

# Disk. Course data is small — hundreds of MB — but SQLite WAL checkpoints and
# markdown writes fail hard and mid-build when the volume fills, and a model
# pull needs 12.7 GB in one go.
DISK_BLOCK_GB = float(os.getenv("HELGA_DISK_BLOCK_GB", "2.0"))
DISK_WARN_GB = float(os.getenv("HELGA_DISK_WARN_GB", "10.0"))

_DEFAULT_OLLAMA_URL = "http://host.docker.internal:11434"


def _advisory_mode():
    """Downgrade `blocked` to `degraded` overall.

    An escape hatch for someone who knows better than the thresholds — a
    developer on a 16 GB laptop pointing at a small model, say. It changes the
    OVERALL state only: the individual checks keep saying what they measured,
    so nothing is hidden, it is just not held shut.
    """
    return os.getenv("HELGA_PREFLIGHT_ADVISORY", "").lower() in ("1", "true", "yes")


def _in_container():
    """Are we reading a container's memory rather than the machine's?

    This matters more than it looks. Inside Docker on macOS, psutil reports the
    Linux VM's allocation — typically 8 GB — not the Mac's 24. Judged as if it
    were the machine, every deployment would report "this machine has 8 GB and
    needs 21" and the gate would hold the entire app shut forever, on a machine
    that is fine. So a containerised reading is reported as `unknown` with the
    reason said out loud, never as a hardware verdict.
    """
    try:
        if os.path.exists("/.dockerenv"):
            return True
        with open("/proc/1/cgroup", "rt") as fh:
            return any(k in fh.read() for k in ("docker", "kubepods", "containerd"))
    except OSError:
        return False


# ------------------------------------------------------------------ probes

def _http_json(url, timeout):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _ollama_url():
    """Where Ollama is, from this process's point of view.

    The project default is `host.docker.internal`, which is correct for the
    containers and does not resolve anywhere else. Running the preflight from a
    host shell would then report a confident, wrong "Ollama is not answering" —
    the false alarm a preflight can least afford. An explicit OLLAMA_URL always
    wins; this only picks the right default.
    """
    explicit = os.getenv("OLLAMA_URL")
    if explicit:
        return explicit.rstrip("/")
    if _in_container():
        return _DEFAULT_OLLAMA_URL
    return "http://127.0.0.1:11434"


def _configured_model():
    from services.common.model_roles import DEFAULT_MODEL
    return (os.getenv("LLM_MODEL") or os.getenv("OLLAMA_MODEL")
            or DEFAULT_MODEL).strip()


def _model_match(wanted, installed):
    """Exact-tag match with the near miss surfaced. Returns (ok, near, entry).

    A substring test was tried in main.py and reported a green preflight for a
    model Ollama could not serve: asking for 'qwen3:14b' with only
    'qwen3:14b-q4_K_M' pulled passed the check, and then every generation call
    404'd. Ollama resolves a bare name to ':latest' and nothing else, so that
    one alias is honoured and no other.
    """
    entries = [m for m in (installed or []) if m.get("name")]
    names = [m["name"] for m in entries]
    candidates = {wanted} | ({f"{wanted}:latest"} if ":" not in wanted else set())
    for m in entries:
        if m["name"] in candidates:
            return True, None, m
    base = wanted.split(":")[0]
    near = (next((n for n in names if n.startswith(wanted)), None)
            or next((n for n in names if n.split(":")[0] == base), None))
    return False, near, None


def _probe_ollama(readings, timeout):
    """Reachability, the configured model, its real size, and whether it is
    already loaded. All four come from two cheap GETs against the same server.

    Residency is the one that stops a false alarm: with the weights already
    resident, the machine has ALREADY paid for them, and demanding another
    13 GB of free space before letting anyone work would block every healthy
    running appliance.
    """
    url = _ollama_url()
    readings["ollama_url"] = url
    try:
        tags = _http_json(f"{url}/api/tags", timeout)
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as e:
        readings["ollama_reachable"] = False
        readings["ollama_error"] = str(e)
        return
    readings["ollama_reachable"] = True

    installed = tags.get("models") or []
    ok, near, entry = _model_match(readings["model"], installed)
    readings["model_installed"] = ok
    readings["model_near_miss"] = near
    if entry and isinstance(entry.get("size"), (int, float)):
        readings["model_weights_gb"] = float(entry["size"]) / 2 ** 30

    # /api/ps is advisory: if it does not answer we simply do not know whether
    # the weights are loaded, and "do not know" must not become "not loaded".
    try:
        ps = _http_json(f"{url}/api/ps", timeout)
        loaded = [m.get("name", "") for m in (ps.get("models") or [])]
        readings["model_resident_now"] = bool(
            _model_match(readings["model"], [{"name": n} for n in loaded])[0])
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        readings["model_resident_now"] = None


def _read_memory_locally(readings):
    from services.common import memory_guard as mg
    snap = mg.snapshot(force=True)
    if snap.source == "unavailable":
        readings["notes"].append("memory could not be measured on this host")
        return
    readings["total_gb"] = float(snap.total_gb)
    readings["available_gb"] = float(snap.available_gb)
    readings["pressure_level"] = mg.macos_pressure_level()
    readings["pressure_reason"] = mg.pressure_reason(snap)


def _read_memory_from_payload(readings, mem):
    if not isinstance(mem, dict) or mem.get("error"):
        readings["notes"].append(
            "the core service could not measure memory: "
            + str((mem or {}).get("error", "no reading")))
        return
    for src, dst in (("total_gb", "total_gb"), ("available_gb", "available_gb")):
        v = mem.get(src)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            readings[dst] = float(v)
    lvl = mem.get("pressure_level")
    readings["pressure_level"] = lvl if isinstance(lvl, int) else None
    readings["pressure_reason"] = mem.get("reason")


def gather(resources=None, probe_ollama=True, timeout=4.0):
    """Collect every reading the verdict needs. Never raises.

    `resources` is the payload of the core service's /api/system/resources when
    the caller already has it — the web UI does, and re-measuring from a
    different container would give a different and equally wrong answer. With
    nothing passed, we measure locally.
    """
    readings = {
        "total_gb": None, "available_gb": None, "pressure_level": None,
        "pressure_reason": None, "disk_free_gb": None,
        "model": _configured_model(), "ollama_url": _ollama_url(),
        "ollama_reachable": None, "model_installed": None,
        "model_near_miss": None, "model_weights_gb": None,
        "model_resident_now": None,
        "scope": "container" if _in_container() else "host",
        "platform": platform.platform(),
        "notes": [],
    }

    try:
        if resources is None:
            _read_memory_locally(readings)
        else:
            _read_memory_from_payload(readings, (resources or {}).get("memory"))
    except Exception as e:                                  # noqa: BLE001
        logger.debug("preflight memory reading failed: %s", e)
        readings["notes"].append(f"memory reading failed: {e}")

    try:
        disk = ((resources or {}).get("storage") or {}).get("disk") if resources \
            else None
        if disk and isinstance(disk.get("free_bytes"), (int, float)):
            readings["disk_free_gb"] = float(disk["free_bytes"]) / 2 ** 30
        else:
            data_root = os.getenv("DATA_ROOT", "/app/data")
            if not os.path.isdir(data_root):
                data_root = os.getcwd()
            readings["disk_free_gb"] = shutil.disk_usage(data_root).free / 2 ** 30
    except Exception as e:                                  # noqa: BLE001
        logger.debug("preflight disk reading failed: %s", e)
        readings["notes"].append(f"disk reading failed: {e}")

    if probe_ollama:
        try:
            _probe_ollama(readings, timeout)
        except Exception as e:                              # noqa: BLE001
            logger.debug("preflight ollama probe failed: %s", e)
            readings["ollama_reachable"] = None
            readings["notes"].append(f"ollama probe failed: {e}")

    return readings


# ------------------------------------------------------------------ checks

def _check(cid, label, state, reason, remedy=None, measured=None):
    return {"id": cid, "label": label, "state": state, "reason": reason,
            "remedy": remedy, "measured": measured or {}}


def model_resident_gb(readings):
    """Best available estimate of what the configured model costs resident.

    Ollama's own tag listing gives the weights file in bytes, which beats any
    table we could hard-code — it stays correct when someone re-quantises. The
    constant is only the fallback for when Ollama is not answering, which is
    exactly when we cannot ask.
    """
    w = readings.get("model_weights_gb")
    if isinstance(w, (int, float)) and w > 0:
        return w + MODEL_RUNTIME_OVERHEAD_GB, True
    return DEFAULT_MODEL_RESIDENT_GB, False


def _check_installed_memory(r):
    """Hardware capacity. The failure this reports is PERMANENT.

    Nothing here ever suggests closing an application, because no amount of
    closing applications adds RAM to a machine. That distinction is the whole
    reason this is a separate check from `available_memory`.
    """
    label = "Installed memory"
    if r["scope"] == "container":
        return _check(
            "installed_memory", label, UNKNOWN,
            "Measured inside Docker, which reports the container VM's memory "
            "rather than this machine's — so it cannot be judged from here.",
            "Run the preflight on the host (python3 -m services.common."
            "startup_preflight) for a true reading.",
            {"platform": r.get("platform")})

    total = r.get("total_gb")
    if not total:
        return _check("installed_memory", label, UNKNOWN,
                      "Total memory could not be read on this machine.", None)

    resident, exact = model_resident_gb(r)
    required = resident + NON_MODEL_FLOOR_GB
    measured = {"total_gb": round(total, 1),
                "model_resident_gb": round(resident, 1),
                "overhead_gb": NON_MODEL_FLOOR_GB,
                "required_gb": round(required, 1),
                "model_size_measured": exact}
    breakdown = (f"{_gb(resident)} for {r['model']} plus about "
                 f"{_gb(NON_MODEL_FLOOR_GB)} for the operating system, Docker "
                 f"and Helga's own services")

    if total < required:
        return _check(
            "installed_memory", label, BLOCKED,
            f"This machine has {_gb(total)} of memory and Helga needs about "
            f"{_gb(required)} — {breakdown}. Closing applications cannot "
            f"recover the difference; the memory is not installed.",
            f"Run a smaller model — one that fits in about "
            f"{_gb(max(1.0, total - NON_MODEL_FLOOR_GB))} resident — or use a "
            f"machine with at least {_gb(required + OOM_GUARD_GB)} of memory.",
            measured)

    if total < required + OOM_GUARD_GB:
        return _check(
            "installed_memory", label, DEGRADED,
            f"{r['model']} fits in {_gb(total)}, but with under "
            f"{_gb(OOM_GUARD_GB)} to spare — {breakdown}. A build that spikes "
            f"has nowhere to go.",
            "Keep other applications closed while a course is building, or "
            "run a smaller model.",
            measured)

    return _check("installed_memory", label, OK,
                  f"{_gb(total)} installed; {r['model']} needs about "
                  f"{_gb(required)} including the operating system.",
                  None, measured)


def _check_available_memory(r):
    """Headroom right now. The failure this reports is TRANSIENT and the
    remedy is always something the person at the keyboard can do in seconds.

    Judged on the same signals memory_guard uses, deliberately: the safeguard
    card and this gate must agree, or the app will hold itself shut while the
    card says everything is fine.
    """
    label = "Memory available now"
    if r["scope"] == "container":
        return _check(
            "available_memory", label, UNKNOWN,
            "Measured inside Docker, so this is the container VM's free memory "
            "and not the machine's.",
            None, {"platform": r.get("platform")})

    avail = r.get("available_gb")
    if avail is None:
        return _check("available_memory", label, UNKNOWN,
                      "Free memory could not be read on this machine.", None)

    lvl = r.get("pressure_level")
    resident, _exact = model_resident_gb(r)
    loaded = r.get("model_resident_now")
    measured = {"available_gb": round(avail, 1),
                "pressure_level": lvl,
                "model_resident_now": loaded}

    def _free_up(target):
        return (f"Close other applications — Helga needs about "
                f"{_gb(max(0.1, target - avail))} more. It will start on its "
                f"own once there is room; nothing needs to be clicked.")

    # The kernel's own verdict outranks our arithmetic. Level 4 is CRITICAL and
    # means the machine is already thrashing, whatever the free figure claims.
    if isinstance(lvl, int) and lvl >= 4:
        return _check("available_memory", label, BLOCKED,
                      f"macOS reports critical memory pressure with only "
                      f"{_gb(avail)} free. Starting work now would make the "
                      f"machine unusable rather than slow.",
                      _free_up(avail + OOM_GUARD_GB), measured)

    if avail < TRANSIENT_BLOCK_GB:
        return _check("available_memory", label, BLOCKED,
                      f"Only {_gb(avail)} of memory is free. Below "
                      f"{_gb(TRANSIENT_BLOCK_GB)} the machine swaps instead of "
                      f"working.",
                      _free_up(TRANSIENT_WARN_GB), measured)

    # The model is not loaded and there is not room to load it. This is the
    # case that used to take the machine down: work started, Ollama pulled
    # 12.7 GB in, and the box went over the cliff mid-build.
    need = resident + MODEL_LOAD_SLACK_GB
    if loaded is False and avail < need:
        return _check(
            "available_memory", label, BLOCKED,
            f"{r['model']} is not loaded and needs about {_gb(resident)} to "
            f"load, but only {_gb(avail)} is free. Starting now would push "
            f"this machine past the point where it stops producing output at "
            f"all.",
            _free_up(need), measured)

    if (isinstance(lvl, int) and lvl == 2) or avail < TRANSIENT_WARN_GB:
        reason = r.get("pressure_reason") or (
            f"{_gb(avail)} free — under the {_gb(TRANSIENT_WARN_GB)} Helga "
            f"likes to keep in hand")
        return _check("available_memory", label, DEGRADED,
                      f"{reason}. Helga will build more slowly to stay out of "
                      f"the way.",
                      "Closing other applications will speed it back up.",
                      measured)

    return _check("available_memory", label, OK,
                  f"{_gb(avail)} free" + ("" if loaded is None else
                                          (" with the model already loaded"
                                           if loaded else
                                           " with the model not yet loaded")),
                  None, measured)


def _check_disk(r):
    label = "Disk space"
    free = r.get("disk_free_gb")
    if free is None:
        return _check("disk_space", label, UNKNOWN,
                      "Free disk space could not be read.", None)
    measured = {"free_gb": round(free, 1)}
    if free < DISK_BLOCK_GB:
        return _check("disk_space", label, BLOCKED,
                      f"Only {_gb(free)} free on the drive holding course "
                      f"data. Writes will fail part-way through a build and "
                      f"leave a half-written course.",
                      f"Free at least {_gb(DISK_BLOCK_GB - free + 1)} before "
                      f"building anything.",
                      measured)
    if free < DISK_WARN_GB:
        return _check("disk_space", label, DEGRADED,
                      f"{_gb(free)} free. Enough for course text, but not for "
                      f"pulling another model.",
                      "Free some space before pulling a model.", measured)
    return _check("disk_space", label, OK, f"{_gb(free)} free.", None, measured)


def _check_ollama(r):
    """Ollama is a hard dependency with no fallback anywhere in this codebase.

    Reporting it as merely degraded would be dishonest: every teaching turn and
    every build step is an HTTP call to this server. If it is not there, the
    app cannot do the thing it is for.
    """
    label = "Language model"
    url, model = r.get("ollama_url"), r.get("model")
    reachable = r.get("ollama_reachable")
    measured = {"url": url, "model": model,
                "weights_gb": (round(r["model_weights_gb"], 1)
                               if r.get("model_weights_gb") else None),
                "loaded": r.get("model_resident_now")}

    if reachable is None:
        return _check("ollama_model", label, UNKNOWN,
                      f"Ollama at {url} was not checked.", None, measured)
    if reachable is False:
        return _check("ollama_model", label, BLOCKED,
                      f"Ollama is not answering at {url}. Helga cannot teach "
                      f"or build without it — there is no offline fallback.",
                      "Start Ollama (`ollama serve`), then this will clear "
                      "itself.", measured)
    if r.get("model_installed") is False:
        near = r.get("model_near_miss")
        extra = ""
        if near:
            extra = (f" The closest installed tag is '{near}', which Ollama "
                     f"treats as a different model — requests for '{model}' "
                     f"return 404.")
        return _check("ollama_model", label, BLOCKED,
                      f"Ollama is running but '{model}' is not installed.{extra}",
                      (f"Run `ollama pull {model}`"
                       + (f", or set OLLAMA_MODEL={near} if that is the one you "
                          f"meant." if near else ".")),
                      measured)

    weights = r.get("model_weights_gb")
    detail = f" ({_gb(weights)} of weights)" if weights else ""
    return _check("ollama_model", label, OK,
                  f"'{model}' is installed and Ollama is answering{detail}.",
                  None, measured)


# ----------------------------------------------------------------- verdict

def evaluate(readings):
    """Turn readings into the structured verdict. Pure; never raises.

    Kept separate from `gather` so the interesting logic can be tested against
    a dict instead of against a machine — including the readings a real machine
    will not produce on demand, like 8 GB of installed RAM.
    """
    checks = []
    # The id and label live here rather than being derived from the function,
    # so a check that throws still comes back under the id the UI keys on —
    # the caller must never have to handle a check simply vanishing.
    for cid, label, fn in (
            ("installed_memory", "Installed memory", _check_installed_memory),
            ("available_memory", "Memory available now", _check_available_memory),
            ("disk_space", "Disk space", _check_disk),
            ("ollama_model", "Language model", _check_ollama)):
        try:
            checks.append(fn(readings))
        except Exception as e:                              # noqa: BLE001
            # A check that throws is a bug in the check, or a reading that was
            # not a number. Either way it is "we could not tell", not a verdict
            # about the machine.
            logger.warning("preflight check %s failed: %s", cid, e)
            checks.append(_check(cid, label, UNKNOWN,
                                 f"This check could not run: {e}",
                                 "This says nothing about the machine — only "
                                 "that it was not measured."))

    worst = OK
    for c in checks:
        if _RANK.get(c["state"], 1) > _RANK[worst]:
            worst = c["state"] if c["state"] != UNKNOWN else DEGRADED
    # `unknown` alone means "we could not tell", which is a degraded verdict,
    # never a blocking one — see the module docstring.
    state = DEGRADED if worst == UNKNOWN else worst

    advisory = _advisory_mode()
    if state == BLOCKED and advisory:
        state = DEGRADED

    blocking = [c["id"] for c in checks if c["state"] == BLOCKED]
    if blocking:
        first = next(c for c in checks if c["state"] == BLOCKED)
        summary = first["reason"]
    elif state == DEGRADED:
        first = next((c for c in checks
                      if c["state"] in (DEGRADED, UNKNOWN)), None)
        summary = first["reason"] if first else "Some checks were inconclusive."
    else:
        summary = "This machine has room to run Helga."

    return {
        "state": state,
        "summary": summary,
        "checks": checks,
        "blocking": blocking,
        "advisory": advisory,
        "scope": readings.get("scope", "host"),
        "notes": list(readings.get("notes") or []),
        "checked_at": time.time(),
    }


def preflight(resources=None, probe_ollama=True, timeout=4.0):
    """The whole thing: measure, judge, return. Never raises.

    Returns a dict with `state` (ok / degraded / blocked), a one-line
    `summary`, and `checks` — each carrying its own state, a reason in plain
    words with the actual numbers in it, and, when it is failing, what the
    person at the keyboard should do about it.
    """
    try:
        return evaluate(gather(resources=resources, probe_ollama=probe_ollama,
                               timeout=timeout))
    except Exception as e:                                  # noqa: BLE001
        # The last net. A preflight that raises takes down the caller that was
        # trying to protect itself, which is the opposite of the job.
        logger.warning("preflight failed entirely: %s", e)
        return {
            "state": DEGRADED,
            "summary": f"The startup check could not run: {e}",
            "checks": [_check("preflight", "Startup preflight", UNKNOWN,
                              f"The check itself failed: {e}",
                              "This does not mean the machine is unhealthy — "
                              "only that it was not measured.")],
            "blocking": [], "advisory": _advisory_mode(), "scope": "unknown",
            "notes": [], "checked_at": time.time(),
        }


def describe(verdict=None):
    """One line per check, for a startup log or a terminal."""
    v = verdict or preflight()
    mark = {OK: "  ok  ", DEGRADED: " warn ", BLOCKED: " BLOCK", UNKNOWN: "  ??  "}
    lines = [f"Helga preflight: {v['state'].upper()} — {v['summary']}"]
    for c in v["checks"]:
        lines.append(f"[{mark.get(c['state'], '  ??  ')}] {c['label']}: "
                     f"{c['reason']}")
        if c.get("remedy") and c["state"] != OK:
            lines.append(f"{'':>9}fix: {c['remedy']}")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    v = preflight()
    print(describe(v))
    sys.exit(1 if v["state"] == BLOCKED else 0)
