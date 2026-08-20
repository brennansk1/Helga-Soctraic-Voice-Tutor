#!/usr/bin/env python3
"""mac_preflight.py — check the Mac is set up the way this build assumes.

WHY THIS EXISTS

v1 targets macOS on Apple Silicon natively (docs/SPRINT_PLAN.md). Most of what
decides whether it feels fast lives OUTSIDE this repository — in Ollama's
environment, in Docker Desktop's VM allocation, in which processes are running
on the host. None of it is visible from inside a container, none of it throws,
and every one of these has a failure mode that looks like "the app is just
slow" rather than like a misconfiguration:

  * Ollama unloads the model after five idle minutes unless OLLAMA_KEEP_ALIVE
    is set, which is shorter than a student's pause to think — so the first
    answer after a pause pays a cold load of a 13 GB model. Pinning it forever
    is not the answer either on a box whose safe ceiling is ~15.0 GB; this
    build asks for a window (30m) and the check below reports which of the
    three you actually have.
  * Many Modelfiles cap num_ctx at 4096. A mastery-5 concept is contracted at
    up to ~3,000 output tokens on top of a ~900-token prompt, which does not
    fit — the generation is cut off, the depth contract reads it as "too
    short", and a full regeneration is triggered that cannot succeed either.
  * TTS and STT need Metal and the Neural Engine. A Linux container on macOS
    has neither, so they run on the host — and if they are not running, the
    stack is healthy and silent in both directions.
  * The image is built for the host's own architecture. An x86_64 image on
    Apple Silicon runs under Rosetta, which is a large, invisible tax.

This reports; it does not fix. Exit status is 1 if anything important is wrong,
so it can gate a deploy.

    python3 scripts/mac_preflight.py
"""

import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.request

OLLAMA = os.environ.get("OLLAMA_HOST_URL", "http://127.0.0.1:11434")

OK, WARN, BAD = "ok", "warn", "bad"
_MARK = {OK: "  ok  ", WARN: " warn ", BAD: " FAIL "}

results = []


def check(name, state, detail, fix=None):
    results.append((name, state, detail, fix))


def _listening(port, host="127.0.0.1"):
    with socket.socket() as s:
        s.settimeout(0.6)
        return s.connect_ex((host, port)) == 0


def _get(path, timeout=4):
    try:
        with urllib.request.urlopen(f"{OLLAMA}{path}", timeout=timeout) as r:
            return json.load(r)
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None


def _sh(*cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=15).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


# ── platform ─────────────────────────────────────────────────────────────────

def check_platform():
    if platform.system() != "Darwin":
        check("platform", WARN,
              f"{platform.system()} {platform.machine()} — v1 targets macOS on "
              "Apple Silicon; the rest of these checks assume it")
        return False
    if platform.machine() != "arm64":
        check("platform", BAD,
              f"macOS on {platform.machine()} — this Python is running under "
              "Rosetta or on Intel; MLX will not work",
              "install an arm64 Python (e.g. `arch -arm64 brew install python`)")
        return True
    check("platform", OK, f"macOS {platform.mac_ver()[0]} on arm64")
    return True


def check_memory():
    total = _sh("sysctl", "-n", "hw.memsize")
    if not total.isdigit():
        return
    gb = int(total) / (1024 ** 3)
    # A 9B at Q4 is ~6 GB resident; the containers declare ~2.2 GB; the user is
    # also using the machine.
    state = OK if gb >= 16 else WARN
    check("host memory", state, f"{gb:.0f} GB unified",
          None if state == OK else
          "16 GB is the practical floor for a 9B plus the container stack")


# ── ollama ───────────────────────────────────────────────────────────────────

def check_ollama_running():
    if not _listening(11434):
        check("ollama", BAD, "not listening on :11434",
              "scripts/host_services.sh start")
        return False
    tags = _get("/api/tags")
    if tags is None:
        check("ollama", BAD, "listening but /api/tags did not answer")
        return False
    names = [m.get("name", "") for m in tags.get("models", [])]
    want = os.environ.get("OLLAMA_MODEL", "qwen3.5:9b")
    if any(want.split(":")[0] in n for n in names):
        check("ollama", OK, f"up, {want} installed")
    else:
        check("ollama", BAD, f"up, but {want} is not installed",
              f"ollama pull {want}")
    return True


def check_keep_alive():
    """How long the model stays resident, read from the server rather than
    assumed. Both extremes are wrong here and they fail in opposite ways: a
    short window hands a ~133 s reload to whoever asks the next question, while
    pinning forever holds ~12.7 GB through every hour nobody is studying, on a
    box whose measured safe ceiling is ~15.0 GB and which thrashes past ~16.4.
    What this build asks for is a window long enough to outlast a pause inside
    a session and short enough to give the memory back after one."""
    want = os.environ.get("OLLAMA_KEEP_ALIVE", "30m")
    ps = _get("/api/ps")
    if ps is None:
        check("model residency", WARN, "/api/ps unavailable — cannot tell")
        return
    models = ps.get("models") or []
    if not models:
        check("model residency", OK,
              "nothing resident — either idle-evicted as intended, or not "
              "asked anything yet")
        return
    expires = models[0].get("expires_at")
    unpin = (f"set OLLAMA_KEEP_ALIVE={want} on the HOST *and* in the "
             "containers — a per-request keep_alive overrides the server's")
    if not expires:
        check("model residency", WARN,
              "resident with no expiry — the weights are never released", unpin)
        return
    try:
        from datetime import datetime, timezone
        when = datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
        mins = (when - datetime.now(timezone.utc)).total_seconds() / 60
    except (ValueError, TypeError):
        check("model residency", WARN, f"unparseable expiry {expires!r}")
        return
    if mins > 60:
        # Ollama writes a year-out expiry for keep_alive=-1, so anything this
        # far out is a pin rather than a long window.
        check("model residency", WARN,
              f"pinned (expires in {mins/60:.0f}h) — ~12.7 GB held while idle",
              unpin)
    elif mins < 10:
        check("model residency", WARN,
              f"unloads in {mins:.0f} min — shorter than a student's pause to "
              "think, so answers after a pause pay a ~133 s reload",
              f"launchctl setenv OLLAMA_KEEP_ALIVE {want}  &&  restart ollama serve")
    else:
        check("model residency", OK,
              f"resident, releases after {mins:.0f} min idle")


def check_context_length():
    """num_ctx, which decides whether mastery 4-5 output is silently truncated."""
    model = os.environ.get("OLLAMA_MODEL", "qwen3.5:9b")
    env_ctx = os.environ.get("OLLAMA_CONTEXT_LENGTH")
    if env_ctx and env_ctx.isdigit() and int(env_ctx) >= 8192:
        check("context length", OK, f"OLLAMA_CONTEXT_LENGTH={env_ctx}")
        return
    shown = _sh("ollama", "show", model) if shutil.which("ollama") else ""
    ctx = None
    for line in shown.splitlines():
        if "context length" in line.lower():
            digits = "".join(c for c in line if c.isdigit())
            if digits:
                ctx = int(digits)
            break
    fix = ("launchctl setenv OLLAMA_CONTEXT_LENGTH 8192  &&  restart ollama serve")
    if ctx is None:
        check("context length", WARN,
              "could not read num_ctx — check `ollama show " + model + "`", fix)
    elif ctx < 8192:
        check("context length", BAD,
              f"num_ctx={ctx}: a mastery-5 concept (~3,000 output tokens on a "
              "~900-token prompt) does not fit and is silently truncated, then "
              "fails the depth contract as 'too short'", fix)
    else:
        check("context length", OK, f"num_ctx={ctx}")


# ── host-native services ─────────────────────────────────────────────────────

def check_host_services():
    for name, port, why in (
            ("TTS (:5005)", 5005, "Kokoro on MLX — the tutor has no voice without it"),
            ("STT (:5001)", 5001, "Nemotron on MLX/ANE — no voice input without it")):
        if _listening(port):
            check(name, OK, "listening")
        else:
            check(name, WARN, f"not running — {why}",
                  "scripts/host_services.sh start")


# ── docker ───────────────────────────────────────────────────────────────────

def check_docker():
    if not shutil.which("docker"):
        check("docker", BAD, "not installed", "install Docker Desktop for Mac")
        return
    info = _sh("docker", "info", "--format", "{{.Architecture}} {{.MemTotal}}")
    if not info:
        check("docker", BAD, "installed but not running", "open Docker Desktop")
        return
    parts = info.split()
    arch = parts[0] if parts else ""
    known = ("aarch64", "arm64", "x86_64", "amd64")
    if not any(k in arch for k in known):
        # `docker info` prints to stderr and leaves stdout near-empty when the
        # daemon is down, which parsed as an architecture of "0" and reported a
        # confident, wrong FAIL. An unreadable answer is unknown, not bad.
        check("docker arch", WARN,
              "could not read architecture — is the daemon running?",
              "open Docker Desktop, then re-run")
    elif "aarch64" in arch or "arm64" in arch:
        check("docker arch", OK, arch)
    else:
        check("docker arch", BAD,
              f"{arch} — images run under emulation, which is a large hidden tax",
              "Docker Desktop → Settings → General → uncheck Rosetta emulation")
    if len(parts) > 1 and parts[1].isdigit():
        vm_gb = int(parts[1]) / (1024 ** 3)
        # The containers declare ~2.2 GB of limits once TTS moved to the host.
        state = OK if vm_gb >= 4 else BAD
        check("docker VM memory", state, f"{vm_gb:.1f} GB allocated to the VM",
              None if state == OK else
              "Docker Desktop → Settings → Resources → Memory: 4 GB or more")


def check_tts_backend_sanity():
    """The container must not be configured for MLX.

    MLX publishes a manylinux wheel, so `pip install mlx-audio` succeeds inside
    a Linux image and the container looks configured — but there is no Metal in
    that VM. The failure surfaces as "no TTS backend available" on the first
    synthesis, long after the health check went green.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    req = os.path.join(root, "services", "tts", "requirements.txt")
    try:
        with open(req) as fh:
            body = fh.read()
    except OSError:
        return
    active = [ln.strip() for ln in body.splitlines()
              if ln.strip() and not ln.strip().startswith("#")]
    if any(ln.startswith("mlx") for ln in active):
        check("tts container deps", BAD,
              "requirements.txt declares an MLX package — there is no Metal "
              "inside a Linux container", "use kokoro (torch) for the container path")
    else:
        check("tts container deps", OK, "container path uses the torch backend")


def main():
    is_mac = check_platform()
    check_memory()
    if check_ollama_running():
        check_keep_alive()
        check_context_length()
    check_host_services()
    check_docker()
    check_tts_backend_sanity()

    print("\nHelga — Mac preflight\n")
    worst = OK
    for name, state, detail, fix in results:
        print(f"[{_MARK[state]}] {name:<20} {detail}")
        if fix and state != OK:
            print(f"{'':<9}{'':<20} fix: {fix}")
        if state == BAD or (state == WARN and worst == OK):
            worst = state if state == BAD else WARN
    bad = sum(1 for _n, s, _d, _f in results if s == BAD)
    warn = sum(1 for _n, s, _d, _f in results if s == WARN)
    print(f"\n{len(results)} checks · {bad} failing · {warn} warnings")
    if not is_mac:
        print("(not macOS — most of this does not apply)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
