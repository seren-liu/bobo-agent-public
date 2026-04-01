from __future__ import annotations

from app.memory.profile import apply_profile_updates, classify_profile_update_stability, is_profile_update_stable
from app.memory import repository


def test_apply_profile_updates_deep_merges_nested_sections():
    user_id = "u-profile-deep-merge"

    apply_profile_updates(
        user_id,
        {
            "display_preferences": {"theme": {"mode": "dark"}},
            "drink_preferences": {"default_sugar": "少糖"},
        },
    )
    apply_profile_updates(
        user_id,
        {
            "display_preferences": {"theme": {"font_scale": "large"}},
            "drink_preferences": {"default_ice": "少冰"},
        },
    )

    profile = repository.get_profile(user_id)
    assert profile["display_preferences"]["theme"] == {"mode": "dark", "font_scale": "large"}
    assert profile["drink_preferences"] == {"default_sugar": "少糖", "default_ice": "少冰"}


def test_classify_profile_update_stability_distinguishes_stable_and_transient_updates():
    assert classify_profile_update_stability("interaction_preferences.reply_style", "brief") == "stable"
    assert is_profile_update_stable("budget_preferences.soft_price_ceiling", 20) is True
    assert classify_profile_update_stability("budget_preferences.soft_price_ceiling", "最近预算紧") == "transient"
