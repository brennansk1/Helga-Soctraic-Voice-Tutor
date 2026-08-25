"""A book course must be written from the WHOLE chapter, not the parts that
happened to match a concept title.

`passage_for` picked the best PASSAGE_CHARS of a chapter for one concept, and
every concept picked independently with the same scoring function. On a
30,000-character chapter with four concepts that is four overlapping windows
over the same high-scoring regions — so the sections matching no concept title
were read by nobody, and nothing reported it.

Measured on a four-section chapter before this existed: one concept saw 15% of
it, and two whole sections were invisible to the entire course.

A chapter's concepts now PARTITION it. Every chunk is assigned to exactly one
concept, so the union is the whole chapter by construction.
"""
import pytest

from services.core.book_source import (PARTITION_MAX_CHARS, partition_chapter,
                                       passage_for)
from services.research.book_reader import Book, Chapter


def _chapter(sections, order=1):
    text = "\n\n".join(f"{k}\n{v}" for k, v in sections.items())
    return Book(title="B", chapters=[Chapter(order=order, title="C", text=text)])


SECTIONS = {
    "Indexes and B-trees": "B-tree index pages and fanout set lookup depth. " * 200,
    "Query planning": "The planner estimates cost and cardinality per path. " * 180,
    "Vacuum and bloat": "Dead tuples accumulate and vacuum reclaims space. " * 170,
    "Replication": "A standby replays the write-ahead log continuously. " * 160,
}
CONCEPTS = [{"uid": "c1", "title": "B-tree Indexes"},
            {"uid": "c2", "title": "Query Planning"}]


def test_the_whole_chapter_is_read():
    _, report = partition_chapter(_chapter(SECTIONS), 1, CONCEPTS)
    assert report["coverage"] == 1.0, (
        f"only {report['coverage']:.0%} of the chapter was read: "
        f"{report['covered_chars']} of {report['total_chars']} chars")


def test_sections_no_concept_is_named_after_are_still_read():
    """Vacuum and Replication match neither concept title. Under per-concept
    selection they were read by nobody."""
    passages, _ = partition_chapter(_chapter(SECTIONS), 1, CONCEPTS)
    union = " ".join(passages.values()).lower()
    for missing in ("vacuum", "standby"):
        assert missing in union, f"{missing!r} was never read by any concept"


def test_this_is_an_improvement_on_what_it_replaces():
    """Pins the actual defect, so a regression is visible as a number."""
    book = _chapter(SECTIONS)
    old = passage_for(book, 1, "B-tree Indexes")
    total = len(book.chapter(1).text)
    _, report = partition_chapter(book, 1, CONCEPTS)
    assert len(old) / total < 0.5, "the old path already covered the chapter?"
    assert report["coverage"] > len(old) / total


def test_each_concept_reads_in_the_authors_order():
    """Relevance-sorted fragments would discard the sequencing the author
    chose, and an explanation that builds across paragraphs stops working."""
    passages, _ = partition_chapter(_chapter(SECTIONS), 1, CONCEPTS)
    body = passages["c1"]
    first, second = body.find("B-tree index pages"), body.find("standby replays")
    if first != -1 and second != -1:
        assert first < second, "chunks were reordered out of reading order"


def test_more_concepts_means_a_smaller_share_each_not_less_coverage():
    many = [{"uid": f"c{i}", "title": t} for i, t in enumerate(
        ["B-tree Indexes", "Query Planning", "Vacuum", "Replication",
         "Cost Estimation", "WAL"])]
    passages, report = partition_chapter(_chapter(SECTIONS), 1, many)
    assert report["coverage"] == 1.0
    assert max(len(p) for p in passages.values()) < 24_000


def test_a_missing_chapter_yields_nothing_rather_than_the_wrong_chapter():
    """Material from the wrong chapter reads as authoritative and is not what
    the lesson is about."""
    passages, report = partition_chapter(_chapter(SECTIONS), 99, CONCEPTS)
    assert passages == {} and report["coverage"] == 0.0


# --- the context budget -----------------------------------------------------

def test_the_partition_fits_the_context_window():
    """A guard on the arithmetic, not a comment about it.

    num_ctx is 32,768, chosen by measurement: at that size the builder, the
    MiniCheck verifier and bge-m3 are all co-resident under a ~15.0 GB ceiling,
    and at 64k none of them are. Raising the passage ceiling or the research
    word budget without re-checking this is how a prompt starts silently losing
    its tail — which is where the required-section spec lives.
    """
    import re

    src = open("services/research/research_server.py").read()
    word_budget = int(re.search(r"^WORD_BUDGET\s*=\s*(\d+)", src, re.M).group(1))

    num_ctx = 32768
    passage = PARTITION_MAX_CHARS / 4        # ~4 chars per token
    research = word_budget * 1.4             # words -> tokens
    instructions = 1200
    output = 3000
    total = passage + research + instructions + output

    assert total < num_ctx * 0.75, (
        f"the worst-case prompt is {total:.0f} tokens against a {num_ctx} "
        f"window ({total / num_ctx:.0%}) — too close to the edge, and the tail "
        f"of this prompt is the part that specifies the required sections")
