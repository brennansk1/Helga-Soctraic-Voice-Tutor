"""Standards seed loader (B16.1, design spec 04 §1.3).

Idempotently upserts every `data/standards/*.yaml|*.json` file into the
`standards` table. YAML for human-edited seeds (multi-line standard text),
JSON with the identical shape for programmatic generation. Admin job — never
in the student request path.

Seed file shape:
    subject: math
    source: USBE
    adopted_year: 2016
    standards:
      - code: 6.RP
        grade_band: 6-8
        grade_numeric: 6
        strand: Ratios & Proportional Relationships
        text: Understand ratio concepts and use ratio reasoning...
        is_enrichment: 0        # optional, ★ supplementary marks
"""

import glob
import json
import logging
import os

logger = logging.getLogger(__name__)

try:
    import yaml
    _HAVE_YAML = callable(getattr(yaml, "safe_load", None))
except ImportError:
    yaml = None
    _HAVE_YAML = False

# Bands plus HS course codes (spec 04 §1.1: grade_band is a band OR a course
# code like 'BIO', 'GFL', 'USG', 'SII'; world-lang proficiency codes use null).
_BANDS = {"K-1", "2-3", "4-5", "6-8", "9-12",
          "K-2", "3-5"}          # legacy names still present in seed data


def _parse_seed(path):
    with open(path, "r") as f:
        raw = f.read()
    if path.endswith(".json"):
        return json.loads(raw)
    if not _HAVE_YAML:
        raise RuntimeError(f"PyYAML unavailable — cannot parse {path}")
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: seed must parse to a mapping")
    return data


def load_standards_dir(storage, path="data/standards", prune=False):
    """Upsert every seed file under `path`. Returns
    {inserted, updated, pruned, blocked, errors[]}.

    prune=True deletes DB codes absent from every seed file (USBE retirement)
    but REFUSES to prune codes with concept_standards rows (would orphan
    published content) — those are counted in `blocked`.
    """
    result = {"inserted": 0, "updated": 0, "pruned": 0, "blocked": 0, "errors": []}
    seed_codes = set()
    files = sorted(glob.glob(os.path.join(path, "*.yaml"))
                   + glob.glob(os.path.join(path, "*.yml"))
                   + glob.glob(os.path.join(path, "*.json")))
    if not files:
        result["errors"].append(f"no seed files under {path}")
        return result

    for fp in files:
        try:
            data = _parse_seed(fp)
        except Exception as e:
            result["errors"].append(f"{fp}: parse failed: {e}")
            continue

        subject = data.get("subject")
        if subject not in storage.standards.SUBJECTS:
            result["errors"].append(f"{fp}: unknown subject {subject!r}")
            continue
        default_source = data.get("source", "USBE")
        default_year = data.get("adopted_year")

        file_codes = set()
        for i, row in enumerate(data.get("standards") or []):
            code = (row.get("code") or "").strip()
            strand = (row.get("strand") or "").strip()
            text = (row.get("text") or "").strip()
            band = row.get("grade_band")
            if not code or not strand or not text:
                result["errors"].append(f"{fp}[{i}]: code/strand/text required")
                continue
            if code in file_codes:
                result["errors"].append(f"{fp}[{i}]: duplicate code {code}")
                continue
            if band is not None and band not in _BANDS and not str(band).isupper():
                # bands or ALL-CAPS HS course codes; anything else is a typo
                result["errors"].append(f"{fp}[{i}]: bad grade_band {band!r}")
                continue
            file_codes.add(code)
            try:
                inserted = storage.standards.upsert(
                    code=code, subject=subject, strand=strand, text=text,
                    grade_band=band, grade_numeric=row.get("grade_numeric"),
                    is_enrichment=bool(row.get("is_enrichment", 0)),
                    source=row.get("source", default_source),
                    adopted_year=row.get("adopted_year", default_year))
                result["inserted" if inserted else "updated"] += 1
            except Exception as e:
                result["errors"].append(f"{fp}[{i}] {code}: {e}")
        seed_codes |= file_codes

    if prune:
        for row in storage.standards.list():
            if row["code"] not in seed_codes:
                if storage.standards.delete(row["code"]):
                    result["pruned"] += 1
                    logger.info(f"Pruned retired standard {row['code']}")
                else:
                    result["blocked"] += 1
                    logger.warning(f"retire_blocked: {row['code']} still has "
                                   f"concept_standards rows")

    logger.info(f"Standards load: {result}")
    return result
