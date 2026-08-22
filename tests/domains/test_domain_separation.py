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


def _domain_packages():
    """Every domain package on disk, discovered the way the registry does."""
    base = ROOT / "services" / "domains"
    return sorted(p.name for p in base.iterdir()
                  if p.is_dir() and (p / "__init__.py").exists()
                  and not p.name.startswith("_"))


def test_shared_pipeline_does_not_import_ANY_domain_directly():
    """The core builds ANY course. It must not name a specific domain.

    Generalised when mathematics was added: the original only checked for
    computer_science, so a core module importing `domains.mathematics` would
    have passed. The rule is about the boundary, not about one domain.
    """
    domains = _domain_packages()
    assert len(domains) >= 2, f"expected several domains, found {domains}"
    offenders = []
    for area in (("services", "core"), ("services", "common"),
                 ("services", "research"), ("services", "rag")):
        for path in _py_files_under(*area):
            rel = str(path.relative_to(ROOT))
            if rel in ALLOWED_TO_IMPORT_CS:
                continue
            for name in _imports(path):
                for d in domains:
                    if f"domains.{d}" in name:
                        offenders.append(f"{rel} imports {name}")
    assert not offenders, (
        "the shared pipeline reached into a domain package directly, which is "
        "exactly what stops another domain being added:\n  "
        + "\n  ".join(offenders))


def test_every_domain_satisfies_the_registry_contract():
    """A domain missing a required hook fails at teaching time, not import."""
    from services.domains import registry

    for key in registry.available():
        module = registry.for_domain(key)
        report = registry.contract_report(module)
        assert not report["missing_required"], f"{key}: {report}"


def test_domains_do_not_share_concept_kinds():
    """Kinds are domain answers, and borrowing them is the bug this prevents.

    `SYNTAX` is a real distinction about code and meaningless about
    mathematics; `THEOREM` is the reverse. If two domains ever agree on their
    whole kind vocabulary, one of them is using the other's answers.
    """
    from services.domains import registry

    # Kind constants are exported as module attributes whose value is their
    # own name (SYNTAX = "SYNTAX"). RANK itself is internal to each domain's
    # concept_kind module and is deliberately not part of the contract, so
    # this reads the public surface rather than reaching inside.
    vocabularies = {}
    for key in registry.available():
        module = registry.for_domain(key)
        kinds = {n for n in dir(module)
                 if n.isupper() and getattr(module, n, None) == n}
        kinds -= {"UNKNOWN", "DOMAIN", "LABEL"}
        if kinds:
            vocabularies[key] = frozenset(kinds)
    assert len(vocabularies) >= 2
    seen = list(vocabularies.items())
    for i in range(len(seen) - 1):
        for j in range(i + 1, len(seen)):
            (ka, va), (kb, vb) = seen[i], seen[j]
            assert va != vb, f"{ka} and {kb} share one kind vocabulary"


def test_no_domain_imports_another_domain():
    """Domains must not depend on each other, or they cannot ship separately."""
    offenders = []
    base = ROOT / "services" / "domains"
    for domain in _domain_packages():
        for path in (base / domain).rglob("*.py"):
            for name in _imports(path):
                if "services.domains." not in name:
                    continue
                if domain in name:
                    continue
                if name.rstrip(".").endswith("services.domains"):
                    continue
                offenders.append(f"{domain}/{path.name} imports {name}")
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
