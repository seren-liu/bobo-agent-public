from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.memory import repository

_PROFILE_SECTIONS = (
    "display_preferences",
    "drink_preferences",
    "interaction_preferences",
    "budget_preferences",
    "health_preferences",
)

_STABLE_FIELD_PATHS = {
    "drink_preferences.default_sugar",
    "drink_preferences.default_ice",
    "drink_preferences.preferred_brands",
    "drink_preferences.preferred_categories",
    "interaction_preferences.reply_style",
    "budget_preferences.soft_price_ceiling",
    "budget_preferences.price_sensitive",
}

_TRANSIENT_HINTS = (
    "最近",
    "这阵子",
    "暂时",
    "先别",
    "近期",
    "本周",
    "今晚",
    "这周",
)


def _deep_merge(existing: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(existing)
    for key, value in patch.items():
        if not isinstance(value, dict):
            if value is not None:
                merged[key] = deepcopy(value)
            continue
        target = merged.get(key)
        if not isinstance(target, dict):
            target = {}
        merged[key] = _deep_merge(target, value)
    return merged


def _clean_patch_value(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, inner_value in value.items():
            cleaned_inner = _clean_patch_value(inner_value)
            if cleaned_inner is not None:
                cleaned[key] = cleaned_inner
        return cleaned
    if value is None:
        return None
    return deepcopy(value)


def merge_profile_patch(existing: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    return _deep_merge(existing, patch)


def classify_profile_update_stability(field_path: str | None, value: Any, raw_text: str | None = None) -> str:
    text = " ".join(part for part in [field_path or "", str(value or ""), raw_text or ""] if part).strip()
    if text and any(hint in text for hint in _TRANSIENT_HINTS):
        return "transient"

    if field_path and field_path in _STABLE_FIELD_PATHS:
        return "stable"

    if field_path and field_path.startswith("interaction_preferences."):
        return "stable"

    if field_path and field_path.startswith("budget_preferences."):
        return "stable" if isinstance(value, (int, float, bool)) else "unknown"

    return "unknown"


def is_profile_update_stable(field_path: str | None, value: Any, raw_text: str | None = None) -> bool:
    return classify_profile_update_stability(field_path, value, raw_text=raw_text) == "stable"


def apply_profile_updates(user_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    current = repository.get_profile(user_id)
    merged = merge_profile_patch(current, updates)
    payload = {section: _clean_patch_value(merged.get(section, {})) for section in _PROFILE_SECTIONS}
    return repository.patch_profile(user_id, payload)


def get_profile(user_id: str) -> dict[str, Any]:
    return repository.get_profile(user_id)


def patch_profile(user_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    return apply_profile_updates(user_id, patch)


def derive_profile_candidates_from_stats(user_id: str) -> dict[str, Any]:
    return repository.derive_profile_candidates_from_stats(user_id)


def refresh_profile_from_records(user_id: str) -> dict[str, Any]:
    candidates = derive_profile_candidates_from_stats(user_id)
    if not candidates:
        return repository.get_profile(user_id)
    return apply_profile_updates(user_id, candidates)
