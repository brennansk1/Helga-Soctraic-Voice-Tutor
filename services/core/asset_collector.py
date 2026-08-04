"""Phase 3 — Asset Collection.

    Phase 1  what should this course contain?    curriculum_research
    Phase 2  what does each concept say?         ContentHydrator
    Phase 3  what does the learner LOOK at?      this module

Runs after hydration, before the course is enterable. Draws every diagram the
course will need and downloads every photograph, so a tutoring session never
authors one mid-dialogue.

WHY A SEPARATE PHASE, NOT PART OF HYDRATION

1. It needs the finished text. You cannot choose the right diagram for a concept
   from a half-written one.
2. Only a whole-course pass can see that eight concepts all want the same water
   cycle. Per-concept hydration structurally cannot dedupe across concepts, and
   duplicate figures are the most visible way this feature could go wrong.
3. Different failure semantics. Hydration failing means no course. Assets
   failing means a course with fewer pictures — degradable, and it must never
   fail a build.
4. Different resource profile. Hydration is LLM-bound and serialised behind one
   Ollama. Assets are part LLM, part network, and the network half overlaps.
5. It is re-runnable on its own. Add a source, re-collect; you cannot cheaply
   re-hydrate.

WHY BUILD TIME AND NOT THE DIALOGUE

A retry costs nothing here. `geometry` — the kind a 9B model is least reliable
at — gets generated, validated, and regenerated against the NAMED validation
failure, exactly as the depth contract already works. Generation is also
grammar-constrained here (Ollama `format`), which an inline ```aid fence in
mid-prose can never be. What reaches the learner is therefore checked, and the
session merely SELECTS from it.

WAIT TIME

The course is locked until this finishes, so the cost is real and every lever
below is about doing less work rather than deferring it:

  * SKIP        a concept whose subject routes to no visual kind never makes an
                LLM call at all. On a typical course this is 30-50% of concepts.
  * REUSE       assets are content-addressed in a course-wide index, so the
                second concept wanting the same figure costs nothing.
  * ONE CALL    all of a concept's slots come back in a single constrained
                request, not one call per slot.
  * OVERLAP     image downloads run in a thread pool while the LLM works.
                Ollama is serialised on one GPU; network I/O is not, so images
                are close to free in wall-clock terms.
  * BUDGET      a hard course-wide cap, so a 120-concept course cannot silently
                turn into a two-hour build.
"""

import json
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

try:  # container (flat) vs package import
    from services.common.aid_policy import (
        SLOT_MISCONCEPTION, SLOT_OPENING, SLOT_STUCK, SLOT_WORKED, suggest_kinds)
    from services.common.visual_aids import (
        normalize_aid, parse_concept_aids, render_concept_aids)
except ImportError:  # pragma: no cover
    from aid_policy import (                                   # type: ignore
        SLOT_MISCONCEPTION, SLOT_OPENING, SLOT_STUCK, SLOT_WORKED, suggest_kinds)
    from visual_aids import (                                  # type: ignore
        normalize_aid, parse_concept_aids, render_concept_aids)


# --- Budgets -----------------------------------------------------------------
MAX_AIDS_PER_CONCEPT = 2      # the runtime policy shows at most 3; 2 pre-built
                              # plus the generate fallback covers a concept well
MAX_ASSETS_PER_COURSE = 90
IMAGE_WORKERS = 4
DEFAULT_TIMEOUT = 90

# Subjects where a PHOTOGRAPH is the right answer and a diagram would be a lie:
# the particular artefact, organism, place or document IS the content.
_PHOTO_DOMAINS = ("art", "history", "geography", "biology", "astronomy")
_PHOTO_HINT = re.compile(
    r"\b(painting|sculpture|artist|artwork|portrait|architecture|"
    r"photograph|artefact|artifact|specimen|organism|habitat|landscape|"
    r"landform|rock|mineral|fossil|anatomy|microscop|"
    r"planet|galaxy|nebula|telescope|spacecraft|"
    r"monument|manuscript|document|primary source)\b", re.I)

# The shape the model must return. Passed to Ollama as `format`, so invalid
# JSON cannot be generated in the first place — the single biggest reliability
# difference between build-time and in-dialogue authoring.
AID_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "aids": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "slot": {"type": "string"},
                    "kind": {"type": "string"},
                    "title": {"type": "string"},
                    "caption": {"type": "string"},
                    "spec": {"type": "object"},
                },
                "required": ["slot", "kind", "spec"],
            },
        }
    },
    "required": ["aids"],
}


def wants_photograph(title, text, domains=()):
    """True when a real image beats a drawing. A concept map beats a photo of a
    leaf for photosynthesis; nothing substitutes for the actual Vermeer."""
    if set(domains or ()) & set(_PHOTO_DOMAINS):
        return True
    return bool(_PHOTO_HINT.search(f"{title} {text[:1500]}"))


def plan_slots(title, content, misconceptions):
    """Which teaching moments this concept should have a diagram for.

    Returns [] for concepts that do not want one — and returning [] is the
    single most valuable thing this function does, because it is what stops an
    LLM call being made at all.
    """
    kinds = suggest_kinds(title, content)
    if not kinds:
        return []                       # abstract subject: no diagram, no call
    slots = [SLOT_OPENING]
    if misconceptions:
        slots.append(f"{SLOT_MISCONCEPTION}:0")
    elif re.search(r"\b(step|solve|method|procedure|derivation|calculate)\b",
                   f"{title} {content[:1200]}", re.I):
        slots.append(SLOT_WORKED)
    return slots[:MAX_AIDS_PER_CONCEPT]


def _extract_section(markdown, name):
    match = re.search(rf"##+\s*{name}\s*\n(.*?)(?=\n##\s|\Z)", markdown or "",
                      re.DOTALL | re.IGNORECASE)
    if not match:
        return []
    return [l.strip().lstrip("- ").strip()
            for l in match.group(1).split("\n")
            if l.strip() and not l.strip().startswith("#")]


class AssetCollector:
    """Phase 3. Never raises out of `collect` — a course with no pictures is a
    course; a build that died gathering them is not."""

    def __init__(self, storage, status_callback=None, enabled=None,
                 images_enabled=None, budget=MAX_ASSETS_PER_COURSE):
        self.storage = storage
        self.status_callback = status_callback
        self.budget = budget
        self.enabled = (
            os.getenv("HELGA_ASSET_PHASE", "true").lower() == "true"
            if enabled is None else enabled)
        self.images_enabled = (
            os.getenv("HELGA_ASSET_IMAGES", "true").lower() == "true"
            if images_enabled is None else images_enabled)
        # Course-wide index: content hash -> aid. This is what stops the same
        # figure being drawn once per concept that mentions it.
        self._index = {}
        self._library = None
        self._lock = threading.Lock()
        self.stats = {"concepts": 0, "skipped": 0, "generated": 0, "reused": 0,
                      "images": 0, "failed": 0, "llm_calls": 0, "seconds": 0.0}

    # -- reporting ----------------------------------------------------------
    def _say(self, message):
        if self.status_callback:
            try:
                self.status_callback(message)
            except Exception:
                pass

    def _stage(self, pct, message):
        self._say(f"ASSET:PROGRESS:{int(pct)}:{message}")

    # -- main ---------------------------------------------------------------
    def collect(self, course_uid):
        """Gather every asset the course needs. Returns the manifest."""
        started = time.perf_counter()
        manifest = {"course_uid": course_uid, "version": 1, "concepts": {},
                    "stats": self.stats, "generated_at": None}
        if not self.enabled:
            logger.info("Asset phase disabled (HELGA_ASSET_PHASE=false)")
            self._say("ASSET:SKIPPED:disabled")
            return manifest

        try:
            concepts = self._concept_list(course_uid)
        except Exception as e:
            logger.error(f"Asset phase could not read the course: {e}")
            self._say(f"ASSET:ERROR:{e}")
            return manifest

        total = len(concepts)
        self._say(f"ASSET:START:{total}")
        logger.info(f"[ASSETS] Phase 3 starting for {total} concept(s)")

        # Images overlap the LLM work: Ollama is serialised on one GPU, network
        # I/O is not, so a download costs almost nothing in wall-clock terms.
        pool = ThreadPoolExecutor(max_workers=IMAGE_WORKERS,
                                  thread_name_prefix="asset-img")
        pending_images = {}
        try:
            for idx, (uid, title) in enumerate(concepts):
                if len(self._index) >= self.budget:
                    logger.warning(f"[ASSETS] course budget {self.budget} reached; "
                                   f"{total - idx} concept(s) left without assets")
                    self._say(f"ASSET:BUDGET:{self.budget}")
                    break
                try:
                    content = self.storage.courses.get_concept_content(course_uid, uid) or ""
                except Exception as e:
                    logger.warning(f"[ASSETS] {uid}: content unreadable ({e})")
                    self.stats["failed"] += 1
                    continue

                self.stats["concepts"] += 1
                misconceptions = _extract_section(content, "Misconceptions")

                # Queue the photo fetch first so it downloads while the LLM draws.
                if self.images_enabled and wants_photograph(title, content):
                    pending_images[uid] = pool.submit(self._fetch_image, title, content)

                aids = self._aids_for_concept(uid, title, content, misconceptions)
                if aids is None:
                    self.stats["skipped"] += 1
                else:
                    manifest["concepts"].setdefault(uid, {})["slots"] = sorted(aids)
                    self._write_aids(course_uid, uid, content, aids)

                self._stage(5 + 85 * (idx + 1) / max(total, 1),
                            f"{idx + 1}/{total} · {title[:44]}")

            # Collect the images that were downloading alongside.
            for uid, future in pending_images.items():
                try:
                    record = future.result(timeout=DEFAULT_TIMEOUT)
                except Exception as e:
                    logger.warning(f"[ASSETS] image fetch for {uid} failed: {e}")
                    continue
                if not record:
                    continue
                self.stats["images"] += 1
                manifest["concepts"].setdefault(uid, {})["image"] = {
                    "src": record["src"], "source": record.get("source", ""),
                    "license": record.get("license", ""),
                    "author": record.get("author", ""),
                }
                self._attach_image(course_uid, uid, record)
        finally:
            pool.shutdown(wait=False)

        self.stats["seconds"] = round(time.perf_counter() - started, 1)
        manifest["generated_at"] = int(time.time())
        manifest["stats"] = dict(self.stats)
        self._write_manifest(course_uid, manifest)

        logger.info(
            f"[ASSETS] done in {self.stats['seconds']}s — "
            f"{self.stats['generated']} drawn, {self.stats['reused']} reused, "
            f"{self.stats['images']} images, {self.stats['skipped']} concepts "
            f"needed none, {self.stats['llm_calls']} LLM calls")
        self._say(
            f"ASSET:DONE:{self.stats['generated']}:{self.stats['images']}:"
            f"{self.stats['skipped']}:{self.stats['seconds']}")
        return manifest

    # -- per concept --------------------------------------------------------
    def _aids_for_concept(self, uid, title, content, misconceptions):
        """Returns {slot: aid}, or None when this concept wants no diagram."""
        slots = plan_slots(title, content, misconceptions)
        if not slots:
            logger.debug(f"[ASSETS] {title[:40]}: no visual subject, skipping")
            return None

        # Reuse, checked BEFORE the LLM call rather than after — keyed on
        # subject + slots, so a hit costs nothing at all rather than costing a
        # generation that is then discarded.
        #
        # Two tiers. The course-local index catches the eight concepts in one
        # course that all want the water cycle. The shared library catches the
        # same figure across DIFFERENT courses: "the water cycle" is drawn once
        # on this machine, ever. On a second course in a related subject that is
        # the difference between a 40-minute phase and a 4-minute one.
        key = (self._subject_key(title), tuple(slots))
        with self._lock:
            cached = self._index.get(key)
        if not cached:
            cached = self._library_get(key)
            if cached:
                with self._lock:
                    self._index[key] = cached
        if cached:
            self.stats["reused"] += len(cached)
            logger.debug(f"[ASSETS] {title[:40]}: reused {len(cached)} asset(s)")
            return dict(cached)

        aids = self._generate(title, content, slots, misconceptions)
        if not aids:
            self.stats["failed"] += 1
            return None
        with self._lock:
            self._index[key] = dict(aids)
            for aid in aids.values():
                self._index[aid["id"]] = aid
        self._library_put(key, aids)
        return aids

    # -- shared asset library ----------------------------------------------
    def _library_path(self):
        root = os.environ.get("DATA_ROOT", "data")
        return os.path.join(root, "asset_library.json")

    def _library_load(self):
        if getattr(self, "_library", None) is None:
            try:
                with open(self._library_path()) as fh:
                    self._library = json.load(fh)
            except (OSError, ValueError):
                self._library = {}
        return self._library

    def _library_get(self, key):
        """A previously-drawn figure for this exact subject+slots, from any
        course built on this machine. Re-validated on the way out: the library
        is a cache, and a cache that hands back something the current validator
        would reject is worse than a miss."""
        entry = self._library_load().get(self._library_key(key))
        if not entry:
            return None
        out = {}
        for slot, payload in entry.items():
            aid, err = normalize_aid(payload, default_tier=payload.get("tier", "authored"))
            if not aid:
                logger.info(f"[ASSETS] library entry rejected on re-validation: {err}")
                return None
            aid["slot"] = slot
            out[slot] = aid
        return out or None

    def _library_put(self, key, aids):
        try:
            library = self._library_load()
            library[self._library_key(key)] = {
                slot: {"kind": a["kind"], "title": a.get("title", ""),
                       "caption": a.get("caption", ""), "alt": a.get("alt", ""),
                       "reveal": a.get("reveal", "tutor"),
                       "tier": a.get("provenance", {}).get("tier", "authored"),
                       "spec": a["spec"]}
                for slot, a in aids.items()}
            path = self._library_path()
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w") as fh:
                json.dump(library, fh)
            os.replace(tmp, path)
        except Exception as e:
            logger.debug(f"[ASSETS] could not update shared library: {e}")

    @staticmethod
    def _library_key(key):
        subject, slots = key
        return f"{subject}|{','.join(slots)}"

    def _subject_key(self, title):
        return re.sub(r"[^a-z0-9 ]", "", (title or "").lower()).strip()[:60]

    def _generate(self, title, content, slots, misconceptions, attempts=2):
        """One constrained call for all of a concept's slots, retried against
        the NAMED validation failure — the same regenerate-against-the-error
        loop the depth contract uses, and the reason build time beats runtime."""
        from services.common.llm_utils import llm_generate_json

        kinds = suggest_kinds(title, content)
        note = ""
        for attempt in range(attempts):
            prompt = self._build_prompt(title, content, slots, kinds,
                                        misconceptions, note)
            try:
                self.stats["llm_calls"] += 1
                raw = llm_generate_json(
                    prompt,
                    sys_prompt=("You design teaching diagrams. You return only "
                                "JSON matching the requested shape."),
                    retries=1, max_tokens=1100, expected_type="dict",
                    schema=AID_PLAN_SCHEMA)
            except Exception as e:
                logger.warning(f"[ASSETS] generation call failed for {title[:40]}: {e}")
                return {}
            if not isinstance(raw, dict):
                note = "Your last reply was not a JSON object with an 'aids' array."
                continue

            out, errors = {}, []
            for item in (raw.get("aids") or [])[:MAX_AIDS_PER_CONCEPT]:
                if not isinstance(item, dict):
                    continue
                slot = str(item.get("slot") or SLOT_OPENING)[:40]
                aid, err = normalize_aid(item, default_tier="authored")
                if aid:
                    aid["slot"] = slot
                    out[slot] = aid
                else:
                    errors.append(f"{item.get('kind')}: {err}")
            if out:
                self.stats["generated"] += len(out)
                return out
            note = ("Your last attempt was rejected: "
                    + "; ".join(errors[:3])
                    + ". Fix exactly that and return valid JSON.")
            logger.info(f"[ASSETS] retry {attempt + 1} for {title[:40]}: {errors[:2]}")
        return {}

    def _build_prompt(self, title, content, slots, kinds, misconceptions, note):
        slot_lines = []
        for slot in slots:
            if slot == SLOT_OPENING:
                slot_lines.append(
                    f'- "{slot}": the figure a tutor puts up when introducing this '
                    "concept. It must pose the question, NOT answer it — label the "
                    'unknown "?" and give the answering element "stage": 1.')
            elif slot.startswith(SLOT_MISCONCEPTION):
                idx = int(slot.split(":")[1]) if ":" in slot else 0
                wrong = misconceptions[idx] if idx < len(misconceptions) else ""
                slot_lines.append(
                    f'- "{slot}": a figure that makes THIS specific error visible: '
                    f'"{wrong[:160]}"')
            elif slot == SLOT_WORKED:
                slot_lines.append(
                    f'- "{slot}": the method as steps, with the final step at '
                    '"stage": 1 so it stays hidden until the learner commits.')
            else:
                slot_lines.append(f'- "{slot}": a supporting figure.')

        return f"""Design teaching diagrams for one concept of a course.

CONCEPT: {title}

MATERIAL:
{(content or '')[:2200]}

Produce exactly {len(slots)} diagram(s), one per slot:
{chr(10).join(slot_lines)}

Best-suited kinds for this subject: {', '.join(kinds) or 'any'}
Allowed kinds: number_line, geometry, plot, bars, graph, timeline, table, venn,
cycle, steps, fraction.

Field shapes:
  number_line {{"min":-3,"max":7,"marks":[{{"at":2,"label":"2"}}],"intervals":[{{"from":2,"to":5,"open_start":true,"label":"2 < x <= 5"}}]}}
  geometry    {{"points":{{"A":[0,0],"B":[4,0],"C":[0,3]}},"segments":[{{"from":"A","to":"B","label":"4"}}],"polygons":[{{"vertices":["A","B","C"]}}],"angles":[{{"at":"A","from":"B","to":"C","right":true}}]}}
  graph       {{"nodes":[{{"id":"a","label":"Sunlight"}}],"edges":[{{"from":"a","to":"b","label":"makes"}}],"direction":"TB"}}
  bars        {{"categories":["Mon","Tue"],"series":[{{"values":[4,7]}}],"y_label":"cm"}}
  steps       {{"steps":[{{"label":"Square both sides","detail":"x = 16"}}]}}
  timeline    {{"events":[{{"at":1687,"label":"Principia"}}]}}
  table       {{"columns":["","A","B"],"rows":[["Divisions","1","2"]]}}
  venn        {{"sets":[{{"label":"Mammals","items":["dog"]}}],"overlaps":[{{"sets":[0,1],"items":["whale"]}}]}}
  cycle       {{"steps":[{{"label":"Evaporation"}}]}}
  fraction    {{"models":[{{"parts":8,"shaded":3,"label":"3/8"}}]}}
  plot        {{"series":[{{"points":[[0,0],[1,1]],"label":"y = x"}}]}}

RULES
- geometry coordinates must actually form the shape described; a right angle
  marked with "right": true must genuinely be 90 degrees.
- Never draw the result the learner is meant to find. Hide it with "stage": 1.
- Give every diagram a short "title" and a "caption" that asks something.
{note}

Return: {{"aids":[{{"slot":"...","kind":"...","title":"...","caption":"...","spec":{{...}}}}]}}"""

    # -- images -------------------------------------------------------------
    def _fetch_image(self, title, content):
        """Runs on the pool thread — network only, no LLM, no shared state."""
        try:
            from services.common.media_cache import cache_image
            from services.research.image_sources import fetch_images
            from services.research.domain_sources import classify_domains
        except ImportError as e:
            logger.debug(f"[ASSETS] image stack unavailable: {e}")
            return None
        try:
            domains = classify_domains(title, content[:1200])
            hits = fetch_images(title, domains=domains, per_source=1, budget=3)
            for hit in hits:
                record = cache_image(hit.get("image_url"), hit)
                if record:
                    return record
        except Exception as e:
            logger.warning(f"[ASSETS] image search failed for {title[:40]}: {e}")
        return None

    def _attach_image(self, course_uid, uid, record):
        """Add the cached photograph as an `image` aid in the concept."""
        try:
            content = self.storage.courses.get_concept_content(course_uid, uid) or ""
            existing = parse_concept_aids(content)
            aid, err = normalize_aid({
                "kind": "image",
                "title": record.get("title") or "",
                "caption": "",
                "provenance": {"tier": "retrieved", "source": record.get("source", ""),
                               "license": record.get("license", ""),
                               "url": record.get("page", "")},
                "spec": {"src": record["src"]},
            }, default_tier="retrieved")
            if err:
                logger.warning(f"[ASSETS] cached image rejected for {uid}: {err}")
                return
            aid["slot"] = "photo"
            existing["photo"] = aid
            self._write_aids(course_uid, uid, content, existing, merge=False)
        except Exception as e:
            logger.warning(f"[ASSETS] could not attach image to {uid}: {e}")

    # -- persistence --------------------------------------------------------
    def _write_aids(self, course_uid, uid, content, aids, merge=True):
        """Replace the concept's `## Visual Aids` section.

        Stored in the concept markdown beside `## Misconceptions` and
        `## Analogies`, which the FSM already parses the same way — so the
        diagrams travel with the content they belong to and no schema migrates.
        """
        try:
            if merge:
                merged = parse_concept_aids(content)
                merged.update(aids)
                aids = merged
            body = re.sub(r"\n##+\s*Visual Aids\s*\n.*?(?=\n##\s|\Z)", "\n",
                          content, flags=re.DOTALL | re.IGNORECASE).rstrip()
            section = render_concept_aids(aids)
            if section:
                body = f"{body}\n\n{section}"
            self.storage.courses.save_concept_content(course_uid, uid, body)
        except Exception as e:
            logger.warning(f"[ASSETS] could not write aids for {uid}: {e}")

    def _write_manifest(self, course_uid, manifest):
        """A course-level index so a session can learn what exists in ONE read
        instead of parsing every concept's markdown at startup."""
        try:
            path = os.path.join(self.storage.courses.courses_dir, course_uid,
                                "assets.json")
            tmp = path + ".tmp"
            with open(tmp, "w") as fh:
                json.dump(manifest, fh, indent=2)
            os.replace(tmp, path)
        except Exception as e:
            logger.warning(f"[ASSETS] could not write manifest: {e}")

    # -- inputs -------------------------------------------------------------
    def _concept_list(self, course_uid):
        """(uid, title) in syllabus order — the order a learner will meet them."""
        return [(c["uid"], c.get("title", ""))
                for c in (self.storage.courses.get_flat_concepts(course_uid) or [])
                if c.get("uid")]


def load_manifest(storage, course_uid):
    """Read a course's asset manifest, or None. Used at session start."""
    try:
        path = os.path.join(storage.courses.courses_dir, course_uid, "assets.json")
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None
