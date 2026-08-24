# Rosenshine's Principles and Bloom's revised taxonomy: verb/level alignment

Research date: 2026-08-07. Web research only; no code was changed.

**Verification key:** `[V]` = the page/PDF was fetched and the claim read in it · `[UNV]` = could not confirm, **do not publish as fact** · `⚠️` = non-peer-reviewed host or source.

---

## ⚠️ Three corrections up front

1. **The "24 vs 8 questions per 50 minutes" figure is NOT in Rosenshine (2012).** What the article actually says (p. 14, `[V]`) is: *"In one study, **the least effective teachers asked only nine questions in a 40-minute period**."* **No figure is given for the most effective teachers.** The 24 / 8.6 pair is real but comes from **Rosenshine & Stevens (1986), "Teaching Functions"** — `[UNV]`, paywalled. **Do not cite it without the chapter.**
2. **Automated 6-way Bloom classification is not reliable enough to gate a build.** Human inter-rater agreement is **ICC = 0.231 / 0.267** and accuracy **46.0%**. A frontier LLM zero-shot scores **0.72–0.73**; a plain SVM on the same data scores **0.94**. **Collapsing to 3 tiers lifts human accuracy 46.0% → 81.8%.**
3. **Bloom's revised hierarchy is explicitly NOT strictly monotone.** Krathwohl (2002), verbatim: *"some cognitive processes associated with Understand (e.g., Explaining) are more cognitively complex than at least one of the cognitive processes associated with Apply (e.g., Executing)."* **Do not implement `level(hook) > level(Apply)` as an integer comparison.**

---

## Findings — Rosenshine's Principles of Instruction

### Sources and URL status

| URL | Status |
|---|---|
| https://www.aft.org/sites/default/files/Rosenshine.pdf | ✅ **WORKS** — canonical full text, *American Educator* 36(1), Spring 2012, pp. 12–19, 39 |
| https://www.aft.org/ae/spring2012/rosenshine | ✅ resolves — landing page only, no article text |
| `ibe.unesco.org/…/EdPractices_21.pdf` | ❌ **404** — and this is the URL printed *inside* the 2012 article itself |
| `iaoed.org/downloads/EdPractices_21.pdf` | ❌ **TLS cert mismatch** |
| http://formapex.com/telechargementpublic/rosenshine2010a.pdf | ✅ **WORKING MIRROR** of Rosenshine (2010), IAE *Educational Practices Series–21*, IBE/2010/ST/EP21, 32 pp. |

The 2012 article states `[V]`: *"This article is adapted with permission from Principles of Instruction by Barak Rosenshine. Published by the International Academy of Education in 2010."* **Prefer the 2010 booklet as the citation** — it names attributions inline rather than in endnotes.

### The 10 principles — exact printed wording

⚠️ **Two different wordings exist in the same article. Do not mix them.**

**Short form, p. 12** (all `[V]`):
1. Begin a lesson with a short review of previous learning.
2. Present new material in small steps with student practice after each step.
3. Ask a large number of questions and check the responses of all students.
4. Provide models.
5. Guide student practice.
6. Check for student understanding.
7. Obtain a high success rate.
8. Provide scaffolds for difficult tasks.
9. Require and monitor independent practice.
10. Engage students in weekly and monthly review.

**Long form, section headings pp. 13–19** — each principle plus its research claim, e.g. `[V]`: *"**Obtain a high success rate:** It is important for students to achieve a high success rate during classroom instruction."*

Rosenshine also prints a **17-item "Principles of Effective Instruction" sidebar (p. 19)** `[V]`, which he says *"offers slightly more detail."* It is **materially more mechanically tractable** — it adds *"Limit the amount of material students receive at one time," "Provide models of worked-out problems," "Ask students to explain what they have learned," "Provide many examples," "Reteach material when necessary."*

### Every number, with attribution and verification status

| Number | Verbatim | Verified? | Attribution |
|---|---|---|---|
| **80%** | *"the optimal success rate for fostering student achievement appears to be about **80 percent**. A success rate of 80 percent shows that students are learning the material, and it also shows that the students are challenged."* (p. 17) | ✅ `[V]` | ⚠️ **No inline citation.** Endnote 7 readings: **Anderson & Burns (1987)**, *RER* 57(2), 215–223 and **Frederiksen (1984)**, *RER* 54(3), 363–407 — both `[V]` in the endnotes |
| **82% / 73%** | *"**82 percent** of students' answers were correct in the classrooms of the most successful teachers, but the least successful teachers had a success rate of only **73 percent**"* (fourth-grade math) | ✅ `[V]` p. 17 | Attribution to Good & Grouws (1977) is `[UNV]` |
| **5–8 minutes** | *"they began their lessons with a **five- to eight-minute review** of previously covered material"* | ✅ `[V]` p. 13 | "The most effective teachers in the studies of classroom instruction" — unattributed |
| **8 minutes** | *"Teachers in the experiment were taught to spend **eight minutes** on review."* | ✅ `[V]` p. 13 | "a successful experiment in elementary school mathematics," **unnamed** — `[UNV]` |
| **9 questions / 40 min** | *"the least effective teachers asked only **nine questions in a 40-minute period**"* | ✅ `[V]` p. 14 | Evertson, Anderson, Anderson & Brophy (1980), *AERJ* 17(1), 43–60; Brophy & Good (1990) |
| **23 of 40 min / 11 min** | *"the most effective mathematics teachers spent about **23 minutes** of a 40-minute period in lecture, demonstration, questioning, and working examples"*; least effective spent **11** | ✅ `[V]` p. 14 | Evertson et al. (1980) |
| **4–6 main ideas; 2–4 details** | scaffold prompt wording | ✅ `[V]` p. 18 | **Explicitly attributed** — Berkowitz (1986), *Reading Research Quarterly* 21(2), 161–178 `[V]` in both texts |
| **30 seconds** | *"The optimal time for these contacts was **30 seconds or less**."* | ✅ `[V]` p. 19 | Rosenshine (2009); Slavin (1996) |
| Monday / 4th-Monday review | weekly + monthly review cadence | ✅ `[V]` p. 19 | Good & Grouws (1979); Kulik & Kulik (1979) |
| **"24 vs 8.6 questions / 50 min"** | — | ❌ **`[UNV]`** | Rosenshine & Stevens (1986), in Wittrock (ed.), *Handbook of Research on Teaching* 3rd ed., pp. 376–391. **Paywalled. Do not cite.** |

⚠️ **Stallings & Kaskowitz (1974) is NOT the source of the 80% claim.** It appears in the 2010 IAE reference list `[V]` but is attached to no number anywhere, and is **absent from the 2012 endnotes entirely.** Drop that attribution.

⚠️ **Citation hygiene bug in the primary sources themselves:** Good & Grouws (1979) pagination differs between the two Rosenshine texts — AFT endnote 3 gives *JEP* **71**(3), 355–362 (correct); the IAE reference list gives 71, 143–155 (wrong). Both `[V]` as printed.

---

## Findings — Bloom's revised taxonomy (Anderson & Krathwohl 2001)

### Sources

- **Krathwohl, D. R. (2002).** A Revision of Bloom's Taxonomy: An Overview. *Theory Into Practice* **41**(4), 212–218. DOI 10.1207/s15430421tip4104_2. Peer-reviewed.
  ERIC: https://eric.ed.gov/?id=EJ667155 · **free full text `[V]`:** https://cmapspublic2.ihmc.us/rid=1Q2PTM7HL-26LTFBX-9YN8/Krathwohl%202002.pdf
- **Book:** Anderson, L. W. (Ed.), Krathwohl, D. R. (Ed.), Airasian, P. W., Cruikshank, K. A., **Mayer, R. E.**, Pintrich, P. R., Raths, J., & Wittrock, M. C. (2001). *A Taxonomy for Learning, Teaching, and Assessing.* New York: Longman. `[V]` citation.

### The 2D taxonomy — all verbatim `[V]`

**Cognitive Process Dimension — 6 categories, 19 processes:**
- **1.0 Remember** — *"Retrieving relevant knowledge from long-term memory."* → Recognizing, Recalling
- **2.0 Understand** — *"Determining the meaning of instructional messages…"* → Interpreting, Exemplifying, Classifying, Summarizing, Inferring, Comparing, Explaining
- **3.0 Apply** — *"Carrying out or using a procedure in a given situation."* → Executing, Implementing
- **4.0 Analyze** — *"Breaking material into its constituent parts and detecting how the parts relate…"* → Differentiating, Organizing, Attributing
- **5.0 Evaluate** — *"Making judgments based on criteria and standards."* → Checking, Critiquing
- **6.0 Create** — *"Putting elements together to form a novel, coherent whole…"* → Generating, Planning, Producing

(2+7+2+3+2+3 = 19 ✓)

**Knowledge Dimension — 4 types, 11 subtypes:**
- **A. Factual** — terminology; specific details and elements
- **B. Conceptual** — classifications and categories; principles and generalizations; theories, models, and structures
- **C. Procedural** — subject-specific skills and algorithms; techniques and methods; criteria for determining when to use appropriate procedures
- **D. Metacognitive** — strategic knowledge; knowledge about cognitive tasks (contextual and conditional); self-knowledge

**The Taxonomy Table = 4 rows × 6 columns = 24 cells.** Classification rule, verbatim `[V]`: *"any objective could be classified… in one or more cells that correspond with the intersection of the column(s) appropriate for categorizing the **verb**(s) and the row(s) appropriate for categorizing the **noun**(s)."*

🔑 **Critical caveat, verbatim `[V]`:** *"the requirement of a strict hierarchy has been relaxed to allow the categories to overlap one another… some cognitive processes associated with Understand (e.g., **Explaining**) are more cognitively complex than at least one of the cognitive processes associated with Apply (e.g., **Executing**)."*

### Verb lists

| Claim | Source | Type | URL | Verified? |
|---|---|---|---|---|
| **The only verb list with a page-level primary citation** is the taxonomy's own **"alternative names" for the 19 processes, Anderson & Krathwohl 2001, pp. 67–68.** E.g. *interpreting (clarifying, paraphrasing, representing, translating)* · *inferring (concluding, extrapolating, interpolating, predicting)* · *organizing (finding coherence, integrating, outlining, parsing, structuring)* · *differentiating (discriminating, distinguishing, focusing, selecting)* · *checking (coordinating, detecting, monitoring, testing)*. | Reproduced with explicit page attribution in the Iowa State CELT "A Model of Learning Objectives" handout (Rex Heer, CC BY-NC-SA), Table 2 | ⚠️ **University teaching-centre document, NOT peer-reviewed** — but it is an adaptation of *specific book pages* rather than a folk list, which makes it the most defensible option available | https://eddl.tru.ca/wp-content/uploads/2019/07/EDDL5111_IowaStateRevisedBloomsHandout-1.pdf | ✅ `[V]` |
| ⚠️ **Dead or empty alternatives, all checked:** `cft.vanderbilt.edu/guides-sub-pages/blooms-taxonomy/` **301-redirects away — dead.** Iowa State CELT landing page **resolves but contains no verb list.** EIU's page **resolves but defers to a third-party PDF.** | | | | ✅ checked |
| 🔑 **Every free-floating "Bloom's verb list" online is non-peer-reviewed and they MUTUALLY CONTRADICT** — *classify*, *compare*, and *organize* are assigned to different levels by different teaching centres. **Freeze one lexicon; do not scrape.** | | | | — |

### Automated Bloom classification — the accuracy record

**Human reliability first, because it bounds everything:**

| Finding | Numbers | Source | Verified? |
|---|---|---|---|
| Pharmacy faculty classifying exam questions | **inter-rater reliability 0.25**; **accuracy 46.0%**; **collapsing 6 levels → 3 tiers raised accuracy to 81.8%.** Optimal grouping: *{Knowledge} / {Comprehension, Application} / {Analysis, Synthesis, Evaluation}* | Karpen & Welch (2016), *Currents in Pharmacy Teaching and Learning* 8(6), 885–888, DOI 10.1016/j.cptl.2016.08.003 · https://dc.etsu.edu/etsu-works/15008/ | ✅ `[V]` ⚠️ only six example questions rated |
| CS educators tagging **after a training tutorial** | **ICC(agreement) = 0.231; ICC(consistency) = 0.267**, p<.001, 8 raters × 42 questions. Verbatim: *"even with a training tutorial, our raters obtained only **poor** levels of agreement."* | Sanders et al., *The Canterbury QuestionBank*, ITiCSE-WGR 2013 · https://www.cs.mcgill.ca/~patitsas/publications/canterbury.pdf | ✅ `[V]` full PDF |

⚠️ **Correction to a widely-repeated number:** several sources report "Fleiss's κ = 0.189" for Sanders et al.'s Bloom tagging. **The paper explicitly states *"Fleiss's Kappa is inappropriate for ordinal data"* and reports ICC instead.** The 0.191 κ in that paper belongs to the **Block Model** tag, not Bloom. **Cite ICC 0.231/0.267.**

**Reported machine accuracies:**

| Study | Data | Method | Result | Verified? |
|---|---|---|---|---|
| Mohammed & Omar (2020), *PLOS ONE* 15(3):e0230442, open access · https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0230442 | DS1 = 141 q; DS2 = 600 q | TF-IDF → TFPOS-IDF → W2V-TFPOS-IDF × {kNN, LR, SVM} | DS1 weighted-F1: SVM 76.5 → 83.7 → **86.4**. DS2: SVM → **91.8** | ✅ `[V]` all 18 figures |
| Ifham et al., *IJEDICT* · https://files.eric.ed.gov/fulltext/EJ1413430.pdf | **16,584** expert-labelled questions (**largest in the literature**) | ANN+TF-IDF vs LSTM+GloVe vs LSTM+BERT | **LSTM+BERT 88.7%** (M=88.70, SD=0.82) > LSTM+GloVe (M=85.78, SD=1.04) | ✅ `[V]` |
| 🔑 **Kumar, Gulwani & Singh (2025), arXiv:2511.10903** · https://arxiv.org/abs/2511.10903 | 600 labelled sentences, 100/level | classical ML, RNNs, transformers, **zero-shot LLMs** | **GPT-4o-mini 0.73 · Gemini-1.5-Pro 0.72 · Claude-3.5-Haiku 0.58. SVM + synonym augmentation 0.94.** BERT 0.35–0.47 (overfits) | ✅ `[V]` full PDF |
| Zhang, Wong, Giacaman & Luxton-Reilly (2021), ACE, DOI 10.1145/3441636.3442305 | 504 expert-tagged Canterbury questions | BERT | "reasonable accuracy," **better at LOWER Bloom levels** | dataset `[V]`; accuracy `[UNV]` (ACM 403s) |
| Omar et al. (2012), 135 questions, F1 ≈ 77% · Yahya & Osman (2011), 190 questions, acc 87.4% / F1 44.64% | | rule-based / SVM | | `[UNV]` — secondary summaries only. The Yahya acc/F1 gap is a red flag |

🔑 **The direct answer to "can an LLM do this?": a frontier LLM zero-shot scores ~0.72–0.73 while a plain SVM on the same 600 sentences scores 0.94 — against a human gold standard whose own reliability is ICC ≈ 0.23–0.27.** Do not use an LLM as a 6-way Bloom classifier. Expect **asymmetric error exactly where you care most** (Zhang et al.: BERT better at lower levels).

**Cross-reference:** a much larger 2026 study (Wang et al., KDD '26, arXiv:2606.18257, 20,700 questions, 7–9B open-weight models — Helga's size band) found **automated Bloom assessment agreed with expert annotation just 46.58%** of the time, and **Bloom-level consistency of generated questions only 32–58%.** See `QUALITY_coverage_standards.md`.

---

## How this becomes an automated check

Target: `services/common/concept_doc.py` (section parser), gated from `services/core/course_builder.py`.

### Rosenshine → concrete rules

| # | Principle | Check | Warrant |
|---|---|---|---|
| 1 | Daily review | First ≤150 words of `Core Explanation` contains ≥1 explicit backward reference (`recall\|previously\|earlier\|as you (saw\|learned)`) **or** front-matter `prerequisites:` with ≥1 resolvable prior concept UID. Rosenshine's 5–8 min dosage ≈ **8–15% of word count**. | pp. 13 `[V]` |
| 2 | Small steps | No `##`/`###` subsection >250 words; no unbroken prose run >6 sentences without a heading, list, or example block. | p. 13; 17-item sidebar *"Limit the amount of material students receive at one time"* |
| 3 | Many questions | `?` count in `Socratic Hooks` **≥6** (target 8–12); require **≥2 process questions** (`how did\|why does\|explain how\|what would happen if\|walk through`). | p. 14 `[V]`. ⚠️ Do **not** justify a specific target with the 24/8.6 figure — it is unverified. |
| 4 | Provide models | `Worked Example` ≥3 enumerated steps, each with a rationale clause. | 17-item sidebar *"Provide models of worked-out problems"* |
| 5 | Guide practice | ≥1 faded / partially-completed item; ≥1 Socratic Hook sharing ≥2 content nouns with the `Worked Example`. | p. 15 |
| 6 | Check understanding | Ratio of `?`-bearing lines to `##` headings ≥ 1.0; blocklist generic checks (`any questions\|make sense\|got it`). | p. 16 |
| 7 | High success rate (~80%) | **Proxy: ≥80% of technical terms defined on first use**; cap new-term density at ≤1 per 80 words. ⚠️ **Document this explicitly AS A STAND-IN** — the original 80% is an oral-response rate with no clean textual analogue. | p. 17 `[V]`, no inline citation |
| 8 | Scaffolds | ≥1 explicit scaffold artifact (checklist, prompt stems, think-aloud markers: `first I\|then I ask myself\|I notice that`); **`Misconceptions` non-empty with ≥3 items** — this is the operationalization of *"anticipate students' errors and warn them."* | p. 18; Berkowitz (1986) 4–6 main ideas / 2–4 details is the one **explicitly attributed** scaffold number |
| 9 | Independent practice | ≥1 solo item whose key nouns ⊆ (`Worked Example` ∪ `Core Explanation`) nouns — Rosenshine explicitly flags mismatched independent practice as inappropriate. | p. 19 |
| 10 | Weekly/monthly review | **≥3 retrieval-ready atomic Q/A items** in front-matter (`review_items:`) for `fsrs_engine.py` to consume. | p. 19 |

### Bloom → concrete rules

| # | Rule | Warrant |
|---|---|---|
| 11 | 🔑 **Gate on 3 TIERS, not 6 levels.** `T1 = Remember` · `T2 = Understand + Apply` · `T3 = Analyze + Evaluate + Create`. Require **≥1 T1, ≥1 T2, ≥2 T3** in `Socratic Hooks`. **This is the defensible version of "≥3 levels including one above Apply."** | Karpen & Welch: 46.0% → **81.8%** on exactly this collapse |
| 12 | **Never gate a build on a 6-way label.** Report it as advisory metadata only. | Human ICC 0.231/0.267; LLM zero-shot 0.72–0.73 |
| 13 | **Do NOT implement `level(hook) > level(Apply)` as an integer comparison.** Use tier membership, and **document the non-monotonicity in a comment** so it isn't later "fixed" as a bug. | Krathwohl verbatim: Explaining > Executing |
| 14 | **Classify TWICE, on the 2D table:** head verb → cognitive process; head noun phrase → knowledge type. Require hooks to occupy **≥3 distinct CELLS**, not just ≥3 levels. This catches "3 different verbs, all about the same factual noun." | Krathwohl verbatim classification rule |
| 15 | **Frozen lexicon first, LLM last.** Build a frozen `verb → process → level` lexicon from A&K pp. 67–68 **only**; lint each hook's head verb against it and **flag out-of-lexicon verbs rather than guessing a level**. Order: lexicon → small SVM → LLM as tiebreak. **Log lexicon/LLM disagreement rate** as a doc-quality signal rather than trusting either. | Kumar et al.: SVM **0.94** vs LLM 0.73; teaching-centre lists mutually contradict |
| 16 | **Do NOT fine-tune a transformer on a few hundred labelled hooks.** BERT scored 0.35–0.47 on 600 samples (overfits). **Bag-of-words + SVM + synonym augmentation is the evidence-backed choice at that data scale.** | Kumar et al. `[V]` |
| 17 | **Warn when knowledge rows C (Procedural) and D (Metacognitive) are entirely unoccupied** — these are the rows LLM content leaves empty. Require ≥1 metacognitive hook (`how would you know\|what would you check\|when should you\|how confident are you`). | Krathwohl 4-row dimension; Bloom-coverage findings in `QUALITY_coverage_standards.md` |

---

## Confidence and gaps

**High confidence — primary PDFs fetched and read:** the full Rosenshine 2012 text including both the 10-principle short form, the long-form headings, the 17-item sidebar, and every number listed above with its page; Krathwohl (2002) full text including the 19 processes, 11 knowledge subtypes, the taxonomy-table classification rule, and the non-monotonicity caveat; Karpen & Welch; Sanders et al. full PDF; Mohammed & Omar all 18 figures; Kumar et al. full PDF; Ifham et al.

**Could NOT be sourced credibly — reported as gaps, not filled:**
- **Rosenshine & Stevens (1986) "24 vs 8.6 questions"** and the "6 vs 1.3 process questions" pair — paywalled book chapter, no open copy. **The commonly-quoted figure is not in the 2012 article.**
- **The attribution of Rosenshine's 82%/73% to Good & Grouws (1977)** — the article gives no inline citation.
- **The source of the 8-minute review experiment** — the article calls it only "a successful experiment in elementary school mathematics."
- **The 80% success-rate claim has NO inline citation** in either Rosenshine text. Endnote *readings* exist (Anderson & Burns 1987; Frederiksen 1984) but are not presented as the source of the number. **Do not attribute the 80% to Stallings & Kaskowitz** — that attribution is circulating and is wrong.
- **Anderson & Krathwohl (2001) pp. 67–68 verb list** could only be verified through a university teaching-centre adaptation (⚠️ non-peer-reviewed), not against the book itself. It is the best available option *because* it cites specific pages, but flag it as an adaptation.
- **Zhang et al. (2021) ACE accuracy figures** — ACM 403s.
- **Omar et al. (2012) and Yahya & Osman (2011)** figures — secondary summaries only.

**Two source-quality notes worth keeping:**
- The IBE/IAE URL **printed inside the 2012 article itself is dead (404)**, and the IAOED mirror has a cert failure. Use the formapex.com mirror or the AFT PDF.
- The two Rosenshine texts **disagree with each other** on the Good & Grouws (1979) page range. The AFT endnote is correct.
