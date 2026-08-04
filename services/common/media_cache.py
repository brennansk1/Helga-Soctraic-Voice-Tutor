"""Local media cache — fetch online, serve local.

This is the piece that makes "use the internet when it is there, never break
when it is not" true rather than aspirational.

An image referenced by remote URL is a lesson with an expiry date: the host
rewrites its CDN paths, the collection is reorganised, the file is deleted, and
a year later a concept has a hole in it. Worse, a remote `<img>` inside a
tutoring turn puts a network round-trip on the critical path of a session that
already costs ~30 s of inference.

So every image is downloaded ONCE at course-build time, stored under
DATA_ROOT/media, and referenced by a same-origin path. That single decision buys
four things at once:

  * offline works, because the bytes are already here
  * the dialogue turn never touches the network
  * the visual-aid `image` kind's same-origin rule is satisfied (it refuses
    remote hosts by design — see visual_aids._SAFE_SRC)
  * attribution is captured at fetch time, when the licence metadata is still
    in hand, instead of being reconstructed later from a bare URL

Content-addressed by URL hash, so the same image referenced by ten concepts is
stored once and re-downloading is a no-op.
"""

import hashlib
import json
import logging
import os
import re

logger = logging.getLogger(__name__)

MAX_BYTES = 6 * 1024 * 1024          # a teaching image is not 40 MB
FETCH_TIMEOUT = 20

# Only formats a browser renders natively and that we can identify by magic
# bytes. SVG is deliberately EXCLUDED: it is executable markup, and an SVG from
# a public archive is an XSS vector, not a picture.
_EXT_BY_MAGIC = (
    (b"\xff\xd8\xff", "jpg"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"GIF87a", "gif"), (b"GIF89a", "gif"),
    (b"RIFF", "webp"),               # RIFF....WEBP
)

_SAFE_NAME = re.compile(r"^[a-f0-9]{16}\.(jpg|png|gif|webp)$")


def media_root():
    root = os.environ.get("DATA_ROOT", "data")
    return os.path.join(root, "media")


def _sniff_ext(head):
    for magic, ext in _EXT_BY_MAGIC:
        if head.startswith(magic):
            if ext == "webp" and b"WEBP" not in head[:16]:
                continue
            return ext
    return None


def safe_media_name(name):
    """Validate a cached filename before it is used in a path. The web route
    that serves these takes the name from a URL, so this is the boundary that
    stops `../../helga.db` — a whitelist of exactly what we ourselves write."""
    return bool(_SAFE_NAME.match(str(name or "")))


def cache_image(url, meta=None, timeout=FETCH_TIMEOUT):
    """Download one image into the local cache.

    Returns a record with the same-origin `src` an `image` aid can use, or None
    if the fetch failed, the file was too large, or the bytes were not actually
    an image. Never raises: a missing picture must degrade a concept, not fail
    a course build.
    """
    if not url or not str(url).lower().startswith(("http://", "https://")):
        return None
    key = hashlib.sha1(str(url).encode("utf-8")).hexdigest()[:16]
    root = media_root()
    try:
        os.makedirs(root, exist_ok=True)
    except OSError as e:
        logger.warning("media cache unavailable (%s); skipping image", e)
        return None

    # Already have it? Content-addressed, so identity is enough.
    for ext in ("jpg", "png", "gif", "webp"):
        path = os.path.join(root, f"{key}.{ext}")
        if os.path.exists(path):
            return _record(key, ext, url, meta, os.path.getsize(path), cached=True)

    try:
        import requests
        resp = requests.get(str(url), timeout=timeout, stream=True,
                            headers={"User-Agent": "Helga-Tutor/1.0 (educational use)"})
        resp.raise_for_status()

        declared = int(resp.headers.get("Content-Length") or 0)
        if declared > MAX_BYTES:
            logger.info("image too large (%d bytes), skipping: %s", declared, url)
            return None

        chunks, total = [], 0
        for chunk in resp.iter_content(65536):
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_BYTES:        # servers lie about Content-Length
                logger.info("image exceeded %d bytes mid-stream, skipping: %s",
                            MAX_BYTES, url)
                return None
        body = b"".join(chunks)
    except Exception as e:
        logger.warning("image fetch failed (%s): %s", e, url)
        return None

    ext = _sniff_ext(body[:16])
    if not ext:
        # Trust the bytes, not the Content-Type header or the file extension.
        logger.info("not a recognised image format, skipping: %s", url)
        return None

    path = os.path.join(root, f"{key}.{ext}")
    try:
        tmp = path + ".part"
        with open(tmp, "wb") as fh:
            fh.write(body)
        os.replace(tmp, path)            # atomic: never a half-written image
    except OSError as e:
        logger.warning("could not write media cache entry: %s", e)
        return None

    record = _record(key, ext, url, meta, len(body))
    _write_sidecar(root, key, record)
    logger.info("cached image %s.%s (%d KB) from %s",
                key, ext, len(body) // 1024, (meta or {}).get("source", "?"))
    return record


def _record(key, ext, url, meta, size, cached=False):
    meta = meta or {}
    return {
        "name": f"{key}.{ext}",
        # Same-origin, and matches the `/api/` prefix visual_aids._SAFE_SRC
        # permits — a remote URL would be refused there by design.
        "src": f"/api/media/{key}.{ext}",
        "bytes": size,
        "origin_url": url,
        "source": meta.get("source", ""),
        "license": meta.get("license", ""),
        "author": meta.get("author", ""),
        "page": meta.get("url", ""),
        "title": meta.get("title", ""),
        "already_cached": cached,
    }


def _write_sidecar(root, key, record):
    """Attribution lives beside the bytes. Captured now, while the licence
    metadata is in hand — reconstructing it later from a bare URL is guesswork,
    and guessed attribution is worse than none."""
    try:
        with open(os.path.join(root, f"{key}.json"), "w") as fh:
            json.dump(record, fh, indent=2)
    except OSError as e:
        logger.debug("could not write attribution sidecar: %s", e)


def attribution(name):
    """Attribution record for a cached file, or None."""
    if not safe_media_name(name):
        return None
    key = str(name).split(".")[0]
    try:
        with open(os.path.join(media_root(), f"{key}.json")) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def cache_stats():
    root = media_root()
    if not os.path.isdir(root):
        return {"files": 0, "bytes": 0, "root": root}
    files = [f for f in os.listdir(root) if safe_media_name(f)]
    total = 0
    for f in files:
        try:
            total += os.path.getsize(os.path.join(root, f))
        except OSError:
            pass
    return {"files": len(files), "bytes": total, "root": root}
