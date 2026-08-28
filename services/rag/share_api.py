# REGISTER WITH (librarian.py, after `storage` is constructed):
#   from services.rag.share_api import create_share_blueprint; app.register_blueprint(create_share_blueprint(storage))
"""Course export/import — "build a course once, hand the file to a friend."

A course is already almost a self-contained directory, but only almost: since
schema v12/v15/v16 the parts a learner actually trusts — concept bodies, spoken
math, the retained sources behind the trust panel, and licensed assets — live
in SQLite beside the files. A bundle that copied only the directory would open
on the other machine as a course with no trust panel, no images and no spoken
formulas, and nothing would say why. So the bundle carries both stores.

THE BUNDLE (format "helga-course-bundle", version 1)
----------------------------------------------------
A plain zip, because a learner should be able to open it and see their course:

  manifest.json               format/version, source app version, course uid +
                              title, created_at, row counts, file inventory
  course/structure.json       the course tree (completion flags scrubbed)
  course/content/<uid>.md     concept bodies, human-readable
  course/...                  anything else the build left in the course dir
  db/concepts.json            `concepts` rows (content + hash; `path` dropped —
                              it is machine-local and recomputed on import)
  db/concept_math.json        LaTeX → speech spans
  db/sources.json             retained source passages (trust panel)
  db/claim_sources.json       which claims rest on which sources
  db/assets.json              asset metadata (licence, provenance, captions)
  db/concept_assets.json      concept → asset links with their roles
  assets/<sha256>             asset bytes, keyed by content hash

WHAT IS DELIBERATELY NOT IN IT
------------------------------
Learner state: user_progress, flashcards, scheduled_reviews, activity_log,
session_notes, interactions. A shared course must arrive fresh — the friend's
FSRS clock starts at zero, not at the sender's. The `completed` flags inside
structure.json are scrubbed at export for the same reason.

IMPORT DISCIPLINE
-----------------
Validate EVERYTHING before touching either store, in a temp dir: a malformed
bundle is rejected with a NAMED reason and leaves zero residue on disk or in
SQLite. Zip entries are checked for traversal (absolute paths, `..`, drive
letters) and the bundle is capped in file count and unpacked size, because a
bundle is by definition a file from someone else's machine. Only then does the
write begin, and it goes through storage.create_course, which already writes
disk-first / SQLite-second (AUTO-10) and rolls back the directory if the row
fails. A failure after that point purges every row and file this import wrote.

A uid collision is not an error: the incoming course gets a fresh uid and every
internal reference is rewritten, so importing a course you already have gives
you a second, independent copy.
"""

import io
import json
import logging
import os
import posixpath
import re
import shutil
import sqlite3
import tempfile
import uuid
import zipfile
from datetime import datetime

logger = logging.getLogger(__name__)

FORMAT_NAME = "helga-course-bundle"
FORMAT_VERSION = 1
SUPPORTED_VERSIONS = (1,)

# Caps for a file that arrives from someone else's machine. A bachelor's worth
# of content is ~32 MB (storage.py's own estimate) and assets are capped per
# course well below this, so these are generous for any real course while
# still bounding what a hostile zip can make us hold.
MAX_BUNDLE_BYTES = 256 * 1024 * 1024      # compressed upload
MAX_UNPACKED_BYTES = 512 * 1024 * 1024    # sum of declared uncompressed sizes
MAX_BUNDLE_FILES = 4000

# Files that must never travel even if something left them in the course dir.
_EXCLUDE_FILES = {"user_state.json"}

# Every entry must live under one of these roots; anything else is a bundle we
# did not write and will not unpack.
_ALLOWED_ROOTS = ("manifest.json", "course/", "db/", "assets/")

_DB_TABLES = ("concepts", "concept_math", "sources", "claim_sources",
              "assets", "concept_assets")


class BundleError(Exception):
    """A rejected bundle, with the reason NAMED so the caller can act on it."""

    def __init__(self, reason: str, detail: str = "", status: int = 400):
        self.reason = reason
        self.detail = detail
        self.status = status
        super().__init__(f"{reason}: {detail}" if detail else reason)


# --------------------------------------------------------------------------
# Export
# --------------------------------------------------------------------------

def _scrub_learner_keys(node):
    """Drop per-learner keys from a structure tree, recursively.

    The RAG merges completion from SQLite at read time, but older builds wrote
    `completed` straight into structure.json. Either way it is the SENDER's
    progress and must not arrive looking like the recipient's.
    """
    if isinstance(node, dict):
        return {k: _scrub_learner_keys(v) for k, v in node.items()
                if k != "completed"}
    if isinstance(node, list):
        return [_scrub_learner_keys(v) for v in node]
    return node


def _rows(db, sql, params):
    """Rows as dicts; an OperationalError means the table predates the schema
    that holds this data, which is an empty export, not a failed one."""
    try:
        cur = db.execute(sql, params)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    except sqlite3.OperationalError:
        return []


def build_bundle(storage, uid: str, out_fp) -> dict:
    """Write the course `uid` as a bundle zip into the open file `out_fp`.

    Returns the manifest. Raises BundleError("course_not_found") if the course
    has no structure.json — the row alone is derived metadata, not a course.
    """
    course = storage.courses.get_course(uid)
    if not course:
        raise BundleError("course_not_found",
                          f"no structure.json for course {uid}", status=404)

    db = storage.courses._get_db()
    course_dir = os.path.join(storage.courses.courses_dir, uid)

    # `path` is dropped from concepts and assets rows: it names a location on
    # the EXPORTING machine and would only ever be wrong on the importing one.
    tables = {
        "concepts": _rows(db,
            "SELECT concept_uid, title, content, content_hash, words, "
            "updated_at FROM concepts WHERE course_uid=?", (uid,)),
        "concept_math": _rows(db,
            "SELECT concept_uid, latex, mathml, speech, unspoken, ordinal "
            "FROM concept_math WHERE course_uid=?", (uid,)),
        "sources": _rows(db,
            "SELECT source_id, concept_uid, title, url, passage, source_type, "
            "domain_tier, grounding, degraded, retrieved_at "
            "FROM sources WHERE course_uid=?", (uid,)),
        "claim_sources": _rows(db,
            "SELECT concept_uid, claim, source_id, supplementary "
            "FROM claim_sources WHERE course_uid=?", (uid,)),
        "concept_assets": _rows(db,
            "SELECT concept_uid, asset_id, role FROM concept_assets "
            "WHERE course_uid=?", (uid,)),
    }
    asset_ids = sorted({r["asset_id"] for r in tables["concept_assets"]})
    asset_rows, asset_blobs = [], {}
    for aid in asset_ids:
        rows = _rows(db,
            "SELECT asset_id, sha256, bytes, path, mime, width, height, "
            "source, license, provenance_url, alt_text, caption, "
            "caption_verified FROM assets WHERE asset_id=?", (aid,))
        if not rows:
            continue
        row = rows[0]
        blob = row.pop("bytes", None)
        path = row.pop("path", None)
        if blob is None and path and os.path.isfile(path):
            # Large assets spill to disk with `path` set; read them back so
            # the bundle is complete without depending on this filesystem.
            with open(path, "rb") as f:
                blob = f.read()
        if blob is not None and row.get("sha256"):
            asset_blobs[row["sha256"]] = blob
        else:
            # An asset whose bytes cannot be found is exported as metadata
            # with the loss NAMED, not silently thinned out of the course.
            row["missing_bytes"] = True
        asset_rows.append(row)
    tables["assets"] = asset_rows

    inventory = []
    zf = zipfile.ZipFile(out_fp, "w", zipfile.ZIP_DEFLATED)

    def _write(arcname, data):
        zf.writestr(arcname, data)
        inventory.append({"path": arcname,
                          "bytes": len(data if isinstance(data, bytes)
                                       else data.encode("utf-8"))})

    _write("course/structure.json",
           json.dumps(_scrub_learner_keys(course), indent=2))

    # Everything else the build left in the course directory (content/*.md,
    # figures, ...) travels verbatim. Symlinks do not: a link's target is a
    # statement about THIS machine.
    for root, _dirs, files in os.walk(course_dir):
        for name in files:
            full = os.path.join(root, name)
            rel = os.path.relpath(full, course_dir)
            if name in _EXCLUDE_FILES or rel == "structure.json":
                continue
            if os.path.islink(full):
                continue
            with open(full, "rb") as f:
                _write("course/" + rel.replace(os.sep, "/"), f.read())

    for table in _DB_TABLES:
        _write(f"db/{table}.json", json.dumps(tables[table], indent=2))
    for sha, blob in asset_blobs.items():
        _write(f"assets/{sha}", blob)

    manifest = {
        "format": FORMAT_NAME,
        "format_version": FORMAT_VERSION,
        "app_version": os.getenv("HELGA_VERSION", "dev"),
        "course_uid": uid,
        "course_title": course.get("title", ""),
        "course_created_at": course.get("created_at", ""),
        "created_at": datetime.now().isoformat(),
        "counts": {
            "concepts": len(tables["concepts"]),
            "math_spans": len(tables["concept_math"]),
            "sources": len(tables["sources"]),
            "claims": len(tables["claim_sources"]),
            "assets": len(tables["assets"]),
            "attachments": len(tables["concept_assets"]),
        },
        "files": inventory,
    }
    # Written last, so the inventory describes the finished zip rather than an
    # intention about it.
    zf.writestr("manifest.json", json.dumps(manifest, indent=2))
    zf.close()
    return manifest


# --------------------------------------------------------------------------
# Import — validation
# --------------------------------------------------------------------------

_DRIVE_RE = re.compile(r"^[A-Za-z]:")


def _safe_entry_name(name: str) -> bool:
    """True only for a relative, forward-slash path with no escape hatches."""
    if not name or "\\" in name or "\x00" in name:
        return False
    if name.startswith("/") or _DRIVE_RE.match(name):
        return False
    parts = posixpath.normpath(name).split("/")
    return ".." not in parts and parts[0] not in ("", ".")


def _load_json_entry(zf, name, reason):
    try:
        with zf.open(name) as f:
            return json.load(io.TextIOWrapper(f, encoding="utf-8"))
    except KeyError:
        raise BundleError(reason.replace("invalid", "missing"),
                          f"bundle has no {name}")
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise BundleError(reason, f"{name} does not parse: {e}")


def _validate_bundle(zf: zipfile.ZipFile):
    """Every check that can reject the bundle runs HERE, before any write.

    Returns (manifest, structure, db_tables). Raises BundleError with a named
    reason otherwise; at this point nothing outside the temp file exists, so
    rejection is residue-free by construction.
    """
    infos = zf.infolist()
    if len(infos) > MAX_BUNDLE_FILES:
        raise BundleError("too_many_files",
                          f"{len(infos)} entries, cap is {MAX_BUNDLE_FILES}")

    unpacked = 0
    for info in infos:
        name = info.filename
        if name.endswith("/"):        # directory entries carry no bytes
            continue
        if not _safe_entry_name(name):
            raise BundleError("path_traversal",
                              f"unsafe entry name {name!r}")
        if not name.startswith(_ALLOWED_ROOTS):
            raise BundleError("unexpected_entry",
                              f"{name!r} is outside the bundle layout")
        unpacked += info.file_size
        # A tiny compressed entry declaring a huge uncompressed size is a zip
        # bomb, not a course.
        if (info.compress_size and info.file_size > 1024 * 1024
                and info.file_size / info.compress_size > 1000):
            raise BundleError("zip_bomb",
                              f"{name!r} expands {info.file_size // max(info.compress_size, 1)}x")
    if unpacked > MAX_UNPACKED_BYTES:
        raise BundleError("unpacked_too_large",
                          f"{unpacked} bytes unpacked, cap is {MAX_UNPACKED_BYTES}")

    manifest = _load_json_entry(zf, "manifest.json", "manifest_invalid")
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT_NAME:
        raise BundleError("manifest_invalid",
                          "manifest is not a helga-course-bundle manifest")
    version = manifest.get("format_version")
    if version not in SUPPORTED_VERSIONS:
        raise BundleError("unsupported_format_version",
                          f"bundle is version {version!r}; this build reads "
                          f"{list(SUPPORTED_VERSIONS)}")
    if not manifest.get("course_uid"):
        raise BundleError("manifest_invalid", "manifest names no course_uid")

    structure = _load_json_entry(zf, "course/structure.json",
                                 "structure_invalid")
    if not isinstance(structure, dict) or not structure.get("uid") \
            or not structure.get("title"):
        raise BundleError("structure_invalid",
                          "structure.json lacks a uid or title")
    if structure["uid"] != manifest["course_uid"]:
        raise BundleError("manifest_mismatch",
                          f"manifest says {manifest['course_uid']}, "
                          f"structure.json says {structure['uid']}")

    db_tables = {}
    for table in _DB_TABLES:
        name = f"db/{table}.json"
        if name not in zf.namelist():
            db_tables[table] = []       # pre-v16 exporter or minimal bundle
            continue
        rows = _load_json_entry(zf, name, "db_rows_invalid")
        if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
            raise BundleError("db_rows_invalid", f"{name} is not a row list")
        db_tables[table] = rows
    return manifest, structure, db_tables


# --------------------------------------------------------------------------
# Import — write, and the rollback that makes failure residue-free
# --------------------------------------------------------------------------

def _purge_course_rows(storage, uid: str, created_asset_ids):
    """Remove every SQLite row an import wrote for `uid`.

    delete_course's cascade list predates the v12/v15/v16 tables, so the newer
    ones are purged here explicitly. Assets are shared rows deduped by sha256:
    only the ones THIS import inserted are deleted, and only if nothing else
    has since attached them.
    """
    db = storage.courses._get_db()
    for table in ("concepts", "concepts_fts", "concept_math", "sources",
                  "claim_sources", "concept_assets", "taught_concepts",
                  "taught_claims"):
        try:
            db.execute(f"DELETE FROM {table} WHERE course_uid=?", (uid,))
        except sqlite3.OperationalError:
            pass
    for aid in created_asset_ids:
        try:
            still_used = db.execute(
                "SELECT 1 FROM concept_assets WHERE asset_id=? LIMIT 1",
                (aid,)).fetchone()
            if not still_used:
                db.execute("DELETE FROM assets WHERE asset_id=?", (aid,))
        except sqlite3.OperationalError:
            pass
    db.commit()


def import_bundle(storage, bundle_path: str) -> dict:
    """Validate, then install, a bundle. Returns the import report.

    Raises BundleError; on any raise, disk and SQLite are exactly as they were
    before the call.
    """
    if not zipfile.is_zipfile(bundle_path):
        raise BundleError("not_a_zip", "upload is not a zip archive")
    try:
        zf = zipfile.ZipFile(bundle_path)
    except zipfile.BadZipFile as e:
        raise BundleError("not_a_zip", str(e))

    with zf:
        manifest, structure, db_tables = _validate_bundle(zf)

        old_uid = structure["uid"]
        # Collision policy: never overwrite. The local course and the incoming
        # one may have diverged, and "import" must not become "silently
        # replace". A fresh uid plus reference rewrite gives an independent
        # copy instead.
        collides = (storage.courses.get_course(old_uid) is not None
                    or os.path.exists(os.path.join(
                        storage.courses.courses_dir, old_uid)))
        new_uid = f"course_{uuid.uuid4().hex[:8]}" if collides else old_uid
        if new_uid != old_uid:
            # The uid's `course_` + hex shape makes a plain string substitution
            # precise: nothing else in a structure can contain it by accident.
            structure = json.loads(
                json.dumps(structure).replace(old_uid, new_uid))
        structure["uid"] = new_uid

        # THE UID WAS DISAMBIGUATED AND THE TITLE WAS NOT.
        #
        # Collision policy above deliberately never overwrites: an import of a
        # course you already have becomes an independent copy under a fresh
        # uid. But the learner never sees a uid. Importing a bundle exported
        # from this same machine produced two cards reading "Practical Regular
        # Expressions", identical in title, subtitle and module counts, with
        # nothing on either to say which was which or which had just arrived.
        # The response even said renamed:true -- true of the uid, invisible in
        # the UI.
        #
        # Give the copy a distinguishable title, counting up if that is taken
        # too, so importing the same bundle twice does not produce two
        # "(imported)" twins.
        if collides:
            _base = (structure.get("title") or "Untitled course").strip()
            _existing = {(c.get("title") or "").strip()
                         for c in (storage.courses.list_courses() or [])}
            _candidate = f"{_base} (imported)"
            _n = 2
            while _candidate in _existing:
                _candidate = f"{_base} (imported {_n})"
                _n += 1
            structure["title"] = _candidate

        # Provenance, so a support question about an imported course can be
        # answered from the course itself.
        structure["share"] = {
            "original_uid": old_uid,
            "source_app_version": manifest.get("app_version", "unknown"),
            "imported_at": datetime.now().isoformat(),
        }
        # A bundle is by definition a finished course; arriving with no status
        # would default to "skeleton" and render as "Building..." forever.
        structure.setdefault("status", "ready")

        # Stage the payload files in a temp dir first, so the extraction
        # itself cannot half-write into the live course directory.
        staging = tempfile.mkdtemp(prefix="helga_import_")
        warnings = []
        created_course = False
        created_asset_ids = []
        try:
            for info in zf.infolist():
                name = info.filename
                if name.endswith("/") or not name.startswith("course/"):
                    continue
                rel = name[len("course/"):]
                if rel == "structure.json" or posixpath.basename(rel) in _EXCLUDE_FILES:
                    continue
                dest = os.path.join(staging, *rel.split("/"))
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with zf.open(info) as src, open(dest, "wb") as out:
                    shutil.copyfileobj(src, out)

            # DISK-FIRST, SQLITE-SECOND (AUTO-10): create_course writes
            # structure.json, then the courses row, and rolls the directory
            # back itself if the row fails — so a failure HERE is already
            # residue-free.
            storage.courses.create_course(structure)
            created_course = True
            course_dir = os.path.join(storage.courses.courses_dir, new_uid)

            for root, _dirs, files in os.walk(staging):
                for fname in files:
                    src = os.path.join(root, fname)
                    rel = os.path.relpath(src, staging)
                    dest = os.path.join(course_dir, rel)
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    shutil.copy2(src, dest)

            report = _install_db_rows(storage, new_uid, db_tables, zf,
                                      created_asset_ids, warnings)
        except BundleError:
            if created_course:
                _rollback(storage, new_uid, created_asset_ids)
            raise
        except Exception as e:
            if created_course:
                _rollback(storage, new_uid, created_asset_ids)
            logger.exception(f"import of {old_uid} failed mid-write")
            raise BundleError("import_write_failed", str(e), status=500)
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    return {
        "ok": True,
        "course_uid": new_uid,
        "original_uid": old_uid,
        "renamed": new_uid != old_uid,
        "title": structure.get("title", ""),
        "imported": report,
        "warnings": warnings,
    }


def _rollback(storage, uid: str, created_asset_ids):
    """Zero residue: rows first (needs the asset reference check), then the
    directory + courses row via delete_course's own cascade."""
    try:
        _purge_course_rows(storage, uid, created_asset_ids)
        storage.courses.delete_course(uid)
        try:
            storage.search.drop_course(uid)
        except Exception:
            pass
        logger.error(f"import rolled back: course {uid} removed from both stores")
    except Exception as rb_err:
        # The one path that can leave residue, so it is NAMED with the repair
        # command rather than swallowed.
        logger.error(
            f"IMPORT ROLLBACK FAILED for course {uid}: {rb_err}. Repair with: "
            f"python3 tools/reconcile_courses.py {storage.data_dir} --fix")


def _install_db_rows(storage, uid, db_tables, zf, created_asset_ids, warnings):
    """Insert the course's SQLite rows under its (possibly new) uid."""
    cs = storage.courses
    db = cs._get_db()

    # Concept bodies go through save_concept_content so the .md mirror, the
    # `concepts` row, FTS and the search index all stay consistent — the same
    # single path every hydrator write uses. Bundle rows are the primary
    # source; content/*.md files cover bundles from a pre-v15 database. A
    # concept in neither stays ABSENT (never attempted), never becomes a fake
    # empty row.
    by_uid = {r.get("concept_uid"): r for r in db_tables["concepts"]
              if r.get("concept_uid")}
    content_dir = os.path.join(cs.courses_dir, uid, "content")
    seen = set()
    written = 0
    for concept in cs.get_flat_concepts(uid):
        cuid = concept["uid"]
        seen.add(cuid)
        row = by_uid.get(cuid)
        content = row.get("content") if row else None
        if content is None:
            md_path = os.path.join(content_dir, f"{cuid}.md")
            if os.path.isfile(md_path):
                with open(md_path, "r") as f:
                    content = f.read()
        if content is None:
            continue
        cs.save_concept_content(uid, cuid, content)
        written += 1
    for cuid, row in by_uid.items():
        # Rows for concepts the structure no longer names are still course
        # data (the exporter saw them); keep them rather than judging them.
        if cuid not in seen and row.get("content") is not None:
            cs.save_concept_content(uid, cuid, row["content"])
            written += 1

    # concept_math directly rather than via save_concept_math: the helper
    # discards `mathml`, and an importer's job is fidelity, not opinion.
    for r in db_tables["concept_math"]:
        db.execute(
            "INSERT INTO concept_math (course_uid, concept_uid, latex, "
            "mathml, speech, unspoken, ordinal) VALUES (?,?,?,?,?,?,?)",
            (uid, r.get("concept_uid"), r.get("latex"), r.get("mathml"),
             r.get("speech"), r.get("unspoken"), r.get("ordinal")))

    # Sources get fresh AUTOINCREMENT ids here; the old→new map keeps the
    # claim links pointing at the same passages they pointed at on export.
    source_map = {}
    for r in db_tables["sources"]:
        cur = db.execute(
            "INSERT INTO sources (course_uid, concept_uid, title, url, "
            "passage, source_type, domain_tier, grounding, degraded, "
            "retrieved_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (uid, r.get("concept_uid"), r.get("title"), r.get("url"),
             r.get("passage"), r.get("source_type"), r.get("domain_tier"),
             r.get("grounding"), r.get("degraded", 0), r.get("retrieved_at")))
        if r.get("source_id") is not None:
            source_map[r["source_id"]] = cur.lastrowid
    for r in db_tables["claim_sources"]:
        db.execute(
            "INSERT INTO claim_sources (course_uid, concept_uid, claim, "
            "source_id, supplementary) VALUES (?,?,?,?,?)",
            (uid, r.get("concept_uid"), r.get("claim"),
             source_map.get(r.get("source_id")), r.get("supplementary", 0)))
    db.commit()

    # Assets go through save_asset so the two policies that make the asset
    # store trustworthy — dedup by sha256 and fail-closed licensing — apply to
    # imported bytes exactly as they apply to fetched ones. A refused asset is
    # a WARNING with its sha, never a silent thinning of the course.
    existing = {row[0] for row in db.execute(
        "SELECT asset_id FROM assets").fetchall()}
    names = set(zf.namelist())
    asset_map = {}
    for r in db_tables["assets"]:
        sha = r.get("sha256")
        blob = None
        if sha and f"assets/{sha}" in names:
            with zf.open(f"assets/{sha}") as f:
                blob = f.read()
        new_id = cs.save_asset(
            sha, data=blob, mime=r.get("mime"), width=r.get("width"),
            height=r.get("height"), source=r.get("source"),
            license=r.get("license"), provenance_url=r.get("provenance_url"),
            alt_text=r.get("alt_text"), caption=r.get("caption"),
            caption_verified=bool(r.get("caption_verified")))
        if new_id is None:
            warnings.append(f"asset_refused_unlicensed:{sha or 'unknown'}")
            continue
        if new_id not in existing:
            created_asset_ids.append(new_id)
        if r.get("asset_id") is not None:
            asset_map[r["asset_id"]] = new_id
    attached = 0
    for r in db_tables["concept_assets"]:
        new_id = asset_map.get(r.get("asset_id"))
        if new_id is None:
            continue        # its asset was refused above, already warned
        if cs.attach_asset(uid, r.get("concept_uid"), new_id, r.get("role")):
            attached += 1
        else:
            warnings.append(f"asset_attach_refused:{r.get('role')!r}")

    return {
        "concepts": written,
        "math_spans": len(db_tables["concept_math"]),
        "sources": len(db_tables["sources"]),
        "claims": len(db_tables["claim_sources"]),
        "assets": len(asset_map),
        "attachments": attached,
    }


# --------------------------------------------------------------------------
# Blueprint
# --------------------------------------------------------------------------

def create_share_blueprint(storage):
    """Flask blueprint factory — mounted on the RAG process like the exam
    engine: shares the StorageManager, no new container. The /api/share/*
    namespace is distinct for the same reason library_api's is: registering
    this file must not be able to shadow any route already shipped."""
    from flask import Blueprint, jsonify, request, send_file

    bp = Blueprint("share", __name__)

    @bp.route("/api/share/course/<uid>/export", methods=["GET"])
    def export_course(uid):
        # Spooled: a normal course zips in memory; a huge one overflows to a
        # temp file instead of holding the request hostage in RAM.
        fp = tempfile.SpooledTemporaryFile(max_size=32 * 1024 * 1024)
        try:
            manifest = build_bundle(storage, uid, fp)
        except BundleError as e:
            fp.close()
            return jsonify({"ok": False, "error": e.reason,
                            "detail": e.detail}), e.status
        except Exception as e:
            fp.close()
            logger.exception(f"export of {uid} failed")
            return jsonify({"ok": False, "error": "export_failed",
                            "detail": str(e)}), 500
        fp.seek(0)
        slug = re.sub(r"[^A-Za-z0-9]+", "-",
                      manifest["course_title"]).strip("-").lower() or "course"
        return send_file(fp, mimetype="application/zip", as_attachment=True,
                         download_name=f"{slug}-{uid}.helga-course.zip")

    @bp.route("/api/share/course/import", methods=["POST"])
    def import_course():
        upload = request.files.get("bundle")
        if upload is None or not upload.filename:
            return jsonify({"ok": False, "error": "empty_upload",
                            "detail": "no 'bundle' file in the request"}), 400
        if request.content_length and request.content_length > MAX_BUNDLE_BYTES:
            return jsonify({"ok": False, "error": "bundle_too_large",
                            "detail": f"cap is {MAX_BUNDLE_BYTES} bytes"}), 413

        tmp = tempfile.NamedTemporaryFile(prefix="helga_bundle_",
                                          suffix=".zip", delete=False)
        try:
            # content_length is client-asserted; count the bytes we actually
            # receive as well.
            size = 0
            while True:
                chunk = upload.stream.read(1 << 20)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_BUNDLE_BYTES:
                    return jsonify({"ok": False, "error": "bundle_too_large",
                                    "detail": f"cap is {MAX_BUNDLE_BYTES} bytes"}), 413
                tmp.write(chunk)
            tmp.close()
            report = import_bundle(storage, tmp.name)
            return jsonify(report)
        except BundleError as e:
            return jsonify({"ok": False, "error": e.reason,
                            "detail": e.detail}), e.status
        except Exception as e:
            logger.exception("course import failed")
            return jsonify({"ok": False, "error": "import_failed",
                            "detail": str(e)}), 500
        finally:
            tmp.close()
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

    return bp
