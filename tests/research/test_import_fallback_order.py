"""Package import first, flat fallback second — and the order matters.

The research image mounts ./services/research at /app, so its modules import
as `doc_reader`, not `services.research.doc_reader`. Several imports had no
flat fallback and failed at runtime with "No module named 'services'", inside
try/except blocks that swallowed it — so doc-set expansion never ran in the
container and never said so.

Adding the fallback FLAT-FIRST fixed the container and broke the tests: with
both names importable from the repo root, one file becomes two module objects,
and `mock.patch.object(services.research.libretexts, "chapters_for")` does not
affect code that imported `libretexts`. A test that mocks a network lookup
quietly went to the network instead.

Package first is correct in both places: in the container the package import
raises and the flat one answers; everywhere else the package import wins and
module identity stays single.
"""
import glob
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BLOCK = re.compile(
    r"try:[^\n]*\n\s*(?P<first>from|import)[^\n]*\n\s*except ImportError:[^\n]*\n\s*(?P<second>from|import)[^\n]*",
)


def _blocks(src):
    for m in re.finditer(
            r"try:[^\n]*\n(?P<a>\s*(?:from|import)[^\n]*)\n\s*except ImportError:[^\n]*\n(?P<b>\s*(?:from|import)[^\n]*)",
            src):
        yield m.group("a").strip(), m.group("b").strip()


def test_every_fallback_tries_the_package_first():
    offenders = []
    for path in glob.glob(os.path.join(ROOT, "services", "research", "*.py")):
        with open(path, encoding="utf-8") as f:
            src = f.read()
        for first, second in _blocks(src):
            if "services." in second and "services." not in first:
                offenders.append(f"{os.path.basename(path)}: {first!r} before {second!r}")
    assert not offenders, (
        "flat-first import(s) found; these split module identity under test:\n  "
        + "\n  ".join(offenders))


def test_the_research_modules_have_no_unguarded_package_import():
    """The original bug: an import that only works as a package, in a service
    that runs flat."""
    offenders = []
    for path in glob.glob(os.path.join(ROOT, "services", "research", "*.py")):
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
        for i, line in enumerate(lines):
            if re.match(r"\s*(from|import) services\.research", line):
                window = "".join(lines[max(0, i - 3):i])
                if "except ImportError" not in window and "try:" not in window:
                    offenders.append(f"{os.path.basename(path)}:{i + 1} {line.strip()}")
    assert not offenders, (
        "unguarded package import(s) in a flat-layout service:\n  "
        + "\n  ".join(offenders))
