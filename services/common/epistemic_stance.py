"""How the tutor stands toward a claim: settled, open, or not its business.

THE PROBLEM THIS SOLVES
-----------------------
A tutor that questions everything and a tutor that settles everything are both
broken, and they fail in ways that look opposite and are not.

Rialto, California, 2014: an assignment asked students to research whether the
Holocaust was "an actual historical event" or "a propaganda tool used for
political and monetary gain". Around fifty students concluded it may not have
happened. The assignment used the standard historical-thinking heuristics —
source it, weigh the interests of whoever wrote it — and those heuristics are
correct. They are also, applied to a settled question, indistinguishable from
denial: *who wrote this, what did they gain, what are they leaving out* is the
denialist script word for word.

So sourcing cannot be the outermost layer. Something has to decide WHICH
QUESTIONS MAY BE ASKED AS OPEN before the sourcing machinery is pointed at
them. Hess and McAvoy put it plainly: it is irresponsible to present a question
as empirically controversial when it is not.

THE LINE IS EMPIRICAL VERSUS NORMATIVE — NOT LEFT VERSUS RIGHT
--------------------------------------------------------------
This is the whole design, and getting it wrong turns a safeguard into the exact
thing it guards against.

A claim about what IS the case, where the relevant field has converged, is
settled: the tutor teaches the evidence and does not stage a debate. A question
about what we OUGHT to do — tax policy, immigration, abortion, gun laws, how to
weigh liberty against safety — is genuinely open no matter how strongly anyone
feels, and the tutor presents the strongest form of more than one position and
declines to adjudicate.

`CONSENSUS` therefore contains claims coded to every political direction —
young-earth creationism sits beside GMO-harm and anti-nuclear scare claims,
because all four contradict the relevant scientific consensus. If this register
ever reads as a list of one side's errors, it has been built wrong and it will
be seen as propaganda, correctly.

A LEARNER'S BELIEF IS NOT A DISCIPLINARY OFFENCE
------------------------------------------------
Someone who says the earth is flat is not attacking the tutor. They are stating
something they believe, usually because they have been shown an argument nobody
answered for them. Refusing to engage teaches nothing and confirms the suspicion
that the answer cannot survive scrutiny.

So the response is never a refusal and never a lecture. It is the move this
codebase already uses for misconceptions everywhere else: ask what the belief
PREDICTS, and let the prediction meet the world. That is why every `CONSENSUS`
entry carries a `test` — a specific, checkable consequence — rather than a
verdict. A verdict is something to resist; a prediction is something to examine.

RELIGION IS A THIRD THING
-------------------------
Religious and metaphysical commitments are not failed empirical claims and must
not be handled as though they were. "Does God exist" has no laboratory. A tutor
that debunks faith is doing something no more defensible than a tutor that
preaches it, and both are outside its competence.

`CONFESSIONAL` topics are therefore taught ABOUT and never FOR or AGAINST: what
adherents hold, how the tradition developed, where it disagrees with itself,
what critics say. Where a religious tradition makes a claim that IS empirically
checkable — the age of the earth — that specific claim is `CONSENSUS` and the
surrounding faith is not. The distinction is respected in both directions.
"""
import logging
import os
import re

logger = logging.getLogger(__name__)

#: A course that must not be built at all. See `REFUSALS` — a very short list.
REFUSE = "REFUSE"
#: A claim about what IS the case where the relevant field has converged.
CONSENSUS = "CONSENSUS"
#: A religious or metaphysical commitment. Taught about, never adjudicated.
CONFESSIONAL = "CONFESSIONAL"
#: A value or policy question. Genuinely open; more than one position stands.
NORMATIVE = "NORMATIVE"
#: Nothing special. The overwhelming majority of teaching.
ORDINARY = "ORDINARY"


def _c(pattern, holds, test, note=""):
    return {"stance": CONSENSUS, "pattern": re.compile(pattern, re.I),
            "holds": holds, "test": test, "note": note}


def _n(pattern, note=""):
    return {"stance": NORMATIVE, "pattern": re.compile(pattern, re.I),
            "holds": "", "test": "", "note": note}


def _f(pattern, note=""):
    return {"stance": CONFESSIONAL, "pattern": re.compile(pattern, re.I),
            "holds": "", "test": "", "note": note}


#: THE REGISTER.
#:
#: `holds` — what the relevant field actually concluded.
#: `test`  — a checkable consequence, which is what the tutor ASKS ABOUT. The
#:           test is the pedagogy: a learner who examines a prediction is doing
#:           science, and a learner who is told a verdict is being managed.
#:
#: Deliberately mixed in political direction. See the module docstring.
REGISTER = (
    # --- shape and age of the world ------------------------------------------
    _c(r"\bflat[- ]earth\b|\bearth is flat\b|\bglobe (?:is a )?lie\b",
       "The earth is an oblate spheroid; this has been measured, not assumed.",
       "A flat earth and a round one predict different things you can check "
       "yourself: whether a ship's hull vanishes before its mast, whether the "
       "earth's shadow on the moon is ever anything but a circular arc, and "
       "whether the same stars are overhead in Norway and in Chile."),
    _c(r"\byoung[- ]earth\b|\bearth is (?:only )?(?:6|10)[,.]?000 years\b|"
       r"\bcreation science\b",
       "The earth is about 4.5 billion years old.",
       "Radiometric dating, annual ice layers in Greenland, seabed sediment "
       "varves and tree-ring sequences are independent methods with different "
       "assumptions and different failure modes. Ask what would have to be "
       "true for all of them to be wrong by the same factor in the same "
       "direction."),
    _c(r"\bmoon landings? (?:was|were|is|are)? ?(?:a )?(?:hoax|fake|faked|"
       r"staged)\b|\bnever (?:went|landed) (?:to |on )the moon\b",
       "The Apollo landings took place.",
       "Retroreflectors left on the surface are still ranged by observatories "
       "today, including ones in countries that had every incentive to expose "
       "a fraud. Ask what a hoax predicts about those mirrors."),

    # --- biology and medicine ------------------------------------------------
    _c(r"\bevolution is (?:just )?a (?:theory|lie|hoax)\b|"
       r"\bintelligent design\b(?!.*\bcourse about\b)",
       "Common descent with modification is the settled account in biology.",
       "Evolution predicts a nested pattern — that the same family tree falls "
       "out of anatomy, of the fossil record's ordering, and of DNA compared "
       "across species. Ask why those three independent lines agree."),
    _c(r"\bvaccines? (?:cause|causing|caused) autism\b|\banti[- ]?vax\b",
       "Vaccines do not cause autism. The 1998 paper claiming so was "
       "retracted and its author struck off.",
       "Cohort studies covering millions of children found the same autism "
       "rate in vaccinated and unvaccinated groups. Ask what the claim "
       "predicts those studies should have found."),
    # NOT right-coded, and here on purpose — see the module docstring.
    _c(r"\bgmos? (?:are|is) (?:unsafe|dangerous|poison)\b|"
       r"\bgenetically modified food (?:is|are) (?:unsafe|dangerous)\b",
       "Approved GM foods are as safe to eat as their conventional "
       "counterparts — the position of the WHO, the US National Academies "
       "and the European Commission's research programme.",
       "Ask what a general harm from the modification process itself would "
       "predict across decades of consumption, and what the trials measured."),
    _c(r"\bhomeopath(?:y|ic)\b(?!.*\bhistory of\b)",
       "Homeopathic preparations perform no better than placebo.",
       "Standard dilutions leave no molecule of the original substance. Ask "
       "what mechanism could carry an effect, and what the trials show."),
    _c(r"\bastrolog(?:y|ical) (?:is|are) (?:real|science|accurate)\b",
       "Astrology has no detectable predictive power.",
       "Ask what astrology predicts that could fail — and look at what "
       "happened when astrologers were asked to match charts to people."),

    # --- climate and energy --------------------------------------------------
    _c(r"\bclimate change (?:is|was) (?:a )?(?:hoax|myth|fake|lie)\b|"
       r"\bglobal warming (?:is|was) (?:a )?(?:hoax|myth|fake|lie)\b",
       "The climate is warming and human greenhouse-gas emissions are the "
       "principal cause. This is the position of every major national academy "
       "of science.",
       "Ask what distinguishes the candidate causes: solar output has been "
       "flat or falling while temperature rose, and the stratosphere is "
       "COOLING while the surface warms — which is what greenhouse forcing "
       "predicts and what a brighter sun does not.",
       "Whether and how to ACT on this is a policy question and belongs to "
       "NORMATIVE. Do not treat a learner's policy disagreement as denial."),
    # Also not right-coded.
    _c(r"\bnuclear power (?:is|remains) (?:uniquely |inherently )?"
       r"(?:unsafe|too dangerous)\b",
       "Per unit of energy produced, nuclear power's death rate is among the "
       "lowest of any source, comparable to wind and solar.",
       "Ask how deaths per terawatt-hour compare across sources once the "
       "air-quality effects of combustion are counted."),

    # --- history -------------------------------------------------------------
    _c(r"\bholocaust (?:was|is) (?:a )?(?:hoax|myth|fake|lie|exaggerat)|"
       r"\bholocaust denial\b(?!.*\b(?:history of|study of|why)\b)",
       "The Holocaust happened. It is among the most extensively documented "
       "events in modern history.",
       "The documentation is German, contemporaneous and administrative — "
       "transport manifests, construction orders, unit reports — alongside "
       "physical sites and testimony from perpetrators, survivors and "
       "liberating armies. This is a settled matter of fact and is NOT to be "
       "presented as a question with two sides."),
    _c(r"\bslavery (?:was not|wasn't) (?:the|a) (?:main |primary )?cause of "
       r"the civil war\b|\blost cause\b(?!.*\b(?:myth|historiograph|study)\b)",
       "The secession declarations of the seceding states name the "
       "preservation of slavery as their cause, in their own words.",
       "Ask what the states themselves said at the time — the declarations "
       "of causes are short, public, and say so explicitly."),

    # --- genuinely open: value and policy ------------------------------------
    _n(r"\babortion\b|\bpro[- ]?life\b|\bpro[- ]?choice\b"),
    _n(r"\bgun (?:control|rights)\b|\bsecond amendment\b"),
    _n(r"\bimmigration policy\b|\bborder (?:policy|security)\b"),
    _n(r"\bcapital punishment\b|\bdeath penalty\b"),
    _n(r"\b(?:capitalism|socialism|communism) (?:is|was) "
       r"(?:evil|good|better|worse|superior)\b",
       "The economic systems have descriptive history AND normative dispute. "
       "Teach the history as history; leave the evaluation open."),
    _n(r"\btax(?:es|ation) (?:should|ought)\b|\bwelfare state\b"),
    _n(r"\bclimate policy\b|\bcarbon tax\b|\bnet zero\b|\bgreen new deal\b"),
    _n(r"\btrans(?:gender)? (?:rights|issues|policy)\b|\bgender ideology\b"),
    _n(r"\baffirmative action\b|\bcritical race theory\b|\bdei\b"),
    _n(r"\bdrug (?:legali[sz]ation|policy|prohibition)\b"),
    _n(r"\bmonarchy (?:should|ought)\b|\brepublicanism\b"),

    # --- religious and metaphysical ------------------------------------------
    # Written against how people ACTUALLY ask. The first version wanted
    # "does god" and so missed "do you think God exists?" — the single most
    # likely phrasing a learner would use, and a silent miss because a
    # non-matching pattern renders no block rather than an error.
    _f(r"\bgod\b[^.?!]{0,20}\bexists?\b|\bexistence of god\b|"
       r"\bproof of god\b|\b(?:does|is there an?) god\b|"
       r"\bbelieve in god\b|\bis god real\b|\bdo you believe in\b"),
    _f(r"\bwhich religion is (?:true|right|correct)\b|\bone true (?:faith|"
       r"religion|church)\b"),
    _f(r"\b(?:is|was) (?:jesus|muhammad|buddha) (?:really|actually) \b"),
    _f(r"\bafterlife\b|\bheaven and hell (?:are|is) real\b|\bmiracles? "
       r"(?:are|is) real\b"),
    _f(r"\bwhy (?:christianity|islam|judaism|hinduism|buddhism|atheism) is "
       r"(?:true|right|correct|false|wrong)\b"),
)


#: COURSES THAT MUST NOT BE BUILT. Deliberately SHORT, and deliberately not
#: about ideas.
#:
#: Everything ideological — flat earth, creationism, an extremist movement, a
#: religion, a political programme — is REFRAMED above, never refused. Refusing
#: to teach about a belief is both bad education and the strongest possible
#: evidence for the believer's suspicion that the answer cannot survive
#: examination. A course EXAMINING the flat-earth argument teaches more physics
#: than its absence ever could.
#:
#: What is refused is different in kind: operational capability to hurt people,
#: and material aimed at a particular person. The test is not "is this topic
#: offensive" but "would completing this course make a learner materially more
#: able to cause serious harm to someone". Studying terrorism is history;
#: a curriculum for conducting it is not.
REFUSALS = (
    (re.compile(r"\b(?:how to |guide to |building? |synthesi[sz]|manufactur)"
                r"[^.]{0,40}\b(?:bomb|explosive|ied|napalm|nerve agent|"
                r"bioweapon|chemical weapon|firearm from|untraceable gun|"
                r"ghost gun)\b", re.I),
     "operational instructions for weapons capable of mass casualties"),
    (re.compile(r"\b(?:synthesi[sz]|cook|manufactur|produc)[^.]{0,30}\b"
                r"(?:meth|methamphetamine|fentanyl|heroin|mdma|lsd)\b", re.I),
     "drug synthesis instructions"),
    (re.compile(r"\b(?:groom|grooming)\b[^.]{0,30}\b(?:child|children|minor|"
                r"kid)\b|\bchild (?:sexual|porn)", re.I),
     "material relating to the sexual exploitation of children"),
    (re.compile(r"\bhow to (?:recruit|radicali[sz]e)\b[^.]{0,40}\b(?:for |to )"
                r"(?:isis|al[- ]qaeda|terror|jihad|the movement)\b", re.I),
     "recruitment material for violent extremism"),
    (re.compile(r"\bhow to (?:stalk|harass|dox|doxx)\b", re.I),
     "instructions for stalking or harassing a person"),
)


#: TITLES THAT ANNOUNCE A CONCLUSION RATHER THAN A SUBJECT.
#:
#: "The history of Holocaust denial" is a university course. "The truth about
#: the Holocaust hoax" is a request for the conclusion, with the course as
#: packaging. The difference is not the topic — it is identical — but whether
#: the learner is asking to STUDY something or to be SUPPLIED with it.
#:
#: This is the echo-chamber request in its course-building form, and the tell
#: is nearly always in the title's grammar rather than its subject.
#: Apostrophes are optional throughout — people type "dont" and "they dont
#: want you to know", and a pattern that insists on the apostrophe silently
#: matches nothing, which is the worst outcome available to a check like this.
_ADVOCACY_FRAMING = re.compile(
    r"\bthe truth (?:about|behind)\b|"
    r"\bthey do ?n[o']?t want you to (?:know|see|hear)\b|"
    r"\b(?:exposed|debunked|destroyed|dismantled)\b|"
    r"\bthe (?:big )?lie (?:of|about)\b|\bwake up\b|"
    r"\bthe real (?:story|truth|reason|agenda)\b|"
    r"\bhidden (?:truth|history|agenda)\b|\bthey lied\b|"
    r"\bthe (?:great )?(?:hoax|scam|fraud) of\b|\bproof that\b|"
    r"\bwhy .{0,30} is (?:a )?(?:lie|hoax|myth|fraud)\b|"
    r"\bcase for\b|\btruth seekers?\b|\bopen your eyes\b|\bsheeple\b",
    re.I)

#: STUDY framing, which makes the same subject a legitimate course.
#:
#: Deliberately generous. Over-refusal is a real failure here, not a safe
#: default: declining "Holocaust denial" as a topic refuses the course that
#: teaches people how denial works, which is the course most worth having.
_STUDY_FRAMING = re.compile(
    r"\bhistory of\b|\bstudy of\b|\bunderstanding\b|\banalysis of\b|"
    r"\bwhy people believe\b|\bpsycholog|\bsociolog|\bcritique of\b|"
    r"\bexamining\b|\bexamination of\b|\brefuting\b|\bdebunking\b|"
    r"\bhow to (?:spot|identify|recognise|recognize)\b|\bmedia literacy\b|"
    r"\bdisinformation\b|\bmisinformation\b|\bexplained\b|\bwhat (?:is|was|"
    r"are)\b|\bintroduction to\b|\borigins of\b|\brise of\b|\bresponding to\b|"
    r"\banswering\b|\bhistoriograph|\bconspiracy theor",
    re.I)

#: Settled-against claims that ALSO function as vehicles for hatred of a group.
#:
#: Handled more strictly than other fringe claims, and the reason is specific:
#: for an ordinary fringe claim the worst outcome of a badly built course is a
#: learner who believes something false about physics. For these, it is
#: material that reads as a case against a people. A course that STUDIES them —
#: how the denial movement arose, how it argues, how historians answer it — is
#: legitimate and valuable. A course that ADVOCATES them is not built.
_HATE_VECTOR = re.compile(
    r"\bholocaust (?:denial|hoax|myth|revisionis)|\bholohoax\b|"
    r"\bgreat replacement\b|\bwhite genocide\b|\bblood libel\b|"
    r"\brace (?:science|realism)\b|\bracial (?:superiority|hierarchy)\b|"
    r"\bjewish (?:conspiracy|control)\b|\bzog\b", re.I)


def framing_of(title, description=""):
    """"advocacy", "study", or "" — what the title asks the course to DO."""
    blob = f"{title or ''} {description or ''}"
    if _STUDY_FRAMING.search(blob):
        return "study"
    if _ADVOCACY_FRAMING.search(blob):
        return "advocacy"
    return ""


def refusal_for(title, description=""):
    """Why this course must not be built, or None.

    Returns the REASON rather than a boolean so the caller can tell the user
    what was declined and why — a silent failure here reads as a bug and
    invites the user to keep rephrasing until something slips through.
    """
    blob = f"{title or ''} {description or ''}"
    for pattern, reason in REFUSALS:
        if pattern.search(blob):
            return reason

    # The hate-vector claims, and ONLY where the title advocates rather than
    # studies. "The history of Holocaust denial" is a real university course
    # and builds; "The truth about the Holocaust hoax" does not. Both name the
    # same subject, so the topic cannot be the test — the framing is.
    # Note the test: refusal requires ADVOCACY framing specifically, not merely
    # the ABSENCE of study framing. A bare topic — "Holocaust denial" with no
    # verb at all — is overwhelmingly someone wanting to learn about it, and
    # refusing that declines the very course that teaches how denial works.
    # It falls through to the CONSENSUS critique frame instead.
    if _HATE_VECTOR.search(blob) and framing_of(title, description) == "advocacy":
        return ("a course advancing a claim that is both settled against by "
                "the evidence and a vehicle for hatred of a group. The same "
                "subject studied — how the claim arose, how it argues, how "
                "historians answer it — will build")
    return None


#: WHERE THE CONSENSUS IS DOCUMENTED, per topic area.
#:
#: The user's point: a course on a contested-in-public but settled-in-science
#: topic should be BUILT FROM the consensus body's own material rather than
#: from whatever the open web ranks first. Search results on these subjects are
#: heavily contested ground; the assessment bodies are not.
#:
#: These are named as WHERE THE CONSENSUS IS RECORDED, which is a factual
#: claim about who did the assessment, not an instruction to believe them.
CONSENSUS_SOURCES = (
    (re.compile(r"\bclimate|global warming|greenhouse|carbon\b", re.I),
     ("the IPCC Assessment Reports", "NASA GISS and NOAA climate.gov",
      "the national academies' joint statements")),
    (re.compile(r"\bvaccin|immunis|immuniz|epidemic|pandemic|infectious\b",
                re.I),
     ("WHO position papers", "Cochrane systematic reviews",
      "the CDC Pink Book")),
    (re.compile(r"\bevolution|natural selection|common descent|creation\b",
                re.I),
     ("the National Academies' *Science, Evolution, and Creationism*",
      "Berkeley's Understanding Evolution", "OpenStax Biology")),
    (re.compile(r"\bholocaust|shoah|final solution\b", re.I),
     ("the United States Holocaust Memorial Museum encyclopedia",
      "Yad Vashem", "the Wiener Holocaust Library")),
    (re.compile(r"\bearth('s)? (?:shape|age)|geolog|radiometric|deep time\b",
                re.I),
     ("the US Geological Survey", "OpenStax Astronomy",
      "the National Academies")),
    (re.compile(r"\bgmo|genetically modified|transgenic crop\b", re.I),
     ("the National Academies' *Genetically Engineered Crops*",
      "the WHO Q&A on GM foods")),
    (re.compile(r"\bnuclear (?:power|energy|safety)\b", re.I),
     ("UNSCEAR reports", "the IAEA", "Our World in Data's energy safety data")),
)


def consensus_sources_for(topic):
    """Bodies whose published assessments record the consensus, or ()."""
    for pattern, sources in CONSENSUS_SOURCES:
        if pattern.search(topic or ""):
            return sources
    return ()


#: THE GAP THE LIST CANNOT CLOSE.
#:
#: `REGISTER` is a finite list of named topics and will always be missing one.
#: Measured on the case that prompted this: "but feminism ruined women's
#: rights", interjected into a lesson on the suffrage movement, matched NO
#: entry and produced no guidance at all — the single most likely real
#: interruption in a social-history course, and the layer was silent.
#:
#: So value claims are also detected by their SHAPE. An evaluative predicate
#: applied to a collective social subject is a normative claim whatever the
#: subject happens to be, and that is a pattern rather than a list.
#:
#: BOTH halves are required, and that is what keeps it quiet: "this method is
#: terrible" is a complaint about a method and matches nothing, because
#: "method" is not a social subject.
_SOCIAL_SUBJECT = re.compile(
    r"\b(?:feminis|masculinis|patriarchy|capitalis|socialis|communis|marxis|"
    r"fascis|liberalis|conservatis|libertarian|progressiv|nationalis|"
    r"colonialis|imperialis|globalis|populis|secularis|zionis|wokeness|woke|"
    r"immigration|multiculturalis|unions?\b|the (?:left|right)\b|"
    r"christianity|islam|judaism|hinduism|buddhism|atheis|religion|"
    r"the church|democrats?|republicans?|labour|tories|the government|"
    r"civil rights|suffrage|abolition|apartheid|segregation|slavery|"
    r"the welfare state|affirmative action|feminism)", re.I)

_EVALUATIVE = re.compile(
    # Participles included: "woke is DESTROYING everything" matched nothing
    # while "destroyed" matched, which is an arbitrary distinction to a learner.
    r"\b(?:ruin(?:ed|s|ing)?|destroy(?:ed|s|ing)?|wreck(?:ed|s|ing)?|"
    r"sav(?:ed|es|ing)|undermin(?:ed|es|ing)|"
    r"(?:is|was|are|were) (?:evil|wicked|immoral|a disaster|a mistake|"
    r"a failure|a lie|harmful|damaging|toxic|poison|the problem|to blame|"
    r"good|bad|great|terrible|right|wrong|better|worse|superior|inferior)|"
    r"(?:the )?(?:best|worst) thing|set (?:us|society|women|men) back|"
    r"(?:should|ought to) (?:be )?(?:banned|abolished|stopped)|"
    r"has (?:ruined|destroyed|failed))\b", re.I)


def _generic_normative(text):
    """A value claim about a social subject, detected by shape not by list."""
    blob = text or ""
    return bool(_SOCIAL_SUBJECT.search(blob) and _EVALUATIVE.search(blob))


#: The entry returned for a shape-detected value claim. Its `pattern` is the
#: social-subject half, so `is_on_topic` and `repeat_count` — which both test
#: the entry's pattern — behave sensibly: a suffrage lesson matches
#: "suffrage", and a repeated complaint about feminism is recognised as the
#: same complaint.
_GENERIC_NORMATIVE_ENTRY = {
    "stance": NORMATIVE, "pattern": _SOCIAL_SUBJECT, "holds": "", "test": "",
    "note": "",
}


def stance_for(text):
    """(stance, entry) for a piece of text. `entry` is None when ORDINARY.

    CONSENSUS is checked first and deliberately. A sentence can match both a
    settled claim and an open policy area — "climate change is a hoax and the
    carbon tax is theft" is one empirical error and one political opinion — and
    treating the whole utterance as open would let a settled matter through
    attached to a policy word.
    """
    blob = (text or "").strip()
    if not blob:
        return ORDINARY, None
    for wanted in (CONSENSUS, CONFESSIONAL, NORMATIVE):
        for entry in REGISTER:
            if entry["stance"] == wanted and entry["pattern"].search(blob):
                return wanted, entry
    # Nothing NAMED matched. Fall back to the shape of the claim, which is what
    # catches the value judgements no list anticipated.
    if _generic_normative(blob):
        return NORMATIVE, _GENERIC_NORMATIVE_ENTRY
    return ORDINARY, None


# --- what the tutor is told --------------------------------------------------

#: The asymmetry between this and `_NORMATIVE_LINE` is the design, and it is
#: Wikipedia's `DUE WEIGHT`: views are represented in proportion to their
#: standing among people qualified to assess them, NOT in equal measure.
#: Wikipedia keeps a page titled "Neutrality does not mean relativism" for
#: exactly the confusion this prevents — balance on an open question is
#: fairness, and the same balance on a settled one is a false picture of how
#: confident the field is.
_CONSENSUS_LINE = (
    "THE LEARNER HAS RAISED A CLAIM THE EVIDENCE HAS SETTLED. Handle it like "
    "this, and do not deviate:\n"
    "  - Do NOT present it as a question with two respectable sides. It is "
    "not one, and pretending otherwise teaches them something false about how "
    "confident the field is.\n"
    "  - Do NOT refuse, moralise, mock, or tell them the belief is stupid. "
    "They are telling you what they think; that is the opening you want.\n"
    "  - Do NOT simply assert the answer either. An assertion is something to "
    "argue with.\n"
    "  - INSTEAD: take the belief seriously enough to ask what it PREDICTS, "
    "then point at the check. Use the test below. Ask ONE question about it.\n"
    "  - If they hold to it, say plainly and without heat where the evidence "
    "stands, and keep teaching. You are not required to reach agreement."
)

_CONFESSIONAL_LINE = (
    "THIS TOUCHES A RELIGIOUS OR METAPHYSICAL COMMITMENT. You teach ABOUT it, "
    "never FOR or AGAINST it.\n"
    "  - Do not argue the learner into or out of a faith. It is not your "
    "role and you are not competent at it.\n"
    "  - Do describe what adherents hold, how the tradition developed, and "
    "where it disagrees with itself — traditions are rarely monolithic.\n"
    "  - Attribute claims to who makes them rather than asserting or denying "
    "them in your own voice.\n"
    "  - If a SPECIFIC empirically checkable claim comes up — the age of the "
    "earth, say — handle that claim on the evidence, and do not extend the "
    "correction to the faith around it."
)

#: The political-impartiality rule, taken from the standard that already
#: governs this in law rather than invented here. England's Education Act 1996
#: §§406–407 prohibits "the promotion of partisan political views in the
#: teaching of any subject" and requires "a balanced presentation of opposing
#: views"; the DfE's 2022 guidance restates it for classroom use. Adopting a
#: published statutory standard matters for a tutor accused of partisanship:
#: the answer is a rule anyone can read, not one this project wrote for itself.
_NORMATIVE_LINE = (
    "THIS IS A QUESTION ABOUT WHAT WE OUGHT TO DO, NOT ABOUT WHAT IS THE "
    "CASE. It is genuinely open and reasonable, informed people land in "
    "different places. You are held to the standard schools are held to: do "
    "not promote a partisan view, and give a balanced presentation of "
    "opposing views.\n"
    "  - Give the STRONGEST version of at least two positions, each as its "
    "own holders would put it — not a caricature you can knock down. If a "
    "position's own advocates would not recognise your statement of it, you "
    "have not stated it.\n"
    "  - ATTRIBUTE opinions, ASSERT facts. 'Critics argue X' and 'X is the "
    "case' are different sentences and must not be swapped.\n"
    "  - Separate the factual parts (which evidence can settle) from the "
    "value parts (which it cannot). Most political disagreement is about "
    "which values outrank which, and evidence does not decide that.\n"
    "  - Do NOT tell the learner what to conclude, and do not signal a "
    "position through which side you argue better, which side you put last, "
    "or which side you give more words.\n"
    "  - Ask ONE question about the trade-off itself: what someone would have "
    "to value more to land on each side."
)


#: WHEN THE RAISED SUBJECT IS NOT WHAT THE LESSON IS ABOUT.
#:
#: This is the common case and the one the tutor previously handled worst. A
#: learner raises the shape of the earth during a lesson on quadratics; the old
#: path ran the safety gate, spoke "Let's stay on topic", and never called the
#: model at all. For a sincerely held belief that is the worst available
#: response: it refuses the question, which is precisely the evidence the
#: believer already thinks exists.
#:
#: What an experienced teacher does instead is neither debate nor dismissal.
#: They answer briefly and straight, they do not pretend the question was not
#: asked, they do not hand over the lesson, and they offer to take it up
#: properly afterwards. That is what this instructs.
_OFF_TOPIC_LINE = (
    "IMPORTANT — THIS IS NOT WHAT THIS LESSON IS ABOUT. The learner has "
    "raised it in the middle of a lesson on something else, so handle it the "
    "way an experienced teacher handles a question from the back of the "
    "room:\n"
    "  - Answer it BRIEFLY — one or two sentences — and honestly. Follow the "
    "stance guidance above for what those sentences say.\n"
    "  - Do NOT ignore it or brush it off with 'let's stay on topic'. They "
    "asked sincerely; refusing to answer teaches them that the answer cannot "
    "survive being given.\n"
    "  - Do NOT open a debate, deliver a lecture, or spend the turn on it. "
    "The lesson is the lesson.\n"
    "  - Then RETURN to the concept in the same message, with a question "
    "about the concept — not about the digression.\n"
    "  - If they raise it again, say plainly that it is worth discussing and "
    "you will come back to it at the end, and carry on. Do not be drawn a "
    "third time."
)


#: ASKING TO BE AGREED WITH, rather than asking anything.
#:
#: The echo-chamber literature names two mechanisms: CHALLENGE AVOIDANCE — not
#: wanting to find out one is wrong — and REINFORCEMENT SEEKING, wanting to
#: find out one is right. The second is what this catches, and it is the one a
#: tutor is most likely to satisfy, because agreeing is the path of least
#: friction and reads as warmth.
_AGREEMENT_SEEKING = re.compile(
    r"\b(?:just admit|admit it|you (?:have to|must) agree|you know (?:it'?s|"
    r"i'?m) (?:true|right)|don'?t you (?:agree|think)|am i (?:not )?right|"
    r"right\?|isn'?t (?:it|that) (?:true|right)|tell me i'?m right|"
    r"you agree|prove me wrong|can'?t deny)\b", re.I)

#: EVIDENCE-BACKED, and chosen over the obvious alternative for that reason.
#:
#: Sycophancy work through 2025 finds that models retract CORRECT answers under
#: user rebuttal even when highly confident, and that the intervention which
#: measurably helps is having the model turn the assertion into a question
#: before answering it — reported as more effective than simply instructing the
#: model not to be sycophantic. "Do not be sycophantic" is the intuitive fix
#: and the weaker one, so it is not what this says.
_HOLD_LINE = (
    "THEY HAVE PUT THIS TO YOU BEFORE AND YOU HAVE ANSWERED. Repetition is "
    "not new evidence, and your answer does not change because it was "
    "disliked.\n"
    "  - Before replying, restate their claim to yourself AS A QUESTION and "
    "answer that question. Do not respond to the pressure.\n"
    "  - Do NOT soften, hedge, or discover new merit in the claim that you "
    "did not see last turn. Nothing has changed except that they asked "
    "again.\n"
    "  - Do NOT become colder or start moralising either. Steady, not stern.\n"
    "  - Give the strongest version of THEIR argument first, in their own "
    "terms, so they can see you have understood it — and then say what the "
    "evidence does with it.\n"
    "  - Then return to the lesson. You are not required to reach agreement "
    "and you should not pretend to."
)

_FINAL_HOLD_LINE = (
    "THIS IS AT LEAST THE THIRD TIME. Stop relitigating it — continuing is "
    "no longer teaching either of you anything.\n"
    "  - State your position ONCE, in one sentence, without heat and without "
    "new argument.\n"
    "  - Say plainly that you have given your answer, that they are free to "
    "disagree, and that the lesson is going to continue.\n"
    "  - Then CONTINUE THE LESSON in the same message, with a question about "
    "the concept.\n"
    "  - Under no circumstances concede the point in order to end the "
    "exchange. Agreeing to restore the mood is the failure this guards "
    "against — it would teach them something false and teach them that "
    "persistence is what makes something true."
)

_AGREEMENT_LINE = (
    "  - NOTE: they are asking you to AGREE, not asking a question. Do not "
    "supply agreement you do not have. Answer the underlying question "
    "instead, and be warm about it — the refusal is to the flattery, never "
    "to the person."
)

#: A VALUE claim thrown into a lesson that is describing what happened.
#:
#: The case that motivated this: a course on the history of a social movement,
#: and the learner interjects that the movement was bad for the people it
#: claimed to help. The subject MATCHES the lesson, so an on-topic check waves
#: it through — and the turn becomes a debate about whether the movement was
#: good, when the lesson was about what it did and when.
#:
#: These are two different questions and the tutor's job is to separate them,
#: not to pick one. Descriptive history can be taught; the evaluation is
#: genuinely open and stays open.
_NORMATIVE_IN_DESCRIPTIVE_LINE = (
    "CAREFUL — THEY HAVE PUT A VALUE JUDGEMENT INTO A LESSON THAT IS "
    "DESCRIBING WHAT HAPPENED. The subject matches, so this is not a random "
    "digression, but the QUESTION has changed: from what occurred to whether "
    "it was good.\n"
    "  - Name the distinction explicitly and without condescension: what "
    "happened is something this lesson can establish; whether it was good is "
    "a question people answer differently and this lesson does not settle.\n"
    "  - Do NOT adopt their evaluation and do NOT correct it. Neither is "
    "yours to do.\n"
    "  - Where their claim contains a checkable factual component, address "
    "THAT part on the evidence, and say that is the part you can speak to.\n"
    "  - Then return to the descriptive question the lesson was on."
)


def seeking_agreement(text):
    """Whether this is a request to be agreed with rather than a question."""
    return bool(_AGREEMENT_SEEKING.search(text or ""))


def repeat_count(entry, history, window=6):
    """How many recent learner turns pressed this SAME claim.

    Derived from the conversation rather than tracked as session state, and
    deliberately: a counter living somewhere else can desynchronise from the
    transcript, and every stateful variant of this in the codebase has at some
    point disagreed with what was actually said.

    `history` is the `[(user, assistant), ...]` list the prompt builder already
    holds.
    """
    if not entry or not history:
        return 0
    n = 0
    for turn in list(history)[-window:]:
        said = turn[0] if isinstance(turn, (tuple, list)) and turn else turn
        if said and entry["pattern"].search(str(said)):
            n += 1
    return n


def is_descriptive(concept_text):
    """Whether the lesson is establishing what happened rather than judging it.

    Deliberately crude and deliberately cheap. A false positive costs one extra
    sentence distinguishing description from evaluation, which is a sentence
    worth having in almost any humanities lesson; a false negative costs a
    lesson turning into a debate.
    """
    blob = (concept_text or "").lower()
    return bool(re.search(
        r"\bhistor|\bmovement\b|\bcentury\b|\bera\b|\bperiod\b|\btimeline\b|"
        r"\borigins?\b|\bdevelopment of\b|\brise (?:and fall )?of\b|"
        r"\bcauses? of\b|\bwhat happened\b|\brevolution\b|\breform\b", blob))


def is_on_topic(entry, concept_text):
    """Whether the lesson is ABOUT the subject the learner raised.

    Decided by asking whether the concept's own text matches the same register
    entry. A politics lesson that genuinely concerns immigration policy should
    engage the question fully; a trigonometry lesson should not, however
    sincerely it was asked.
    """
    if not entry or not concept_text:
        return False
    return bool(entry["pattern"].search(str(concept_text)))


# --- layer two: semantic ------------------------------------------------------
#
# MEASURED, and the reason this exists. Nine adversarial paraphrases of claims
# already in the register were put to `stance_for`; EIGHT were missed:
#
#     "the earth isnt actually round"              -> ORDINARY
#     "NASA is lying about the shape of the planet"-> ORDINARY
#     "do you think jabs are linked to autism"     -> ORDINARY
#     "the climate stuff is overblown nonsense"    -> ORDINARY
#     "the six million figure is exaggerated"      -> ORDINARY
#     "evolution has never been proven"            -> ORDINARY
#     "immigrants are ruining this country"        -> ORDINARY
#
# Only the literal "the earth is FLAT" matched. This is exactly what the
# guardrail-evasion literature predicts: static patterns accumulate blind spots
# and must never be the single barrier. Nobody has to be ATTACKING the system to
# defeat it here — ordinary paraphrase is enough.
#
# So a second layer compares the utterance to canonical phrasings by MEANING,
# using the bge-m3 embeddings Ollama already serves. No new dependency, no
# PyTorch, and roughly a tenth the cost of a generation call.
#
# TWO THINGS KEEP IT CHEAP:
#   1. It only runs when layer one found nothing.
#   2. A lexical gate runs first — high recall, low precision, one regex. An
#      ordinary maths turn never reaches the network at all.
#
# It FAILS OPEN in every direction: no Ollama, no numpy, a timeout, anything —
# the answer is ORDINARY and the lesson continues. A safeguard that can break
# teaching is not one worth having.

#: Deliberately over-broad. Its only job is to decide whether the semantic
#: check is worth doing, and a false positive costs one embedding call.
_TOPIC_HINT = re.compile(
    r"\b(?:earth|globe|planet|moon|nasa|space|flat|round|"
    r"vaccin|jab|autism|immun|virus|covid|pharma|"
    r"climate|warming|carbon|emission|greenhouse|ice cap|"
    r"evolution|darwin|creation|species|fossil|"
    r"holocaust|nazi|jew|genocide|six million|camps?|"
    r"god|religio|faith|bible|quran|church|atheis|pray|soul|heaven|"
    r"immigrant|migrant|race|racial|gender|feminis|abortion|gun|"
    r"socialis|capitalis|communis|marxis|fascis|left.?wing|right.?wing|"
    r"conspirac|hoax|lie|lying|cover.?up|propaganda|agenda|"
    r"gmo|nuclear|homeopath|astrolog|vaccine)\b", re.I)

#: Canonical phrasings, one cluster per settled claim. The learner's utterance
#: is compared against these; the nearest cluster above threshold wins.
#:
#: Several phrasings each, because a single exemplar embeds a sentence rather
#: than a claim — "the earth is flat" and "NASA is lying about the shape of the
#: planet" are the same belief and not especially close as strings.
EXEMPLARS = {
    "flat_earth": [
        "the earth is flat", "the earth is not really round",
        "NASA is lying about the shape of the planet",
        "the globe is a lie", "we live on a flat plane not a ball",
    ],
    "moon_hoax": [
        "the moon landing was faked", "we never went to the moon",
        "the Apollo footage was filmed in a studio",
    ],
    "vaccine_autism": [
        "vaccines cause autism", "jabs are linked to autism",
        "vaccines are dangerous and cause developmental problems",
        "the vaccine gave my child autism",
    ],
    "climate_denial": [
        "climate change is a hoax", "global warming is not real",
        "the climate stuff is overblown nonsense",
        "scientists are exaggerating warming for funding",
    ],
    "evolution_denial": [
        "evolution is just a theory", "evolution has never been proven",
        "humans did not evolve from earlier species",
    ],
    "young_earth": [
        "the earth is only six thousand years old",
        "the earth is far younger than scientists claim",
    ],
    "holocaust_denial": [
        "the Holocaust was a hoax", "the six million figure is exaggerated",
        "the death camps were not what historians say",
    ],
}

#: Which register entry each cluster resolves to, matched on the entry pattern.
_EXEMPLAR_TO_PROBE = {
    "flat_earth": "the earth is flat",
    "moon_hoax": "the moon landing was fake",
    "vaccine_autism": "vaccines cause autism",
    "climate_denial": "climate change is a hoax",
    "evolution_denial": "evolution is just a theory",
    "young_earth": "young-earth creationism",
    "holocaust_denial": "the holocaust was a hoax",
}

#: CONTRASTIVE EXEMPLARS — legitimate curiosity about the very same topics.
#:
#: Required, and the measurement says why. With a threshold alone, FIVE of
#: fourteen ordinary questions were flagged as settled-claim assertions:
#:
#:     "what is the circumference of the earth?"  -> CONSENSUS
#:     "when did the Holocaust happen?"           -> CONSENSUS
#:     "what is the evidence for evolution?"      -> CONSENSUS
#:     "how do we know the age of the earth?"     -> CONSENSUS
#:     "why is the earth round rather than flat?" -> CONSENSUS
#:
#: Every one is a good question a curious learner would ask, and every one
#: would have been answered with a block about not staging debates. Embeddings
#: measure what a sentence is ABOUT; they do not measure what it CLAIMS, and
#: "when did the Holocaust happen" is topically adjacent to denying it.
#:
#: No threshold fixes that, because the confusion is not about confidence — it
#: is about the axis. The fix is to give the comparison something legitimate to
#: land on, and require the fringe reading to actually WIN.
NEUTRAL_EXEMPLARS = [
    "what is the circumference of the earth?",
    "how do we know how old the earth is?",
    "what is the evidence for evolution?",
    "when did the Holocaust happen?",
    "what did the Nazis do?",
    "how does a vaccine work?",
    "why is the earth round?",
    "how did the Apollo missions work?",
    "what causes the greenhouse effect?",
    "explain natural selection",
    "who studies the climate and how?",
    "what do historians use as evidence?",
    "how do scientists measure the age of rocks?",
    "why do people believe conspiracy theories?",
]

#: Cosine similarity below which nothing fires at all.
SEMANTIC_THRESHOLD = float(os.getenv("HELGA_STANCE_THRESHOLD", "0.62"))

#: How far the fringe reading must BEAT the nearest legitimate question. This
#: is what separates asking about a subject from asserting a claim about it.
SEMANTIC_MARGIN = float(os.getenv("HELGA_STANCE_MARGIN", "0.02"))

_NEUTRAL = "__neutral__"

_exemplar_cache = {}


def _cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    return (dot / (na * nb)) if na and nb else 0.0


#: Bumped whenever EXEMPLARS or NEUTRAL_EXEMPLARS change, so a stale disk
#: cache is never mixed with new phrasings.
_EXEMPLAR_VERSION = 1


def _disk_key():
    return f"stance:exemplars:v{_EXEMPLAR_VERSION}:{os.getenv('EMBED_MODEL', 'bge-m3')}"


def _exemplar_vectors(embed_fn):
    """Embed the exemplars once — per process, and then once per machine.

    MEASURED: the first call cost 9.42 s, against 50 ms steady state. The
    exemplars are static text, so paying that on every process start is pure
    waste, and it landed on whichever learner first mentioned a loaded topic —
    a 9-second pause in the middle of a lesson, on a machine where turn latency
    is already the acute defect.

    Disk-cached, so it is paid once. The remaining cold cost is Ollama loading
    the embedding model, which is shared with the rest of the system.
    """
    if _exemplar_cache:
        return _exemplar_cache

    key = _disk_key()
    try:
        import os as _os
        from diskcache import Cache
        _dc = Cache(_os.environ.get("HELGA_CACHE_DIR", "/tmp/helga-doc-cache"))
        hit = _dc.get(key)
        if hit:
            _exemplar_cache.update(hit)
            return _exemplar_cache
    except Exception as e:
        logger.debug(f"[STANCE] exemplar cache unavailable: {e}")
        _dc = None

    flat, index = [], []
    for key, phrasings in EXEMPLARS.items():
        for p in phrasings:
            flat.append(p)
            index.append(key)
    for p in NEUTRAL_EXEMPLARS:
        flat.append(p)
        index.append(_NEUTRAL)
    vectors = embed_fn(flat)
    for k, vec in zip(index, vectors):
        _exemplar_cache.setdefault(k, []).append(vec)
    if _dc is not None:
        try:
            _dc.set(key, dict(_exemplar_cache))
        except Exception:
            pass
    return _exemplar_cache


def semantic_stance(text, embed_fn=None):
    """(stance, entry) by MEANING, or (ORDINARY, None).

    `embed_fn` is injectable so tests need no Ollama. Never raises.
    """
    blob = (text or "").strip()
    if len(blob) < 8 or not _TOPIC_HINT.search(blob):
        return ORDINARY, None
    try:
        if embed_fn is None:
            from services.common.embeddings import embed, is_available
            if not is_available():
                return ORDINARY, None
            embed_fn = embed
        vectors = _exemplar_vectors(embed_fn)
        probe = embed_fn([blob])[0]
        best_key, best, neutral_best = None, 0.0, 0.0
        for key, vecs in vectors.items():
            for v in vecs:
                s = _cosine(probe, v)
                if key == _NEUTRAL:
                    neutral_best = max(neutral_best, s)
                elif s > best:
                    best_key, best = key, s
        # CONTRASTIVE: the fringe reading must clear the floor AND beat the
        # nearest legitimate question about the same subject. Asking about a
        # topic is not asserting a claim about it.
        if (best_key and best >= SEMANTIC_THRESHOLD
                and best >= neutral_best + SEMANTIC_MARGIN):
            stance, entry = stance_for(_EXEMPLAR_TO_PROBE[best_key])
            if entry:
                logger.info(f"[STANCE] semantic match {best_key} "
                            f"({best:.2f} vs neutral {neutral_best:.2f}) "
                            f"for {blob[:60]!r}")
                return stance, entry
    except Exception as e:
        logger.debug(f"[STANCE] semantic layer unavailable: {e}")
    return ORDINARY, None


def tutor_block(text, concept_text=None, history=None, embed_fn=None,
                semantic=True):
    """The instruction to ride in the tutor prompt, or "" when ORDINARY.

    `concept_text` is what the lesson is about; supplying it makes the response
    PROPORTIONATE — full engagement when the subject is the lesson, a brief
    honest answer and a return to the material when it is not.

    `history` is the conversation so far; supplying it makes the response
    RESILIENT — the tutor holds its answer when the same claim is pressed
    repeatedly, instead of drifting toward agreement.
    """
    stance, entry = stance_for(text)
    if stance == ORDINARY and semantic:
        # Layer one found nothing. Ask layer two, which catches the paraphrases
        # patterns cannot — see the measurement above `_TOPIC_HINT`.
        stance, entry = semantic_stance(text, embed_fn=embed_fn)
    if stance == ORDINARY or not entry:
        return ""

    if stance == CONSENSUS:
        block = (f"{_CONSENSUS_LINE}\n\n"
                 f"  WHERE THE EVIDENCE STANDS: {entry['holds']}\n"
                 f"  THE TEST TO ASK ABOUT: {entry['test']}")
        if entry.get("note"):
            block += f"\n  NOTE: {entry['note']}"
    elif stance == CONFESSIONAL:
        block = _CONFESSIONAL_LINE
    else:
        block = _NORMATIVE_LINE
        if entry.get("note"):
            block += f"\n  NOTE: {entry['note']}"

    if seeking_agreement(text):
        block += "\n" + _AGREEMENT_LINE

    # PRESSURE. Counted BEFORE the topical checks below, because holding a
    # position matters more than where the lesson was going.
    pressed = repeat_count(entry, history)
    if pressed >= 3:
        block += "\n\n" + _FINAL_HOLD_LINE
    elif pressed >= 2:
        block += "\n\n" + _HOLD_LINE

    on_topic = is_on_topic(entry, concept_text)
    if not on_topic:
        block += "\n\n" + _OFF_TOPIC_LINE
    elif stance == NORMATIVE and is_descriptive(concept_text):
        # On subject, but it has turned a descriptive lesson into an
        # evaluative argument. See `_NORMATIVE_IN_DESCRIPTIVE_LINE`.
        block += "\n\n" + _NORMATIVE_IN_DESCRIPTIVE_LINE
    return block


# --- what happens when a COURSE is about one of these ------------------------

_CRITIQUE_FRAME = (
    "THIS COURSE'S TOPIC IS A CLAIM THAT THE EVIDENCE HAS SETTLED AGAINST. "
    "Build it as an EXAMINATION of the claim, never as instruction in it.\n"
    "That means the course teaches: where the belief came from and why it is "
    "persuasive, exactly what it asserts, what those assertions predict, how "
    "those predictions were tested, what was found, and why the relevant "
    "field concluded as it did. It should also teach what makes the claim "
    "durable — which is a real and interesting question.\n"
    "It must NOT be built as though the claim were true, must not be "
    "structured as 'the case for' and 'the case against' as if those were "
    "balanced, and must not conclude that the matter is unresolved. It is "
    "resolved."
)

_BALANCED_FRAME = (
    "THIS COURSE'S TOPIC IS A CONTESTED VALUE QUESTION, and its title takes a "
    "side. Build it as an examination of the DISPUTE rather than a case for "
    "either answer.\n"
    "Give each serious position its strongest form, stated as its own holders "
    "would state it. Separate the factual questions (which evidence can "
    "settle) from the value questions (which it cannot). The course should "
    "leave a learner better able to argue EITHER side, and should not "
    "conclude for one."
)

#: Said to the builder when the TITLE has already announced the conclusion.
_ADVOCACY_NOTE = (
    "  NOTE ON THE TITLE: it announces a conclusion rather than naming a "
    "subject. Build the SUBJECT. Do not let the title's framing set the "
    "module plan, do not adopt its vocabulary, and do not produce a course "
    "whose modules march toward the conclusion the title assumes. A course "
    "that only confirms what its title already asserted has taught nothing."
)

_ABOUT_FRAME = (
    "THIS COURSE'S TOPIC IS A RELIGIOUS OR METAPHYSICAL COMMITMENT. Build it "
    "as a course ABOUT the tradition and its claims — history, texts, "
    "practice, internal diversity, and what both adherents and critics say — "
    "not as a case for the tradition's truth and not as a refutation of it.\n"
    "Attribute claims to those who make them. Do not adjudicate."
)


def course_frame(title, description=""):
    """(stance, instruction) for building a course on this topic.

    The point is REFRAMING, not refusal. "Flat Earth" is a genuinely
    interesting subject — how the belief is argued, why it survives, what
    happens when its predictions are checked — and a course examining it
    teaches more science than one that declines to exist. What must not happen
    is a course that instructs the learner IN the claim.
    """
    blob = f"{title or ''} {description or ''}"

    reason = refusal_for(title, description)
    if reason:
        return REFUSE, (f"This course will not be built: it asks for "
                        f"{reason}. Topics ABOUT this subject — its history, "
                        f"how it is policed, why people are drawn to it — can "
                        f"be taught, and a course framed that way is welcome.")

    stance, entry = stance_for(blob)
    sources = consensus_sources_for(blob)
    # WHERE TO BUILD FROM, for the topics where the open web is contested
    # ground and the assessment bodies are not. Pointing the builder at the
    # body that DID the assessment is the difference between a course built
    # from evidence and one built from whatever ranks.
    source_line = ""
    if sources:
        source_line = ("\n\n  BUILD FROM THE ASSESSMENT BODIES, not from "
                       "general web results, which are contested ground on "
                       "this topic: " + "; ".join(sources) + ".")

    framing = framing_of(title, description)

    if stance == CONSENSUS:
        return stance, (f"{_CRITIQUE_FRAME}\n\n"
                        f"  WHERE THE EVIDENCE STANDS: {entry['holds']}\n"
                        f"  THE DECISIVE TEST: {entry['test']}{source_line}")
    if stance == NORMATIVE:
        frame = _BALANCED_FRAME
        if framing == "advocacy":
            frame += "\n\n" + _ADVOCACY_NOTE
        return stance, frame + source_line
    if stance == CONFESSIONAL:
        return stance, _ABOUT_FRAME

    # ADVOCACY FRAMING ON A TOPIC WITH A SCIENTIFIC CONSENSUS.
    #
    # "Vaccines: the truth they don't want you to know" names no specific
    # fringe claim, so no register pattern fires — but the title has already
    # announced its conclusion, and a course built to that brief is an echo
    # chamber with a syllabus. The consensus-source routing catches the topic;
    # this catches the intent.
    if framing == "advocacy" and sources:
        return CONSENSUS, (_CRITIQUE_FRAME + "\n\n" + _ADVOCACY_NOTE
                           + source_line)

    # A hate-vector claim that SURVIVED the refusal check — because it was
    # framed as study — still gets the critique frame. Study framing decides
    # whether the course is built; it does not make the claim an open question.
    # Without this, "The Great Replacement explained" built as a neutral
    # exposition of a conspiracy theory, which is the worst of both outcomes.
    if _HATE_VECTOR.search(blob):
        return CONSENSUS, (_CRITIQUE_FRAME + "\n\n"
                           "  NOTE: this claim is not merely false; it is used "
                           "to argue against a group of people. Teach what it "
                           "asserts, where it came from, who promotes it and "
                           "why, and what the evidence shows — and do not "
                           "reproduce its rhetoric in the course's own "
                           "voice." + source_line)
    # A topic can be scientifically settled without its TITLE asserting the
    # fringe claim — "climate change" plainly, or "vaccines". No reframing is
    # called for, but the sources still are.
    if source_line:
        return ORDINARY, source_line.strip()
    return ORDINARY, ""
