"""The code default and the compose default must name the same model.

They did not, for as long as Nail has been the project model. `docs/MODEL.md`
said nail-35b-a3b-ctx was "the default in docker-compose.yml and in the code
defaults"; only the compose half was done, so every container ran Nail and
everything run on the HOST -- every tool in tools/, every benchmark, every long
run -- quietly ran qwen3.5:9b instead.

That is expensive in a specific way: the golden matrix ran for an hour and a
half producing quality numbers for a model the project does not ship, and
nothing about the output said so. A benchmark of the wrong configuration does
not look broken, it looks like evidence.

Two of the readers were worse than a wrong benchmark. `startup_preflight` sizes
the memory gate from the configured model, so the check that exists to stop
someone loading a model their machine cannot hold was doing its arithmetic on a
6.6 GB model while the stack loads 13.7 GB. And `setup_api` asked "is the model
installed" about a tag the machine had no reason to have.

So this pins the agreement rather than the value: change the model on purpose in
both places and the test follows you; change it in one and it fails.
"""
import os
import re
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "docker-compose.yml"

# ${OLLAMA_MODEL:-<default>} -- the default is what a machine with no .env gets.
_COMPOSE_DEFAULT = re.compile(r"OLLAMA_MODEL=\$\{OLLAMA_MODEL:-([^}]+)\}")


def _compose_defaults():
    if not COMPOSE.exists():
        pytest.skip("docker-compose.yml not present")
    found = _COMPOSE_DEFAULT.findall(COMPOSE.read_text())
    if not found:
        pytest.skip("compose does not set an OLLAMA_MODEL default")
    return found


def test_compose_agrees_with_the_code_default():
    from services.common.model_roles import DEFAULT_MODEL

    for got in _compose_defaults():
        assert got == DEFAULT_MODEL, (
            f"docker-compose.yml defaults OLLAMA_MODEL to {got!r} but "
            f"model_roles.DEFAULT_MODEL is {DEFAULT_MODEL!r}. Containers and "
            f"host-side tools would run different models."
        )


def test_every_compose_service_names_the_same_default():
    """One service left behind is the same bug with a smaller blast radius."""
    found = _compose_defaults()
    assert len(set(found)) == 1, f"compose services disagree: {sorted(set(found))}"


def test_the_preflight_sizes_for_the_model_that_will_actually_load(monkeypatch):
    """The memory gate must not size a model smaller than the one that loads."""
    from services.common.model_roles import DEFAULT_MODEL
    from services.common.startup_preflight import _configured_model

    for var in ("LLM_MODEL", "OLLAMA_MODEL"):
        monkeypatch.delenv(var, raising=False)
    assert _configured_model() == DEFAULT_MODEL


def test_the_setup_page_checks_for_the_model_the_stack_uses(monkeypatch):
    from services.common.model_roles import DEFAULT_MODEL

    for var in ("LLM_MODEL", "OLLAMA_MODEL"):
        monkeypatch.delenv(var, raising=False)
    try:
        from services.web_ui.setup_api import _model_name  # noqa
    except Exception:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "_setup_api", ROOT / "services/web-ui/setup_api.py")
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception as exc:                     # pragma: no cover
            pytest.skip(f"setup_api not importable standalone: {exc}")
        _model_name = mod._model_name

    name, source = _model_name()
    assert (name, source) == (DEFAULT_MODEL, "default")


def test_an_explicit_env_still_wins(monkeypatch):
    """The pin must not become a hardcode -- overriding is the supported path."""
    from services.common import model_roles

    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("HELGA_BUILD_MODEL", raising=False)
    monkeypatch.setenv("OLLAMA_MODEL", "some-other-model")
    _, model = model_roles.resolve(model_roles.BUILD)
    assert model == "some-other-model"
