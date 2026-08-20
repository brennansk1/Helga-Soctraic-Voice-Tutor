# REGISTER WITH (in app.py, next to the notes_api mount):
#
#     from setup_api import setup_api
#     app.register_blueprint(setup_api)
#
"""First-run setup: everything a fresh machine needs before Helga can teach.

WHY THIS EXISTS
---------------
Helga is an offline appliance. Downloading it gets you the code; it does not
get you a working tutor, and until now nothing said so. Every prerequisite was
discovered the same way — something failed strangely, an hour later, with a
message pointing at the wrong layer:

  * The model is a ~12.7 GB `ollama pull` that nothing prompts for. Without it
    every generation call 404s and the symptom surfaces as "the LLM
    consistently failed to generate 3 modules".
  * Voice was dead in four places at once (services/tts/requirements-host.txt
    records all four). The containers were healthy the entire time. The
    services simply were not running, and nothing was looking.
  * The host services need `.venv-host` on Python >= 3.10, because this
    machine's `python3` is 3.9 and pip's answer to an unsatisfiable pin is to
    silently install a three-year-old release rather than refuse.
  * searxng and research must be up or course content has no grounding — and a
    course built without grounding looks fine until you read it.

Each of those is cheap to check and expensive to discover. So they are checked
here, at the start, by name, with the exact command that fixes them.

WHAT IT IS NOT
--------------
Not a replacement for `startup_preflight`. That module answers "can this
hardware run the thing" and this one reuses it wholesale as the first step —
re-deriving those thresholds here would give the appliance two opinions about
its own memory. This module answers the larger and more boring question: is
this installation FINISHED.

Not an installer. It changes exactly one thing on the machine — it can pull the
configured model through Ollama's own API — and that is the only prerequisite
that is both large enough to need a progress bar and safe enough to automate.
Creating a virtualenv, installing system packages, or starting daemons all
either need the user's password or write outside the repository, so they are
INSTRUCTIONS with the literal command to run, never executions. A setup page
that runs `sudo` on your behalf is not a convenience, it is a liability.

HONEST ABOUT WHAT IT DID NOT MEASURE
------------------------------------
Three states, not two. "Ollama says the model is not installed" and "Ollama did
not answer, so nobody knows whether the model is installed" look identical from
a green tick and are opposite problems: the first is fixed by a download, the
second by starting a daemon, and pulling a model at a server that is not there
just fails weirdly. So a step whose prerequisite is down reports `unknown` with
`blocked_by` naming the step it is waiting on, and the page says so in words.

NEVER RAISES
------------
`evaluate()` is pure and total: any reading may be missing, None, or the wrong
type and it still returns a report. A setup page that 500s is a first-run
experience that ends at a stack trace.
"""

import json
import logging
import os
import shutil
import sys
import threading
import time

import requests
from flask import (Blueprint, Response, current_app, jsonify, render_template,
                   request, session, stream_with_context)

logger = logging.getLogger(__name__)

setup_api = Blueprint("setup_api", __name__)

# States, deliberately the same four words startup_preflight and resources.js
# already use. A second vocabulary for the same idea is how two surfaces start
# disagreeing in public about the same machine.
OK = "ok"
DEGRADED = "degraded"
BLOCKED = "blocked"
UNKNOWN = "unknown"

_RANK = {OK: 0, DEGRADED: 1, UNKNOWN: 1, BLOCKED: 2}

# Probes are against localhost or the Docker host gateway. Short, because a
# setup page that takes thirty seconds to tell you something is down is itself
# a bad first-run experience.
PROBE_TIMEOUT = float(os.getenv("HELGA_SETUP_PROBE_TIMEOUT", "3.0"))

# Refuse to start a multi-gigabyte download onto a nearly full disk. Not a
# guess at the model size — we cannot know it before pulling — just the point
# below which the write is certain to fail part-way and leave a broken blob.
PULL_MIN_FREE_GB = float(os.getenv("HELGA_PULL_MIN_FREE_GB", "5.0"))


# --------------------------------------------------------------- configuration

def _model_name():
    """The configured model, and WHERE the name came from.

    The provenance matters more than it looks. The web-ui service does not
    currently receive OLLAMA_MODEL from docker-compose, so this process falls
    back to a default that may not be the model the rest of the stack is
    using — and a setup page confidently checking the wrong model name is
    worse than one that admits it is guessing. So the guess is labelled.
    """
    for var in ("LLM_MODEL", "OLLAMA_MODEL"):
        val = (os.getenv(var) or "").strip()
        if val:
            return val, var
    from services.common.model_roles import DEFAULT_MODEL
    return DEFAULT_MODEL, "default"


def _in_container():
    try:
        if os.path.exists("/.dockerenv"):
            return True
        with open("/proc/1/cgroup", "rt") as fh:
            return any(k in fh.read() for k in ("docker", "kubepods", "containerd"))
    except OSError:
        return False


def _ollama_url():
    """Same resolution rule as startup_preflight, restated rather than imported.

    Importing it would make this blueprint fail to load on the deployments
    where services/common is not in the image — exactly the deployments where
    someone most needs the setup page. The rule is four lines; the coupling
    would cost more than the duplication.
    """
    explicit = os.getenv("OLLAMA_URL")
    if explicit:
        return explicit.rstrip("/")
    return ("http://host.docker.internal:11434" if _in_container()
            else "http://127.0.0.1:11434")


def _service_urls():
    """The services this installation expects, and how badly each is needed.

    `required` is the difference between "Helga cannot teach" and "Helga will
    teach with a gap you should know about". core and rag are the tutor itself;
    searxng and research are what stop a generated course from being confident
    fiction, which is serious but not fatal to starting.
    """
    return [
        {"id": "core", "label": "Core logic",
         "url": os.getenv("CORE_LOGIC_URL", "http://helga-core-logic:5003"),
         "path": "/health", "required": True,
         "what": "the tutor itself — the state machine that runs a lesson"},
        {"id": "rag", "label": "Course library",
         "url": os.getenv("RAG_URL", "http://helga-rag-engine:5002"),
         "path": "/health", "required": True,
         "what": "course storage, search and flashcards"},
        {"id": "searxng", "label": "Search",
         "url": os.getenv("SEARXNG_URL", "http://helga-searxng:8080"),
         "path": "/healthz", "required": False,
         "what": "the offline search index course building draws on"},
        {"id": "research", "label": "Research",
         "url": os.getenv("RESEARCH_URL", "http://helga-research:5006"),
         "path": "/health", "required": False,
         "what": "grounds new course content in real sources"},
    ]


def _voice_urls():
    return [
        {"id": "tts", "label": "Speech out (Kokoro)",
         "url": os.getenv("TTS_URL", "http://host.docker.internal:5005")},
        {"id": "stt", "label": "Speech in (Nemotron)",
         "url": os.getenv("STT_URL", "http://host.docker.internal:5001")},
    ]


def _repo_root():
    """The repository on disk, if this process can see it at all.

    In the deployed container it cannot: docker-compose mounts
    ./services/web-ui at /app and nothing above it, so `.venv-host` is
    genuinely invisible from here. That is reported as "could not check", never
    as "not installed" — telling a user to create a virtualenv they already
    have is how a setup page loses their trust on the first screen.
    """
    candidates = []
    env = os.getenv("HELGA_REPO_ROOT")
    if env:
        candidates.append(env)
    here = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.abspath(os.path.join(here, "..", "..")))
    candidates.append("/app")
    for c in candidates:
        try:
            if os.path.isfile(os.path.join(c, "scripts", "host_services.sh")):
                return c
        except OSError:
            continue
    return None


def _gb(x):
    return f"{x:.1f} GB"


# -------------------------------------------------------------------- probing

def _get_json(url, timeout=PROBE_TIMEOUT):
    """(payload, error). Never raises; the error string is for the page."""
    try:
        r = requests.get(url, timeout=timeout)
        if r.status_code >= 400:
            return None, f"HTTP {r.status_code}"
        return r.json(), None
    except ValueError:
        # Answered, but not with JSON. That is still "it is up" for a health
        # probe, so callers distinguish this from a connection failure.
        return {}, None
    except requests.Timeout:
        return None, f"no answer within {timeout:.0f}s"
    except requests.ConnectionError as e:
        # urllib3's exception text is a four-line nest of pool wrappers, and
        # putting it on a first-run page taught the reader nothing except that
        # the product leaks stack traces. The two cases that actually differ
        # are named; anything else stays generic rather than becoming a dump.
        msg = str(e).lower()
        if "refused" in msg:
            return None, "connection refused"
        if "not known" in msg or "nodename nor servname" in msg \
                or "name resolution" in msg:
            return None, "that address does not resolve from here"
        return None, "could not connect"
    except requests.RequestException as e:
        return None, str(e)[:160]
    except Exception as e:                                   # noqa: BLE001
        return None, str(e)[:160]


def _model_match(wanted, names):
    """Exact-tag match, with the near miss surfaced. Returns (ok, near).

    Deliberately the same rule as startup_preflight: Ollama resolves a bare
    name to ':latest' and to nothing else, so a substring test reports a green
    check for a model the server will 404 on. That exact bug shipped once
    already, in main.py's own preflight.
    """
    names = [n for n in (names or []) if isinstance(n, str)]
    candidates = {wanted} | ({f"{wanted}:latest"} if ":" not in wanted else set())
    if any(n in candidates for n in names):
        return True, None
    base = wanted.split(":")[0]
    near = (next((n for n in names if n.startswith(wanted)), None)
            or next((n for n in names if n.split(":")[0] == base), None))
    return False, near


def _probe_ollama(readings):
    url = readings["ollama_url"]
    tags, err = _get_json(f"{url}/api/tags", timeout=PROBE_TIMEOUT)
    if tags is None:
        readings["ollama_reachable"] = False
        readings["ollama_error"] = err
        # Left as None on purpose: not False. Nobody knows.
        readings["model_installed"] = None
        return
    readings["ollama_reachable"] = True
    entries = [m for m in (tags.get("models") or []) if isinstance(m, dict)]
    names = [m.get("name") for m in entries if m.get("name")]
    readings["installed_models"] = names
    ok, near = _model_match(readings["model"], names)
    readings["model_installed"] = ok
    readings["model_near_miss"] = near
    if ok:
        for m in entries:
            if m.get("name") in (readings["model"], readings["model"] + ":latest"):
                if isinstance(m.get("size"), (int, float)):
                    readings["model_size_gb"] = float(m["size"]) / 2 ** 30
                break


def _probe_services(readings):
    out = []
    for svc in _service_urls():
        payload, err = _get_json(svc["url"] + svc["path"])
        out.append({
            "id": svc["id"], "label": svc["label"], "url": svc["url"],
            "required": svc["required"], "what": svc["what"],
            "up": payload is not None, "error": err,
        })
    readings["services"] = out


def _probe_voice(readings):
    out = []
    for svc in _voice_urls():
        payload, err = _get_json(svc["url"] + "/health")
        entry = {"id": svc["id"], "label": svc["label"], "url": svc["url"],
                 "up": payload is not None, "error": err, "backend": None}
        if isinstance(payload, dict):
            # The backend that ACTUALLY loaded, not the one configured. A
            # silent fall back from mlx to torch is the regression that hid
            # inside a healthy-looking /health for weeks.
            entry["backend"] = payload.get("backend_active") \
                or payload.get("backend") or payload.get("backend_configured")
        out.append(entry)
    readings["voice"] = out


def _probe_venv(readings):
    """Is the host virtualenv there, and is it on a Python that can run voice?

    We can only read the interpreter's own version string from disk without
    executing it, so what is checked is existence plus the pyvenv.cfg version —
    enough to catch the failure that actually happened (a venv built on 3.9,
    where pip resolves mlx-audio to a three-year-old release instead of
    refusing) without running anything out of a directory we do not control.
    """
    root = _repo_root()
    if not root:
        readings["venv"] = {
            "state": UNKNOWN, "path": None,
            "detail": "The repository is not visible from this process — in the "
                      "deployed container only services/web-ui is mounted — so "
                      "the host virtualenv could not be inspected from here."}
        return
    path = os.path.join(root, ".venv-host")
    py = os.path.join(path, "bin", "python")
    if not os.path.exists(py):
        readings["venv"] = {
            "state": BLOCKED, "path": path,
            "detail": f"{path} does not exist."}
        return
    version = None
    try:
        cfg = os.path.join(path, "pyvenv.cfg")
        if os.path.isfile(cfg):
            with open(cfg, "rt") as fh:
                for line in fh:
                    if line.lower().startswith("version"):
                        version = line.split("=", 1)[-1].strip()
                        break
    except OSError as e:
        logger.debug("could not read pyvenv.cfg: %s", e)

    if version:
        try:
            major, minor = (int(p) for p in version.split(".")[:2])
        except ValueError:
            major, minor = 0, 0
        if (major, minor) < (3, 10):
            readings["venv"] = {
                "state": BLOCKED, "path": path, "version": version,
                "detail": f"{path} is built on Python {version}. mlx-audio "
                          f"dropped 3.9 at 0.2.10, so pip silently installs a "
                          f"three-year-old release here instead of refusing — "
                          f"which is how the voice path came to be dead while "
                          f"every version pin in the repository looked correct."}
            return
    readings["venv"] = {"state": OK, "path": path, "version": version,
                        "detail": f"{path} exists"
                                  + (f" on Python {version}." if version else ".")}


def _load_preflight_module():
    """services.common.startup_preflight, or None with the reason kept.

    Written defensively for the same reason app.py's copy is: the web-ui image
    does not always contain services/common, and an ImportError here must
    degrade the hardware step to "not measured", not take down the page that
    was going to explain the problem.
    """
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    for candidate in (root, "/app"):
        if candidate not in sys.path:
            sys.path.insert(0, candidate)
    try:
        from services.common import startup_preflight as mod
        return mod, None
    except Exception as e:                                   # noqa: BLE001
        return None, str(e)


def gather(probe=True):
    """Every reading the report needs. Never raises."""
    model, model_source = _model_name()
    readings = {
        "model": model, "model_source": model_source,
        "ollama_url": _ollama_url(),
        "ollama_reachable": None, "ollama_error": None,
        "model_installed": None, "model_near_miss": None,
        "model_size_gb": None, "installed_models": [],
        "services": None, "voice": None, "venv": None,
        "preflight": None, "preflight_error": None,
        "scope": "container" if _in_container() else "host",
        "pull": pull_snapshot(),
        "notes": [],
    }
    if not probe:
        return readings

    mod, err = _load_preflight_module()
    if mod is None:
        readings["preflight_error"] = err
    else:
        try:
            resources = None
            core = os.getenv("CORE_LOGIC_URL", "http://helga-core-logic:5003")
            payload, _e = _get_json(f"{core}/api/system/resources", timeout=6)
            if isinstance(payload, dict) and payload:
                resources = payload
            readings["preflight"] = mod.preflight(resources=resources)
        except Exception as e:                               # noqa: BLE001
            logger.warning("setup: preflight failed: %s", e)
            readings["preflight_error"] = str(e)

    for fn in (_probe_ollama, _probe_services, _probe_voice, _probe_venv):
        try:
            fn(readings)
        except Exception as e:                               # noqa: BLE001
            logger.warning("setup probe %s failed: %s", fn.__name__, e)
            readings["notes"].append(f"{fn.__name__} could not run: {e}")
    return readings


# --------------------------------------------------------------------- steps

def _down_reason(url, err):
    """Why a health probe failed, in words that match what happened.

    "nothing is listening at http://…:5006" was being printed for a server that
    answered 404 — something IS listening, it just is not the service we asked
    for, and the two send you to completely different places: one to start a
    container, the other to check a port or a path. A probe that describes the
    wrong failure is worse than one that says nothing.
    """
    if err and str(err).startswith("HTTP "):
        return f"{url} answered {err} instead of a health check"
    return f"nothing is listening at {url}" + (f" ({err})" if err else "")


def _step(sid, title, state, headline, detail=None, commands=None,
          blocked_by=None, fixable=None, measured=None, sub=None, why=None):
    return {"id": sid, "title": title, "state": state, "headline": headline,
            "detail": detail, "why": why, "commands": list(commands or []),
            "blocked_by": blocked_by, "fixable": fixable,
            "measured": measured or {}, "sub": list(sub or [])}


def _as_bool(v):
    """Tri-state coercion. Anything that is not a real boolean is `unknown`,
    because a reading we cannot interpret is not a verdict about the machine."""
    return v if isinstance(v, bool) else None


def _disk_free_gb(preflight):
    try:
        for c in (preflight or {}).get("checks") or []:
            if c.get("id") == "disk_space":
                v = (c.get("measured") or {}).get("free_gb")
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    return float(v)
    except Exception:                                        # noqa: BLE001
        pass
    return None


def _step_hardware(r):
    """Step 1 — reuse startup_preflight verbatim rather than re-deciding.

    Every threshold in that module is measured on the target machine. A second
    opinion here would eventually drift from the safeguard card and the startup
    gate, and then the appliance would contradict itself about its own memory
    in three places at once.
    """
    why = ("A ~12.7 GB model on a 24 GB machine has a measured cliff, not a "
           "slope: full speed up to about 15.0 GB resident, then no usable "
           "output at all past 16.4.")
    v = r.get("preflight")
    if not isinstance(v, dict) or not v.get("checks"):
        reason = r.get("preflight_error") or "the hardware check did not run"
        return _step(
            "hardware", "This machine", UNKNOWN,
            "This machine was not measured.",
            f"The hardware check could not run here: {reason}. That says "
            f"nothing about the machine — only that nobody looked.",
            commands=["python3 -m services.common.startup_preflight"],
            why=why)

    checks = [c for c in v["checks"] if isinstance(c, dict)]
    sub = [{"label": c.get("label") or c.get("id"),
            "state": c.get("state") or UNKNOWN,
            "reason": c.get("reason") or "",
            "remedy": c.get("remedy") or ""} for c in checks
           # The language-model check belongs to steps 2 and 3, where it has
           # a remedy the user can act on. Repeating it here would make one
           # missing model look like two separate problems.
           if c.get("id") != "ollama_model"]

    worst = OK
    for s in sub:
        st = s["state"] if s["state"] in _RANK else UNKNOWN
        if _RANK[st] > _RANK[worst]:
            worst = st
    # `unknown` folds to `degraded`, exactly as startup_preflight folds it: not
    # knowing is a caveat on the page, never a reason to hold the door shut.
    state = DEGRADED if worst == UNKNOWN else worst

    # HELGA_PREFLIGHT_ADVISORY is the existing escape hatch for someone who
    # knows better than the thresholds — a developer on a 16 GB laptop pointing
    # at a small model. It is honoured here rather than reinvented, because a
    # setup page that stays blocked after the operator has explicitly turned
    # blocking off is a second opinion the appliance has no business having.
    # The sub-rows keep saying exactly what they measured; only the door opens.
    if state == BLOCKED and v.get("advisory"):
        state = DEGRADED

    # The headline is composed from THIS step's own checks and never borrowed
    # from the preflight's overall summary. The summary covers the
    # language-model check dropped just above, so borrowing it put "Ollama is
    # running but 'fake-model' is not installed" under a green "This machine"
    # heading — the hardware step reporting a problem that belongs to step 3,
    # and reporting it while calling itself done.
    worst_sub = next((s for s in sub if s["state"] == BLOCKED),
                     None) or next((s for s in sub if s["state"] == DEGRADED),
                                   None)
    if r.get("scope") == "container":
        headline = "Measured inside Docker, so memory describes the container."
        detail = ("Docker Desktop on macOS reports the Linux VM's allocation, "
                  "not this Mac's. Run the host command below for a true "
                  "reading; nothing here is a verdict on your hardware.")
        if worst_sub and worst_sub["state"] == BLOCKED:
            headline = worst_sub["reason"] or headline
        cmds = ["python3 -m services.common.startup_preflight"]
        state = DEGRADED if state != BLOCKED else BLOCKED
    elif worst_sub:
        headline = worst_sub["reason"] or "One of the hardware checks failed."
        # The sub-row this sentence came from keeps its label, its dot and its
        # remedy but loses its prose: printing the identical paragraph twice,
        # once as the headline and again three lines below it, made the page
        # look padded and made the OTHER two checks harder to find.
        worst_sub["reason"] = ""
        detail = None
        cmds = []
    else:
        headline = "This machine has the memory and the disk to run Helga."
        detail = None
        cmds = []
    return _step("hardware", "This machine", state, headline, detail,
                 commands=cmds, sub=sub, why=why,
                 measured={"scope": r.get("scope")})


def _step_ollama(r):
    why = ("Every teaching turn and every build step is an HTTP call to "
           "Ollama. There is no offline fallback anywhere in the codebase.")
    url = r.get("ollama_url") or "the configured address"
    reachable = _as_bool(r.get("ollama_reachable"))
    start_cmds = ["ollama serve"]
    if r.get("scope") == "container":
        start_cmds.append("# run this on the Mac itself, not inside a container")

    if reachable is True:
        return _step("ollama", "Ollama is running", OK,
                     f"Answering at {url}.", why=why,
                     measured={"url": url})
    if reachable is False:
        err = r.get("ollama_error")
        detail = "Install it from ollama.com if it is not on this machine yet."
        if r.get("scope") == "container" and "host.docker.internal" in str(url):
            detail += (" From inside Docker this address depends on the "
                       "`extra_hosts: host.docker.internal:host-gateway` entry "
                       "in docker-compose.yml.")
        return _step("ollama", "Ollama is running", BLOCKED,
                     f"Nothing is answering at {url}.",
                     detail + (f" (probe said: {err})" if err else ""),
                     commands=start_cmds, why=why, measured={"url": url})
    return _step("ollama", "Ollama is running", UNKNOWN,
                 f"Ollama at {url} was not checked.",
                 "This is not a pass — it is an unmeasured step.",
                 commands=start_cmds, why=why, measured={"url": url})


def _step_model(r):
    """Step 3 — and the one place where "missing" and "unmeasured" must not
    collapse into the same tick.

    Ollama down and model absent are opposite problems with opposite fixes, and
    a pull fired at a server that is not there fails in a way that reads like a
    network fault. So when step 2 is not green this step reports `unknown` and
    names what it is waiting on, and the pull button is not offered."""
    model = r.get("model") or "the configured model"
    source = r.get("model_source")
    why = (f"'{model}' is the model this installation is configured to use. "
           f"Ollama resolves a bare name to ':latest' and to nothing else, so "
           f"a near-miss tag is a 404 on every request, not a close enough.")
    measured = {"model": model, "source": source}
    if r.get("model_size_gb") is not None:
        try:
            measured["size_gb"] = round(float(r["model_size_gb"]), 1)
        except (TypeError, ValueError):
            pass

    guessing = None
    if source == "default":
        # The web-ui service is not given OLLAMA_MODEL by docker-compose, so
        # this process can be checking a different model from the one core and
        # rag call. That used to be likely, because the built-in default and
        # the compose default were different models; they are the same now and
        # a test keeps them that way, so the only way they diverge is if
        # OLLAMA_MODEL is set for the other services and not for this one.
        # Saying so beats a blanket "this may be wrong" on the ordinary path,
        # which teaches people to ignore the warning.
        guessing = (f"No LLM_MODEL or OLLAMA_MODEL is set here, so this is the "
                    f"built-in default '{model}' — the same model "
                    f"docker-compose defaults to. If you set OLLAMA_MODEL for "
                    f"the other services, set it here too so this check reads "
                    f"the model they actually call.")

    reachable = _as_bool(r.get("ollama_reachable"))
    if reachable is not True:
        return _step("model", "The language model", UNKNOWN,
                     f"Whether '{model}' is installed is unknown.",
                     "Ollama is not answering, so nothing could ask it what it "
                     "has. This is different from the model being missing: "
                     "start Ollama first, then this step can check.",
                     blocked_by="ollama", why=why, measured=measured)

    installed = _as_bool(r.get("model_installed"))
    if installed is True:
        size = measured.get("size_gb")
        return _step("model", "The language model", OK,
                     f"'{model}' is installed"
                     + (f" ({_gb(size)} of weights)." if size else "."),
                     guessing, why=why, measured=measured)

    if installed is None:
        return _step("model", "The language model", UNKNOWN,
                     "Ollama answered, but its model list could not be read.",
                     "Treat this as unmeasured rather than as a missing model.",
                     commands=["ollama list"], why=why, measured=measured)

    near = r.get("model_near_miss")
    detail = guessing or ""
    if near:
        detail = ((detail + " ") if detail else "") + (
            f"The closest installed tag is '{near}'. Ollama treats that as a "
            f"different model — requests for '{model}' return 404. If '{near}' "
            f"is the one you meant, set OLLAMA_MODEL={near} instead of pulling.")
    free = _disk_free_gb(r.get("preflight"))
    if free is not None:
        measured["disk_free_gb"] = round(free, 1)
        if free < 20:
            detail = ((detail + " ") if detail else "") + (
                f"Only {_gb(free)} free on this disk; a model download runs to "
                f"several gigabytes.")
    return _step("model", "The language model", BLOCKED,
                 f"'{model}' is not installed.",
                 detail or "This is a multi-gigabyte download. It shows real "
                           "byte progress once it starts.",
                 commands=[f"ollama pull {model}"],
                 fixable="pull", why=why, measured=measured)


def _step_voice(r):
    """Step 4 — voice, and deliberately NOT a blocker.

    Helga teaches in text without it, so holding the whole app shut over a
    silent Kokoro would be a lie about what is broken. But it gets the loudest
    non-blocking wording available, because "the containers were healthy and
    there was simply no voice" is the exact failure this page was built for.
    """
    why = ("TTS and STT need Metal and the Neural Engine, which a Linux "
           "container on macOS does not have — so they run natively on the "
           "host out of .venv-host, and a green Docker stack says nothing "
           "about whether they are up.")
    voice = r.get("voice")
    venv = r.get("venv") if isinstance(r.get("venv"), dict) else {}
    cmds = ["scripts/host_services.sh start"]
    sub = []

    if not isinstance(voice, list):
        return _step("voice", "Voice", UNKNOWN,
                     "The voice services were not checked.",
                     "Nothing was measured here; this is not a pass.",
                     commands=cmds, why=why)

    for v in voice:
        if not isinstance(v, dict):
            continue
        up = _as_bool(v.get("up"))
        sub.append({
            "label": v.get("label") or v.get("id"),
            "state": OK if up is True else (BLOCKED if up is False else UNKNOWN),
            "reason": (f"answering at {v.get('url')}"
                       + (f" (backend {v['backend']})" if v.get("backend") else "")
                       ) if up is True else
                      _down_reason(v.get("url"), v.get("error")),
            "remedy": "",
        })

    venv_state = venv.get("state") or UNKNOWN
    if venv_state != OK:
        sub.append({"label": "Host virtualenv (.venv-host)",
                    "state": venv_state,
                    "reason": venv.get("detail") or "",
                    "remedy": ""})
        if venv_state == BLOCKED:
            cmds = ["python3.12 -m venv .venv-host",
                    ".venv-host/bin/pip install -r services/tts/"
                    "requirements-host.txt",
                    "scripts/host_services.sh start"]
    else:
        sub.append({"label": "Host virtualenv (.venv-host)", "state": OK,
                    "reason": venv.get("detail") or "", "remedy": ""})

    down = [s for s in sub if s["state"] == BLOCKED]
    unmeasured = [s for s in sub if s["state"] == UNKNOWN]
    if down:
        return _step("voice", "Voice", DEGRADED,
                     f"{len(down)} of {len(sub)} voice checks "
                     f"{'is' if len(down) == 1 else 'are'} failing.",
                     "Helga can still teach in text — this does not stop you. "
                     "But nothing will be spoken and the microphone will not "
                     "be heard, and neither failure announces itself later.",
                     commands=cmds, sub=sub, why=why)
    if unmeasured:
        return _step("voice", "Voice", DEGRADED,
                     "Voice is running, but part of it could not be checked.",
                     "See the lines below for what was not measured.",
                     commands=cmds, sub=sub, why=why)
    return _step("voice", "Voice", OK, "Speech in and speech out are answering.",
                 None, sub=sub, why=why)


def _step_services(r):
    why = ("core and rag are the tutor itself. searxng and research are what "
           "keep generated course content tied to real sources — without them "
           "a course still builds, and reads like confident fiction.")
    svcs = r.get("services")
    cmds = ["docker compose up -d"]
    if not isinstance(svcs, list):
        return _step("services", "Background services", UNKNOWN,
                     "The service health checks did not run.",
                     "Nothing was measured here; this is not a pass.",
                     commands=cmds, why=why)

    sub = []
    for s in svcs:
        if not isinstance(s, dict):
            continue
        up = _as_bool(s.get("up"))
        sub.append({
            "label": s.get("label") or s.get("id"),
            "state": OK if up is True else (BLOCKED if up is False else UNKNOWN),
            "reason": (f"answering at {s.get('url')}" if up is True
                       else _down_reason(s.get("url"), s.get("error"))),
            "remedy": "" if up is True else s.get("what") or "",
        })

    required_down = [s for s in svcs if isinstance(s, dict)
                     and s.get("required") and _as_bool(s.get("up")) is not True]
    optional_down = [s for s in svcs if isinstance(s, dict)
                     and not s.get("required") and _as_bool(s.get("up")) is not True]
    if required_down:
        names = ", ".join(str(s.get("label")) for s in required_down)
        return _step("services", "Background services", BLOCKED,
                     f"{names} not answering.",
                     "The web interface you are reading is up, so Docker "
                     "itself is running — these containers are not.",
                     commands=cmds, sub=sub, why=why)
    if optional_down:
        names = ", ".join(str(s.get("label")) for s in optional_down)
        return _step("services", "Background services", DEGRADED,
                     f"{names} not answering.",
                     "Courses will still build, but their content will not be "
                     "grounded in sources — which is a quality problem you "
                     "will not see until you read the course.",
                     commands=cmds, sub=sub, why=why)
    return _step("services", "Background services", OK,
                 "Every service is answering.", None, sub=sub, why=why)


_STEP_FNS = (
    ("hardware", "This machine", _step_hardware),
    ("ollama", "Ollama is running", _step_ollama),
    ("model", "The language model", _step_model),
    ("voice", "Voice", _step_voice),
    ("services", "Background services", _step_services),
)


def evaluate(readings):
    """Readings in, setup report out. Pure, total, never raises.

    Two numbers, because they answer different questions and conflating them
    would let the page lie in one direction or the other:

      `done`  counts only steps that are actually green. A step we could not
              measure is never counted done — that is the whole discipline of
              this file.
      `ready` is true when nothing is BLOCKED. A degraded step (voice down,
              memory unmeasurable inside Docker) does not stop a user from
              starting, and pretending it does would send them off fixing
              something on a machine that already works.

    So "4 of 5 · you can start" is a legitimate and common state, and the page
    says both parts out loud rather than picking one.
    """
    r = readings if isinstance(readings, dict) else {}
    steps = []
    for sid, title, fn in _STEP_FNS:
        try:
            s = fn(r)
            if not isinstance(s, dict) or not s.get("id"):
                raise ValueError("step returned no report")
            steps.append(s)
        except Exception as e:                               # noqa: BLE001
            # A step that throws is a bug in the step or a reading of the wrong
            # type. Either way it is "we could not tell", reported under the id
            # the page keys on — a step must never simply vanish from the list.
            logger.warning("setup step %s failed: %s", sid, e)
            steps.append(_step(sid, title, UNKNOWN,
                               "This step could not run.",
                               f"{e}. That says nothing about the machine — "
                               f"only that it was not measured."))

    total = len(steps)
    done = sum(1 for s in steps if s["state"] == OK)
    blocked = [s["id"] for s in steps if s["state"] == BLOCKED]
    ready = not blocked

    worst = OK
    for s in steps:
        st = s["state"] if s["state"] in _RANK else UNKNOWN
        if _RANK[st] > _RANK[worst]:
            worst = st
    state = DEGRADED if worst == UNKNOWN else worst

    if blocked:
        first = next(s for s in steps if s["state"] == BLOCKED)
        summary = first["headline"]
    elif done == total:
        summary = "Everything checks out. Helga is ready to teach."
    else:
        rough = [s for s in steps if s["state"] != OK]
        summary = ("You can start — nothing is blocking. "
                   + str(len(rough)) + " step"
                   + ("s" if len(rough) != 1 else "")
                   + " still worth fixing.")

    return {
        "state": state,
        "ready": ready,
        "done": done,
        "total": total,
        "blocking": blocked,
        "summary": summary,
        "steps": steps,
        "model": {"name": r.get("model"), "source": r.get("model_source")},
        "pull": r.get("pull"),
        "scope": r.get("scope", "host"),
        "notes": list(r.get("notes") or []),
        "checked_at": time.time(),
    }


# ------------------------------------------------------------------ the pull

# The one thing on this page that changes the machine. It is a download through
# Ollama's own HTTP API — no subprocess, no shell, nothing written outside
# Ollama's own model store. State is a single module-level record guarded by a
# lock, because a 12.7 GB pull outlives any one request and must survive the
# user reloading the page halfway through.

_PULL_LOCK = threading.Lock()
_PULL = {
    "state": "idle",        # idle | running | done | error
    "model": None,
    "status": None,         # Ollama's own phase string, verbatim
    "completed": 0,
    "total": 0,
    "percent": None,
    "bytes_per_sec": None,
    "eta_seconds": None,
    "started_at": None,
    "updated_at": None,
    "finished_at": None,
    "error": None,
    "serial": 0,            # bumped on every change, so SSE can skip no-ops
}


def pull_snapshot():
    with _PULL_LOCK:
        return dict(_PULL)


def _pull_set(**kw):
    with _PULL_LOCK:
        _PULL.update(kw)
        _PULL["updated_at"] = time.time()
        _PULL["serial"] += 1


def _pull_worker(url, model):
    """Stream `POST /api/pull` and keep the shared record current.

    Ollama reports progress per layer as {digest, completed, total}. Summing
    every layer seen so far is the only overall figure available — the totals
    grow as new layers are announced, so the percentage can briefly move
    backwards. That is honest: it is what the server actually said. A smoothed
    number that never went backwards would be a number we made up.
    """
    layers = {}
    started = time.time()
    _pull_set(state="running", model=model, status="contacting Ollama",
              completed=0, total=0, percent=None, error=None,
              started_at=started, finished_at=None,
              bytes_per_sec=None, eta_seconds=None)
    try:
        resp = requests.post(f"{url}/api/pull",
                             json={"model": model, "stream": True},
                             stream=True, timeout=(10, 300))
        if resp.status_code >= 400:
            _pull_set(state="error", finished_at=time.time(),
                      error=f"Ollama answered HTTP {resp.status_code} to the "
                            f"pull request. Check that '{model}' is a real "
                            f"model name in the Ollama library.")
            return
        for line in resp.iter_lines(decode_unicode=True):
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("error"):
                _pull_set(state="error", finished_at=time.time(),
                          error=str(msg["error"]))
                return
            status = msg.get("status") or ""
            digest = msg.get("digest")
            if digest and isinstance(msg.get("total"), (int, float)):
                layers[digest] = (float(msg.get("completed") or 0),
                                  float(msg["total"]))
            done_b = sum(c for c, _t in layers.values())
            total_b = sum(t for _c, t in layers.values())
            elapsed = max(0.001, time.time() - started)
            rate = done_b / elapsed if done_b else None
            eta = ((total_b - done_b) / rate) if (rate and total_b > done_b) else None
            _pull_set(status=status, completed=done_b, total=total_b,
                      percent=(100.0 * done_b / total_b) if total_b else None,
                      bytes_per_sec=rate, eta_seconds=eta)
            if status == "success":
                _pull_set(state="done", finished_at=time.time(), percent=100.0,
                          status="success")
                return
        # The stream ended without ever saying success. Ollama does say it, so
        # this is a truncated connection, not a quiet completion — and calling
        # it done would leave the user with a model that is not there.
        _pull_set(state="error", finished_at=time.time(),
                  error="The download stopped before Ollama reported success. "
                        "Nothing was installed. Re-running the pull resumes "
                        "from where it stopped.")
    except requests.RequestException as e:
        _pull_set(state="error", finished_at=time.time(),
                  error=f"The connection to Ollama failed during the download: "
                        f"{e}")
    except Exception as e:                                   # noqa: BLE001
        logger.exception("model pull failed")
        _pull_set(state="error", finished_at=time.time(),
                  error=f"The download failed: {e}")


def _csrf_ok():
    """Mirror of app.py's csrf_protect, inlined.

    Importing the decorator would mean importing app.py from a module app.py
    imports — a cycle. Twelve lines of duplication beat that, and the session
    key is a fixed contract either way.
    """
    try:
        if current_app.config.get("TESTING"):
            return True
        token = (request.headers.get("X-CSRF-Token")
                 or request.form.get("csrf_token"))
        return bool(token) and token == session.get("_csrf_token")
    except RuntimeError:
        return True


# ------------------------------------------------------------------- routes

@setup_api.route("/setup")
def setup_page():
    """Standalone, NOT extending base.html, on purpose.

    Two reasons. The chrome in base.html links to Learn, Practice and Review,
    none of which work yet on the machine this page exists to fix — offering
    them is an invitation to the confusing failure we are trying to prevent.
    And base.html loads resources.js, whose blocking startup gate would cover
    this page with an opaque panel precisely when the machine is blocked, which
    is the one moment the setup page must be readable.
    """
    return render_template("setup.html")


@setup_api.route("/api/setup/status", methods=["GET"])
def setup_status():
    """Every step's real state, re-measured on each call.

    Re-measured rather than cached because the page's whole promise is that a
    user who fixed something in a terminal can press Check again and see it.
    A cache would make the fix look like it did not work.
    """
    try:
        return jsonify(evaluate(gather())), 200
    except Exception as e:                                   # noqa: BLE001
        logger.exception("setup status failed")
        # Even the total failure comes back as a readable report rather than a
        # 500, so the page renders the problem instead of a stack trace.
        return jsonify(evaluate({"notes": [f"the setup check itself failed: {e}"]})), 200


@setup_api.route("/api/setup/model/pull", methods=["POST"])
def setup_model_pull():
    """Start pulling the CONFIGURED model. Idempotent while one is running.

    The model name comes from this server's configuration and never from the
    request body. A request-supplied name would turn a setup page into an
    arbitrary-download endpoint on an appliance the whole household can reach;
    a caller may pass `model` only to assert which one it thinks it is asking
    for, and a mismatch is refused rather than honoured.
    """
    if not _csrf_ok():
        return jsonify({"error": "CSRF token invalid or missing"}), 403

    model, _source = _model_name()
    asked = None
    try:
        asked = (request.get_json(silent=True) or {}).get("model")
    except Exception:                                        # noqa: BLE001
        asked = None
    if asked and asked != model:
        return jsonify({
            "error": f"This server is configured for '{model}'. It will not "
                     f"download '{asked}'. Change OLLAMA_MODEL if you meant "
                     f"a different model."}), 400

    snap = pull_snapshot()
    if snap["state"] == "running":
        return jsonify({"started": False, "reason": "already running",
                        "pull": snap}), 200

    url = _ollama_url()
    tags, err = _get_json(f"{url}/api/tags")
    if tags is None:
        # Naming the real problem instead of starting a download at a server
        # that is not there and reporting the resulting timeout as a failed
        # download.
        return jsonify({"error": f"Ollama is not answering at {url}, so there "
                                 f"is nothing to download from. Start it "
                                 f"first ({err})."}), 503

    # Measured here rather than taken from the status report: the report may be
    # minutes old, and the whole point of this guard is the state of the disk
    # at the moment a multi-gigabyte write is about to begin.
    try:
        root = os.getenv("DATA_ROOT", "/app/data")
        if not os.path.isdir(root):
            root = os.getcwd()
        free = shutil.disk_usage(root).free / 2 ** 30
    except OSError as e:
        logger.debug("could not measure disk before pull: %s", e)
        free = None
    if free is not None and free < PULL_MIN_FREE_GB:
        return jsonify({"error": f"Only {_gb(free)} free on this disk. A model "
                                 f"download runs to several gigabytes and would "
                                 f"fail part-way, leaving a broken blob. Free "
                                 f"some space first."}), 507

    t = threading.Thread(target=_pull_worker, args=(url, model),
                         name="helga-model-pull", daemon=True)
    t.start()
    return jsonify({"started": True, "model": model, "pull": pull_snapshot()}), 202


@setup_api.route("/api/setup/model/pull", methods=["GET"])
def setup_model_pull_status():
    """A plain snapshot, so a reloaded page finds the download again.

    The stream below is the good experience; this is the one that still works
    when EventSource is unavailable or the connection dropped mid-download.
    """
    return jsonify(pull_snapshot()), 200


@setup_api.route("/api/setup/model/pull/events", methods=["GET"])
def setup_model_pull_events():
    """Server-sent events carrying the pull record until it is terminal.

    A 12.7 GB download over an unknown connection is exactly the case the house
    rule about spinners was written for: this emits real bytes, a rate and an
    ETA about once a second, and the page renders a counter from them.
    """
    def stream():
        last_serial = -1
        last_beat = 0.0
        # An hour is longer than any plausible pull on a local network and
        # shorter than forever, so a forgotten tab cannot hold a worker open.
        deadline = time.time() + 3600
        while time.time() < deadline:
            snap = pull_snapshot()
            now = time.time()
            # The heartbeat matters as much as the data: a stalled download
            # produces no new serial, and a silent stream is indistinguishable
            # from a dead one. Every 15s the page hears that we are still here.
            if snap["serial"] != last_serial or (now - last_beat) > 15:
                last_serial = snap["serial"]
                last_beat = now
                yield "data: " + json.dumps(snap) + "\n\n"
            if snap["state"] in ("idle", "done", "error"):
                break
            time.sleep(1)
    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
               "Connection": "keep-alive"}
    return Response(stream_with_context(stream()),
                    mimetype="text/event-stream", headers=headers)
