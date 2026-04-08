"""
Shared prompt templates for all services.

This module consolidates all prompt generation functions used across the system,
providing a single source of truth for LLM interactions.

All prompt functions return a list of message dicts compatible with the
OpenAI chat completions API:
  [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}, ...]

This makes the system model-agnostic — Ollama applies the correct chat template
for whichever model is configured.
"""

import uuid
import re

# Generate a session-unique hash
session_hash = str(uuid.uuid4())


def generate_security_token():
    """Generate a unique security token for session management."""
    return str(uuid.uuid4())


# --- EXAMINER PROMPTS (Spaced Repetition) ---

def get_examiner_question_prompt(context_text):
    """
    Generates a prompt for the Examiner to ask a question.

    Returns:
        list: Messages array for chat completions API
    """
    return [{"role": "system", "content": f"""You are the EXAMINER. Your job is to test the student's understanding of the provided text.

Context:
"{context_text}"

Task:
- Ask ONE open-ended conceptual question based *strictly* on the context.
- The question should test deep understanding, not just keyword matching.
- Do NOT provide the answer.
- Output ONLY the question text."""}]


def get_examiner_grade_prompt(question, user_answer, context_text):
    """
    Generates a prompt to grade the user's answer (JSON output).

    Returns:
        list: Messages array for chat completions API
    """
    return [{"role": "system", "content": f"""You are the EXAMINER. You must grade the student's answer.

Question: "{question}"
Student Answer: "{user_answer}"
Source Truth Context: "{context_text}"

Task:
1. Compare the Student Answer against the Source Truth.
2. Determine if the answer is conceptually correct.
3. Assign a grade: PASS or FAIL.
4. Provide a score (0-100).
5. Identify any key concepts from the context that were missing.
6. Provide a concise simplified explanation of the correct answer (Feedback).

Output Format: JSON ONLY
{{
  "grade": "PASS" | "FAIL",
  "score": int,
  "missing_concepts": ["concept1", "concept2"],
  "feedback": "string"
}}"""}]

# --- SOCRATIC TUTOR PROMPTS ---

# 6 typed question types that form a pedagogical progression per concept
SOCRATIC_QUESTION_TYPES = [
    {
        "key": "SCENARIO",
        "name": "Scenario",
        "icon": "\U0001f3ac",
        "color": "#3b82f6",
        "description": "Build intuition via real-world setup",
        "instruction": (
            "QUESTION TYPE: SCENARIO.\n"
            "Present a concrete, real-world SCENARIO that naturally leads the student toward the concept. "
            "Describe a situation and ask what they think would happen or what they notice. "
            "Do NOT name the concept directly \u2014 let them discover it through the scenario.\n"
            "Example pattern: 'Imagine you are [situation]. What do you think happens when [trigger]?'"
        )
    },
    {
        "key": "MECHANISM",
        "name": "Mechanism",
        "icon": "\u2699\ufe0f",
        "color": "#8b5cf6",
        "description": "Probe causal understanding",
        "instruction": (
            "QUESTION TYPE: MECHANISM.\n"
            "The student has some intuition. Now probe the WHY \u2014 ask about the underlying mechanism or cause. "
            "Use 'why' or 'how does' questions. Push for causal explanation, not just description.\n"
            "Example pattern: 'Why do you think [phenomenon] happens?' or 'What causes [effect]?'"
        )
    },
    {
        "key": "CONTRAST",
        "name": "Contrast",
        "icon": "\u2696\ufe0f",
        "color": "#ec4899",
        "description": "Sharpen distinctions",
        "instruction": (
            "QUESTION TYPE: CONTRAST.\n"
            "Ask the student to distinguish between two related but different ideas. "
            "Present a comparison or ask them to explain the difference between similar concepts. "
            "This sharpens their mental model.\n"
            "Example pattern: 'How is [A] different from [B]?' or 'Could someone confuse [X] with [Y]? Why?'"
        )
    },
    {
        "key": "APPLICATION",
        "name": "Application",
        "icon": "\U0001f527",
        "color": "#f59e0b",
        "description": "Transfer knowledge to new context",
        "instruction": (
            "QUESTION TYPE: APPLICATION.\n"
            "Ask the student to APPLY what they've learned to a new, unfamiliar context. "
            "Give them a novel problem or scenario they haven't seen and ask them to use the concept. "
            "This tests transfer, not recall.\n"
            "Example pattern: 'How would you use [concept] to solve [new problem]?' or 'Design a [thing] that uses [principle].'"
        )
    },
    {
        "key": "EDGE_CASE",
        "name": "Edge Case",
        "icon": "\U0001f52c",
        "color": "#ef4444",
        "description": "Test boundaries and exceptions",
        "instruction": (
            "QUESTION TYPE: EDGE CASE.\n"
            "Push the student to the boundaries of the concept. Ask about extreme cases, exceptions, or limitations. "
            "What happens when assumptions break down?\n"
            "Example pattern: 'What happens if [extreme condition]?' or 'When does [concept] stop working?'"
        )
    },
    {
        "key": "SYNTHESIS",
        "name": "Synthesis",
        "icon": "\U0001f9e9",
        "color": "#10b981",
        "description": "Connect to broader context",
        "instruction": (
            "QUESTION TYPE: SYNTHESIS.\n"
            "Ask the student to connect this concept to something else they've learned, a broader principle, "
            "or a real-world implication. This is the highest level \u2014 combining ideas.\n"
            "Example pattern: 'How does [concept] relate to [previous topic]?' or 'What would the world look like if [concept] didn't exist?'"
        )
    },
]


def get_typed_socratic_prompt(question_type_key, context_text, conversation_history,
                               system_note=None, misconceptions=None, analogies=None,
                               style_modifier=None, user_profile=None, bloom_level=1,
                               prior_concepts=None):
    """
    Generates a Socratic prompt with a specific question TYPE instruction injected.

    Returns:
        list: Messages array for chat completions API
    """
    # Find the type instruction
    type_instruction = ""
    for qt in SOCRATIC_QUESTION_TYPES:
        if qt["key"] == question_type_key:
            type_instruction = qt["instruction"]
            break

    # Combine type instruction with any existing system note
    combined_note = type_instruction
    if system_note:
        combined_note += f"\n{system_note}"

    return get_socratic_tutor_prompt(
        context_text, conversation_history,
        system_note=combined_note,
        misconceptions=misconceptions,
        analogies=analogies,
        style_modifier=style_modifier,
        user_profile=user_profile,
        bloom_level=bloom_level,
        prior_concepts=prior_concepts,
    )


def get_socratic_tutor_prompt(context_text, conversation_history, system_note=None, misconceptions=None, analogies=None, style_modifier=None, user_profile=None, bloom_level=1, prior_concepts=None):
    """
    Generates a Socratic question or response as a messages array.

    Returns:
        list: Messages array for chat completions API
    """
    hook = ""
    notes = ""
    if context_text:
        # Match both old "Socratic Hook" and new "Socratic Hooks" section names
        hook_match = re.search(r"## Socratic Hooks?\n(.*?)(?=\n## |\Z)", context_text, re.DOTALL)
        if hook_match:
            hook = hook_match.group(1).strip()

        # Match both old "Advanced Notes" and new "Edge Cases & Limitations"
        notes_match = re.search(r"## (?:Advanced Notes|Edge Cases & Limitations)\n(.*?)(?=\n## |\Z)", context_text, re.DOTALL)
        if notes_match:
            notes = notes_match.group(1).strip()

    misc_str = ""
    if misconceptions:
        misc_str = f"\nWARNING: Students often believe {', '.join(misconceptions)}. Correct this if they mention it."

    analog_str = ""
    if analogies:
        analog_str = f"\nTOOL: Use the analogy of {', '.join(analogies)} if they are stuck."

    # User profile personalization
    profile_str = ""
    if user_profile:
        parts = []
        if user_profile.get('name'):
            parts.append(f"The student's name is {user_profile['name']}.")
        if user_profile.get('interests'):
            interests = ', '.join(user_profile['interests'][:5])
            parts.append(f"They are interested in: {interests}. Use these domains for analogies when possible.")
        if user_profile.get('goals'):
            parts.append(f"Their learning goal: {user_profile['goals'][:100]}.")
        if parts:
            profile_str = "\nSTUDENT PROFILE: " + " ".join(parts)

    # Dynamic Persona Configuration
    persona_str = "You are the SOCRATIC TUTOR."
    style_constraint = ""
    if style_modifier:
        style_lower = style_modifier.lower().strip()
        if "eli5" in style_lower or "five" in style_lower or "child" in style_lower or "simple" in style_lower:
            persona_str = "You are a FRIENDLY SOCRATIC TUTOR for young learners."
            style_constraint = "\nUse simple language and everyday metaphors. Avoid jargon."
        elif "academic" in style_lower or "strict" in style_lower or "formal" in style_lower:
            persona_str = "You are a RIGOROUS ACADEMIC SOCRATIC TUTOR."
            style_constraint = "\nUse precise academic language. Expect well-structured answers."
        elif "analogy" in style_lower or "analogies" in style_lower or "metaphor" in style_lower:
            persona_str = "You are a CREATIVE SOCRATIC TUTOR who loves analogies."
            style_constraint = "\nAlways include a vivid analogy or metaphor."
        elif "drill" in style_lower or "quiz" in style_lower:
            persona_str = "You are a DRILL SERGEANT SOCRATIC TUTOR."
            style_constraint = "\nBe direct and rapid-fire. Ask precise factual questions."
        else:
            persona_str = "You are the SOCRATIC TUTOR."
            style_constraint = f"\n{style_modifier}"

    hook_str = ""
    # Inject hook directive heavily on the first real turn
    if hook and len(conversation_history) <= 1:
        hook_str = f'\nRECOMMENDED OPENING QUESTION (Use this or a variation to start the lesson):\n"{hook}"'

    notes_str = ""
    if notes:
        notes_str = f"\nADVANCED INSTRUCTOR NOTES:\n{notes}"

    # Bloom's Taxonomy cognitive level directive
    bloom_labels = {
        1: ("Remember", "Ask questions that test recall of facts and basic definitions."),
        2: ("Understand", "Ask questions that test comprehension — can the student explain the idea in their own words?"),
        3: ("Apply", "Ask questions that require applying the concept to a new situation or problem."),
        4: ("Analyze", "Ask questions that require breaking down the concept, comparing parts, or identifying relationships."),
        5: ("Evaluate", "Ask questions that require judging, critiquing, or defending a position about the concept."),
        6: ("Create", "Ask questions that require the student to design, propose, or synthesize something new using the concept."),
    }
    bloom_name, bloom_directive = bloom_labels.get(bloom_level, bloom_labels[1])
    bloom_str = f"\nCOGNITIVE LEVEL (Bloom's Taxonomy): Level {bloom_level} — {bloom_name}. {bloom_directive}"

    # GAP 7: Prior concepts for continuity across transitions
    prior_str = ""
    if prior_concepts:
        summaries = [f"{p.get('title', '?')} (Bloom {p.get('bloom_achieved', '?')})" for p in prior_concepts[-3:]]
        prior_str = f"\nPREVIOUS CONCEPTS COVERED: {', '.join(summaries)}. You may reference these to build connections."

    # Build system prompt
    system_content = f"""{persona_str}

YOUR ROLE: You are a Socratic Tutor. Your goal is to help the student build mental models by reasoning from simple premises.
CRITICAL: The student has NOT read the text. You must TEACH them by guiding them through reasoning, not quizzing them on facts they haven't learned yet.

TEACHING STRATEGY:
1. Never ask for a definition (e.g., "What is selection bias?") as an opening question. They don't know yet.
2. Use Scenarios: Describe a simple real-world situation, then ask the student what they think would happen or why.
   Bad: "What is the difference between observational and experimental data?"
   Good: "If we just watch people who choose to take a vitamin, versus specifically assigning who takes it, which group gives us better proof that the vitamin works? Why?"
3. Handle 'I don't know':
   - If the student says "I don't know", "I'm stuck", or admits ignorance (or if the system notes indicate this):
   - STOP ASKING QUESTIONS.
   - EXPLAIN the concept simply (Micro-Lecture, 2-3 sentences).
   - Then ask a simple verification question to check they got it.
   - NEVER say "You're close" if they admit ignorance.
4. Fill In The Gaps: Use your broad knowledge base to supplement the reference material. If the reference text lacks sufficient detail to properly teach the concept, fill in the gaps with accurate information, analogies, and examples to ensure a comprehensive lesson.

STRICT OUTPUT RULES:
- Write 2-4 sentences in plain conversational text.
- If the SYSTEM NOTE mentions the student's prior answer, START by briefly acknowledging their answer (what was right or wrong), THEN smoothly transition into your next question. This should feel like one flowing response, not two separate messages.
- Ask exactly ONE question per response. The question MUST be the last sentence, ending with a question mark (?).
- NEVER use markdown formatting (no #, ##, -, *, bold, italic, numbered lists).
- NEVER include meta-commentary ("Let's explore", "Great question", "That's interesting").
- NEVER repeat the context material verbatim.
- NEVER prefix your response with a role label like "Tutor:" or "Lecturer:".
{style_constraint}{profile_str}{misc_str}{analog_str}{bloom_str}{prior_str}{hook_str}{notes_str}

INSTRUCTOR NOTES (pedagogical guidance only — the student has NOT seen any of this material):
"{context_text}" """

    messages = [{"role": "system", "content": system_content}]

    # Add conversation history
    for user_text, assistant_text in conversation_history:
        if user_text:
            messages.append({"role": "user", "content": user_text})
        if assistant_text:
            messages.append({"role": "assistant", "content": assistant_text})

    # Inject system note as a final system message if present
    if system_note:
        messages.append({"role": "system", "content": system_note})

    return messages


def get_socratic_grading_prompt(concept, question, user_answer, context_text="",
                                 bloom_level=None, learning_objectives=None,
                                 mastery_criteria=None):
    """
    Generates a prompt to grade a Socratic answer using FSRS grades.
    GAP 5: Now Bloom-aware — grading criteria scale with cognitive level.

    Returns:
        list: Messages array for chat completions API
    """
    context_str = f'\nSource Truth Context: "{context_text}"\n' if context_text else ""

    # GAP 5: Bloom-scaled grading criteria — concept-specific overrides generic
    bloom_criteria = ""
    if mastery_criteria:
        bloom_criteria = f"\nCONCEPT-SPECIFIC MASTERY CRITERIA:\n{mastery_criteria}"
    elif bloom_level and bloom_level > 1:
        bloom_map = {
            2: "Bloom 2 (Understand): Grade 3 requires the student to explain in their own words, not just recall.",
            3: "Bloom 3 (Apply): Grade 3 requires demonstrating how to apply the concept to a situation.",
            4: "Bloom 4 (Analyze): Grade 3 requires breaking down, comparing, or identifying relationships.",
            5: "Bloom 5 (Evaluate): Grade 3 requires judging, critiquing, or defending a position.",
            6: "Bloom 6 (Create): Grade 3 requires proposing or synthesizing something original.",
        }
        bloom_criteria = f"\nCOGNITIVE LEVEL: {bloom_map.get(bloom_level, bloom_map.get(3, ''))}"

    objectives_str = ""
    if learning_objectives and len(learning_objectives) > 0:
        obj_list = ", ".join(str(o) for o in learning_objectives[:3])
        objectives_str = f"\nLearning Objectives: {obj_list}"

    return [{"role": "system", "content": f"""You are a strict grading assistant. Grade the student's answer.

Concept: {concept}
Question: {question}
Student Answer: {user_answer}
{context_str}
Evaluate the student's mastery. Be STRICT — do not give Grade 3 unless the answer truly demonstrates understanding.

- Grade 1 (Again): Wrong, "I don't know", admitted ignorance, off-topic, or answer is just restating the question.
- Grade 2 (Hard): Partially correct but vague, just keywords without reasoning, restating facts without explaining WHY, or missing the core mechanism. If the student copies text without showing they understand it, this is Grade 2.
- Grade 3 (Good): Correct AND explains the reasoning/mechanism. The student must show they understand WHY, not just WHAT. They connect cause to effect or explain the underlying logic.
- Grade 4 (Easy): EXCEPTIONAL — novel connection, unprompted edge case, precise mechanism explanation, or multi-concept synthesis. RARE (1 in 5 correct answers).
{bloom_criteria}{objectives_str}
GRADING RULES:
- If the student admits they don't know -> Grade 1, always.
- If the answer is just keywords or definitions without reasoning -> Grade 2, not 3.
- If the answer restates content without explaining the mechanism -> Grade 2.
- Only Grade 3 if the student demonstrates causal reasoning or applies the concept.
- The "feedback" field is MANDATORY and SPOKEN aloud. It must reference something SPECIFIC from the student's answer. NEVER write generic feedback like "Correct" or "Good answer".

You MUST output valid JSON only, nothing else:
{{"grade": 3, "reason": "brief reason", "missing_concepts": [], "feedback": "Your point about X shows understanding of Y."}}"""}]



# --- ADAPTIVE TEACHING PROMPTS ---

def get_teaching_mode_classifier_prompt(user_text):
    """
    Classifies user response to determine if they need a LECTURE (explanation) or QUESTIONS.
    """
    return [{"role": "system", "content": f"""You are a Teacher's Assistant. Analyze the student's response.

Classify as either:
- LECTURE: If student says "I don't know", "unsure", "explain", "help", or admits ignorance.
- QUESTION: If student attempts an answer (even if wrong/partial) or asks a clarifying question about the topic.

Input: "{user_text}"
Return ONLY the single word: LECTURE or QUESTION."""}]

# --- MICRO-LECTURE PROMPTS ---

def get_micro_lecture_prompt(topic, context_text, history=[], style_modifier="standard",
                             missing_concepts=None, next_question_type=None, bloom_level=1,
                             prior_concepts=None):
    """
    Generates a short explanation (Micro-Lecture) for introducing a topic or after failed attempts.
    The lecture always ends with a follow-up question whose type matches the current
    Socratic progression stage (SCENARIO -> MECHANISM -> CONTRAST -> APPLICATION -> EDGE_CASE -> SYNTHESIS).

    Returns:
        list: Messages array for chat completions API
    """
    context_safe = (context_text or "")[:2000]

    # Build recent conversation context from history (last 2 exchanges)
    history_str = ""
    if history:
        recent = history[-2:] if len(history) > 2 else history
        history_parts = []
        for user_text, assistant_text in recent:
            if user_text:
                history_parts.append(f"Student: {user_text}")
            if assistant_text:
                history_parts.append(f"Tutor: {assistant_text}")
        if history_parts:
            history_str = "\n\nRecent conversation:\n" + "\n".join(history_parts)

    missing_str = ""
    if missing_concepts:
        missing_str = (
            f"\n\nThe student specifically struggled with: {', '.join(missing_concepts)}. "
            f"Focus your explanation on these gaps."
        )

    # Map question type to follow-up style so lecture flows into the right Socratic mode
    question_style_map = {
        "SCENARIO": "Describe a concrete real-world scenario and ask what the student thinks would happen.",
        "MECHANISM": "Ask WHY or HOW something works — probe the underlying mechanism.",
        "CONTRAST": "Ask the student to compare or contrast two related ideas.",
        "APPLICATION": "Give a novel problem and ask the student to apply the concept.",
        "EDGE_CASE": "Ask about an extreme case, exception, or limitation.",
        "SYNTHESIS": "Ask the student to connect this concept to something else they've learned.",
    }
    q_type = next_question_type or "SCENARIO"
    question_style = question_style_map.get(q_type, question_style_map["SCENARIO"])

    # Bloom's Taxonomy cognitive level for the follow-up question
    bloom_labels = {
        1: ("Remember", "Your follow-up question should test basic recall of what you just explained."),
        2: ("Understand", "Your follow-up question should check if the student can explain the idea in their own words."),
        3: ("Apply", "Your follow-up question should ask the student to apply the concept to a new situation."),
        4: ("Analyze", "Your follow-up question should ask the student to break down or compare aspects of the concept."),
        5: ("Evaluate", "Your follow-up question should ask the student to judge or critique an aspect of the concept."),
        6: ("Create", "Your follow-up question should ask the student to propose or design something using the concept."),
    }
    bloom_name, bloom_directive = bloom_labels.get(bloom_level, bloom_labels[1])
    bloom_str = f"\nCognitive Level (Bloom's): Level {bloom_level} — {bloom_name}. {bloom_directive}"

    return [{"role": "system", "content": f"""You are the LECTURER. The student is learning about '{topic}'.
Teaching Style: {style_modifier}

Context:
"{context_safe}"
{history_str}{missing_str}

Task:
1. Explain the concept clearly and simply in 2-4 sentences.
2. Use an analogy if helpful.
3. Keep the explanation under 100 words.
4. Fill in the gaps: Use your broad knowledge base to supplement the reference Context.
5. NEVER use markdown formatting (no #, ##, -, *, **, numbered lists, bold, italic). Write plain conversational text only.
6. NEVER prefix your response with a role label like "Lecturer:" or "Tutor:".
{bloom_str}

FOLLOW-UP QUESTION (MANDATORY):
After your explanation, you MUST end with exactly ONE engaging follow-up question.
Question style for this turn: {question_style}
The very last sentence of your entire response MUST be this question, ending with a question mark (?).
Do NOT ask for a definition. Ask the student to think, reason, or apply."""}]


# --- BRIDGE PROMPTS (Transition between topics) ---

def get_bridge_prompt(last_title, next_title):
    """
    Creates a natural bridge sentence connecting two topics.

    Returns:
        list: Messages array for chat completions API
    """
    return [{"role": "system", "content": f"""Previous topic: {last_title}
Next topic: {next_title}

Task: Create a natural bridge sentence (under 20 words) connecting these topics. Write plain text only, no markdown."""}]


# --- HINT PROMPTS ---

def get_hint_prompt(card_title, card_text, attempts):
    """
    Generates a hint for a struggling student.

    Returns:
        list: Messages array for chat completions API
    """
    hint_type = "subtle" if attempts == 1 else "direct"
    return [{"role": "system", "content": f"""context: {card_text}
Concept: {card_title}
Task: Provide a {hint_type} hint (under 15 words) without revealing the answer."""}]


# --- ENRICHMENT PROMPTS (Ingestion) ---

def get_concept_extraction_prompt(text):
    """
    Analyzes text to extract key concepts for knowledge graph linking.

    Returns:
        list: Messages array for chat completions API
    """
    return [{"role": "system", "content": f"""Analyze the following text. Identify the 5 most critical scientific or historical concepts mentioned that might need external definition.

Text Snippet:
"{text[:2000]}..."

Task:
1. Identify 5 key concepts (noun phrases).
2. Return as a JSON list of strings.

Output JSON ONLY: ["Concept 1", "Concept 2", ...]"""}]
