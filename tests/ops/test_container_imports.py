"""Every module a service imports must actually be in its image.

WHY
---
services/research/Dockerfile copied ONE file:

    COPY services/research/research_server.py .

When ranking.py was extracted into its own module for testability, nothing
added it to the image. The container then crash-looped on
`ModuleNotFoundError: No module named 'ranking'` from the moment that refactor
landed — restart:unless-stopped meant it looped quietly rather than failing
loudly.

The consequence was not "the research service is a bit degraded". It was that
grounding fell back to Wikipedia-only, so source_confidence capped at 0.40
against a 0.5 floor, so EVERY concept in EVERY course shipped with a "Limited
sources" marker. That was diagnosed for weeks as "SearXNG is down". SearXNG was
fine.

This checks statically that each service's local imports are present in what
its Dockerfile copies — no daemon required, so it runs in the normal suite.
"""

import os
import re
import unittest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))

SERVICES = {
    'research': 'services/research',
    'rag': 'services/rag',
    'core': 'services/core',
}


def _read(p):
    with open(p) as f:
        return f.read()


class TestDockerfilesCopyEveryLocalImport(unittest.TestCase):

    def _dockerfile_for(self, service_dir):
        for name in ('Dockerfile',):
            p = os.path.join(_ROOT, service_dir, name)
            if os.path.exists(p):
                return p
        return None

    def test_local_modules_are_copied_into_the_image(self):
        problems = []
        for service, sdir in SERVICES.items():
            dockerfile = self._dockerfile_for(sdir)
            if not dockerfile:
                continue
            df = _read(dockerfile)

            # A whole-directory COPY covers everything in the service,
            # whatever the destination — `COPY services/core /app/services/core`
            # is as complete as `COPY services/research/ ./`.
            if re.search(r'^COPY\s+' + re.escape(sdir) + r'/?\s+\S', df, re.M):
                continue

            copied = set()
            for m in re.finditer(r'^COPY\s+(\S+)', df, re.M):
                copied.add(os.path.basename(m.group(1)))

            local_mods = {
                f[:-3] for f in os.listdir(os.path.join(_ROOT, sdir))
                if f.endswith('.py') and f != '__init__.py'
            }

            for pyfile in os.listdir(os.path.join(_ROOT, sdir)):
                if not pyfile.endswith('.py'):
                    continue
                src = _read(os.path.join(_ROOT, sdir, pyfile))
                for m in re.finditer(r'^\s*(?:from|import)\s+(\w+)', src, re.M):
                    mod = m.group(1)
                    if mod not in local_mods or mod == pyfile[:-3]:
                        continue
                    # A guarded import with a package-qualified fallback
                    # (`except ImportError: from services.x.y import ...`) is
                    # not a crash risk, so it is not reported.
                    if re.search(r'from\s+services\.\w+\.' + mod + r'\s+import', src):
                        continue
                    if f'{mod}.py' not in copied:
                        problems.append(
                            f"{service}: {pyfile} imports '{mod}' but "
                            f"{os.path.basename(dockerfile)} never copies {mod}.py"
                        )

        self.assertEqual(
            sorted(set(problems)), [],
            "These containers will crash-loop on ModuleNotFoundError:\n  "
            + "\n  ".join(sorted(set(problems)))
        )


class TestGroundingCanClearTheFloor(unittest.TestCase):
    """The floor is 0.5. Wikipedia alone scores 0.40, so a dead research
    service does not merely reduce quality — it makes the floor unreachable
    for every concept, forever, silently."""

    def test_wikipedia_alone_cannot_clear_the_floor(self):
        from services.research.ranking import compute_confidence
        self.assertLess(compute_confidence(True, 0, 0), 0.5)

    def test_one_web_and_one_primary_source_clears_it(self):
        from services.research.ranking import compute_confidence
        self.assertGreaterEqual(compute_confidence(True, 1, 1), 0.5)
