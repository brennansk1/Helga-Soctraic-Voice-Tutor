"""The domain boundary, enforced rather than documented.

The requirement: computer science is the FIRST domain, not the only one. Its
code must stay in its own package so a second domain can be added without
untangling it from the shared pipeline.

A rule like that decays silently — one convenient import from the core into
`domains/computer_science` and the boundary is gone, with nothing failing. So
it is asserted here, where crossing it breaks the build.
"""
import ast
import os
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
CS = ROOT / "services" / "domains" / "computer_science"

#: Modules that are allowed to know a domain package exists at all. The
#: registry is the seam by design; everything else must go through it.
ALLOWED_TO_IMPORT_CS = {
    "services/domains/registry.py",
    "services/domains/__init__.py",
}


def _imports(path):
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return []
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.append(node.module)
    return out


def _py_files_under(*parts):
    base = ROOT.joinpath(*parts)
    return [p for p in base.rglob("*.py")] if base.exists() else []


def test_the_cs_domain_package_exists():
    assert CS.is_dir(), "computer science must live in its own package"
    assert (CS / "__init__.py").exists()


def test_shared_pipeline_does_not_import_the_cs_domain_directly():
    """The core builds ANY course. It must not name a specific domain."""
    offenders = []
    for area in (("services", "core"), ("services", "common"),
                 ("services", "research"), ("services", "rag")):
        for path in _py_files_under(*area):
            rel = str(path.relative_to(ROOT))
            if rel in ALLOWED_TO_IMPORT_CS:
                continue
            for name in _imports(path):
                if "domains.computer_science" in name:
                    offenders.append(f"{rel} imports {name}")
    assert not offenders, (
        "the shared pipeline reached into the CS domain directly, which is "
        "exactly what stops a second domain being added:\n  "
        + "\n  ".join(offenders))


def test_the_cs_domain_does_not_import_another_domain():
    """Domains must not depend on each other, or they cannot ship separately."""
    offenders = []
    for path in CS.rglob("*.py"):
        for name in _imports(path):
            if "services.domains." in name and "computer_science" not in name:
                if not name.rstrip(".").endswith("services.domains"):
                    offenders.append(f"{path.name} imports {name}")
    assert not offenders, offenders


def test_registry_discovers_the_domain_without_naming_it():
    """Discovery has to be by lookup, or every new domain edits the registry."""
    from services.domains import registry

    found = registry.available()
    assert "computer_science" in found, found


def test_an_unknown_domain_degrades_instead_of_raising():
    """A subject with no domain package must still build, without guidance.

    A missing domain is the NORMAL case — most subjects have no package — so it
    has to be a quiet None, never an exception that fails a course build.
    """
    from services.domains import registry

    assert registry.for_domain("underwater_basket_weaving") is None
    # No LLM available and no keyword match: it must not guess a domain.
    assert registry.for_subject("underwater basket weaving") is None


def test_a_cs_subject_resolves_to_the_cs_domain():
    from services.domains import registry

    mod = registry.for_subject("Python programming")
    assert mod is not None, "an obvious CS subject failed to resolve"
    assert "computer_science" in mod.__name__


def test_domain_is_recorded_on_the_course_under_a_stable_key():
    """Everything downstream reads this key; renaming it breaks it silently."""
    from services.domains import registry

    assert registry.DOMAIN_KEY == "teaching_domain"
    course = {"title": "Learning Rust", "modules": []}
    registry.classify_course(course, subject="Learning Rust")
    assert course.get(registry.DOMAIN_KEY) == "computer_science"
    assert registry.domain_of(course) == "computer_science"
