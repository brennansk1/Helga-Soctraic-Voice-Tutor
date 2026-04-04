#!/usr/bin/env python3
"""
Learn Tab Teaching Loop Tester & Quality Auditor
=================================================
Drives the FSM through a Socratic learning session against live services.
Evaluates teaching quality, conversation flow, and content accuracy.

Usage:
    python3 tools/test_learn_loop.py                           # auto-pick first real course
    python3 tools/test_learn_loop.py --course course_9bc4fb01  # specific course
    python3 tools/test_learn_loop.py --max-concepts 3          # limit concepts
    python3 tools/test_learn_loop.py --verbose                 # full detail
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
import requests

CORE_URL = "http://localhost:5003"
RAG_URL = "http://localhost:5002"
WEBUI_URL = "http://localhost:5050"

EVENT_TIMEOUT = 120  # 14B model needs more time per exchange (grading + question gen)
POLL_INTERVAL = 2.0
MAX_WAIT = 120


class C:
    H = '\033[95m'; B = '\033[94m'; CY = '\033[96m'; G = '\033[92m'
    Y = '\033[93m'; R = '\033[91m'; BO = '\033[1m'; DM = '\033[2m'; E = '\033[0m'


class Issue:
    def __init__(self, sev, cat, desc, detail=None):
        self.sev, self.cat, self.desc, self.detail = sev, cat, desc, detail
    def __str__(self):
        c = {'CRITICAL': C.R, 'HIGH': C.R, 'MEDIUM': C.Y, 'LOW': C.DM}.get(self.sev, '')
        s = f"{c}[{self.sev}] {self.cat}: {self.desc}{C.E}"
        if self.detail:
            s += f"\n      {C.DM}{self.detail}{C.E}"
        return s


class LearnLoopTester:
    def __init__(self, verbose=False):
        self.verbose = verbose
        self.issues = []
        self.conversation = []       # (role, text, meta)
        self.quality_notes = []      # teaching quality observations
        self.concepts_tested = 0
        self.concepts_completed = 0
        self.content_cache = {}

    def p(self, msg, c=''):
        print(f"{c}{msg}{C.E}")

    def pv(self, msg, c=C.DM):
        if self.verbose:
            print(f"{c}    {msg}{C.E}")

    def issue(self, sev, cat, desc, detail=None):
        i = Issue(sev, cat, desc, detail)
        self.issues.append(i)
        if sev in ('CRITICAL', 'HIGH'):
            self.p(f"    >> {i}")

    def quality(self, category, observation):
        self.quality_notes.append((category, observation))

    def log_turn(self, role, text, meta=None):
        self.conversation.append((role, text, meta or {}))

    # ── Service helpers ──
    def get_state(self):
        try:
            return requests.get(f"{CORE_URL}/state", timeout=10).json()
        except Exception as e:
            self.issue("CRITICAL", "CONN", f"FSM unreachable: {e}")
            return None

    def send_event(self, etype, payload=None):
        self.pv(f"-> {etype} {json.dumps(payload or {})[:80]}")
        try:
            return requests.post(f"{CORE_URL}/event",
                json={"type": etype, "payload": payload or {}}, timeout=EVENT_TIMEOUT).json()
        except requests.Timeout:
            self.issue("HIGH", "TIMEOUT", f"{etype} timed out")
            return None
        except Exception as e:
            self.issue("HIGH", "EVENT", f"{etype} failed: {e}")
            return None

    def send_text(self, text):
        return self.send_event("TEXT_INPUT", {"text": text})

    def wait_transcript(self, prev_len, timeout=MAX_WAIT):
        t0 = time.time()
        while time.time() - t0 < timeout:
            st = self.get_state()
            if not st: return None, []
            tr = st.get("transcript", [])
            if len(tr) > prev_len:
                return st, tr[prev_len:]
            time.sleep(POLL_INTERVAL)
        return self.get_state(), []

    def wait_state(self, target, timeout=MAX_WAIT):
        t0 = time.time()
        while time.time() - t0 < timeout:
            st = self.get_state()
            if not st: return None
            if st.get("state") == target: return st
            time.sleep(POLL_INTERVAL)
        return self.get_state()

    def get_concept_content(self, course_uid, concept_uid):
        key = f"{course_uid}/{concept_uid}"
        if key in self.content_cache:
            return self.content_cache[key]
        try:
            r = subprocess.run(
                ["docker", "exec", "helga-rag-engine", "cat",
                 f"/app/data/courses/{course_uid}/content/{concept_uid}.md"],
                capture_output=True, text=True, timeout=5)
            if r.returncode == 0 and r.stdout.strip():
                self.content_cache[key] = r.stdout.strip()
                return r.stdout.strip()
        except: pass
        return None

    def get_course_structure(self, uid):
        try:
            r = subprocess.run(
                ["docker", "exec", "helga-rag-engine", "cat",
                 f"/app/data/courses/{uid}/structure.json"],
                capture_output=True, text=True, timeout=10)
            if r.returncode == 0:
                return json.loads(r.stdout)
        except: pass
        return None

    def flatten_concepts(self, structure):
        out = []
        for m in structure.get("modules", []):
            for u in m.get("units", []):
                for l in u.get("lessons", []):
                    for c in l.get("concepts", []):
                        out.append(c)
        return out

    # ── Content quality checks ──
    def audit_content(self, concept_uid, content, concept_title):
        """Audit a concept's content for teaching quality."""
        if not content:
            self.quality("CONTENT_MISSING", f"{concept_title}: No markdown content file")
            return

        checks = {
            "has_core_def": "## Core Definition" in content,
            "has_context": "## Contextual Explanation" in content,
            "has_hook": "## Socratic Hook" in content,
            "has_analogy": "## Analogies" in content or "## Analogy" in content,
            "has_misconception": "## Misconceptions" in content,
            "has_metadata": "## Metadata" in content,
        }

        missing = [k for k, v in checks.items() if not v]
        if missing:
            self.quality("CONTENT_INCOMPLETE", f"{concept_title}: Missing sections: {', '.join(missing)}")

        # Check Core Definition quality
        core_match = re.search(r"## Core Definition\n(.*?)(?=\n## |\Z)", content, re.DOTALL)
        if core_match:
            core_def = core_match.group(1).strip()
            if len(core_def) < 30:
                self.quality("CONTENT_SHALLOW", f"{concept_title}: Core Definition too short ({len(core_def)} chars)")
            if "[Hydration failed]" in core_def:
                self.quality("CONTENT_FAILED", f"{concept_title}: Hydration failed placeholder in content")

        # Check Contextual Explanation depth
        ctx_match = re.search(r"## Contextual Explanation\n(.*?)(?=\n## |\Z)", content, re.DOTALL)
        if ctx_match:
            ctx = ctx_match.group(1).strip()
            word_count = len(ctx.split())
            if word_count < 50:
                self.quality("CONTENT_SHALLOW", f"{concept_title}: Contextual Explanation only {word_count} words (should be 150-400)")
        else:
            self.quality("CONTENT_MISSING_CTX", f"{concept_title}: No Contextual Explanation section")

        # Check for duplicate/boilerplate content
        if content.count("is a critical component") > 2:
            self.quality("CONTENT_REPETITIVE", f"{concept_title}: Repetitive phrasing detected")

        # Total content length
        content_words = len(content.split())
        if content_words < 100:
            self.quality("CONTENT_SHORT", f"{concept_title}: Only {content_words} words total")

    # ── Answer generation ──
    def clean_content(self, raw):
        lines = raw.split('\n')
        clean = []
        skip_section = False
        for line in lines:
            s = line.strip()
            if s.startswith('#'): continue
            if s.startswith('- **') and ':' in s[:30]: continue
            if s.startswith('## Metadata') or s.startswith('## Session Notes'):
                skip_section = True; continue
            if skip_section:
                if s.startswith('## '): skip_section = False
                else: continue
            if s.startswith('```') or s.startswith('---') or s.startswith('$$'): continue
            if s: clean.append(s)
        return ' '.join(clean)

    def make_answer(self, question, concept_title, concept_content):
        """Generate a reasoning-based answer that demonstrates causal understanding.

        The grading rubric requires causal reasoning and application, not just
        restating definitions. We extract question-specific keywords and build
        answers that directly engage with what was asked, using varied sentence
        pools to avoid repetition across exchanges.
        """
        if not concept_content:
            return f"I think {concept_title} is important because understanding the underlying mechanisms helps us predict outcomes and apply the concept in different situations."

        text = self.clean_content(concept_content)
        if not text or len(text) < 30:
            return f"I believe {concept_title} matters because it explains how different factors interact, which means we can use that understanding to solve real problems."

        sentences = re.split(r'(?<=[.!?])\s+', text)
        sentences = [s.strip() for s in sentences if 20 < len(s.strip()) < 300]

        q_lower = question.lower()
        q_words = set(re.findall(r'\b\w{4,}\b', q_lower))

        # Score sentences by relevance to question
        scored = []
        for s in sentences:
            s_words = set(re.findall(r'\b\w{4,}\b', s.lower()))
            overlap = len(q_words & s_words)
            scored.append((overlap, s))
        scored.sort(key=lambda x: -x[0])

        # Use exchange counter to rotate through different sentence pools
        # This prevents picking the same sentences every time
        offset = getattr(self, '_answer_rotation', 0)
        self._answer_rotation = offset + 1

        # Pick sentences from different parts of the ranked list
        pool_start = (offset * 3) % max(1, len(scored) - 3)
        relevant = [s for _, s in scored[pool_start:pool_start+5] if s]
        if len(relevant) < 2:
            relevant = [s for _, s in scored[:5] if s]

        # Extract key nouns/verbs from the question to reference directly
        q_key = re.findall(r'\b(?:what|how|why|when|where)\s+(\w+(?:\s+\w+){0,2})', q_lower)
        q_topic = q_key[0] if q_key else concept_title.lower()

        # Reasoning connectors - rotate to avoid repetition
        connectors = [
            ("The core reason is that", "which means that", "Therefore"),
            ("This happens because", "and as a consequence", "So in practice"),
            ("The underlying mechanism is that", "This leads to", "Which explains why"),
            ("What drives this is that", "The implication is that", "This is why"),
            ("Fundamentally", "Because of this relationship", "Consequently"),
        ]
        c1, c2, c3 = connectors[offset % len(connectors)]

        # Build answer that engages with the question
        if len(relevant) >= 3:
            answer = f"{c1} {relevant[0][0].lower()}{relevant[0][1:]} {c2} {relevant[1][0].lower()}{relevant[1][1:]} {c3}, {relevant[2][0].lower()}{relevant[2][1:]}"
        elif len(relevant) >= 2:
            answer = f"{c1} {relevant[0][0].lower()}{relevant[0][1:]} {c2} {relevant[1][0].lower()}{relevant[1][1:]}"
        elif relevant:
            answer = f"{c1} {relevant[0][0].lower()}{relevant[0][1:]}"
        else:
            answer = f"{c1} {q_topic} involves understanding how the causal mechanism connects inputs to outcomes, allowing us to predict what happens when conditions change."

        # Frame based on question type - reference the question directly
        if "scenario" in q_lower or "imagine" in q_lower or "what would" in q_lower or "consider" in q_lower:
            # Extract the scenario from the question
            scenario_match = re.search(r'(?:imagine|consider|suppose|what if|scenario where)\s+(.{20,80}?)(?:\.|,|\?)', q_lower)
            if scenario_match:
                answer = f"In that case where {scenario_match.group(1).strip()}, {answer[0].lower()}{answer[1:]}"
            else:
                answer = f"In that specific scenario, {answer[0].lower()}{answer[1:]}"
        elif "difference" in q_lower or "contrast" in q_lower or "distinguish" in q_lower:
            answer = f"The key distinction here is that {answer[0].lower()}{answer[1:]}"
        elif "why" in q_lower:
            answer = f"The reason {q_topic} matters is that {answer[0].lower()}{answer[1:]}"
        elif "how" in q_lower and q_lower.startswith("how"):
            answer = f"The way this works is that {answer[0].lower()}{answer[1:]}"
        elif "edge" in q_lower or "limit" in q_lower or "break" in q_lower or "fail" in q_lower:
            answer = f"This breaks down when {answer[0].lower()}{answer[1:]}"

        return answer[:600]

    # ── Teaching quality analysis ──
    def analyze_teaching_exchange(self, helga_text, student_text, concept_title, exchange_num):
        """Analyze a single teaching exchange for quality."""
        h = helga_text.lower()

        # Check: Did Helga ask a question?
        has_question = '?' in helga_text
        if not has_question and exchange_num > 0:
            self.quality("TEACHING_NO_QUESTION", f"{concept_title} exchange {exchange_num}: Helga response has no question")

        # Check: Is Helga using scenarios/analogies (good) or just definitions (bad)?
        uses_scenario = any(w in h for w in ["imagine", "suppose", "consider", "picture", "think of"])
        uses_definition = any(w in h for w in ["define", "what is the definition", "list the"])
        if uses_definition and not uses_scenario:
            self.quality("TEACHING_DEFINITION_QUIZ",
                f"{concept_title} exchange {exchange_num}: Helga asked for definition (bad Socratic practice)")

        # Check: Is the response too long?
        word_count = len(helga_text.split())
        if word_count > 200:
            self.quality("TEACHING_VERBOSE", f"{concept_title} exchange {exchange_num}: Helga response too long ({word_count} words)")

        # Check: Does response contain markdown artifacts?
        if any(mk in helga_text for mk in ['##', '**', '```', '- **']):
            self.quality("TEACHING_MARKDOWN_LEAK", f"{concept_title} exchange {exchange_num}: Markdown in spoken response")

        # Check: Safety false positive
        if "988" in helga_text or "Crisis Lifeline" in helga_text:
            self.issue("CRITICAL", "SAFETY_FALSE_POS",
                f"Safety filter false positive on exchange {exchange_num}",
                f"Student said: {student_text[:100]}")

        # Check: Generic/unhelpful feedback
        if helga_text.strip() in ["Correct.", "Good.", "Right."]:
            self.quality("TEACHING_GENERIC_FEEDBACK",
                f"{concept_title} exchange {exchange_num}: Only generic feedback, no specific reinforcement")

    # ── Test Phases ──
    def phase0_services(self):
        self.p(f"\n{'='*60}", C.BO)
        self.p("PHASE 0: Service Health", C.BO)
        self.p('='*60, C.BO)
        ok = True
        for name, url in [("core", f"{CORE_URL}/state"), ("rag", f"{RAG_URL}/api/courses"), ("web-ui", f"{WEBUI_URL}/")]:
            try:
                r = requests.get(url, timeout=5)
                col = C.G if r.status_code == 200 else C.Y
                self.p(f"  {col}{'OK' if r.status_code==200 else r.status_code} {name}{C.E}")
                if r.status_code != 200: ok = False
            except Exception as e:
                self.p(f"  {C.R}FAIL {name}: {e}{C.E}")
                self.issue("CRITICAL", "SERVICE", f"{name} down"); ok = False
        return ok

    def phase1_enter(self, course, uid):
        title = course["title"]
        self.p(f"\n{'='*60}", C.BO)
        self.p(f"PHASE 1: Enter '{title}' ({uid})", C.BO)
        self.p('='*60, C.BO)

        self.send_event("SET_CONTEXT", {"course_uid": uid})
        time.sleep(1)
        st = self.get_state()
        if st and st.get("active_course_uid") == uid:
            self.p(f"  {C.G}SET_CONTEXT OK{C.E}")
        else:
            self.issue("CRITICAL", "SET_CONTEXT", "Not handled")

        prev_len = len(self.get_state().get("transcript", []))
        self.send_text(f"open course {title}")
        st = self.wait_state("SOCRATIC_LEARNING", timeout=30)

        if not st or st.get("state") != "SOCRATIC_LEARNING":
            self.issue("CRITICAL", "STATE", f"Stuck in {st.get('state') if st else '?'}")
            return False

        self.p(f"  {C.G}State: SOCRATIC_LEARNING{C.E}")
        self.p(f"  Concept: {st.get('current_lesson_title')} ({st.get('current_lesson_uid')})")
        self.p(f"  Syllabus: {st.get('syllabus_length', 0)} remaining")

        # Check intro
        tr = st.get("transcript", [])
        new = [e for e in tr[prev_len:] if e.get("sender") == "helga"]
        if new:
            intro = new[0]["text"]
            self.log_turn("helga", intro, {"phase": "intro"})
            self.p(f"  {C.G}Intro: \"{intro[:100]}...\"{C.E}")
            # Quality check on intro
            if "988" in intro or "Crisis" in intro:
                self.issue("CRITICAL", "SAFETY_FALSE_POS", "Safety triggered on course open")
        return True

    def phase2_teach(self, course_uid, max_concepts):
        self.p(f"\n{'='*60}", C.BO)
        self.p(f"PHASE 2: Teaching ({max_concepts} concepts)", C.BO)
        self.p('='*60, C.BO)

        for ci in range(max_concepts):
            st = self.get_state()
            if not st or st.get("state") != "SOCRATIC_LEARNING":
                self.p(f"  Left SOCRATIC_LEARNING (state={st.get('state') if st else '?'})")
                break

            concept_uid = st.get("current_lesson_uid")
            concept_title = st.get("current_lesson_title", "?")
            self.concepts_tested += 1

            self.p(f"\n  {'─'*50}", C.CY)
            self.p(f"  CONCEPT {ci+1}: {concept_title} ({concept_uid})", C.CY)
            self.p(f"  {'─'*50}", C.CY)

            # Audit content quality
            content = self.get_concept_content(course_uid, concept_uid) if concept_uid else None
            if content:
                self.p(f"  Content: {len(content)} chars, {len(content.split())} words")
                self.audit_content(concept_uid, content, concept_title)
            else:
                self.quality("CONTENT_MISSING", f"{concept_title}: No content file")

            # Teaching loop
            exchange = 0
            max_exchanges = 20
            concept_done = False
            prev_question = ""
            duplicate_count = 0
            logged_texts = set()  # Track already-logged texts to prevent duplicates

            while exchange < max_exchanges:
                st = self.get_state()
                if not st or st.get("state") != "SOCRATIC_LEARNING": break
                if st.get("current_lesson_uid") != concept_uid:
                    self.p(f"  {C.G}>> CONCEPT ADVANCED{C.E}")
                    concept_done = True
                    self.concepts_completed += 1
                    break

                question = st.get("last_question", "")
                tr = st.get("transcript", [])
                if not question:
                    for e in reversed(tr):
                        if e.get("sender") == "helga":
                            question = e["text"]; break

                if not question:
                    self.issue("HIGH", "QUESTION", f"No question for {concept_title}")
                    break

                # Duplicate detection
                if question == prev_question:
                    duplicate_count += 1
                    if duplicate_count > 1:
                        self.quality("TEACHING_DUPLICATE", f"{concept_title}: Same question repeated {duplicate_count+1} times")
                else:
                    duplicate_count = 0
                    prev_question = question

                # Log and display (skip if already logged from previous exchange's transcript)
                if question not in logged_texts:
                    self.log_turn("helga", question, {"type": "question", "concept": concept_title, "exchange": exchange})
                    logged_texts.add(question)
                self.p(f"\n  {C.B}Q[{exchange}]: {question[:250]}{C.E}")

                # Generate answer
                answer = self.make_answer(question, concept_title, content)
                self.log_turn("student", answer, {"exchange": exchange, "concept": concept_title})
                self.p(f"  {C.DM}A[{exchange}]: {answer[:250]}{C.E}")

                prev_len = len(tr)
                self.send_text(answer)
                exchange += 1

                # Wait for response
                st2, new_entries = self.wait_transcript(prev_len, timeout=MAX_WAIT)
                if not st2:
                    self.issue("CRITICAL", "CONN", "Lost connection"); break
                if not new_entries:
                    self.issue("HIGH", "RESPONSE", f"No response after {MAX_WAIT}s"); continue

                # Analyze each new Helga message (skip already-logged texts)
                for entry in new_entries:
                    if entry.get("sender") != "helga": continue
                    text = entry["text"]
                    if text in logged_texts:
                        continue  # Already logged, don't double-log
                    logged_texts.add(text)
                    self.log_turn("helga", text, {"type": "response", "concept": concept_title, "exchange": exchange})

                    # Quality analysis
                    self.analyze_teaching_exchange(text, answer, concept_title, exchange)

                # Read actual grade from FSM state (not text-matching)
                if st2:
                    actual_grade = st2.get("last_socratic_grade", "?")
                    type_idx = st2.get("socratic_type_index", 0)
                    retry_ct = st2.get("socratic_retry_count", 0)
                    grade_colors = {1: C.R, 2: C.Y, 3: C.G, 4: C.G}
                    gc = grade_colors.get(actual_grade, C.DM)
                    last_text = ""
                    for entry in reversed(new_entries):
                        if entry.get("sender") == "helga":
                            last_text = entry["text"][:120]; break
                    self.p(f"  {gc}>> GRADE {actual_grade} | type_idx={type_idx} retry={retry_ct} | {last_text}{C.E}")
                    if type_idx >= 6:
                        self.p(f"  {C.G}>> MASTERED (all 6 types completed){C.E}")

                # Check for advancement
                if st2 and st2.get("current_lesson_uid") != concept_uid:
                    self.p(f"  {C.G}>> CONCEPT COMPLETED ({exchange} exchanges){C.E}")
                    concept_done = True
                    self.concepts_completed += 1
                    break

                time.sleep(0.3)

            if not concept_done:
                self.p(f"  {C.Y}Not completed after {exchange} exchanges. Skipping.{C.E}")
                self.send_event("SKIP_CONCEPT")
                time.sleep(3)

    def phase3_skip(self):
        self.p(f"\n{'='*60}", C.BO)
        self.p("PHASE 3: Skip Test", C.BO)
        self.p('='*60, C.BO)
        st = self.get_state()
        if not st or st.get("state") != "SOCRATIC_LEARNING":
            self.p("  Not in SOCRATIC_LEARNING"); return
        prev_uid = st.get("current_lesson_uid")
        prev_len = len(st.get("transcript", []))
        self.send_event("SKIP_CONCEPT")
        st2, _ = self.wait_transcript(prev_len, timeout=15)
        if st2 and st2.get("current_lesson_uid") != prev_uid:
            self.p(f"  {C.G}Skip OK -> {st2.get('current_lesson_title')}{C.E}")
        else:
            self.issue("MEDIUM", "SKIP", "SKIP_CONCEPT didn't change concept")

    def phase4_lobby(self):
        self.p(f"\n{'='*60}", C.BO)
        self.p("PHASE 4: Return to LOBBY", C.BO)
        self.p('='*60, C.BO)
        st = self.get_state()
        if st and st.get("state") == "LOBBY":
            self.p("  Already in LOBBY"); return
        self.send_text("stop")
        st = self.wait_state("LOBBY", timeout=10)
        if st and st.get("state") == "LOBBY":
            self.p(f"  {C.G}Returned to LOBBY{C.E}")
        else:
            self.issue("HIGH", "STATE", "Failed to return to LOBBY")

    def phase5_resume(self, course):
        self.p(f"\n{'='*60}", C.BO)
        self.p("PHASE 5: Resume", C.BO)
        self.p('='*60, C.BO)
        st = self.get_state()
        if not st or st.get("state") != "LOBBY":
            self.send_text("stop"); time.sleep(2)
        self.send_event("RESUME_COURSE", {"course_uid": course["uid"]})
        st = self.wait_state("SOCRATIC_LEARNING", timeout=20)
        if st and st.get("state") == "SOCRATIC_LEARNING":
            self.p(f"  {C.G}Resumed: {st.get('current_lesson_title')}{C.E}")
        else:
            self.issue("HIGH", "RESUME", f"Failed (state={st.get('state') if st else '?'})")
        self.send_text("stop"); time.sleep(1)

    def phase6_proxy(self, course):
        uid = course["uid"]
        self.p(f"\n{'='*60}", C.BO)
        self.p("PHASE 6: Web-UI Proxies", C.BO)
        self.p('='*60, C.BO)
        for path in ["/api/fsm_state", f"/api/course_structure?uid={uid}", f"/api/course_status/{uid}"]:
            try:
                r = requests.get(f"{WEBUI_URL}{path}", timeout=10)
                col = C.G if r.status_code == 200 else C.Y
                self.p(f"  {col}{r.status_code} GET {path}{C.E}")
                if r.status_code >= 400:
                    self.issue("MEDIUM", "PROXY", f"{path} -> {r.status_code}", r.text[:80])
            except Exception as e:
                self.issue("HIGH", "PROXY", f"{path} failed: {e}")

    def phase7_content_audit(self, course_uid, structure):
        """Audit content quality across multiple concepts."""
        self.p(f"\n{'='*60}", C.BO)
        self.p("PHASE 7: Content Quality Audit", C.BO)
        self.p('='*60, C.BO)
        concepts = self.flatten_concepts(structure)
        # Sample up to 8 concepts across the course
        step = max(1, len(concepts) // 8)
        sample = concepts[::step][:8]
        for c in sample:
            uid = c.get("uid")
            title = c.get("title", "?")
            content = self.get_concept_content(course_uid, uid)
            self.audit_content(uid, content, title)
            if content:
                wc = len(content.split())
                col = C.G if wc > 200 else C.Y if wc > 100 else C.R
                self.p(f"  {col}{wc:>4} words  {title}{C.E}")
            else:
                self.p(f"  {C.R}MISSING   {title}{C.E}")

    def print_report(self):
        self.p(f"\n{'='*60}", C.BO)
        self.p("FINAL REPORT", C.BO)
        self.p('='*60, C.BO)

        self.p(f"\n  Concepts tested:    {self.concepts_tested}")
        self.p(f"  Concepts completed: {self.concepts_completed}")
        self.p(f"  Conversation turns: {len(self.conversation)}")

        # Issues
        crit = [i for i in self.issues if i.sev == "CRITICAL"]
        high = [i for i in self.issues if i.sev == "HIGH"]
        med = [i for i in self.issues if i.sev == "MEDIUM"]
        low = [i for i in self.issues if i.sev == "LOW"]

        if self.issues:
            self.p(f"\n  {C.BO}ISSUES: {len(self.issues)}{C.E}", C.R if crit else C.Y)
            self.p(f"    CRITICAL: {len(crit)}  HIGH: {len(high)}  MEDIUM: {len(med)}  LOW: {len(low)}")
            for i in self.issues:
                self.p(f"    {i}")

        # Quality observations
        if self.quality_notes:
            self.p(f"\n  {C.BO}TEACHING/CONTENT QUALITY ({len(self.quality_notes)} observations):{C.E}")
            cats = {}
            for cat, obs in self.quality_notes:
                cats.setdefault(cat, []).append(obs)
            for cat, items in sorted(cats.items()):
                self.p(f"\n    {C.Y}{cat} ({len(items)}):{C.E}")
                for item in items[:5]:
                    self.p(f"      - {item}")
                if len(items) > 5:
                    self.p(f"      ... and {len(items)-5} more")

        # Conversation log
        self.p(f"\n  {'─'*50}")
        self.p(f"  CONVERSATION LOG ({len(self.conversation)} turns)", C.BO)
        self.p(f"  {'─'*50}")
        for role, text, meta in self.conversation:
            icon = {"helga": "HELGA", "student": "STUDENT"}.get(role, role)
            concept = meta.get("concept", "")
            ex = meta.get("exchange", "")
            prefix = f"[{concept}]" if concept else ""
            disp = 300 if self.verbose else 200
            trunc = text[:disp] + ("..." if len(text) > disp else "")
            col = C.B if role == "helga" else C.DM
            self.p(f"  {col}{icon} {prefix}: {trunc}{C.E}")

        return len(crit) + len(high)


def main():
    parser = argparse.ArgumentParser(description="Test & audit the Socratic teaching loop")
    parser.add_argument("--course", help="Course UID")
    parser.add_argument("--max-concepts", type=int, default=2)
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--skip-teaching", action="store_true")
    parser.add_argument("--content-only", action="store_true", help="Only run content quality audit")
    args = parser.parse_args()

    t = LearnLoopTester(verbose=args.verbose)
    t.p(f"\n{C.BO}{'='*60}")
    t.p(f"  HELGA Learn Loop Tester & Quality Auditor")
    t.p(f"  Course: {args.course or 'auto'} | Concepts: {args.max_concepts}")
    t.p(f"{'='*60}{C.E}\n")

    if not t.phase0_services():
        t.print_report(); sys.exit(1)

    # Find course
    try:
        courses = requests.get(f"{RAG_URL}/api/courses", timeout=10).json().get("courses", [])
    except: courses = []
    real = [c for c in courses if c.get("stats", {}).get("concepts", 0) > 0]
    if not real:
        t.issue("CRITICAL", "DATA", "No courses with concepts"); t.print_report(); sys.exit(1)

    course = ([c for c in courses if c["uid"] == args.course] or real)[0] if args.course else real[0]
    uid = course["uid"]
    t.p(f"  Course: {course['title']} ({uid})")
    t.p(f"  Stats: {json.dumps(course.get('stats', {}))}")

    structure = t.get_course_structure(uid)
    if structure:
        concepts = t.flatten_concepts(structure)
        t.p(f"  Concepts: {len(concepts)}")

    if args.content_only:
        if structure:
            t.phase7_content_audit(uid, structure)
        t.print_report(); sys.exit(0)

    # Full test
    if t.phase1_enter(course, uid):
        if not args.skip_teaching:
            t.phase2_teach(uid, args.max_concepts)
        st = t.get_state()
        if st and st.get("state") == "SOCRATIC_LEARNING":
            t.phase3_skip()

    t.phase4_lobby()
    t.phase5_resume(course)
    t.phase6_proxy(course)

    if structure:
        t.phase7_content_audit(uid, structure)

    serious = t.print_report()
    sys.exit(1 if serious > 0 else 0)


if __name__ == "__main__":
    main()
