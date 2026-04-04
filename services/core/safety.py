"""
Safety Module for Helga Socratic Voice Tutor

Provides multi-category safety checks including:
- NSFW content detection
- Violence/self-harm detection
- Prompt injection detection
- Input length validation
- Hate speech detection

Uses TF-IDF vectorization for keyword-based detection with context-aware overrides
for legitimate educational content (biology, history, etc.).
"""

import re
import logging
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

logger = logging.getLogger(__name__)

# ============================================================================
# CONFIGURATION
# ============================================================================

MAX_INPUT_LENGTH = 2000  # Maximum characters allowed per input
SIMILARITY_THRESHOLD = 0.65  # Cosine similarity threshold — raised to reduce false positives on academic text

# ============================================================================
# SAFETY CATEGORIES
# ============================================================================

NSFW_KEYWORDS = [
    "sex", "nude", "porn", "erotic", "adult", "nsfw", "explicit",
    "sexual", "naked", "orgasm", "masturbation", "intercourse",
    "vagina", "penis", "breast", "genitalia", "arousal", "fetish",
    "bondage", "pornography"
]

VIOLENCE_KEYWORDS = [
    "kill", "murder", "stab", "shoot", "bomb", "weapon",
    "assault", "attack", "torture", "strangle", "massacre",
    "slaughter", "execute", "assassinate", "bloodshed"
]

SELF_HARM_KEYWORDS = [
    "suicide", "self-harm", "cut myself", "end my life", "kill myself",
    "want to die", "overdose", "self-injury", "harm myself",
    "no reason to live", "better off dead"
]

HATE_SPEECH_KEYWORDS = [
    "racial slur", "hate crime", "supremacy", "ethnic cleansing",
    "genocide", "inferior race", "exterminate", "dehumanize"
]

# Prompt injection patterns
PROMPT_INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?previous\s+instructions",
    r"ignore\s+(all\s+)?above",
    r"disregard\s+(all\s+)?previous",
    r"forget\s+(all\s+)?previous",
    r"you\s+are\s+now\s+(?:a|an)\s+(?:evil|bad|malicious)",
    r"pretend\s+you\s+are",
    r"act\s+as\s+(?:if|though)\s+you",
    r"new\s+instruction[s]?\s*:",
    r"system\s*prompt\s*:",
    r"override\s+(?:your|the)\s+(?:rules|instructions|guidelines)",
    r"jailbreak",
    r"do\s+anything\s+now",
    r"developer\s+mode",
]

# Safe educational contexts that override certain categories
SAFE_CONTEXTS = {
    "nsfw": ["biology", "anatomy", "reproduction", "medical", "health", "science",
             "physiology", "genetics", "evolutionary", "hormones"],
    "violence": ["history", "war", "military", "conflict", "revolution", "defense",
                 "criminal justice", "criminology", "forensic", "security"],
    "self_harm": ["psychology", "mental health", "counseling", "therapy",
                  "suicide prevention", "crisis intervention", "clinical"],
    "hate_speech": ["history", "civil rights", "sociology", "diversity",
                    "discrimination", "social justice", "anthropology"],
}


# ============================================================================
# RESULT DATACLASS
# ============================================================================

@dataclass
class SafetyResult:
    """Result of a safety check."""
    is_safe: bool
    category: str = "safe"  # safe, nsfw, violence, self_harm, hate_speech, prompt_injection, input_too_long
    confidence: float = 0.0
    message: str = ""
    details: dict = field(default_factory=dict)


# ============================================================================
# VECTORIZER INITIALIZATION
# ============================================================================

# Combine all keyword categories for a shared vocabulary
_all_keywords = NSFW_KEYWORDS + VIOLENCE_KEYWORDS + SELF_HARM_KEYWORDS + HATE_SPEECH_KEYWORDS
_vectorizer = TfidfVectorizer()
_all_vectors = _vectorizer.fit_transform(_all_keywords)

# Partition vectors by category for targeted matching
_nsfw_end = len(NSFW_KEYWORDS)
_violence_end = _nsfw_end + len(VIOLENCE_KEYWORDS)
_self_harm_end = _violence_end + len(SELF_HARM_KEYWORDS)
_hate_end = _self_harm_end + len(HATE_SPEECH_KEYWORDS)

_nsfw_vectors = _all_vectors[:_nsfw_end]
_violence_vectors = _all_vectors[_nsfw_end:_violence_end]
_self_harm_vectors = _all_vectors[_violence_end:_self_harm_end]
_hate_vectors = _all_vectors[_self_harm_end:_hate_end]

logger.info(f"Safety module initialized: {len(NSFW_KEYWORDS)} NSFW, {len(VIOLENCE_KEYWORDS)} violence, "
            f"{len(SELF_HARM_KEYWORDS)} self-harm, {len(HATE_SPEECH_KEYWORDS)} hate speech keywords. "
            f"{len(PROMPT_INJECTION_PATTERNS)} injection patterns.")


# ============================================================================
# PUBLIC API
# ============================================================================

def check_safety(user_input: str, current_node_title: str = None) -> bool:
    """
    Legacy-compatible safety check.
    Returns True if safe, False if unsafe.
    """
    result = check_safety_detailed(user_input, current_node_title)
    return result.is_safe


def check_safety_detailed(user_input: str, current_node_title: str = None) -> SafetyResult:
    """
    Comprehensive safety check returning detailed results.
    
    Args:
        user_input: The user's text input to check.
        current_node_title: The current educational context (lesson/concept title).
    
    Returns:
        SafetyResult with is_safe, category, confidence, and message.
    """
    if not user_input:
        return SafetyResult(is_safe=True, category="safe", confidence=1.0)
    
    title = (current_node_title or "").lower().strip()
    
    # 1. Input length validation
    if len(user_input) > MAX_INPUT_LENGTH:
        logger.warning(f"Safety: Input too long ({len(user_input)} chars, max {MAX_INPUT_LENGTH})")
        return SafetyResult(
            is_safe=False,
            category="input_too_long",
            confidence=1.0,
            message=f"Your message is too long. Please keep it under {MAX_INPUT_LENGTH} characters.",
            details={"length": len(user_input), "max": MAX_INPUT_LENGTH}
        )
    
    # 2. Prompt injection detection (regex-based, high priority)
    input_lower = user_input.lower()
    for pattern in PROMPT_INJECTION_PATTERNS:
        if re.search(pattern, input_lower):
            logger.warning(f"Safety: Prompt injection detected: pattern='{pattern}' in input '{user_input[:80]}...'")
            return SafetyResult(
                is_safe=False,
                category="prompt_injection",
                confidence=0.9,
                message="I'm designed to help you learn. Let's stay focused on your studies!",
                details={"matched_pattern": pattern}
            )
    
    # 3. Self-harm check (highest priority — provide supportive response)
    self_harm_result = _check_category(user_input, _self_harm_vectors, "self_harm", title)
    if not self_harm_result.is_safe:
        self_harm_result.message = (
            "I care about your well-being. If you're struggling, please reach out to the "
            "988 Suicide & Crisis Lifeline by calling or texting 988. "
            "Let's continue with your learning when you're ready."
        )
        return self_harm_result
    
    # 4. NSFW check
    nsfw_result = _check_category(user_input, _nsfw_vectors, "nsfw", title)
    if not nsfw_result.is_safe:
        nsfw_result.message = "Let's keep our conversation focused on the learning material. How about we continue with the lesson?"
        return nsfw_result
    
    # 5. Violence check
    violence_result = _check_category(user_input, _violence_vectors, "violence", title)
    if not violence_result.is_safe:
        violence_result.message = "I'm here to help you learn. Let's redirect to the educational content."
        return violence_result
    
    # 6. Hate speech check
    hate_result = _check_category(user_input, _hate_vectors, "hate_speech", title)
    if not hate_result.is_safe:
        hate_result.message = "Let's maintain a respectful learning environment. Can we return to the lesson material?"
        return hate_result
    
    return SafetyResult(is_safe=True, category="safe", confidence=1.0)


# ============================================================================
# INTERNAL HELPERS
# ============================================================================

_CATEGORY_KEYWORDS = {
    "nsfw": NSFW_KEYWORDS,
    "violence": VIOLENCE_KEYWORDS,
    "self_harm": SELF_HARM_KEYWORDS,
    "hate_speech": HATE_SPEECH_KEYWORDS,
}


def _check_category(user_input: str, category_vectors, category_name: str, title: str) -> SafetyResult:
    """Check user input against a specific safety category."""
    try:
        # Pre-check: require at least one keyword substring match before running TF-IDF.
        # This prevents false positives from common English words dominating the small vocabulary.
        input_lower = user_input.lower()
        keywords = _CATEGORY_KEYWORDS.get(category_name, [])
        has_keyword_hit = any(kw in input_lower for kw in keywords)
        if not has_keyword_hit:
            return SafetyResult(is_safe=True, category="safe", confidence=1.0)

        user_vector = _vectorizer.transform([user_input])
        similarities = cosine_similarity(user_vector, category_vectors)
        max_similarity = float(np.max(similarities))

        is_flagged = max_similarity > SIMILARITY_THRESHOLD
        
        if is_flagged:
            # Check for educational context override
            safe_contexts = SAFE_CONTEXTS.get(category_name, [])
            is_safe_context = any(ctx in title for ctx in safe_contexts)
            
            if is_safe_context:
                logger.debug(f"Safety: {category_name} override by safe context '{title}' (similarity={max_similarity:.2f})")
                return SafetyResult(is_safe=True, category="safe", confidence=1.0 - max_similarity)
            
            logger.info(f"Safety BLOCK [{category_name}]: similarity={max_similarity:.2f}, context='{title}'")
            return SafetyResult(
                is_safe=False,
                category=category_name,
                confidence=max_similarity,
                details={"max_similarity": max_similarity, "context": title}
            )
        
        return SafetyResult(is_safe=True, category="safe", confidence=1.0 - max_similarity)
        
    except Exception as e:
        logger.error(f"Safety check error for category '{category_name}': {e}")
        # Fail open on errors — don't block legitimate educational use
        return SafetyResult(is_safe=True, category="safe", confidence=0.0,
                           details={"error": str(e)})


def get_safety_redirect_message(result: SafetyResult) -> str:
    """Get a user-friendly redirect message for a blocked input."""
    if result.message:
        return result.message
    
    defaults = {
        "nsfw": "Let's keep our conversation focused on learning.",
        "violence": "I'm here to help you learn. Let's stay on topic.",
        "self_harm": "If you're struggling, please reach out to 988 Suicide & Crisis Lifeline.",
        "hate_speech": "Let's maintain a respectful learning environment.",
        "prompt_injection": "I'm designed to help you learn. Let's focus on your studies!",
        "input_too_long": f"Please keep your message under {MAX_INPUT_LENGTH} characters.",
    }
    return defaults.get(result.category, "Let's continue with your lesson.")