"""Profile field merging and sanitisation.

Lives here, apart from the Flask route, because the rule it encodes is worth
testing on its own: the Settings page autosaves ONE field per change, so a save
must MERGE. The previous version rebuilt the whole profile from
`data.get(field, default)`, which meant changing the theme silently reset the
learner's name, goals, level and interests. It also dropped `theme` and the
other preference fields entirely while answering 200 {"status": "ok"}.
"""
from typing import Any, Dict

LEVELS = ("beginner", "intermediate", "advanced", "expert")
THEMES = ("light", "dark")

DEFAULTS: Dict[str, Any] = {
    "name": "",
    "level": "intermediate",
    "interests": [],
    "goals": "",
}


def _clamp_int(value: Any, low: int, high: int, fallback: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return fallback


def _clamp_float(value: Any, low: float, high: float, fallback: float) -> float:
    try:
        return max(low, min(high, round(float(value), 2)))
    except (TypeError, ValueError):
        return fallback


def _interests(value: Any) -> list:
    if not isinstance(value, (list, tuple)):
        return []
    return [str(i)[:40].strip() for i in value if isinstance(i, str)][:20]


# key -> sanitiser. A key absent from the request is left untouched, which is
# the whole point: partial saves must not disturb what they do not mention.
SANITISERS = {
    "name":          lambda v: str(v or "")[:50].strip(),
    "goals":         lambda v: str(v or "")[:500].strip(),
    "level":         lambda v: v if v in LEVELS else "intermediate",
    "interests":     _interests,
    "theme":         lambda v: v if v in THEMES else "light",
    "default_voice": lambda v: str(v or "")[:40].strip(),
    "avatar_url":    lambda v: str(v or "")[:2048].strip(),
    "gamification":  bool,
    # A MULTIPLIER, not a percentage: the slider is min=0.8 max=1.4 step=0.1 and
    # the page applies it straight to --font-scale. Clamping it as an integer
    # percent turned 1.2 into 1 and then into the 50 floor, i.e. half-size text.
    "font_scale":    lambda v: _clamp_float(v, 0.8, 1.4, 1.0),
    # The Settings page's own names for three fields this endpoint used to
    # drop on the floor. `display_name` is the one that mattered: the page
    # promises "the tutor will address you by this name", the tutor reads
    # profile['name'], and the value went to display_name — so the tutor never
    # learned anyone's name however many times they typed it.
    "display_name":         lambda v: str(v or "")[:50].strip(),
    "daily_goal":           lambda v: _clamp_int(v, 0, 500, 0),
    "gamification_enabled": bool,
}

# Keys the UI writes under one name that something else reads under another.
# Both are stored, so neither reader has to know about the other.
ALIASES = {"display_name": "name"}


def merge_profile(existing: Dict[str, Any], data: Dict[str, Any]) -> Dict[str, Any]:
    """Apply only the keys present in `data` on top of `existing`."""
    profile = dict(existing or {})
    for key, sanitise in SANITISERS.items():
        if key in (data or {}):
            profile[key] = sanitise(data[key])
            twin = ALIASES.get(key)
            if twin:
                profile[twin] = profile[key]
    return profile


def with_defaults(profile: Dict[str, Any]) -> Dict[str, Any]:
    """A profile that has never been saved still answers with honest defaults."""
    out = dict(DEFAULTS)
    out.update(profile or {})
    return out
