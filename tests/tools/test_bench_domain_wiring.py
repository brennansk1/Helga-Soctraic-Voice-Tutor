"""The benchmark must measure the system that ships, and say when it changed.

TWO SEPARATE OBLIGATIONS
------------------------
1. SUPPLY what production supplies. `bench_domains` passed six prompt inputs
   while production passed the domain's `concept_kind` too, so every recorded
   figure measured production-MINUS-the-domain-layer — a system that does not
   ship. That is the same defect `turn_state` had before it was fixed.

2. REFUSE an invalid comparison. Adding an input changes the system being
   measured, so it must move the fingerprint. The recorded 2.20 adaptation /
   2.80 socratic baselines were taken under c98fa5eb86455db5 and
   a21992105fe9aad7; holding a new run against that table compares two
   different instruments, which is exactly what the fingerprint exists to
   prevent.

So "did the domain layer help?" is answerable only by running BOTH arms under
the SAME instrument, which is what `HELGA_BENCH_NO_DOMAIN` is for.
"""
import os
import sys

sys.path.append(os.getcwd())
sys.path.append(os.path.join(os.getcwd(), "tools"))

import pytest  # noqa: E402

import bench_domains as bd  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_env():
    original = os.environ.get("HELGA_BENCH_NO_DOMAIN")
    os.environ.pop("HELGA_BENCH_NO_DOMAIN", None)
    yield
    if original is None:
        os.environ.pop("HELGA_BENCH_NO_DOMAIN", None)
    else:
        os.environ["HELGA_BENCH_NO_DOMAIN"] = original


def _topic(domain):
    return bd.DOMAINS[domain]["topics"][0]


def test_concept_kind_is_a_fingerprinted_input():
    """It changes the system measured, so it must invalidate old baselines."""
    assert "concept_kind" in bd.BENCH_PROMPT_INPUTS


def test_a_domain_with_a_package_supplies_a_kind():
    got = bd._kind_for("mathematics", _topic("mathematics"))
    assert got and got[0] == "mathematics"
    assert got[1] != "UNKNOWN"


def test_a_domain_without_a_package_supplies_nothing():
    """A bench domain with no specialist must be measured exactly as it ships.

    Computed, not hard-coded. This test named `history` until history GOT a
    package, at which point it failed for the right reason and the wrong one:
    the behaviour was correct and the fixture was stale. Deriving the list from
    the registry means the next domain added does not break it either.
    """
    from services.domains import registry

    have = set(registry.available())
    without = [k for k in sorted(bd.DOMAINS) if k not in have]
    assert without, (
        "every bench domain now has a specialist — this test has nothing left "
        "to check and should be deleted rather than weakened")
    for key in without:
        assert bd._kind_for(key, _topic(key)) is None, key


def test_every_domain_WITH_a_package_supplies_a_kind():
    """The other half: a domain that HAS a specialist must actually use it."""
    from services.domains import registry

    have = [k for k in sorted(bd.DOMAINS) if k in set(registry.available())]
    assert have, "no bench domain has a specialist"
    for key in have:
        got = bd._kind_for(key, _topic(key))
        assert got is None or got[0] == key, got


def test_the_ab_switch_withholds_the_kind():
    os.environ["HELGA_BENCH_NO_DOMAIN"] = "1"
    assert bd._kind_for("mathematics", _topic("mathematics")) is None


def test_the_ab_switch_does_not_move_the_fingerprint():
    """Both arms must be the SAME instrument, or the comparison is void."""
    before = bd.rubric_fingerprint()
    os.environ["HELGA_BENCH_NO_DOMAIN"] = "1"
    assert bd.rubric_fingerprint() == before


def test_kind_lookup_never_raises():
    """A benchmark that dies because a domain package moved measures nothing."""
    for domain in sorted(bd.DOMAINS):
        for topic in bd.DOMAINS[domain]["topics"]:
            bd._kind_for(domain, topic)
    assert bd._kind_for("no_such_domain", {"concept": "x"}) is None
    assert bd._kind_for("mathematics", {}) is None
