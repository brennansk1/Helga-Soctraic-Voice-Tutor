"""The domain layer must SHIP, not just exist.

`services/domains` was in no Dockerfile COPY and no compose mount. In the
container every `from services.domains...` raised ImportError, and every call
site swallows that at DEBUG level — `course_builder._classify_concepts_by_domain`,
`fsm_logic._domain_teaching`, `prompts.get_socratic_tutor_prompt`,
`book_skeleton`. All four domain modules were dead in the deployed system while
the entire test suite passed on the host, because on the host the package is
simply there.

That is this repository's signature defect one level up: not code that is never
called, but code that is never PACKAGED. These tests read the build files.
"""
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: Services whose code imports `services.domains`, directly or through
#: `services/common/prompts.py`.
NEEDS_DOMAINS = ("core", "rag")


def _read(path):
    with open(os.path.join(ROOT, path)) as f:
        return f.read()


@pytest.mark.parametrize("service", NEEDS_DOMAINS)
def test_dockerfile_copies_the_domain_layer(service):
    df = _read(f"services/{service}/Dockerfile")
    assert "services/domains" in df, (
        f"services/{service}/Dockerfile does not COPY services/domains — every "
        f"domain import in that container will raise ImportError and be "
        f"swallowed at DEBUG")


@pytest.mark.parametrize("service", NEEDS_DOMAINS)
def test_the_copy_happens_before_the_entrypoint(service):
    """A COPY after CMD is not a COPY."""
    df = _read(f"services/{service}/Dockerfile")
    copy_at = df.index("COPY services/domains")
    for directive in ("\nCMD ", "\nENTRYPOINT "):
        at = df.find(directive)
        if at >= 0:
            assert copy_at < at, f"COPY services/domains comes after {directive.strip()}"


def test_every_service_mounting_common_also_mounts_domains():
    """A bind mount SHADOWS the image.

    Shipping the package in the Dockerfile is not sufficient on its own: a
    service that bind-mounts `services/common` from the host gets the host's
    tree at that path, and without the sibling mount `services/domains` is
    absent under the mounted layout exactly as before.
    """
    compose = _read("docker-compose.yml")
    common = compose.count("./services/common:/app/services/common")
    domains = compose.count("./services/domains:/app/services/domains")
    assert domains >= common, (
        f"{common} services mount services/common but only {domains} mount "
        f"services/domains")


def test_the_registry_is_importable_the_way_the_container_imports_it():
    """`import services.domains.registry` must work from the repo root."""
    from services.domains.registry import available
    found = available()
    for domain in ("mathematics", "history", "science", "computer_science"):
        assert domain in found, f"{domain} not discoverable"


def test_domain_import_failure_would_be_loud_enough_to_notice():
    """The reason this went unseen for so long.

    Every call site catches the ImportError at `logger.debug`, so a whole
    feature tier can be missing with nothing above DEBUG to say so. This test
    does not force a redesign of that handling — it records the call sites, so
    that anyone adding another one sees the pattern and its cost.
    """
    sites = []
    for rel in ("services/core/course_builder.py",
                "services/core/fsm_logic.py",
                "services/common/prompts.py",
                "services/core/book_skeleton.py"):
        body = _read(rel)
        if "services.domains" in body:
            sites.append(rel)
    assert sites, "no domain import sites found — has the layer moved?"
