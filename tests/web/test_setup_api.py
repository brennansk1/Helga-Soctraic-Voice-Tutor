"""First-run setup — the report that decides what a new user is told to do.

The whole value of this page is that it distinguishes things a green tick would
flatten into one. Two of those distinctions are load-bearing enough to pin:

  1. "Ollama says the model is not installed" and "Ollama did not answer, so
     nobody could ask" are opposite problems with opposite fixes. Offering a
     download button for the second one sends the user to watch a progress bar
     that will never move, at a server that is not there.

  2. "Not measured" is never "fine". A step that could not run must not count
     toward the done/total counter, or the counter becomes a way of hiding the
     checks that failed to happen.

And the promise underneath both: `evaluate` is total. A first-run experience
that ends at a stack trace has failed at the one job it had, so it is fed
garbage here deliberately — None, wrong types, half-built dicts — and still has
to produce a report.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
sys.path.insert(0, os.path.join(_root, "services/web-ui"))
if _root not in sys.path:
    sys.path.insert(0, _root)

import setup_api as su  # noqa: E402


# --------------------------------------------------------------- fixtures

def readings(**kw):
    """A fully working installation, as the readings would describe it."""
    base = dict(
        model="nail-35b-a3b-ctx", model_source="OLLAMA_MODEL",
        ollama_url="http://127.0.0.1:11434",
        ollama_reachable=True, ollama_error=None,
        model_installed=True, model_near_miss=None, model_size_gb=12.7,
        installed_models=["nail-35b-a3b-ctx:latest"],
        services=[
            {"id": "core", "label": "Core logic", "url": "http://c:5003",
             "required": True, "what": "the tutor", "up": True, "error": None},
            {"id": "rag", "label": "Course library", "url": "http://r:5002",
             "required": True, "what": "storage", "up": True, "error": None},
            {"id": "searxng", "label": "Search", "url": "http://s:8080",
             "required": False, "what": "search", "up": True, "error": None},
            {"id": "research", "label": "Research", "url": "http://x:5006",
             "required": False, "what": "grounding", "up": True, "error": None},
        ],
        voice=[
            {"id": "tts", "label": "Speech out (Kokoro)", "url": "http://h:5005",
             "up": True, "error": None, "backend": "mlx"},
            {"id": "stt", "label": "Speech in (Nemotron)", "url": "http://h:5001",
             "up": True, "error": None, "backend": "nemotron-mlx"},
        ],
        venv={"state": su.OK, "path": "/repo/.venv-host", "version": "3.12.4",
              "detail": "/repo/.venv-host exists on Python 3.12.4."},
        preflight={
            "state": "ok", "summary": "This machine has room to run Helga.",
            "checks": [
                {"id": "installed_memory", "label": "Installed memory",
                 "state": "ok", "reason": "24.0 GB installed.", "remedy": None,
                 "measured": {"total_gb": 24.0}},
                {"id": "available_memory", "label": "Memory available now",
                 "state": "ok", "reason": "8.0 GB free", "remedy": None,
                 "measured": {"available_gb": 8.0}},
                {"id": "disk_space", "label": "Disk space", "state": "ok",
                 "reason": "120.0 GB free.", "remedy": None,
                 "measured": {"free_gb": 120.0}},
                {"id": "ollama_model", "label": "Language model", "state": "ok",
                 "reason": "installed", "remedy": None, "measured": {}},
            ],
            "blocking": [], "notes": [],
        },
        preflight_error=None,
        scope="host",
        pull={"state": "idle", "serial": 0},
        notes=[],
    )
    base.update(kw)
    return base


def step(report, sid):
    return next(s for s in report["steps"] if s["id"] == sid)


# ------------------------------------------------------------- happy path

class TestFinishedInstallation(unittest.TestCase):
    def test_everything_green_is_ready_and_complete(self):
        v = su.evaluate(readings())
        self.assertEqual(v["state"], su.OK)
        self.assertTrue(v["ready"])
        self.assertEqual(v["done"], v["total"])
        self.assertEqual(v["blocking"], [])
        for s in v["steps"]:
            self.assertEqual(s["state"], su.OK, s)

    def test_every_step_carries_what_the_page_renders(self):
        v = su.evaluate(readings())
        for s in v["steps"]:
            for key in ("id", "title", "state", "headline", "detail", "why",
                        "commands", "blocked_by", "fixable", "measured", "sub"):
                self.assertIn(key, s)
            self.assertIsInstance(s["commands"], list)
            self.assertIsInstance(s["sub"], list)

    def test_the_five_steps_are_always_present_and_in_order(self):
        v = su.evaluate(readings())
        self.assertEqual([s["id"] for s in v["steps"]],
                         ["hardware", "ollama", "model", "voice", "services"])


# --------------------------------------- the distinction the page exists for

class TestModelMissingIsNotOllamaDown(unittest.TestCase):
    """The two failures a single green tick would flatten into one."""

    def test_model_missing_is_blocked_and_offers_the_download(self):
        v = su.evaluate(readings(model_installed=False,
                                 installed_models=["something-else:latest"]))
        m = step(v, "model")
        self.assertEqual(m["state"], su.BLOCKED)
        self.assertEqual(m["fixable"], "pull")
        self.assertIsNone(m["blocked_by"])
        self.assertIn("not installed", m["headline"])
        self.assertIn("ollama pull nail-35b-a3b-ctx", m["commands"])
        self.assertFalse(v["ready"])

    def test_ollama_down_makes_the_model_unknown_not_missing(self):
        v = su.evaluate(readings(ollama_reachable=False,
                                 ollama_error="connection refused",
                                 model_installed=None))
        m = step(v, "model")
        self.assertEqual(m["state"], su.UNKNOWN)
        # The critical part: no download button, and the step names what it is
        # waiting on rather than guessing.
        self.assertIsNone(m["fixable"])
        self.assertEqual(m["blocked_by"], "ollama")
        self.assertIn("unknown", m["headline"].lower())
        self.assertNotIn("not installed", m["headline"])

    def test_the_two_states_produce_different_reports(self):
        missing = step(su.evaluate(readings(model_installed=False)), "model")
        unreachable = step(su.evaluate(readings(ollama_reachable=False,
                                                model_installed=None)), "model")
        self.assertNotEqual(missing["state"], unreachable["state"])
        self.assertNotEqual(missing["headline"], unreachable["headline"])
        self.assertNotEqual(missing["fixable"], unreachable["fixable"])

    def test_ollama_down_is_the_blocking_step_not_the_model(self):
        v = su.evaluate(readings(ollama_reachable=False, model_installed=None))
        self.assertIn("ollama", v["blocking"])
        self.assertNotIn("model", v["blocking"])
        self.assertIn("ollama serve", step(v, "ollama")["commands"])

    def test_a_near_miss_tag_is_named_rather_than_accepted(self):
        v = su.evaluate(readings(model_installed=False,
                                 model_near_miss="nail-35b-a3b-ctx-q4:latest"))
        m = step(v, "model")
        self.assertIn("nail-35b-a3b-ctx-q4:latest", m["detail"])
        self.assertIn("404", m["detail"])

    def test_a_defaulted_model_name_says_it_is_guessing(self):
        """The web-ui service is not given OLLAMA_MODEL by docker-compose, so
        this process can be checking a different model from the one core calls.
        Silently checking the wrong name would be the worst outcome here."""
        v = su.evaluate(readings(model_source="default"))
        self.assertIn("OLLAMA_MODEL", step(v, "model")["detail"])


class TestModelMatching(unittest.TestCase):
    """A substring test shipped once already, in main.py's own preflight, and
    reported a green check for a model Ollama then 404'd on every call."""

    def test_bare_name_matches_only_latest(self):
        self.assertEqual(su._model_match("qwen3", ["qwen3:latest"])[0], True)
        self.assertEqual(su._model_match("qwen3", ["qwen3:14b"])[0], False)

    def test_near_miss_is_reported_not_accepted(self):
        ok, near = su._model_match("qwen3:14b", ["qwen3:14b-q4_K_M"])
        self.assertFalse(ok)
        self.assertEqual(near, "qwen3:14b-q4_K_M")

    def test_junk_in_the_list_does_not_crash_the_match(self):
        ok, _near = su._model_match("x", [None, 3, {"name": "x"}, "x:latest"])
        self.assertTrue(ok)


# ------------------------------------------------- blocking vs merely amber

class TestWhatBlocksAndWhatDoesNot(unittest.TestCase):
    def test_voice_down_does_not_block_a_text_tutor(self):
        """Helga teaches in text without voice. Holding the whole app shut over
        a silent Kokoro would be a lie about what is broken — but it must still
        be said, because the containers stay green while voice is dead."""
        v = su.evaluate(readings(voice=[
            {"id": "tts", "label": "Speech out (Kokoro)", "url": "http://h:5005",
             "up": False, "error": "connection refused", "backend": None},
            {"id": "stt", "label": "Speech in (Nemotron)", "url": "http://h:5001",
             "up": False, "error": "connection refused", "backend": None},
        ]))
        s = step(v, "voice")
        self.assertEqual(s["state"], su.DEGRADED)
        self.assertTrue(v["ready"])
        self.assertLess(v["done"], v["total"])   # amber is never counted done
        self.assertIn("scripts/host_services.sh start", s["commands"])

    def test_a_missing_venv_produces_the_venv_commands(self):
        v = su.evaluate(readings(venv={
            "state": su.BLOCKED, "path": "/repo/.venv-host",
            "detail": "/repo/.venv-host does not exist."}))
        s = step(v, "voice")
        self.assertTrue(any("python3.12 -m venv" in c for c in s["commands"]))
        self.assertTrue(any("requirements-host.txt" in c for c in s["commands"]))

    def test_an_uninspectable_venv_is_unknown_not_missing(self):
        """In the deployed container only services/web-ui is mounted, so
        .venv-host is genuinely invisible. Telling a user to create a
        virtualenv they already have loses their trust on the first screen."""
        v = su.evaluate(readings(venv={
            "state": su.UNKNOWN, "path": None,
            "detail": "The repository is not visible from this process."}))
        s = step(v, "voice")
        self.assertEqual(s["state"], su.DEGRADED)
        self.assertFalse(any("python3.12 -m venv" in c for c in s["commands"]))

    def test_a_404_is_not_reported_as_nothing_listening(self):
        """Caught live: a server that answered 404 was described as "nothing is
        listening". Something is — it just is not the service we asked for, and
        the two send you to different places (start a container vs check a port
        or a path)."""
        r = readings()
        r["services"][2].update(up=False, error="HTTP 404")
        row = next(x for x in step(su.evaluate(r), "services")["sub"]
                   if x["label"] == "Search")
        self.assertNotIn("nothing is listening", row["reason"])
        self.assertIn("404", row["reason"])

    def test_a_refused_connection_still_says_nothing_is_listening(self):
        r = readings()
        r["services"][2].update(up=False, error="connection refused")
        row = next(x for x in step(su.evaluate(r), "services")["sub"]
                   if x["label"] == "Search")
        self.assertIn("nothing is listening", row["reason"])

    def test_a_required_service_down_blocks(self):
        r = readings()
        r["services"][0]["up"] = False
        v = su.evaluate(r)
        self.assertEqual(step(v, "services")["state"], su.BLOCKED)
        self.assertFalse(v["ready"])

    def test_an_optional_service_down_only_warns(self):
        r = readings()
        r["services"][2]["up"] = False       # searxng
        v = su.evaluate(r)
        self.assertEqual(step(v, "services")["state"], su.DEGRADED)
        self.assertTrue(v["ready"])
        self.assertIn("grounded", step(v, "services")["detail"])

    def test_hardware_blocked_blocks_everything(self):
        r = readings()
        r["preflight"]["checks"][0].update(
            state="blocked", reason="This machine has 16.0 GB and Helga needs 21.",
            remedy="Use a machine with more memory.")
        v = su.evaluate(r)
        self.assertEqual(step(v, "hardware")["state"], su.BLOCKED)
        self.assertFalse(v["ready"])
        self.assertIn("16.0 GB", v["summary"])

    def test_advisory_mode_opens_the_door_without_hiding_the_reading(self):
        """HELGA_PREFLIGHT_ADVISORY already exists as the operator's override.
        A setup page that stayed blocked after blocking was explicitly turned
        off would be a second opinion the appliance has no business having."""
        r = readings()
        r["preflight"]["advisory"] = True
        r["preflight"]["checks"][1].update(
            state="blocked", reason="Only 0.9 GB of memory is free.",
            remedy="Close other applications.")
        v = su.evaluate(r)
        s = step(v, "hardware")
        self.assertEqual(s["state"], su.DEGRADED)
        self.assertTrue(v["ready"])
        # The measurement itself is untouched — only the verdict softened.
        row = next(x for x in s["sub"] if x["label"] == "Memory available now")
        self.assertEqual(row["state"], su.BLOCKED)

    def test_inside_docker_memory_is_a_caveat_not_a_blocker(self):
        """psutil inside Docker on macOS reports the Linux VM's 8 GB. Judged as
        hardware it would hold the app shut forever on a machine that is fine."""
        r = readings(scope="container")
        for c in r["preflight"]["checks"][:2]:
            c.update(state="unknown", reason="Measured inside Docker.")
        v = su.evaluate(r)
        s = step(v, "hardware")
        self.assertEqual(s["state"], su.DEGRADED)
        self.assertTrue(v["ready"])
        self.assertIn("Docker", s["headline"])

    def test_the_language_model_check_is_not_repeated_inside_hardware(self):
        """One missing model must not look like two separate problems."""
        v = su.evaluate(readings())
        labels = [x["label"] for x in step(v, "hardware")["sub"]]
        self.assertNotIn("Language model", labels)

    def test_hardware_never_borrows_a_summary_about_the_model(self):
        """Caught live: the hardware step took the preflight's OVERALL summary
        for its headline, and that summary covers the language-model check this
        step drops. The result was a green "This machine" card headlined
        "Ollama is running but 'fake-model' is not installed" — the wrong step
        reporting the problem, while calling itself done."""
        r = readings(model_installed=False)
        r["preflight"]["summary"] = ("Ollama is running but 'nail-35b-a3b-ctx' "
                                     "is not installed.")
        r["preflight"]["checks"][3].update(state="blocked",
                                           reason=r["preflight"]["summary"])
        h = step(su.evaluate(r), "hardware")
        self.assertEqual(h["state"], su.OK)
        self.assertNotIn("not installed", h["headline"])
        self.assertNotIn("Ollama", h["headline"])


# --------------------------------------------- the counter tells the truth

class TestProgressCounting(unittest.TestCase):
    def test_unmeasured_steps_are_never_counted_done(self):
        v = su.evaluate(readings(ollama_reachable=None, model_installed=None,
                                 services=None, voice=None))
        for sid in ("ollama", "model", "services", "voice"):
            self.assertEqual(step(v, sid)["state"], su.UNKNOWN, sid)
        self.assertEqual(v["done"], 1)          # hardware only
        self.assertEqual(v["total"], 5)

    def test_unknown_never_blocks(self):
        v = su.evaluate(readings(ollama_reachable=None, model_installed=None))
        self.assertEqual(v["blocking"], [])
        self.assertTrue(v["ready"])
        self.assertEqual(v["state"], su.DEGRADED)

    def test_ready_but_incomplete_says_both(self):
        r = readings()
        r["services"][3]["up"] = False        # research: advisory only
        v = su.evaluate(r)
        self.assertTrue(v["ready"])
        self.assertEqual(v["done"], 4)
        self.assertIn("can start", v["summary"])


# ---------------------------------------------------------- never raises

class TestEvaluateIsTotal(unittest.TestCase):
    """A setup page that 500s has failed at the one job it had."""

    def test_empty_readings(self):
        v = su.evaluate({})
        self.assertEqual(v["total"], 5)
        self.assertEqual(v["done"], 0)

    def test_none(self):
        v = su.evaluate(None)
        self.assertEqual(v["total"], 5)
        self.assertIsInstance(v["summary"], str)

    def test_wrong_types_everywhere(self):
        junk = {
            "model": 17, "model_source": [], "ollama_url": None,
            "ollama_reachable": "yes",           # a string, not a bool
            "model_installed": "no",
            "services": "not a list", "voice": {"nope": 1},
            "venv": "not a dict", "preflight": ["not", "a", "dict"],
            "scope": 5, "notes": "not a list",
        }
        v = su.evaluate(junk)
        self.assertEqual(len(v["steps"]), 5)
        for s in v["steps"]:
            self.assertIn(s["state"], (su.OK, su.DEGRADED, su.BLOCKED, su.UNKNOWN))

    def test_a_truthy_non_bool_reachable_is_unknown_not_reachable(self):
        """A reading we cannot interpret is not a verdict about the machine."""
        v = su.evaluate(readings(ollama_reachable="probably"))
        self.assertEqual(step(v, "ollama")["state"], su.UNKNOWN)
        self.assertEqual(step(v, "model")["state"], su.UNKNOWN)

    def test_a_step_that_throws_still_appears_under_its_id(self):
        """A step must never simply vanish from the list — the page keys on it."""
        boom = MagicMock(side_effect=RuntimeError("kaboom"))
        with patch.object(su, "_STEP_FNS",
                          (("voice", "Voice", boom),)):
            v = su.evaluate(readings())
        self.assertEqual(len(v["steps"]), 1)
        self.assertEqual(v["steps"][0]["id"], "voice")
        self.assertEqual(v["steps"][0]["state"], su.UNKNOWN)
        self.assertIn("kaboom", v["steps"][0]["detail"])

    def test_missing_preflight_module_is_named_not_a_pass(self):
        v = su.evaluate(readings(preflight=None,
                                 preflight_error="No module named 'services'"))
        s = step(v, "hardware")
        self.assertEqual(s["state"], su.UNKNOWN)
        self.assertIn("No module named", s["detail"])
        self.assertTrue(v["ready"])          # unmeasured must not block


# -------------------------------------------------------------- endpoints

def _client(testing=True):
    from flask import Flask
    # A bare app rather than importing app.py: the blueprint must stand on its
    # own, and app.py does not register it yet (that is a one-line change its
    # owner applies).
    app = Flask(__name__,
                template_folder=os.path.join(_root, "services/web-ui/templates"),
                static_folder=os.path.join(_root, "services/web-ui/static"))
    app.config["TESTING"] = testing
    app.secret_key = "test"
    app.jinja_env.globals["csrf_token"] = lambda: "tok"
    app.register_blueprint(su.setup_api)
    return app, app.test_client()


class TestEndpoints(unittest.TestCase):
    def setUp(self):
        self.app, self.client = _client()

    def test_status_is_200_even_when_gathering_explodes(self):
        with patch.object(su, "gather", side_effect=RuntimeError("no")):
            r = self.client.get("/api/setup/status")
        self.assertEqual(r.status_code, 200)
        v = r.get_json()
        self.assertEqual(len(v["steps"]), 5)

    def test_status_reports_the_five_steps(self):
        with patch.object(su, "gather", return_value=readings()):
            r = self.client.get("/api/setup/status")
        v = r.get_json()
        self.assertEqual(r.status_code, 200)
        self.assertTrue(v["ready"])
        self.assertEqual(v["done"], 5)

    def test_the_page_renders(self):
        r = self.client.get("/setup")
        self.assertEqual(r.status_code, 200)
        body = r.get_data(as_text=True)
        self.assertIn("setup-steps", body)
        self.assertIn("js/setup.js", body)
        # Standalone on purpose: base.html's blocking preflight gate would
        # cover this page exactly when the machine is blocked.
        self.assertNotIn("resources.js", body)

    def test_pull_refuses_when_ollama_is_not_answering(self):
        """Starting a download at a server that is not there would report the
        resulting timeout as a failed download — the wrong problem entirely."""
        with patch.object(su, "_get_json", return_value=(None, "refused")):
            r = self.client.post("/api/setup/model/pull", json={})
        self.assertEqual(r.status_code, 503)
        self.assertIn("not answering", r.get_json()["error"])

    def test_pull_refuses_a_model_the_server_is_not_configured_for(self):
        """The name comes from configuration, never from the request body: an
        appliance the whole household can reach must not expose an
        arbitrary-download endpoint."""
        with patch.object(su, "_get_json", return_value=({"models": []}, None)):
            r = self.client.post("/api/setup/model/pull",
                                 json={"model": "something-enormous"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("will not download", r.get_json()["error"])

    def test_pull_refuses_on_a_nearly_full_disk(self):
        fake = MagicMock()
        fake.free = 1 * 2 ** 30
        with patch.object(su, "_get_json", return_value=({"models": []}, None)), \
                patch.object(su.shutil, "disk_usage", return_value=fake):
            r = self.client.post("/api/setup/model/pull", json={})
        self.assertEqual(r.status_code, 507)
        self.assertIn("free", r.get_json()["error"])

    def test_pull_status_is_readable_before_anything_started(self):
        r = self.client.get("/api/setup/model/pull")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["state"], "idle")

    def test_pull_events_stream_closes_immediately_when_idle(self):
        r = self.client.get("/api/setup/model/pull/events")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.mimetype.startswith("text/event-stream"))
        body = r.get_data(as_text=True)
        self.assertIn('"state": "idle"', body)


class TestPullCsrf(unittest.TestCase):
    def test_a_post_without_a_token_is_refused_outside_testing_mode(self):
        _app, client = _client(testing=False)
        r = client.post("/api/setup/model/pull", json={})
        self.assertEqual(r.status_code, 403)


class TestPullWorker(unittest.TestCase):
    """Ollama's stream, replayed. The failure worth pinning is the one that
    would hand the user a model that is not there."""

    def setUp(self):
        su._PULL.update(state="idle", serial=0, error=None, percent=None)

    def _replay(self, lines, status=200):
        resp = MagicMock()
        resp.status_code = status
        resp.iter_lines.return_value = iter(lines)
        with patch.object(su.requests, "post", return_value=resp):
            su._pull_worker("http://x", "m")
        return su.pull_snapshot()

    def test_a_successful_pull_reports_done_with_real_bytes(self):
        snap = self._replay([
            '{"status":"pulling manifest"}',
            '{"status":"pulling abc","digest":"abc","total":1000,"completed":500}',
            '{"status":"pulling abc","digest":"abc","total":1000,"completed":1000}',
            '{"status":"success"}',
        ])
        self.assertEqual(snap["state"], "done")
        self.assertEqual(snap["percent"], 100.0)
        self.assertEqual(snap["completed"], 1000)

    def test_a_truncated_stream_is_an_error_not_a_quiet_success(self):
        snap = self._replay([
            '{"status":"pulling abc","digest":"abc","total":1000,"completed":400}',
        ])
        self.assertEqual(snap["state"], "error")
        self.assertIn("Nothing was installed", snap["error"])

    def test_an_error_line_is_surfaced_verbatim(self):
        snap = self._replay(['{"error":"model not found"}'])
        self.assertEqual(snap["state"], "error")
        self.assertIn("model not found", snap["error"])

    def test_unparseable_lines_are_skipped_not_fatal(self):
        snap = self._replay(['not json at all', '', '{"status":"success"}'])
        self.assertEqual(snap["state"], "done")

    def test_an_http_error_names_the_model(self):
        snap = self._replay([], status=404)
        self.assertEqual(snap["state"], "error")
        self.assertIn("'m'", snap["error"])


if __name__ == "__main__":
    unittest.main()
