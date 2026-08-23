"""The LibreTexts reader: robots compliance, book selection, extraction.

No network. Every test here exercises pure logic — the fetching path is
deliberately thin so that this is possible.
"""
import pytest

from services.research import libretexts as lt


# --- robots compliance, which is the part that must not regress -------------

@pytest.mark.parametrize("url", [
    "https://math.libretexts.org/@api/deki/pages/123/contents",
    "https://math.libretexts.org/Special:Search?q=calculus",
    "https://math.libretexts.org/Bookshelves/Calculus/X?action=edit",
    "https://human.libretexts.org/User:Someone",
    "https://math.libretexts.org/Template:Foo",
    "https://math.libretexts.org/deki/something",
    "https://math.libretexts.org/index.php?title=Special:Random",
])
def test_disallowed_urls_are_refused(url):
    """These are the verbatim Disallow rules. The MindTouch contents API is
    among them, which matters: it is the obvious way to do this and a web
    search recommends it."""
    assert lt._allowed(url) is False


@pytest.mark.parametrize("url", [
    "https://math.libretexts.org/Bookshelves/Calculus/Calculus_(OpenStax)",
    "https://human.libretexts.org/Bookshelves/History/World_History/X",
    "https://math.libretexts.org/sitemap.xml",
])
def test_allowed_urls(url):
    assert lt._allowed(url) is True


def test_fetch_refuses_a_disallowed_url_without_network(monkeypatch):
    """`_get` must refuse BEFORE it opens a connection."""
    called = []
    monkeypatch.setattr(lt._rl, "wait", lambda u: called.append(u))
    assert lt._get("https://math.libretexts.org/@api/deki/pages/1") is None
    assert called == [], "a disallowed URL reached the rate limiter"


# --- book selection ----------------------------------------------------------

_BOOKS = {
    "Calculus/Calculus_(OpenStax)": ["u"] * 285,
    "Algebra/Algebra_and_Trigonometry_1e_(OpenStax)": ["u"] * 194,
    "Linear_Algebra/Linear_Algebra_with_Applications_(Nicholson)": ["u"] * 178,
    "Arithmetic_and_Basic_Math/Basic_Math_(Grade_6)": ["u"] * 214,
}


def test_shelf_carries_the_level(monkeypatch):
    """The failure `mathematics.source_for` documented and could not fix.

    A generic relevance matcher answers "linear algebra" with *Algebra 1*, a
    school text for a university subject. On the shelf, `Linear_Algebra` and
    `Algebra` are separate directories, so the level is part of the match.
    """
    monkeypatch.setattr(lt, "books", lambda lib: _BOOKS)
    path, _, _ = lt.find("linear algebra", lib="math")
    assert "Linear_Algebra/" in path
    assert "Nicholson" in path


def test_exact_subject_still_wins(monkeypatch):
    monkeypatch.setattr(lt, "books", lambda lib: _BOOKS)
    assert "Calculus_(OpenStax)" in lt.find("Calculus", lib="math")[0]


def test_short_acronyms_are_not_discarded():
    """Measured: with a 3-letter minimum, "US History" scored only on
    "history" and selected *Art History II* off the Art shelf."""
    us = lt._score("US History", "History/National_History/U.S._History_(YAWP)")
    art = lt._score("US History", "Art/Art_History_and_Theory/Art_History_II")
    assert us > art


def test_shelf_scope_constrains_the_search(monkeypatch):
    mixed = {
        "Art/Art_History_and_Theory/Art_History_II": ["u"] * 367,
        "History/National_History/U.S._History_(YAWP)": ["u"] * 338,
    }
    monkeypatch.setattr(lt, "books", lambda lib: mixed)
    path, _, _ = lt.find("US History", lib="human", shelf="History")
    assert path.startswith("History/")


def test_coverage_ranks_on_count_not_fraction():
    """Measured: ranking by fraction answered "the Civil War" with a 29-page
    Spanish-language book, because 6/29 beats 25/338."""
    big = [f"https://x/Bookshelves/H/B/{i}%3A_The_Civil_War" for i in range(25)]
    big += [f"https://x/Bookshelves/H/B/{i}%3A_Other" for i in range(313)]
    small = [f"https://x/Bookshelves/H/S/{i}%3A_civil_war" for i in range(6)]
    small += [f"https://x/Bookshelves/H/S/{i}%3A_otro" for i in range(23)]
    big_n, big_f = lt._coverage("the Civil War", big)
    small_n, small_f = lt._coverage("the Civil War", small)
    assert small_f > big_f          # the fraction favours the small book
    assert big_n > small_n          # the count, which is what ranks, does not


# --- sitemap shapes ----------------------------------------------------------

def test_both_sitemap_shapes_are_handled(monkeypatch):
    """The mathematics library serves <urlset>; humanities serves
    <sitemapindex>. Assuming one finds 43,381 URLs on one library and 2 on the
    other — a silent nothing, which is the failure mode this repo keeps
    hitting."""
    index = ('<sitemapindex><sitemap><loc>https://human.libretexts.org/'
             'sitemap_0.xml</loc></sitemap></sitemapindex>')
    shard = ('<urlset><url><loc>https://human.libretexts.org/Bookshelves/'
             'History/World_History/Book/01%3A_A</loc></url></urlset>')

    def _fake_get(url, timeout=45):
        return index if url.endswith("/sitemap.xml") else shard

    monkeypatch.setattr(lt, "_get", _fake_get)
    monkeypatch.setattr(lt, "_cache", lambda: None)
    urls = lt.sitemap_urls("human")
    assert len(urls) == 1 and "/Bookshelves/" in urls[0]


def test_non_bookshelf_urls_are_dropped(monkeypatch):
    """`/Courses/` is one institution's remix for one term. `/Bookshelves/` is
    the edited shelf."""
    body = ('<urlset>'
            '<url><loc>https://math.libretexts.org/Courses/UCD/X/1</loc></url>'
            '<url><loc>https://math.libretexts.org/Bookshelves/Calculus/B/1</loc></url>'
            '</urlset>')
    monkeypatch.setattr(lt, "_get", lambda u, timeout=45: body)
    monkeypatch.setattr(lt, "_cache", lambda: None)
    urls = lt.sitemap_urls("math")
    assert len(urls) == 1 and "/Bookshelves/" in urls[0]


def test_books_are_found_at_variable_depth(monkeypatch):
    """Maths puts a book at depth 2, history at depth 3. A fixed-depth
    assumption works on one library and silently finds nothing on the other."""
    urls = [
        "https://x/Bookshelves/Calculus/Calc_(OS)/00%3A_Front_Matter",
        "https://x/Bookshelves/Calculus/Calc_(OS)/01%3A_Limits",
        "https://x/Bookshelves/History/National_History/US_(YAWP)/00%3A_Front_Matter",
        "https://x/Bookshelves/History/National_History/US_(YAWP)/01%3A_New_World",
    ]
    monkeypatch.setattr(lt, "sitemap_urls", lambda lib: urls)
    monkeypatch.setattr(lt, "_cache", lambda: None)
    found = lt.books("any")
    assert "Calculus/Calc_(OS)" in found
    assert "History/National_History/US_(YAWP)" in found


# --- extraction --------------------------------------------------------------

_PAGE = """
<html><body><section class="mt-content-container">
<h1>1.1: Review of Functions</h1>
<h5>Learning Objectives</h5>
<ul><li>Evaluate a function.</li></ul>
<p>A function assigns \\(f(x)=3x^2+2x-1\\) to each input.</p>
<h3>Example \\(\\PageIndex{1}\\): Evaluating Functions</h3>
<p>For the function \\(f(x)=3x^2+2x-1\\), evaluate \\(f(-2)\\) and \\(f(a+h)\\).</p>
<h4>Solution</h4>
<p>Substitute the given value for \\(x\\) in the formula for \\(f(x)\\).
Then \\(f(-2)=3(-2)^2+2(-2)-1=12-4-1=7\\), and expanding the binomial
gives \\(f(a+h)=3(a+h)^2+2(a+h)-1\\).</p>
<script>ignore me</script>
</section></body></html>
"""
# NOTE ON THIS FIXTURE: the solution is long on purpose. An earlier version was
# 24 characters against `worked_examples.MIN_SOLUTION = 40`, so the miner
# correctly found nothing and the test read as a broken extractor. Fixtures
# shorter than the thresholds they exercise have impersonated a broken detector
# three times in this repository; build them from real pages.


def test_extract_preserves_what_the_miners_need():
    title, text = lt.extract(_PAGE)
    assert title.startswith("1.1")
    # Headings on their OWN lines, or `^\\s*example` never matches.
    assert "\nExample 1: Evaluating Functions" in text
    assert "\nSolution" in text
    # LaTeX normalised to the delimiter `_has_math` and KaTeX both expect.
    assert "$f(x)=3x^2+2x-1$" in text
    # The PageIndex counter is resolved, not left as markup.
    assert "PageIndex" not in text
    assert "ignore me" not in text


def test_extracted_text_feeds_the_maths_miner():
    """The wiring that matters: extraction output must actually mine."""
    from services.domains.mathematics.worked_examples import examples_in_text
    _, text = lt.extract(_PAGE)
    assert len(examples_in_text(text)) >= 1


def test_extract_survives_empty_input():
    assert lt.extract("") == ("", "")
    assert lt.extract(None) == ("", "")


# --- chapters, which replaced a robots violation -----------------------------

def test_chapters_for_strips_positional_numbers(monkeypatch):
    urls = [
        "https://x/Bookshelves/Calculus/B/00%3A_Front_Matter/a",
        "https://x/Bookshelves/Calculus/B/01%3A_Functions/1.1%3A_Review",
        "https://x/Bookshelves/Calculus/B/02%3A_Limits/2.1%3A_Intro",
        "https://x/Bookshelves/Calculus/B/zz%3A_Back_Matter/a",
    ]
    monkeypatch.setattr(lt, "find", lambda s, lib=None, shelf=None:
                        ("Calculus/B", urls, "math"))
    assert lt.chapters_for("Calculus") == ["Functions", "Limits"]


def test_library_routing():
    assert lt.library_for("Calculus") == "math"
    assert lt.library_for("World History") == "human"
    assert lt.library_for("organic chemistry") == "chem"
    # Statistics has its own library; routing it to `math` selected *Math For
    # Liberal Art Students* over a statistics text.
    assert lt.library_for("statistics") == "stats"
    assert lt.library_for("") is None
