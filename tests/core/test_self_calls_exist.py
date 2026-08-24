"""A method called on `self` must exist on the class that calls it.

Python does not check this until the line runs, so a helper added to one class
and called from another survives every import, every unit test that does not
reach that branch, and fails only in production — at the moment it is reached.

That is not hypothetical. A cancellation checkpoint was added to
SkeletonBuilder and a blind edit dropped a `self._checkpoint("module")` into a
loop belonging to SyllabusAuditor. Nothing failed at import. It killed a real
course build fourteen minutes in with

    AttributeError: 'SyllabusAuditor' object has no attribute '_checkpoint'

after the expensive part was already paid for. The check is static and cheap,
so it runs over the whole build pipeline rather than the one method that broke.
"""
import ast
import os

import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))

MODULES = [
    "services/core/course_builder.py",
    "services/core/fsm_logic.py",
    "services/common/storage.py",
]

#: Attributes set dynamically or inherited from a base this scan cannot see.
#: Kept explicit and short — every entry is a hole in the check.
_DYNAMIC = {"_tl", "_cache", "storage", "provider", "model"}


def _self_method_calls(cls):
    for n in ast.walk(cls):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and isinstance(n.func.value, ast.Name)
                and n.func.value.id == "self"):
            yield n.func.attr, n.lineno


@pytest.mark.parametrize("relpath", MODULES)
def test_every_self_call_resolves_on_its_own_class(relpath):
    path = os.path.join(_ROOT, relpath)
    tree = ast.parse(open(path, encoding="utf-8").read())

    # INHERITANCE IS RESOLVED, NOT WAVED AT.
    #
    # A first version allowed any name defined ANYWHERE in the file, to avoid
    # flagging genuine base-class methods. That hole is precisely the bug this
    # test exists for: `_checkpoint` is defined in SkeletonBuilder, so a stray
    # call to it from SyllabusAuditor would have been waved through. Bases are
    # followed properly instead, and a class whose base lives outside this file
    # is skipped rather than guessed at.
    classes = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}

    def own_names(cls):
        names = {f.name for f in cls.body
                 if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))}
        names |= {t.id for f in cls.body if isinstance(f, ast.Assign)
                  for t in f.targets if isinstance(t, ast.Name)}
        names |= {t.attr for n in ast.walk(cls) if isinstance(n, ast.Assign)
                  for t in n.targets
                  if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name)
                  and t.value.id == "self"}
        names |= {n.target.attr for n in ast.walk(cls)
                  if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Attribute)
                  and isinstance(n.target.value, ast.Name)
                  and n.target.value.id == "self"}
        return names

    def visible(cls, seen=None):
        seen = seen or set()
        if cls.name in seen:
            return set()
        seen.add(cls.name)
        names = own_names(cls)
        for b in cls.bases:
            bn = b.id if isinstance(b, ast.Name) else None
            if bn is None or bn not in classes:
                return None          # base outside this file: cannot judge
            inherited = visible(classes[bn], seen)
            if inherited is None:
                return None
            names |= inherited
        return names

    missing = []
    for cls in classes.values():
        own = visible(cls)
        if own is None:
            continue                 # inherits from outside this file
        for attr, line in _self_method_calls(cls):
            if attr in own or attr in _DYNAMIC:
                continue
            missing.append(f"{relpath}:{line} {cls.name}.{attr}()")

    assert not missing, (
        "these call a method their class does not have, and Python will not "
        "say so until the line runs:\n  " + "\n  ".join(missing))
