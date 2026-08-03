"""A5.3 — the design system must hold offline, in both themes.

Two classes of defect this guards, both measured on 2026-08-03 rather than
imagined:

1. OFFLINE. base.html pulled DM Sans and JetBrains Mono from
   fonts.googleapis.com with no local copies. Helga's entire premise is that it
   runs on a Mac Mini with no internet, so the type system silently fell back
   to system defaults and every page load paid a DNS/connect timeout first.

2. THEME PARITY. 14 of 32 colour tokens were declared in :root and never
   overridden for dark — including --focus-ring (a light-blue ring on a dark
   surface), --shadow-lg (a light-mode shadow drawn over dark), and all six
   --bloom-* colours, which paint the learn path, the product's signature
   screen. "Light and dark both verified" is an A5 gate item; asserting it is
   the only way it stays true.
"""

import os
import re
import sys
import unittest

_here = os.path.dirname(__file__)
_root = os.path.abspath(os.path.join(_here, '../../'))
_static = os.path.join(_root, 'services/web-ui/static')
_templates = os.path.join(_root, 'services/web-ui/templates')


def _read(path):
    with open(path) as f:
        return f.read()


def _tokens(css, selector):
    """Custom properties declared in the first matching block."""
    m = re.search(re.escape(selector) + r'\s*\{(.*?)\n\}', css, re.S)
    if not m:
        return {}
    return dict(re.findall(r'(--[\w-]+)\s*:\s*([^;]+);', m.group(1)))


class TestNoExternalAssets(unittest.TestCase):
    """An offline appliance may not depend on a CDN for how it looks."""

    EXTERNAL = re.compile(r'(?:href|src)\s*=\s*["\']https?://', re.I)

    def test_no_template_loads_a_remote_stylesheet_or_script(self):
        offenders = []
        for name in os.listdir(_templates):
            if not name.endswith('.html'):
                continue
            for i, line in enumerate(_read(os.path.join(_templates, name)).splitlines(), 1):
                if self.EXTERNAL.search(line):
                    offenders.append(f"{name}:{i}")
        self.assertEqual(
            offenders, [],
            "These load an asset over the network. Helga runs with no "
            "internet, so the request cannot succeed — it can only stall the "
            f"page: {offenders}"
        )

    def test_google_fonts_are_gone(self):
        base = _read(os.path.join(_templates, 'base.html'))
        self.assertNotIn('fonts.googleapis.com', base)
        self.assertNotIn('fonts.gstatic.com', base)

    def test_the_font_files_are_actually_present(self):
        fonts = os.path.join(_static, 'fonts')
        self.assertTrue(os.path.isdir(fonts), "no local fonts directory")
        for weight in ('400', '500', '600', '700'):
            self.assertTrue(
                os.path.exists(os.path.join(fonts, f'plexsans-{weight}.woff2')),
                f"IBM Plex Sans {weight} is the UI face; without it the whole "
                f"interface falls back to a system font"
            )
        for weight in ('400', '600'):
            self.assertTrue(os.path.exists(
                os.path.join(fonts, f'jetbrainsmono-{weight}.woff2')))
        self.assertTrue(
            os.path.exists(os.path.join(fonts, 'dmsans-700.woff2')),
            "DM Sans is kept for the wordmark only"
        )

    def test_every_font_face_src_resolves_to_a_real_file(self):
        css = _read(os.path.join(_static, 'css/fonts.css'))
        refs = re.findall(r"url\(['\"]?\.\./fonts/([^'\")]+)", css)
        self.assertTrue(refs, "fonts.css declares no @font-face src")
        for ref in refs:
            self.assertTrue(
                os.path.exists(os.path.join(_static, 'fonts', ref)),
                f"fonts.css points at {ref}, which does not exist"
            )

    def test_font_payload_stays_small(self):
        """A design system is not a licence to ship megabytes."""
        fonts = os.path.join(_static, 'fonts')
        total = sum(os.path.getsize(os.path.join(fonts, f))
                    for f in os.listdir(fonts))
        self.assertLess(total, 600 * 1024,
                        f"font payload is {total // 1024} KB; subset it")


class TestThemeParity(unittest.TestCase):
    """Any colour-valued token must be defined for BOTH themes."""

    @classmethod
    def setUpClass(cls):
        cls.style = _read(os.path.join(_static, 'css/style.css'))
        cls.ds = _read(os.path.join(_static, 'css/design-system.css'))
        cls.light = {}
        cls.light.update(_tokens(cls.style, ':root'))
        cls.light.update(_tokens(cls.ds, ':root'))
        cls.dark = {}
        cls.dark.update(_tokens(cls.style, '[data-theme="dark"]'))
        cls.dark.update(_tokens(cls.ds, '[data-theme="dark"]'))

    def _colour_tokens(self):
        return {k: v for k, v in self.light.items()
                if re.search(r'#[0-9a-fA-F]{3,8}|rgba?\(', v)}

    def test_every_colour_token_has_a_dark_value(self):
        missing = sorted(set(self._colour_tokens()) - set(self.dark))
        self.assertEqual(
            missing, [],
            "These render a light-mode colour on a dark surface. A token "
            f"defined for only one theme is a constant, not a token: {missing}"
        )

    def test_bloom_colours_flip(self):
        """They paint the learn path — the signature screen."""
        for i in range(1, 7):
            self.assertIn(f'--bloom-{i}', self.dark)

    def test_focus_ring_is_theme_aware(self):
        self.assertIn('--focus-ring-color', self.dark,
                      "a light-blue focus ring on a dark surface fails contrast")

    def test_shadows_flip(self):
        """A light-mode shadow over a dark surface reads as a grey smear."""
        for level in ('--shadow-1', '--shadow-2', '--shadow-3', '--shadow-4'):
            self.assertIn(level, self.dark)

    def test_inverted_text_flips(self):
        """'Inverted' means 'reads against the accent'. On dark surfaces that
        is deep ink, not white."""
        self.assertIn('--text-inverted', self.dark)


class TestMotionSystem(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.ds = _read(os.path.join(_static, 'css/design-system.css'))

    def test_durations_and_easings_are_tokens(self):
        for token in ('--dur-fast', '--dur-base', '--dur-slow',
                      '--ease-standard', '--ease-decelerate',
                      '--ease-accelerate', '--ease-emphasized'):
            self.assertIn(token, self.ds)

    def test_reduced_motion_is_respected(self):
        self.assertIn('prefers-reduced-motion: reduce', self.ds,
                      "transform animations are genuinely unpleasant for "
                      "people with vestibular disorders; this is a real "
                      "setting, not a nicety")

    def test_reduced_motion_still_changes_state(self):
        """Suppressing motion must not suppress the state change itself —
        otherwise the UI stops telling the user anything happened."""
        block = self.ds[self.ds.index('prefers-reduced-motion'):]
        self.assertIn('helga-fade-in', block,
                      "a cross-fade does not trigger motion sensitivity and "
                      "keeps state changes legible")

    def test_animations_are_not_infinite_except_loaders(self):
        infinite = re.findall(r'animation:[^;]*infinite[^;]*;', self.ds)
        for decl in infinite:
            self.assertIn(
                'shimmer', decl,
                f"only an indeterminate loader may loop forever: {decl}")


class TestBrandMarkDegradesGracefully(unittest.TestCase):

    def test_gradient_wordmark_has_a_solid_fallback(self):
        ds = _read(os.path.join(_static, 'css/design-system.css'))
        self.assertIn('@supports not', ds,
                      "background-clip:text with transparent fill renders the "
                      "wordmark INVISIBLE where unsupported, not merely plain")


if __name__ == '__main__':
    unittest.main()


class TestNoEmojiInTheUI(unittest.TestCase):
    """Emoji are not an icon system, and this product shipped 127 of them.

    They render as a different typeface on every platform, so the product was
    literally a different shape for different users — including the brand mark,
    which was 🏔️ injected via `content:` and therefore drawn by the operating
    system rather than by us. They also carry their own colour, so they ignore
    the theme, and they sit on a different baseline than DM Sans.

    Replaced by CSS masks in icons.css, which inherit currentColor and share
    one 24px grid. This test stops them coming back one convenient shortcut at
    a time.
    """

    # Pictographs only. Box-drawing, arrows and typographic marks are text.
    EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️⬀-⯿]")

    def _sources(self):
        for sub in ('templates', 'static/js', 'static/css'):
            base = os.path.join(_root, 'services/web-ui', sub)
            for dirpath, _, names in os.walk(base):
                for name in names:
                    if not name.endswith(('.html', '.js', '.css')):
                        continue
                    if 'min.js' in name:
                        continue        # vendored libraries are not ours
                    yield os.path.join(dirpath, name)

    def test_no_emoji_anywhere_in_the_frontend(self):
        offenders = {}
        for path in self._sources():
            found = self.EMOJI.findall(_read(path))
            if found:
                offenders[os.path.relpath(path, _root)] = ''.join(sorted(set(found)))
        self.assertEqual(
            offenders, {},
            "Emoji found in the UI. Use an icon from icons.css instead:\n"
            + "\n".join(f"  {p}: {c}" for p, c in offenders.items())
        )

    def test_the_brand_mark_is_a_file_not_a_glyph(self):
        self.assertTrue(
            os.path.exists(os.path.join(_static, 'img/logo-helga.svg')),
            "the logo is referenced by design-system.css and must exist"
        )
        ds = _read(os.path.join(_static, 'css/design-system.css'))
        self.assertIn('logo-helga.svg', ds)

    def test_favicon_is_not_an_emoji_data_uri(self):
        base = _read(os.path.join(_templates, 'base.html'))
        icon_lines = [l for l in base.splitlines() if 'rel="icon"' in l]
        self.assertTrue(icon_lines)
        for line in icon_lines:
            self.assertNotIn('<text', line,
                             "an emoji favicon is drawn by the OS, so the tab "
                             "icon differed per platform")


class TestIconSystemIsUsable(unittest.TestCase):

    def test_every_icon_class_has_a_mask(self):
        css = _read(os.path.join(_static, 'css/icons.css'))
        classes = set(re.findall(r'\.i-([a-z-]+)\s*\{[^}]*mask-image', css))
        declared = set(re.findall(r'--ic-([a-z-]+)\s*:', css))
        missing = sorted(c for c in classes if c not in declared)
        self.assertEqual(missing, [],
                         f"icon classes with no --ic-* definition: {missing}")

    def test_icons_inherit_currentcolor(self):
        css = _read(os.path.join(_static, 'css/icons.css'))
        self.assertIn('background-color: currentColor', css,
                      "an icon that cannot take the theme's colour is an emoji "
                      "with extra steps")

    def test_no_markup_is_written_through_textcontent(self):
        """Replacing an emoji with a <span> inside a `.textContent = '...'`
        assignment prints the tags literally. That happened four times during
        the migration and is invisible until you look at the page."""
        offenders = []
        for sub in ('templates', 'static/js'):
            base = os.path.join(_root, 'services/web-ui', sub)
            for dirpath, _, names in os.walk(base):
                for name in names:
                    if not name.endswith(('.html', '.js')) or 'min.js' in name:
                        continue
                    path = os.path.join(dirpath, name)
                    for i, line in enumerate(_read(path).splitlines(), 1):
                        if re.search(r'textContent\s*=\s*[\'"`]\s*<', line):
                            offenders.append(f"{os.path.relpath(path, _root)}:{i}")
        self.assertEqual(offenders, [], f"markup assigned to textContent: {offenders}")


class TestReferencedAssetsExist(unittest.TestCase):
    """Every /static/ asset the UI points at must be on disk.

    helga-avatar.svg and user-avatar.svg were referenced from eight places in
    session.js and settings.html and did not exist, so every message in a
    learning session and the Settings profile preview rendered a broken image.
    session.js even had an onerror fallback pointing at the same missing file.
    Nothing failed loudly; it just looked broken.
    """

    REF = re.compile(r'["\'(]/static/(img|fonts)/([\w.\-/]+)')

    def _files(self):
        for sub in ('templates', 'static/js', 'static/css'):
            base = os.path.join(_root, 'services/web-ui', sub)
            for dirpath, _, names in os.walk(base):
                for name in names:
                    if name.endswith(('.html', '.js', '.css')) and 'min.js' not in name:
                        yield os.path.join(dirpath, name)

    def test_every_referenced_image_and_font_exists(self):
        missing = {}
        for path in self._files():
            for kind, ref in self.REF.findall(_read(path)):
                # skip template expressions and obvious placeholders
                if '{' in ref or '$' in ref:
                    continue
                target = os.path.join(_static, kind, ref)
                if not os.path.exists(target):
                    missing.setdefault(f"{kind}/{ref}", []).append(
                        os.path.relpath(path, _root))
        self.assertEqual(
            missing, {},
            "Referenced but not on disk — these render as broken images:\n"
            + "\n".join(f"  {a} <- {', '.join(sorted(set(u)))}"
                        for a, u in missing.items())
        )


class TestNavigationBar(unittest.TestCase):
    """The header is three zones: brand | navigation | utilities.

    The search and theme buttons used to sit BETWEEN the wordmark and the nav,
    stranded mid-bar with ~700px of empty space beside them, so the utilities
    read as part of the navigation. They are now a `.header-utils` cluster at
    the far edge.
    """

    @classmethod
    def setUpClass(cls):
        cls.base = _read(os.path.join(_templates, 'base.html'))
        cls.ds = _read(os.path.join(_static, 'css/design-system.css'))

    def test_dom_order_is_brand_then_nav_then_utilities(self):
        """Laying this out with CSS `order` would look right and tab in a
        nonsense sequence — worse than the problem it solves. The DOM order is
        the accessible order, so it has to be correct here."""
        logo = self.base.index('<div class="logo">')
        nav = self.base.index('<nav class="app-nav"')
        utils = self.base.index('<div class="header-utils">')
        self.assertLess(logo, nav, "the wordmark must precede the navigation")
        self.assertLess(nav, utils, "utilities must come after the navigation")

    def test_search_is_injected_into_the_utilities_cluster(self):
        js = _read(os.path.join(_static, 'js/search.js'))
        self.assertIn(".querySelector('.header-utils')", js,
                      "search.js appended the widget loose in the header, "
                      "which is how it ended up mid-bar")

    def test_utilities_are_not_bordered_boxes(self):
        """Two outlined squares beside six plain text links made the utilities
        the heaviest thing in the bar."""
        block = self.ds[self.ds.index('--- Utilities'):]
        self.assertIn('border: none', block[:900])
        self.assertIn('.header-utils', block[:400])

    def test_mobile_nav_collapses_behind_the_hamburger(self):
        """REGRESSION GUARD. design-system.css loads after style.css, so an
        unconditional `.app-nav { display: flex }` here silently defeated the
        mobile `display: none` — on a phone BOTH the hamburger and the full nav
        rendered. A more specific selector (`.app-header > nav.app-nav`) beat
        the media query outright, which is the subtler version of the same
        mistake."""
        mobile = re.search(r'@media \(max-width: 768px\) \{(.*?)\n\}',
                           self.ds, re.S)
        self.assertIsNotNone(mobile, "no 768px block re-asserting the collapse")
        self.assertIn('display: none', mobile.group(1))
        self.assertIn('.app-nav.nav-open', mobile.group(1))

    def test_no_selector_outranks_the_mobile_collapse(self):
        """Any `.app-nav` rule with a more specific selector than `.app-nav`
        will win over the media query no matter where it sits in the file."""
        offenders = [
            line.strip() for line in self.ds.splitlines()
            if re.search(r'^[^@/]*\b\w+\.app-nav\b|\.app-header\s*>\s*nav', line)
            and 'display' in line
        ]
        self.assertEqual(
            offenders, [],
            f"these outrank the mobile collapse rule: {offenders}")
