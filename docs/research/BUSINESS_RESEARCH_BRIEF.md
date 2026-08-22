# Research brief — turning Helga into a business

**For:** deep research
**Prepared:** 2026-08-21
**Decision this feeds:** whether and how to commercialise Helga — market, pricing,
startup cost, hosting economics, first hire, and the path to profitability.

---

## 0. What we need, and the standard we want held to

A **professional-grade commercial assessment**: market size and reachability,
competitor landscape, unit economics on real hosting costs, startup capital
required, pricing that clears cost with margin, and a realistic path to
profitability with one founder and one employee.

**We want honesty over encouragement.** If the most likely outcome is that this
does not clear the bar as a business, say so and say why. If a specific
competitor already does this well enough that the wedge is gone, name them. The
worst outcome is a plan that reads well and loses two years.

Sections 1–3 describe what exists. Sections 4–9 are the research questions.
Section 10 states what we will not act on without a licensed professional.

---

## 1. The product, factually

**Helga is an offline-first, self-hosted AI tutor.** A local language model
builds courses from researched sources and teaches them through dialogue, with
spaced repetition (FSRS), inline diagrams, and voice in both directions. It is
built and running.

**Two modes:**

- **Mode A — adults, self-directed.** Name a topic, get a researched course,
  learn it Socratically, plan a credit-bearing degree from your courses.
  **Built and working.**
- **Mode B — K-12, standards-aligned, parent-supervised.** Multi-tenant auth,
  per-child profiles, assessment engine, parent dashboard, grade-band
  adaptation, COPPA/FERPA compliance controls. **Machinery largely built; the
  standards content table is empty, so it cannot teach a standards-aligned
  lesson today.** Curriculum content is the blocking dependency.

**The defensible property is that inference is self-hosted.** No third-party
model sees minor data. That is what makes the compliance story tractable and is
the clearest differentiator against anything built on the OpenAI or Anthropic
APIs. It is also the main cost driver — see §5.

**Measured quality, stated plainly:** our own benchmark scores the tutor's
Socratic questioning at roughly **2.1/5** and its adaptation to the individual
learner at **1.3–2.8/5**, while factual accuracy sits at 3.0–4.5. In plain
terms: it is a competent, accurate explainer that mostly lectures. That is the
current state of the core product and any market claim has to survive it.

---

## 2. Where the founder is, factually

- **One founder, software-heavy.** Strong on building; the gaps are commercial —
  sales, marketing, curriculum/pedagogy credibility, customer support, and
  compliance operations.
- **Intent: hire one employee** to cover those gaps.
- **Bootstrapping assumption** unless the research argues otherwise.

---

## 3. Pricing and cost assumptions already in the design docs

These are **our current guesses**, not findings. Test them.

| | current assumption |
|---|---|
| Family plan | **$30/month** base + $15/month per additional seat |
| Annual | **$300/year** base + $150/year per additional seat |
| Assumed fully-loaded cost floor | **$8–15 per active student per month** |
| Grant path | annual invoice SKU, priced under the Utah scholarship ceiling so one award can fund several children |

**Capacity model already computed:** with 4 parallel slots on one GPU we
modelled ~**64 tutor turns per minute** system-wide, at ~180 output tokens per
turn and roughly one turn per active student per minute. So one GPU supports
roughly **60 concurrently-active students** before queueing. Enrolled students
vastly exceed concurrently-active.

**No Mode A price has been set at all.** That is a question, not an assumption.

---

## 4. Market — homeschool, Utah first

- **4.1** Size the **US homeschool market**: households, students, growth since
  2020, and spend per student on curriculum and supplemental tutoring. Break out
  **Utah** specifically.
- **4.2** **Utah Fits All Scholarship** — current award amount, total programme
  funding and cap, number of recipients, eligibility, application timing, and
  **exactly what it may be spent on**. Is AI tutoring software an eligible
  expense? Is there an **approved-vendor list**, and what does it take to get on
  it? What is the reimbursement mechanism and its cash-flow implication for a
  small vendor?
- **4.3** **Which other states** run ESAs, microgrants or tax-credit
  scholarships that homeschoolers can spend on curriculum or tutoring? For each:
  award size, eligibility, vendor approval process, and whether out-of-state
  vendors qualify. Rank by reachability for a two-person company.
- **4.4** **How do homeschool families actually buy?** What are the real
  acquisition channels — conventions, co-ops, state associations, Facebook
  groups, curriculum review sites, influencers? What does customer acquisition
  cost look like, and what is the seasonality (buying is presumably concentrated
  before a school year)?
- **4.5** **Attitudes to AI teaching children.** Is there measurable resistance
  in the homeschool market specifically, and does the religious or philosophical
  motivation of a segment change how AI is received? Our offline/private-by-
  design property may matter more here than in the general market — does the
  evidence support that?
- **4.6** **Mode A's market is entirely different.** Who buys a self-hosted
  adult learning tool, at what price, and through what channel? Is it a real
  business or a feature that supports the K-12 one? Consider the
  self-hosted/privacy-conscious segment, and whether "runs entirely offline on
  your own machine" is a selling point or a support burden.

---

## 5. Hosting and unit economics — the question that decides pricing

**Constraint:** inference is self-hosted by design and for compliance. Any
answer that routes minor data to a third-party model API breaks the product's
core claim.

- **5.1** **Own hardware vs rented GPU.** For the concurrency in §3, cost out:
  (a) purchased GPU servers colocated, (b) rented cloud GPU (L40S/A100/H100
  class), (c) a rack of Apple Silicon machines, which is what we develop on.
  Include hardware, power, colocation or cloud hourly, bandwidth, and
  amortisation. **What is the real fully-loaded cost per concurrently-active
  student per month?**
- **5.2** How does that cost behave as we scale from 10 → 100 → 1,000 → 10,000
  enrolled students? Where are the step changes? What ratio of enrolled to
  concurrently-active should we plan for in a homeschool market — where usage is
  presumably concentrated in school hours?
- **5.3** Does the **$8–15/student/month** floor survive contact with real
  quotes? If the true figure is materially higher, our $30 price does not work
  and the model needs rethinking before anything else.
- **5.4** **What would change if we relaxed self-hosting?** Cost of the same
  workload on a commercial API, and what that would cost us in compliance
  exposure and differentiation. We do not intend to, but we should know the
  number we are choosing to pay.
- **5.5** Server build for scale: what does a credible production topology look
  like at 1,000 paying families — redundancy, backups, uptime expectations, and
  what a two-person company can realistically operate.

---

## 6. Competitors — who already owns this

For each, we want: what they do, price, business model, funding, scale, and
**where the genuine gap is** — or an honest statement that there is not one.

- **6.1 Homeschool curriculum platforms:** Time4Learning, Power Homeschool,
  Acellus, Miacademy, Monarch/Switched-On Schoolhouse, Sonlight, Oak Meadow.
  These are the incumbents a family compares us against.
- **6.2 AI tutoring:** Khanmigo (price, scale, school channel), Synthesis Tutor,
  Alpha School / 2hr Learning, Ello, Amira Learning, MagicSchool, Brisk.
- **6.3 Adaptive practice:** IXL, Prodigy, DreamBox, Zearn.
- **6.4 The free tier problem:** Khan Academy is free and excellent. **What can
  we charge for that Khan Academy does not already give away?** Answer this
  directly — it is the hardest question in this brief.
- **6.5 General-purpose AI:** a parent can put a child in front of ChatGPT's
  study mode for $20/month. What is the honest case that a family pays us
  instead, or as well?
- **6.6 Mode A competitors:** Coursera, Brilliant, MasterClass, and again
  general-purpose AI. Is there a real market for a self-hosted adult tutor?
- **6.7** Which competitors are **already approved vendors** for the Utah
  scholarship and comparable programmes? That list is effectively the
  competitive set for our primary channel.

---

## 7. Starting the business

- **7.1 Entity and formation** in Utah — LLC vs S-corp election, formation cost,
  registered agent, annual obligations. What actually changes for a two-person
  bootstrapped software company.
- **7.2 Compliance costs, which are unusually high here because we serve
  minors:** COPPA verifiable parental consent (what methods are accepted and
  what do the vendors charge?), state student-data-privacy laws including
  Utah's, FERPA if we ever touch school data, and what a compliance review by
  counsel costs.
- **7.3 Insurance:** general liability, errors & omissions/professional, cyber.
  What does working with minors do to premiums and to what is available?
- **7.4 Accounting and tax:** bookkeeping, sales tax on SaaS (is SaaS taxable in
  Utah and in the states we would sell into?), and the compliance burden of
  multi-state sales.
- **7.5 Total realistic startup capital** before first revenue — formation,
  legal, insurance, hardware, hosting, tooling, and a marketing budget. Give a
  range with what drives the difference.
- **7.6 Payment processing:** Stripe fees, chargeback exposure, and how the
  grant/invoice path changes cash flow versus card subscriptions.

---

## 8. The first hire

The founder is software-heavy. One employee.

- **8.1** Which single role has the **highest leverage** for this specific
  business at this specific stage — sales/growth, a credentialed educator for
  curriculum and credibility, customer success, or operations/compliance? Argue
  it rather than listing options.
- **8.2** What does that role cost in Utah — salary range, employer taxes,
  benefits, total loaded cost? What changes if they are a contractor rather than
  an employee, and where is the legal line?
- **8.3** Is **equity** appropriate for a first hire in a bootstrapped company,
  and what are typical terms? What are the ways this commonly goes wrong?
- **8.4** What must be true before hiring — a revenue threshold, a signed pilot,
  a specific bottleneck the founder cannot clear?
- **8.5** Given a credentialed educator is one option: how much does **pedagogical
  credibility** matter to a homeschool buyer, and does a named educator on the
  team measurably affect conversion in this market?

---

## 9. Pricing, pay and reinvestment

- **9.1** **What should Mode B cost?** Test our $30/month against what this
  market actually pays, against the scholarship award size, and against
  competitors. Per-family or per-child? Is annual-only simpler and better given
  the grant cycle?
- **9.2** **What should Mode A cost?** No price has been set. Is it a
  subscription, a one-off licence, or free-as-a-funnel into Mode B?
- **9.3** **Free tier or trial?** What converts in this market, and what does a
  free tier cost us when every active user consumes GPU time — unlike a
  typical SaaS where marginal cost is near zero?
- **9.4** **Founder compensation vs reinvestment.** What frameworks do
  bootstrapped founders actually use for deciding owner draw versus reinvestment
  at each revenue stage? We want the standard approaches and the trade-offs, not
  a number pulled from the air.
- **9.5** **Break-even.** At the costs from §5 and §7, how many paying families
  are needed to cover fixed costs, then to cover one salary, then two? Show the
  arithmetic so we can re-run it when the inputs change.
- **9.6** **What kills companies like this?** The realistic failure modes for a
  small edtech selling to homeschoolers — churn after one school year,
  scholarship programme rule changes, a well-funded incumbent adding the same
  feature, support burden per family, seasonality of cash flow.

---

## 10. What we will not act on from this research

State findings freely, but we understand that:

- **Legal, tax and insurance decisions require a licensed professional.** We
  want the landscape and the questions to ask counsel, not a substitute for
  counsel. Flag anything that must go to an attorney or CPA.
- **Nothing here is investment advice**, and we are not asking whether to invest
  personal money — only what the business requires and what it plausibly returns.
- Where a figure depends on a quote we have not obtained (hosting, insurance,
  legal), give a **range with its source and date**, and say what would move it.

---

## 11. What good output looks like

- **Numbers with sources and dates.** Scholarship amounts, competitor prices and
  hosting costs all change; an undated figure is not usable.
- **A recommended path, not a menu.** We can evaluate options; what we cannot do
  is choose between twelve of them. Recommend one and say what would change it.
- **Name the strongest objection to this business and answer it.** If the honest
  answer is that Khan Academy plus ChatGPT covers most of the value for free, we
  need to read that here rather than discover it after hiring someone.
- **Distinguish what is known from what is estimated.** Our own §3 assumptions
  are guesses; treat them as such and replace them where you can.
