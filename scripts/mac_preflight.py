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
    is set, so the first answer after a student pauses to think pays a
    multi-second cold load of a 9B.
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
    """Whether the model is pinned, read from the server rather than assumed."""
    ps = _get("/api/ps")
    if ps is None:
        check("model residency", WARN, "/api/ps unavailable — cannot tell")
        return
    models = ps.get("models") or []
    if not models:
        check("model residency", WARN,
              "no model resident right now — send one request, then re-run to "
              "see whether it stays loaded")
        return
    expires = models[0].get("expires_at")
    fix = ("launchctl setenv OLLAMA_KEEP_ALIVE -1  &&  restart ollama serve")
    if not expires:
        check("model residency", OK, "resident, no expiry reported")
        return
    try:
        from datetime import datetime, timezone
        when = datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
        mins = (when - datetime.now(timezone.utc)).total_seconds() / 60
    except (ValueError, TypeError):
        check("model residency", WARN, f"unparseable expiry {expires!r}")
        return
    if mins > 60:
        check("model residency", OK, f"pinned (expires in {mins/60:.0f}h)")
    else:
        check("model residency", BAD,
              f"unloads in {mins:.0f} min — every answer after a pause pays a "
              "cold model load", fix)


def check_inference_engine():
    """Which engine Ollama is actually using on this Mac.

    Ollama shipped an MLX backend in preview in v0.19 (March 2026) and made it
    the default on Apple Silicon in v0.30 (May 2026) — reported as roughly
    doubling decode. Decode is 93% of a course build here
    (docs/PERFORMANCE_PASS.md), so this is the largest single lever available
    and it costs no code change: the MLX engine is behind the same Ollama API,
    so `format` schema constraints, keep_alive, /api/ps and NUM_PARALLEL all
    keep working.

    The trap, and the reason this check exists: the default is reported to be
    gated on machine memory, with 32 GB as the threshold and llama.cpp Metal
    below it. THE TARGET BOX IS 24 GB. So the fast path may be silently off on
    exactly the machine this project is tuned for, and nothing anywhere says
    so — the model loads, answers, and is simply slower than it should be.

    This reports what it can determine and points at the check that is
    authoritative. It deliberately does not assert an override variable: the
    mechanism differs across versions and guessing one here would be worse
    than saying "go look".
    """
    ver = (_get("/api/version") or {}).get("version")
    if not ver:
        check("inference engine", WARN, "could not read Ollama version")
        return

    def _tuple(v):
        parts = []
        for piece in str(v).split(".")[:3]:
            digits = "".join(c for c in piece if c.isdigit())
            parts.append(int(digits) if digits else 0)
        return tuple(parts + [0] * (3 - len(parts)))

    total = _sh("sysctl", "-n", "hw.memsize")
    gb = int(total) / (1024 ** 3) if total.isdigit() else 0

    if _tuple(ver) < (0, 19, 0):
        check("inference engine", BAD,
              f"Ollama {ver} predates the MLX backend",
              "upgrade Ollama — MLX roughly doubles decode on Apple Silicon")
    elif gb and gb < 32:
        check("inference engine", WARN,
              f"Ollama {ver} on a {gb:.0f} GB Mac — MLX is reported to default "
              "on only at 32 GB+, so this machine may be on llama.cpp Metal",
              "confirm with `ollama ps` (look for 100% GPU) and check "
              "docs.ollama.com for the MLX override on this version")
    else:
        check("inference engine", OK,
              f"Ollama {ver} — MLX should be the default; confirm with `ollama ps`")


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
        check_inference_engine()
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
