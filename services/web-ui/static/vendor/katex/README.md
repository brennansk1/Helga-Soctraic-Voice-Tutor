# KaTeX (vendored, offline)

The Learn chat renders LaTeX math with **KaTeX**, loaded from this directory so it
works fully offline (no CDN). `learn.html` references:

- `static/vendor/katex/katex.min.css`
- `static/vendor/katex/katex.min.js`
- `static/vendor/katex/fonts/…` (referenced by the CSS)

`renderMarkdown()` in `session.js` guards on `window.katex`, so **until these files
are present the chat gracefully falls back to showing the raw TeX** (readable, just
unrendered). Nothing breaks if KaTeX is missing.

## Install (one-time, on a machine with network access)

KaTeX is MIT-licensed. Drop the release here, e.g.:

```bash
# pick a pinned version
VER=0.16.11
cd services/web-ui/static/vendor/katex
curl -L -o katex.tar.gz "https://github.com/KaTeX/KaTeX/releases/download/v$VER/katex.tar.gz"
tar xzf katex.tar.gz --strip-components=1 katex/katex.min.css katex/katex.min.js katex/fonts
rm katex.tar.gz
```

Result layout:
```
static/vendor/katex/
  katex.min.css
  katex.min.js
  fonts/...
  README.md   (this file)
```

## Supported syntax in chat
- Block: `$$ … $$` and `\[ … \]`
- Inline: `\( … \)` and `$ … $` (the `$…$` form only triggers when the content
  looks like LaTeX — contains `\ ^ _ { }` — so plain currency like `$5` is left alone).

Rendering uses `throwOnError:false` and `trust:false` (no raw-HTML injection from model output).
