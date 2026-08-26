"""The Settings page autosaves ONE field per change. The save endpoint rebuilt
the whole profile from `data.get(field, default)`, so every autosave reset the
fields it was not given — changing the theme wiped the learner's name, goals,
level and interests. It also dropped `theme` while answering
200 {"status": "ok"}, which is why the theme control read "Light" on a dark
page after every reload.
"""
from services.common.user_profile import merge_profile, with_defaults


def test_single_field_save_preserves_the_rest():
    saved = merge_profile({}, {"name": "Brennan", "goals": "learn SQL",
                               "level": "advanced", "interests": ["databases"]})
    after = merge_profile(saved, {"theme": "dark"})   # what the theme control sends
    assert after["name"] == "Brennan", "a theme autosave wiped the learner's name"
    assert after["goals"] == "learn SQL"
    assert after["level"] == "advanced"
    assert after["interests"] == ["databases"]
    assert after["theme"] == "dark"


def test_preference_fields_are_stored_not_dropped():
    p = merge_profile({}, {"theme": "dark", "default_voice": "af_sky",
                           "sound_effects": False, "font_scale": 120})
    assert p["theme"] == "dark"
    assert p["default_voice"] == "af_sky"
    assert p["sound_effects"] is False
    assert p["font_scale"] == 120


def test_absent_keys_are_untouched_even_when_falsy():
    """An empty string is a real value; absence is not. Only absence is ignored."""
    p = merge_profile({"name": "Brennan"}, {"goals": ""})
    assert p["name"] == "Brennan"
    assert p["goals"] == ""


def test_invalid_values_fall_back_without_corrupting_the_file():
    assert merge_profile({}, {"level": "sudo"})["level"] == "intermediate"
    assert merge_profile({}, {"theme": "neon"})["theme"] == "light"
    assert merge_profile({}, {"font_scale": "huge"})["font_scale"] == 100
    assert merge_profile({}, {"font_scale": 9999})["font_scale"] == 200
    assert merge_profile({}, {"interests": "not-a-list"})["interests"] == []


def test_never_saved_profile_answers_with_defaults():
    d = with_defaults({})
    assert d["level"] == "intermediate" and d["name"] == "" and d["interests"] == []


def test_defaults_do_not_overwrite_stored_values():
    assert with_defaults({"name": "Brennan"})["name"] == "Brennan"


def test_display_name_reaches_the_tutor():
    """Settings promises "the tutor will address you by this name". The tutor
    reads profile['name'] (services/common/prompts.py); the page saves
    display_name. Both must end up set or the promise is false."""
    p = merge_profile({}, {"display_name": "Brennan"})
    assert p["display_name"] == "Brennan"
    assert p["name"] == "Brennan", "the tutor still cannot see the learner's name"


def test_the_pages_own_keys_are_all_accepted():
    """Every key the Settings page autosaves must survive a round trip; three
    of them (display_name, daily_goal, gamification_enabled) were dropped."""
    sent = {"display_name": "B", "daily_goal": 20, "gamification_enabled": False,
            "theme": "dark", "default_voice": "af_sky", "sound_effects": True,
            "font_scale": 110, "avatar_url": "/x.png"}
    p = merge_profile({}, sent)
    for key in sent:
        assert key in p, f"Settings saves {key!r} and the server drops it"
