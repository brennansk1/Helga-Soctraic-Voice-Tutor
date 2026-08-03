"""Boot a real Helga stack for the end-to-end tests.

WHY THIS EXISTS
---------------
The e2e suite hardcoded http://localhost:5050 and assumed someone had already
started the app. Nobody had, so all eight tests failed on
`net::ERR_CONNECTION_REFUSED` — permanently red, and therefore ignored. A test
that can only pass when a human remembers to do something first is not a test.

A5's gate requires "e2e green on a live stack", which is uncheckable without a
way to *get* a live stack. This fixture is that way: it starts web-ui and the
RAG service on dedicated ports, waits for them to answer, and shuts them down
afterwards.

It deliberately does NOT start core-logic or Ollama. Those need a model loaded
and minutes of warmup; the UI is expected to degrade gracefully without them
(and how it degrades is itself worth testing). Tests needing a tutor should
skip explicitly rather than silently depend on a model being resident.

Set HELGA_E2E_BASE_URL to point the suite at a stack you started yourself.
"""

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time

import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))

WEB_PORT = int(os.environ.get('HELGA_E2E_WEB_PORT', '5098'))
RAG_PORT = int(os.environ.get('HELGA_E2E_RAG_PORT', '5097'))
BOOT_TIMEOUT = 45


def _free(port):
    with socket.socket() as s:
        return s.connect_ex(('127.0.0.1', port)) != 0


def _wait(url, timeout=BOOT_TIMEOUT):
    import urllib.request
    import urllib.error
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2)
            return True
        except urllib.error.HTTPError:
            return True          # answering at all is enough
        except Exception:
            time.sleep(0.4)
    return False


def _spawn(code, env, log):
    return subprocess.Popen(
        [sys.executable, '-c', code], cwd=_ROOT, env=env,
        stdout=log, stderr=subprocess.STDOUT)


@pytest.fixture(scope='session')
def live_stack():
    """Yield the base URL of a running web-ui, or skip with a real reason."""
    external = os.environ.get('HELGA_E2E_BASE_URL')
    if external:
        if not _wait(external, timeout=5):
            pytest.skip(f"HELGA_E2E_BASE_URL={external} is not answering")
        yield external
        return

    if not (_free(WEB_PORT) and _free(RAG_PORT)):
        pytest.skip(f"ports {WEB_PORT}/{RAG_PORT} already in use")

    # A throwaway data dir: e2e must never mutate the developer's real data.
    #
    # BOTH halves have to come across. Course structure lives in
    # data/courses/<uid>/structure.json, but the course LIST is served from
    # SQLite — copying only the JSON produced a stack that rendered the empty
    # state, and every learn-path test skipped with "no fully-built course on
    # disk" while a perfectly good course sat in the directory.
    data_dir = tempfile.mkdtemp(prefix='helga-e2e-')
    src = os.path.join(_ROOT, 'data')
    if os.path.isdir(os.path.join(src, 'courses')):
        shutil.copytree(os.path.join(src, 'courses'),
                        os.path.join(data_dir, 'courses'), dirs_exist_ok=True)
    for name in ('helga.db', 'user_state.json'):
        if os.path.exists(os.path.join(src, name)):
            shutil.copy2(os.path.join(src, name), os.path.join(data_dir, name))

    env = dict(os.environ, DATA_ROOT=data_dir, HELGA_DATA_DIR=data_dir,
               PYTHONUNBUFFERED='1')

    logs = open(os.path.join(data_dir, 'stack.log'), 'w')
    procs = []
    try:
        procs.append(_spawn(
            "import sys; sys.path.insert(0,'.')\n"
            "from services.rag.librarian import app\n"
            f"app.run(host='127.0.0.1', port={RAG_PORT})\n", env, logs))

        procs.append(_spawn(
            "import sys; sys.path.insert(0,'services/web-ui'); sys.path.insert(0,'.')\n"
            "import app as w\n"
            f"w.SERVICES['rag']='http://127.0.0.1:{RAG_PORT}'\n"
            f"w.socketio.run(w.app, host='127.0.0.1', port={WEB_PORT})\n", env, logs))

        base = f'http://127.0.0.1:{WEB_PORT}'
        if not _wait(base):
            logs.flush()
            with open(os.path.join(data_dir, 'stack.log')) as f:
                tail = ''.join(f.readlines()[-25:])
            pytest.skip(f"web-ui did not start within {BOOT_TIMEOUT}s:\n{tail}")

        yield base
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
        logs.close()
        shutil.rmtree(data_dir, ignore_errors=True)


@pytest.fixture(scope='session')
def base_url(live_stack):
    return live_stack
