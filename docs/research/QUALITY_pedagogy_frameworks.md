# Pedagogy frameworks as checkable criteria: CLT, Mayer, desirable difficulties, refutation text, analogy

Research date: 2026-08-07. Web research only; no code was changed.

**Verification key:** `[V]` = the page/PDF was fetched and the claim read in it · `[V-2°]` = verified in a fetched peer-reviewed source *citing* the primary · `[UNV]` = could not confirm on any fetched page, **do not publish as fact** · `⚠️` = non-peer-reviewed host or source.

---

## ⚠️ Read this first: five corrections that change what to build

These were premises in the original brief that the literature contradicts. Each would have shipped a rule the evidence does not support.

1. **Mayer's redundancy principle is NOT "don't repeat yourself."** It is *graphics + narration* vs *graphics + narration + identical on-screen text*. It requires simultaneous audio and visual streams, so a markdown file cannot instantiate it. It is also now Mayer's **weakest** principle (median *d* = **0.10**), and Adesope & Nesbit (2012) found adding **text to audio actually helps** (g = 0.29). **Justify any anti-repetition check under *coherence*, or under Sweller's separate redundancy effect — never Mayer's.**
2. **Interleaving fails on exactly Helga's material class.** Brunmair & Richter (2019): overall g = 0.42, but **words g = −0.39 (blocking wins)** and **expository texts non-significant**. A generic "must interleave" check is not supported.
3. **Cepeda's optimal spacing gap is not a flat 10–20%.** It is **~20% of the retention interval at week-scale, declining to ~5% at one year.** Observed ratios: 14%, 31%, 30%, 6% for 7/35/70/350-day intervals.
4. **A retrieval question without an answer is worth nothing.** Rowland (2014): no feedback + ≤50% initial accuracy → **g = 0.03, CI [−0.21, 0.27]**. With feedback: **g = 0.73.**
5. **Mayer's published medians run 2–3× the independent pooled estimates.** Mayer's per-principle medians are 0.67–1.35; Noetel et al. (2022) pool to **g = 0.38**, and Cromley & Chen (2025) re-meta-analyzing **Mayer's own corpus** get **g = 0.37**.

🔑 **Cross-cutting consequence.** Given the logged HelgaBench noise floor of **±1.4/5 between identical runs**, a design effect of g ≈ 0.3–0.4 is **below an LLM judge's resolution**. Every check below is specified to be **mechanical** (parse / count / regex / threshold), not judged. That is not a style preference — it is the only way effects this size are observable in Helga's pipeline.

---

## Findings — Cognitive Load Theory (Sweller)

| Claim / number | Source name | Type | URL | Verified? |
|---|---|---|---|---|
| **Worked-example effect origin.** Five experiments, algebra. | Sweller & Cooper (1985), *Cognition and Instruction* 2(1), 59–89, DOI 10.1207/s1532690xci0201_3 | Peer-reviewed | https://www.tandfonline.com/doi/abs/10.1207/s1532690xci0201_3 | ✅ citation `[V]`; **the "6× longer / 1/5 the errors" figures are `[UNV]`** — paper fully paywalled, all circulating sources are secondary. **Do not cite them.** |
| **Use this instead — modern, verified magnitude: g = 0.48, 95% CI [0.36, 0.60]**, p=0.01, I²=93.72%, from **8,033 abstracts screened → 43 articles / 55 studies / 181 effect sizes**. Trim-and-fill adjusted **g = 0.44**. | Barbieri, Miller-Cotto, Clerjuste & Chawla (2023), *Educational Psychology Review* 35, Art. 11, DOI 10.1007/s10648-023-09745-1 | Peer-reviewed | https://www.danamillercotto.com/uploads/4/7/7/2/47725475/barbieri_et_al__2023__we_meta-analysis.pdf | ✅ `[V]` full PDF |
| 🔑 **Counterintuitive moderator:** correct examples alone **β = +0.26 (p=0.027)**; **pairing with self-explanation prompts β = −0.24 (p=0.042) — NEGATIVE.** Timing n.s. | same | Peer-reviewed | same | ✅ `[V]` — **collides with Fiorella & Mayer's self-explaining d=0.61. Do not make "worked example + self-explanation prompt" a scored requirement.** |
| **Expertise reversal, verbatim:** *"Instructional techniques that are highly effective with inexperienced learners can lose their effectiveness and even have negative consequences when used with more experienced learners."* When worked examples HURT: *"When a problem can be solved relatively effortlessly, analyzing a redundant worked example… may impose a greater cognitive load than problem solving."* | Kalyuga, Ayres, Chandler & Sweller (2003), *Educational Psychologist* 38(1), 23–31 | Peer-reviewed | https://ro.uow.edu.au/articles/journal_contribution/The_expertise_reversal_effect/27724950 (free full text) | ✅ `[V]` |
| **The documented crossover:** inexperienced trainees did better with worked examples; *"With more experience… the superiority of worked examples disappeared. Eventually, with sufficient experience, additional learning was facilitated more by problem solving."* | same | Peer-reviewed | same | ✅ `[V]` |
| 🔑 **The concrete BACKWARD-fading schedule** (3-step problems, 4 tasks): Task 1 = all 3 worked → Task 2 = steps 1,2 worked, **step 3 omitted** → Task 3 = step 1 worked, **steps 2,3 omitted** → Task 4 = all omitted. **Fade from the LAST step backward, never the first.** Results: near transfer F(1,73)=4.50, **f=.23**; far transfer F(1,73)=5.99, **f=.27**; anticipation accuracy **f=.33**. Self-explanation prompts had an independent main effect with **no interaction**. | Atkinson, Renkl & Merrill (2003), *J. Educational Psychology* 95(4), 774–783, DOI 10.1037/0022-0663.95.4.774 | Peer-reviewed | ⚠️ https://mrbartonmaths.com/resourcesnew/8.%20Research/Making%20the%20most%20of%20examples/Fading%20out%20and%20Prompts.pdf (mirror; cite the DOI) | ✅ `[V]` Table 1 extracted |
| **Completion problems, verbatim:** *"worked examples are completion problems with a complete solution and conventional problems are completion problems with a partial solution."* | Sweller, van Merriënboer & Paas (2019); origin van Merriënboer & Krammer (1987), *Instructional Science* 16, 251–285 | Peer-reviewed | see 2019 row below | ✅ `[V]` |
| **Split-attention, verbatim:** *"Replace multiple sources of information, distributed either in space or time, with one integrated source."* **Redundancy, verbatim:** *"Replace multiple sources of information that are **self-contained**… with one source."* 🔑 **The discriminator is self-containedness:** complementary-and-unintelligible-alone → integrate; self-contained-and-duplicative → **delete**. A checker must not conflate them. | Sweller, van Merriënboer & Paas (2019) Table 1; origins Tarmizi & Sweller (1988), Chandler & Sweller (1991) | Peer-reviewed | https://doi.org/10.1007/s10648-019-09465-5 · ⚠️ full PDF mirror https://leadinglearner.me/wp-content/uploads/2019/02/sweller2019_article_cognitivearchitectureandinstru.pdf | ✅ `[V]` Table 1 extracted in full |
| **Element interactivity IS operationally measurable**, verbatim: *"The only metric currently available… is to **count the number of assumed interacting elements**."* Worked counts: algebra `a/b = c` solve-for-*a* ≈ **28** elements for a novice; composite-area geometry ≈ **25**; puzzle-poem ≈ **14**. | Chen, Paas & Sweller (2023), *Educational Psychology Review* 35, Art. 63, DOI 10.1007/s10648-023-09782-w | Peer-reviewed, **open access** | https://link.springer.com/article/10.1007/s10648-023-09782-w | ✅ `[V]` |
| 🔑 **The honest limit, verbatim:** *"The effects of relatively **small** differences in element interactivity… are not likely to be visible. In contrast, the effects of **very large** differences… can be readily demonstrated."* **Gate only on large differences.** Also: *"For an expert, the same squiggles may constitute only a single element… measures that ignore knowledge when determining complexity are largely useless."* | same + Sweller 2019 | Peer-reviewed | same | ✅ `[V]` |
| 🔑 **Five of the 17 catalogued CLT effects are "compound"** — element interactivity, expertise reversal, guidance-fading, transient information, self-management. Verbatim: *"Compound effects frequently indicate **the limits of other cognitive load effects**."* **A third of CLT is about when the rules STOP applying.** Any flat checklist misreads the theory. | Sweller, van Merriënboer & Paas (2019) Table 1 | Peer-reviewed | as above | ✅ `[V]` |

**Applicable to a static text doc:** worked example, completion problem, split attention, redundancy, self-explanation, isolated elements, element interactivity, expertise reversal, guidance fading, variability.
**NOT applicable:** modality, transient information, human movement, collective working memory, imagination.

---

## Findings — Mayer's multimedia principles

**Mayer's own medians** — Mayer (2024), *Educational Psychology Review* 36, Art. 8, **CC-BY open access**, Table 6 (reproduces the *Multimedia Learning* 3rd ed. table). https://doi.org/10.1007/s10648-023-09842-1 ✅ `[V]`

| Group | Principle | median *d* | *k* |
|---|---|---|---|
| Reduce extraneous | Coherence | **0.86** | 19 |
| | Signaling | **0.69** | 16 |
| | Redundancy | **0.10** | 12 |
| | Spatial contiguity | **0.82** | 9 |
| | Temporal contiguity | **1.31** | 8 |
| Manage essential | Segmenting | **0.67** | 7 |
| | Pretraining | **0.78** | 10 |
| | Modality | **1.00** | 19 |
| | Multimedia | **1.35** | 13 |
| Foster generative | Personalization | **1.00** | 15 |
| | Voice | 0.74 | 7 |
| | Image | 0.20 | 7 |
| | Embodiment | 0.58 | 17 |
| | Immersion | **−0.10** | 9 |
| | Generative activity | **0.71** | 44 |

🔑 **Use these independent numbers to weight any rubric.** Noetel et al. (2022), *Review of Educational Research* 92(3), 413–454, DOI 10.3102/00346543211052329 — **29 reviews, 1,189 studies, 78,177 participants. Overall pooled g = 0.38 [0.27, 0.49], k = 808.** https://osf.io/preprints/psyarxiv/pynzr/ ✅ `[V]`

| Principle | pooled g | 95% CI | k |
|---|---|---|---|
| Contiguity (combined) | 0.74 | [0.67, 0.82] | 46 |
| — spatial contiguity | 0.63 | [0.55, 0.71] | 58 |
| **Signaling** | **0.43** | [0.35, 0.50] | **209** |
| Modality | 0.38 → **0.20** bias-corrected | [0.33, 0.43] | 86 |
| Segmentation | 0.34 | [0.30, 0.38] | 123 |
| Personalisation | 0.33 | [0.23, 0.44] | 55 |
| Coherence (seductive details) | 0.33 | [0.18, 0.48] | 68 |
| Verbal redundancy | 0.15 | [0.08, 0.22] | 57 |

**Moderators `[V]`:** design quality matters far more for **complex** material (**g = 0.70 vs 0.20 simple**) and **system-paced** delivery (0.41 vs 0.27); **learner prior knowledge did NOT moderate** (p = 0.14).
**Two text-critical sub-findings `[V]`:** Schneider (2018) — **text signaling improved retention and transfer MORE than graphic signaling**; Rey (2019) — **instructor-imposed segmentation g = 0.41 (k=32) beat learner-controlled g = 0.20 (k=32)**.
**Cromley & Chen (2025)**, *Educational Research Review* 49, DOI 10.1016/j.edurev.2025.100730 — meta-analysis of **Mayer's own corpus**, 92 articles / 181 studies / 591 effects: **overall g = 0.37** `[V]` via NSF PAR 10637927.

**Generative decomposition** — Fiorella & Mayer (2016), *EPR* 28(4), 717–741, Table 1 `[V]`: matrix organizer **1.07** · teaching **0.77** · imagining 0.65 · **self-explaining 0.61 (44/54)** · concept map 0.62 · **self-testing 0.57 (70/76)** · enacting 0.51 · summarizing 0.50 · drawing 0.40.

**TEXT-APPLICABLE (6 + 1 partial):** coherence · signaling · segmenting · pre-training · personalization · generative activity · *spatial contiguity only via the split-attention generalization — label it as such; Mayer's d=0.82 does not support a prose-distance rule.*
**MULTIMEDIA-ONLY (8) — no check possible:** multimedia · **redundancy** · temporal contiguity · modality · voice · image · embodiment · immersion.

🔑 **The architectural ceiling this implies is recorded in the synthesis, and it is the single most important finding in this file.** Mayer's largest effect is the **multimedia principle, d = 1.35, 13/13 tests** — words *and pictures*. A text-only doc forfeits it, plus modality (1.00) and temporal contiguity (1.31). The seven text-applicable principles pool to g ≈ 0.33–0.43.

---

## Findings — desirable difficulties (Bjork)

| Claim / number | Source | Type | URL | Verified? |
|---|---|---|---|---|
| **Storage vs retrieval strength, verbatim:** *"current performance is entirely a function of current retrieval strength, but **storage strength acts to retard the loss (forgetting) and enhance the gain (relearning) of retrieval strength**."* Their four desirable difficulties: varying conditions · interleaving · spacing · **using tests, rather than presentations, as study events**. Boundary condition: desirable only if the learner *"[has] the background knowledge or skills to respond to them successfully."* | Bjork & Bjork (2011), in *Psychology and the Real World*, Ch. 5, pp. 56–64 | Book chapter | https://bjorklab.psych.ucla.edu/wp-content/uploads/sites/13/2016/11/Making-Things-Hard-on-Yourself-but-in-a-Good-Way-20111.pdf | ✅ `[V]` p. 58 |
| **New theory of disuse** — Bjork & Bjork (1992), in *From Learning Processes to Cognitive Processes*, Vol. 2, pp. 35–67, Erlbaum. | | Book chapter | ⚠️ **No working full-text URL exists.** The UCLA Bjork Lab site does not host it. | citation `[V]`; text `[UNV]` |
| **Testing effect, Roediger & Karpicke (2006) Exp. 1** (N=120, within-subjects): 5 min **81% study-study vs 75% study-test (d=0.52, study WINS)** · 2 days **54% vs 68% (d=0.95)** · 1 week **42% vs 56% (d=0.83)**. **Exp. 2** (N=180): 5 min SSSS 83% / STTT 71%; 1 week **SSSS 40% / STTT 61%, d=1.26**. Proportional forgetting over the week: SSSS **52%**, STTT **14%**. | *Psychological Science* 17(3), 249–255, DOI 10.1111/j.1467-9280.2006.01693.x | Peer-reviewed | ⚠️ WUSTL PDF host has an **expired TLS cert** — cite the DOI | ✅ `[V]` |
| 🔑 **The fluency trap, same study:** SSSS students read the passage **14.2 times** vs STTT **3.4 times** — more exposure, worse retention — and **SSSS predicted 4.8/7 vs STTT 4.0/7. The group that did worst was most confident.** | same | Peer-reviewed | same | ✅ `[V]` |
| **Rowland (2014) meta-analysis: overall g = 0.50 [0.42, 0.58], k = 159 effects from 61 studies**, I²=84.35, 81% of effects positive. | *Psychological Bulletin* 140(6), 1432–1463, DOI 10.1037/a0037559 | Peer-reviewed | https://courseware.epfl.ch/assets/courseware/v1/fdde2f0aa590bf3b1324077a6bf1540c/asset-v1:EPFL+DEMO+2020+type@asset+block/Rowland2014-meta-analysis.pdf | ✅ `[V]` full PDF |
| 🔑 **Rowland moderators — the most actionable table in this file.** Feedback: **no g=0.39 (k=107) vs yes g=0.73 (k=52)**. Retention interval: <1 day 0.41 vs ≥1 day **0.69**. Initial test type: **cued recall 0.61 (k=104) vs recognition 0.29 (k=19)**. **Retrievability × reexposure: no feedback + ≤50% correct → g = 0.03, CI [−0.21, 0.27] (k=17).** | same | Peer-reviewed | same | ✅ `[V]` |
| **Better-verified alternatives to Adesope:** Yang, Luo, Vadillo, Yu & Shanks (2021), *Psych. Bulletin* 147(4), 399–435 — **g = 0.499 from 222 studies, 48,478 students**. Pan & Rickard (2018), *Psych. Bulletin* 144(7), 710–756 — transfer **d = 0.40 [0.31, 0.50]**, 192 effects / N=10,382. | | Peer-reviewed | https://pubmed.ncbi.nlm.nih.gov/33683913/ · https://pubmed.ncbi.nlm.nih.gov/29733621/ | ✅ `[V]` |
| 🔑 **Pan & Rickard: transfer is strongest for application/inference questions and WEAKEST for "untested materials from initial study."** Retrieval practice does **not** spill over to untested material in the same document. Questions must cover what you want retained. | same | Peer-reviewed | same | ✅ `[V]` |
| ⚠️ **Adesope, Trevisan & Sundararajan (2017)**, *RER* 87(3), 659–701 — **closed access, Unpaywall `is_oa: false`, ERIC abstract has no effect sizes.** Overall **g = 0.61** is `[UNV]`. The MC-vs-short-answer figures were found only on an ⚠️ advocacy site. **Adesope (MC > short-answer) and Rowland (recall > recognition) DISAGREE — do not build a check assuming either direction is settled.** | | | https://eric.ed.gov/?id=EJ1141817 | `[UNV]` for numbers |
| **Spacing: massed 36.7% vs spaced 47.3% — a 10.6 percentage-point advantage**, t(540)=6.6, p<.001. **Only 12 of 271 comparisons** showed no or negative effect (95.6% favored spacing). Scope: 839 assessments / 317 experiments / 184 articles. ⚠️ **The commonly-quoted "9%" is specifically for retention intervals under 1 minute** — the overall figure is 10.6 points. | Cepeda, Pashler, Vul, Wixted & Rohrer (2006), *Psych. Bulletin* 132(3), 354–380 | Peer-reviewed | https://augmentingcognition.com/assets/Cepeda2006.pdf | ✅ `[V]` |
| 🔑 **Optimal gap SHRINKS with the horizon, verbatim:** *"The optimum gap value was about **20% of the test delay for delays of a few weeks, falling to about 5% when delay was one year**."* Observed optimal gaps for RIs of 7/35/70/350 days = **1/11/21/21 days** → ratios **14%, 31%, 30%, 6%**. At optimal vs zero gap: **+64% recall, d = 1.1.** N=1,354. | Cepeda, Vul, Rohrer, Wixted & Pashler (2008), *Psych. Science* 19(11), 1095–1102 | Peer-reviewed | ⚠️ https://files.eric.ed.gov/fulltext/ED505660.pdf is the **author "in press" manuscript**, not the copy of record | ✅ `[V]` |
| 🔑 **Directly relevant to `fsrs_engine.py`:** spaced vs massed retrieval practice **g = 0.74** (39 effects), but **expanding vs uniform schedules g = 0.034, NOT SIGNIFICANT** (54 effects). **The expanding-interval assumption has no meta-analytic support on its own.** | Latimier, Peyre & Ramus (2021), *EPR* 33(3), 959–987, DOI 10.1007/s10648-020-09572-8 | Peer-reviewed | https://eric.ed.gov/?id=EJ1310148 | ✅ `[V]` |
| **Real-course effects are much smaller than lab estimates.** Mathematics: spacing **g = 0.28** (27 studies); isolated material 0.43 vs **course-embedded 0.24**; **testing vs restudy g = 0.18 with CI crossing zero.** | Murray, Horner & Göbel (2025), *EPR* 37, Art. 75 | Peer-reviewed | https://eric.ed.gov/?id=EJ1478558 | ✅ `[V]` |
| 🔑 **Interleaving — the qualifier that kills the generic rule.** 59 studies, 238 effect sizes. Overall **g = 0.42**, but by material: **paintings 0.67 · math 0.34 · words −0.39 (blocking wins) · expository texts NON-SIGNIFICANT.** Moderator, verbatim: stronger effects for material *"more similar between categories… less similar within categories… more complex."* Authors' own caution: *"should be used with caution… **especially for expository texts and words**."* | Brunmair & Richter (2019), *Psych. Bulletin* 145(11), 1029–1052 | Peer-reviewed | ⚠️ author accepted manuscript: https://www.psychologie.uni-wuerzburg.de/fileadmin/06020400/2019/Brunmair_Richter_in_press__2019_META-ANALYSIS_OF_INTERLEAVED_LEARNING.pdf | ✅ `[V]` |
| **Clean verified interleaving substitute** (the famous Rohrer & Taylor 2007 "63% vs 20%" is `[V-2°]` only): grade 7, n=140, nine weeks, unannounced test two weeks later — **72% vs 38%, d = 1.05.** | Rohrer, Dedrick & Burgess (2014), *Psychonomic Bulletin & Review*, DOI 10.3758/s13423-014-0588-3 | Peer-reviewed | https://gwern.net/doc/psychology/spaced-repetition/2014-rohrer.pdf | ✅ `[V]` |
| **Generation effect: overall d = .40 [.38, .42], 445 effect sizes / 86 studies / N = 17,711.** By rule: **calculation .92 · sentence completion .60 · rhyme .46 · association .32 · ANAGRAM −.05.** By stimulus: numbers .87 · words .41 · **nonwords .05**. By delay: **.32 (<1 min) → .64 (>1 day)**. Incidental .65 vs intentional .32. | Bertsch, Pesta, Wiscott & McDaniel (2007), *Memory & Cognition* 35(2), 201–210 | Peer-reviewed | https://mcdaniel97.github.io/Publications/Bertsch%20et%20al.%202007.pdf | ✅ `[V]` Table 1 |
| 🔑 **Anagram/scramble and nonword generation are worth NOTHING (d = −.05, .05). Surface-level manipulation is not a desirable difficulty; SEMANTIC generation is.** | same | Peer-reviewed | same | ✅ `[V]` |
| 🔑 **Pretesting — the cheapest structural change available to a text doc.** Five experiments with **total time equated**. Exp. 1: pretest accuracy **5%**; posttest **75% vs 56%, d = 1.1**; within-condition pretested vs non-pretested items **75% vs 50%, d = 1.7** *(excluding any item answered correctly on the pretest)*. Exp. 2 (attention equated by italicizing key sentences in both): **71% vs 54%, d = 0.61.** **Testing did not harm untested items.** | Richland, Kornell & Kao (2009), *JEP: Applied* 15(3), 243–257 | Peer-reviewed | https://learninglab.uchicago.edu/Pre-Testing_files/RichlandKornellKao.pdf | ✅ `[V]` |
| **Even guaranteed-to-fail retrieval helps**, verbatim: *"Unsuccessful retrieval attempts enhanced learning with both types of materials."* Six experiments. | Kornell, Hays & Bjork (2009), *JEP: LMC* 35(4), 989–998 | Peer-reviewed | https://web.williams.edu/Psychology/Faculty/Kornell/Publications/Kornell.Hays.Bjork.2009.pdf | ✅ `[V]` |
| 🔑 **The utility ranking to weight all checks by.** **HIGH: practice testing, distributed practice. MODERATE: elaborative interrogation, self-explanation, interleaved practice. LOW: summarization, highlighting, keyword mnemonic, imagery for text, rereading.** | Dunlosky, Rawson, Marsh, Nathan & Willingham (2013), *Psychological Science in the Public Interest* 14(1), 4–58, DOI 10.1177/1529100612453266 | Peer-reviewed | https://www.whz.de/fileadmin/lehre/hochschuldidaktik/docs/dunloskiimprovingstudentlearning.pdf | ✅ `[V]` Table 4 |

---

## Findings — misconceptions / refutation text

| Claim / number | Source | Type | URL | Verified? |
|---|---|---|---|---|
| **Definition, verbatim:** *"Refutation texts are those that provide an explicit statement of a commonly held misconception followed by a direct refutation of that misconception."* | Sinatra & Broughton (2011), *Reading Research Quarterly* 46(4), 374–393, DOI 10.1002/RRQ.005 | Peer-reviewed | https://rossierapps.usc.edu/facultydirectory/publications/35/Sinatra_Broughton_RRQ.pdf | ✅ `[V]` |
| 🔑 **The worked 3-part example — the citable structural rule, verbatim:** *"'Some people think that seasons change because the Earth is closer to the sun in summer…' A direct refutation of this misconception and an explicit statement of the current scientific explanation would then follow… the text would go on to explain, '**However, this is not the case. Rather, it is the tilt of the Earth's axis** that causes the seasons to change.' The text would then likely go on to further explain the phenomenon."* | same | Peer-reviewed | same | ✅ `[V]` |
| **Why the misconception must be STATED, verbatim:** *"The **coactivation** of the misconception with the scientifically correct concept increases the likelihood that the reader will notice the discrepancy between the two, which in turn facilitates conceptual change."* | same | Peer-reviewed | same | ✅ `[V]` |
| 🔑 **The citable magnitude: g = 0.41, p < .001, from 44 independent comparisons, n = 3,869**; *"consistent and robust across a wide variety of contexts."* | Schroeder & Kucera (2022), *Educational Psychology Review* 34(2), 957–987, DOI 10.1007/s10648-021-09656-z | Peer-reviewed | https://eric.ed.gov/?id=EJ1334754 | ✅ `[V]` |
| **Largest review: 71 articles / 76 studies / 111 samples / 294 effect sizes / 26 moderators**; direction is *"a consistent and statistically significant advantage of refutation texts"*; **moderators neither enhanced nor diminished the impact.** ⚠️ **Pooled g is `[UNV]`** (T&F 403s) — cite direction + counts, not a magnitude. | Danielson et al. (2025), *Educational Psychologist* 60(1), 23–47 | Peer-reviewed | https://eric.ed.gov/?id=EJ1456277 | counts `[V]`, g `[UNV]` |
| 🔑 **The backfire question — restating a misconception once is SAFE.** Verbatim: *"overall there was substantial evidence against familiarity backfire… **it is safe to repeat misinformation when correcting it**, even when the audience might be unfamiliar with the misinformation."* Three experiments, **total N = 1,718**. Replicated: *"no backfire effects were observed"* (N=380, PLOS ONE 2023, DOI 10.1371/journal.pone.0281140). | Ecker, Lewandowsky & Chadwick (2020), *Cognitive Research: Principles and Implications* 5(1), Art. 41, DOI 10.1186/s41235-020-00241-6 | Peer-reviewed | https://research-repository.uwa.edu.au/en/publications/can-corrections-spread-misinformation-to-new-audiences-testing-fo/ | ✅ `[V]` |
| ⚠️ **Debunking Handbook 2020** structure: **FACT (truth first) → MYTH → FALLACY → FACT (truth again)**. *"One repetition of the myth is beneficial to belief updating"* but *"needless repetitions of the misinformation should be avoided."* → **exactly one restatement per entry.** | | ⚠️ Practitioner handbook, not peer-reviewed | via reproduction | `[V]` on a ⚠️ host |
| **Posner's four conditions for conceptual change, verbatim (p. 214):** 1. *"There must be **dissatisfaction** with existing conceptions."* 2. *"A new conception must be **intelligible**"* — and notably *"Writers often stress the importance of **analogies and metaphors** in lending initial meaning and intelligibility."* 3. *"must appear initially **plausible**."* 4. *"should suggest the possibility of a **fruitful research program**."* | Posner, Strike, Hewson & Gertzog (1982), *Science Education* 66(2), 211–227, DOI 10.1002/sce.3730660207 | Peer-reviewed | https://eclass.uoa.gr/modules/document/file.php/PHS122/%CE%91%CF%81%CE%B8%CF%81%CE%B1/Posner_Strike_Hewson_Gertzog.pdf | ✅ `[V]` |
| ⚠️ **Tippett (2010)** *IJSMTE* 8(6), 951–970: *"reading refutation text rather than traditional expository text is more likely to result in conceptual change"*; **no developmental pattern emerged.** The oft-repeated "22 studies" count is `[UNV]`. ⚠️ A blog claims Tippett found grades 3–10 most responsive; **ERIC says the opposite. Trust ERIC.** | | Peer-reviewed | https://eric.ed.gov/?id=EJ905216 | direction `[V]`, count `[UNV]` |
| ⚠️ **Guzzetti et al. (1993)** *RRQ* 28(2): the qualitative claim *"effective procedures had a common element of producing conceptual conflict"* is `[V]` / `[V-2°]`. **ALL EFFECT SIZES ARE `[UNV]` — not obtainable at any route; RRQ 1993 is fully paywalled. Do not publish a Guzzetti effect size.** Page range also conflicts between sources (ERIC 116–159 vs Sinatra's reference list 117–155). | | Peer-reviewed | https://eric.ed.gov/?id=EJ462258 | `[UNV]` for numbers |

---

## Findings — analogy quality

| Claim | Source | Type | URL | Verified? |
|---|---|---|---|---|
| **Systematicity, verbatim:** *"A predicate that belongs to a mappable system of mutually interconnecting relationships is more likely to be imported into the target than is an isolated predicate."* (Gentner 1983: 163) | Gentner (1983), *Cognitive Science* 7(2), 155–170 | Peer-reviewed | ⚠️ **No clean fetchable copy of the 1983 original exists** — matt.colorado.edu refuses connections, Northwestern's PDF is a scan with unusable OCR, archive.org 404s, Wiley paywalled | `[V-2°]` via https://plato.stanford.edu/entries/reasoning-analogy/ — **flag the page-163 attribution as secondhand** |
| 🔑 **Fully verifiable substitute**, all verbatim: *"**Parallel connectivity** requires that matching relations must have matching arguments, and **one-to-one correspondence** limits any element in one representation to at most one matching element in the other."* · **Relational focus:** *"analogies must involve common relations but need not involve common object descriptions (e.g., it does not detract from the analogy that the planet does not look like a boat)."* · *"candidate inferences… **are only guesses: Their factual correctness must be checked separately.** … Any process capable of producing novel true inferences is also capable of generating **false** inferences."* | Gentner & Markman (1997), *American Psychologist* 52(1), 45–56 | Peer-reviewed | https://courses.csail.mit.edu/6.803/pdf/gentner.pdf | ✅ `[V]` |
| 🔑 **Glynn's Teaching-With-Analogies, six steps, verbatim (p. 118):** 1. Introduce the target concept. 2. Remind students of the analog concept. 3. Identify relevant features of both. 4. Connect (map) the similar features. **5. Indicate where the analogy between the laws breaks down.** 6. Draw conclusions. **Step 5 is the single strongest citable basis for a mandatory "where this breaks down" clause.** | Glynn (2008), *Making Science Concepts Meaningful to Students*, pp. 113–125 | Book chapter | http://osu-wams-blogs-uploads.s3.amazonaws.com/blogs.dir/548/files/2010/10/Glynn2008MakingScienceConceptsMeaningful.pdf | ✅ `[V]` |
| **Glynn's warning, verbatim (p. 117):** *"It is risky to use analogies without thinking about them. If used effectively, they can enhance learning… **if used ineffectively, they can hinder learning by causing misconceptions**."* And p. 118: teachers should *"ask focused questions about **features that are not shared**."* | same | Book chapter | same | ✅ `[V]` |
| **FAR guide** — two directly checkable elements: ACTION/LIKES asks *"**Are the ideas surface features or deep relation?**"* and ACTION/UNLIKES says *"**Discuss ways in which the analog is unlike the target**."* REFLECTION is a teacher post-hoc step — **not automatable; do not invent a check for it.** | Treagust, Harrison & Venville (1998), *JSTE* 9(2), 85–101 — **original `[UNV]`, no open copy** | Peer-reviewed | Table reproduced verbatim in ⚠️ https://iopscience.iop.org/article/10.1088/1742-6596/1233/1/012022/pdf | table `[V]` on a ⚠️ secondary host |
| 🔑 **Analogies actively create misconceptions**, all verbatim: *"**Misleading or confusing analogies… can be more than just a waste of class time; they can interfere with students' learning**."* · *"students may not have enough information about the target concept to understand those limitations… they may either **accept the analogical explanation as a statement of reality** about the target concept or **incorrectly apply the analogy by taking it too far**."* · *"**When students inappropriately apply irrelevant concepts from the analog domain to the target domain, they can develop misconceptions… The misconceptions that are developed as the result of an analogy can be difficult to remedy**."* | Orgill & Bodner (2004), *Chemistry Education: Research and Practice* 5(1), 15–32 | Peer-reviewed | https://chemed.chem.purdue.edu/chemed/bodnergroup/PDF_2008/77%20Orgill%20CERP.pdf | ✅ `[V]` |
| ⚠️ Harrison & Treagust "multiple analogies are not a safeguard" / "double-edged sword" claims surfaced only in search summaries. `[UNV]` — **do not quote.** | | | | `[UNV]` |

---

## How this becomes an automated check

Concrete, mapped to this repo. Target: `services/common/concept_doc.py` (section parser) with gates invoked from `services/core/course_builder.py` (ContentHydrator), plus scheduler rules in `services/core/fsrs_engine.py`.

### Tier 1 — hard fails (highest-warrant, all mechanical)

| # | Rule | Warrant |
|---|---|---|
| 1 | **Every question carries its answer.** For each `?` in `Socratic Hooks`, assert a sibling `A:` or `<details>` block. **Fail the doc if any question lacks one.** | Rowland: no feedback + low retrievability → **g = 0.03, CI crosses zero**. This is the single highest-value check in the file. |
| 2 | **`Worked Example` ≥3 enumerated steps, each with a rationale clause** (`because\|since\|so that\|this gives`). A single-line answer fails. | Barbieri g = 0.48; Rosenshine "provide models" |
| 3 | **Every `Misconceptions` entry is 3-of-3:** (a) attribution stem `/\b(some people\|many students\|a common misconception\|you might think\|students often)\b/i`, (b) refutation cue `/\b(however\|but in fact\|actually)\b[^.]{0,80}\b(not (the case\|correct\|true)\|incorrect\|mistaken)\b/i`, (c) ≥1 post-cue sentence with a causal connective. **Fail on 2-of-3.** Assert token order misconception → cue → explanation, cap restatement at **exactly one**, and assert the entry's **final** sentence is not the misconception. | Sinatra & Broughton verbatim structure; Schroeder & Kucera g = 0.41; Ecker (backfire is safe, once); Debunking Handbook FACT-MYTH-FALLACY-**FACT** |
| 4 | **Every analogy entry has a breakdown clause** — `/\b(breaks? down\|where (this\|the analogy) fails\|unlike\|differs? from\|no analogy is perfect)\b/i` — **and it must be a full sentence naming a specific disanalogous feature** (contains a noun from the target's vocabulary). Reject generic hedges via a stoplist. | **Glynn step 5** verbatim; FAR ACTION/UNLIKES; Orgill (analogies cause hard-to-remedy misconceptions) |
| 5 | **≥1 question PRECEDES exposition** — byte offset of the first question marker < start of `Core Explanation` body. | Richland d = 0.61–1.1 with time equated. Costs no words, only ordering. |

### Tier 2 — warns

| # | Rule | Warrant |
|---|---|---|
| 6 | **Signaling:** first 2 sentences of `Core Explanation` contain a cardinal preview whose count matches the number of following subheadings; `Key Facts` terms bolded at first occurrence; connective density ≥1.5/100 words. | Noetel g = 0.43, k = 209 — **best-evidenced text-applicable principle**; text signaling > graphic |
| 7 | **Segmenting:** no prose run >180 words without a **structural** markdown element (heading/list/table) — a bare `\n\n` does not count. No `##`/`###` subsection >250 words. | Rey: **instructor-imposed 0.41 vs learner-controlled 0.20** |
| 8 | **Split attention:** worked-example step explanations within ~40 tokens of the step; citation markers in the same sentence as the claim, not in a terminal `Sources` dump; flag jump-forcing cross-references (`see Sources`, `as discussed above`). | Sweller Table 1 verbatim; contiguity g = 0.63–0.74 |
| 9 | **Sweller redundancy (NOT Mayer's):** flag near-duplicate sentence pairs (>80% token overlap) between `Core Explanation` and `Key Facts`. | Chandler & Sweller verbatim — self-contained duplication |
| 10 | **Personalization:** 2nd-person/inclusive density in **0.8–4.0 per 100 words** (a band, not a floor); zero tolerance for `it is to be noted\|one must\|the reader should`. | Noetel g = 0.33 |
| 11 | **Coherence:** regex-blocklist seductive-detail openers (`Fun fact`, `Interestingly`, `Legend has it`); flag >15% of sentences with zero lexical overlap with the `Key Facts` term set. | Noetel g = 0.33 |
| 12 | **Pre-training / define-before-use:** fail any technical term whose first occurrence in `Core Explanation` precedes its `Key Facts` entry or an inline gloss within 15 tokens. ⚠️ **Weakest evidence** — Mayer-only, absent from Noetel's 29 reviews. | Mayer d = 0.78 |
| 13 | **Element interactivity:** count novel terms that must be held simultaneously to parse one sentence; **flag only LARGE deviations** (e.g. >2× the course median). Do not build fine gradations. | Chen/Paas/Sweller verbatim: small differences are not detectable |
| 14 | **Generation quality:** require ≥1 sentence-completion blank inside a ≥8-word sentence, or a numeric/derivation prompt. **Reject anagram/scramble prompts outright.** | Bertsch: **anagram −.05, nonwords .05** vs sentence completion .60, calculation .92 |
| 15 | **Analogy relational focus:** require ≥1 mapping construction (`just as\|corresponds to\|plays the (same )?role\|is to .+ as .+ is to`); **fail if the only mapping language is appearance-based** (`looks like\|is shaped like\|resembles`). Enforce one-to-one (no source term mapping to two targets) and parallel connectivity (no term mentioned exactly once). | Gentner & Markman verbatim; FAR "surface features or deep relation?" |
| 16 | **Analogy leakage:** fail if an analog-domain term is later used in a **declarative claim about the target** outside the `Analogies` section. | Orgill/Zook: analogy-induced misconception in the making |

### Tier 3 — course-level and scheduler rules

| # | Rule | Warrant |
|---|---|---|
| 17 | **Guidance fades across a lesson.** In `structure.json`, worked-example completeness must be **monotonically decreasing** with concept index, fading **last-step-first** (Task 1 all worked → Task 4 none). A checker demanding identical scaffolding at every node is **actively wrong** per expertise reversal. | Atkinson/Renkl/Merrill Table 1, f = .23–.33; Kalyuga et al. |
| 18 | **Interleave only with CONFUSABLE neighbours.** `Contrast with:` requirement gated on MiniLM cosine similarity above a threshold — **never generic.** | Brunmair & Richter: **expository texts non-significant, words −0.39** |
| 19 | **Spacing gap scales with the horizon:** first gap ≈ **20% of the target retention interval at week-scale, decaying to ~5% at year-scale.** Not a fixed percentage. | Cepeda 2008 verbatim; observed 14/31/30/6% |
| 20 | **Remove any assertion that expanding intervals are required** from `fsrs_engine.py` docs/comments. | Latimier: expanding vs uniform **g = 0.034, n.s.** |
| 21 | **Spaced backward re-exposure:** ≥2 outbound `con_[0-9a-f]{8}` links to **already-completed** prerequisites, ≥1 inside a question stem; verify backwardness against `structure.json` ordering. | Cepeda 47.3% vs 36.7% |
| 22 | **Penalize exposition-only bulk:** flag docs where `question_word_count / total_word_count` falls as prose grows. That is precisely the SSSS condition. | R&K Exp. 2: SSSS read 14.2×, scored 40% at one week, and was **most confident** |
| 23 | **Retrieval does not spill over.** Assert questions cover `Edge Cases` and `Misconceptions` content, not only `Core Explanation`. | Pan & Rickard: transfer weakest for "untested materials from initial study" |

**Weighting:** gate hardest on **practice testing** and **distributed practice** (Dunlosky HIGH utility); treat summary/restatement sections as low-value (Dunlosky LOW).

---

## Confidence and gaps

**High confidence — primary PDFs fetched and tables extracted:** Sweller 2019 Table 1 (all 17 effects); Chen/Paas/Sweller element-interactivity quotes and counts; Atkinson/Renkl/Merrill Table 1 fading schedule; Kalyuga 2003 verbatim; Barbieri 2023 full meta-analysis incl. moderators; Mayer 2024 Table 6; Noetel 2022 full table; Fiorella & Mayer Table 1; Roediger & Karpicke both experiments; Rowland full moderator table; Cepeda 2006 and 2008; Latimier; Murray; Brunmair & Richter; Bertsch Table 1; Richland; Kornell; Dunlosky Table 4; Sinatra & Broughton; Schroeder & Kucera; Ecker; Posner p. 214; Gentner & Markman; Glynn p. 117–118; Orgill & Bodner.

**Could NOT be sourced credibly — reported as gaps, not filled:**
- **Sweller & Cooper (1985) "6× longer / 1/5 the errors"** — paper fully paywalled; every circulating source is secondary. **Use Barbieri 2023 g = 0.48 instead.**
- **Guzzetti et al. (1993) effect sizes** — not obtainable at any route. **Use Schroeder & Kucera 2022 g = 0.41 instead.**
- **Danielson et al. (2025) pooled g** — T&F 403s. Counts and direction are verified; the magnitude is not.
- **Adesope, Trevisan & Sundararajan (2017) g = 0.61** and its sub-figures — closed access, ERIC abstract numberless, sub-figures found only on an advocacy site. **Use Yang et al. 2021 g = 0.499 or Rowland g = 0.50 instead.** Note Adesope and Rowland **disagree** on MC vs short-answer.
- **Gentner (1983) systematicity quote against the original** — no clean fetchable copy exists. Use Gentner & Markman (1997), which is fully verifiable.
- **Bjork & Bjork (1992) chapter text** — no working URL. Citation only.
- **Rohrer & Taylor (2007) "63% vs 20%"** — `[V-2°]` only. **Use Rohrer, Dedrick & Burgess 2014 (72% vs 38%, d = 1.05) instead.**
- **Treagust et al. (1998) FAR original** — no open copy; table verified only on a secondary open-access host.
- **Harrison & Treagust "multiple analogies" claims** — search summaries only.
- **Tippett (2010) "22 studies" count.**

**Preprints / accepted manuscripts used rather than copies of record** (flagged inline): Noetel et al. 2022 (OSF preprint); Cepeda et al. 2008 (ERIC "in press" manuscript, whose own header warns it may differ from the published version); Brunmair & Richter 2019 (Würzburg AAM).

**Non-peer-reviewed hosts used for verbatim content, all flagged inline:** leadinglearner.me (Sweller 2019 mirror), mrbartonmaths.com (Atkinson mirror), IOP 1233:012022 (FAR table reproduction), a blog reproduction of the Debunking Handbook, winginstitute.org (Adesope figures — **rejected as a source**). For the mirrors, content was checked against publisher metadata; **cite the DOIs, not those URLs.**
