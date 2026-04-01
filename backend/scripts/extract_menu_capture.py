from __future__ import annotations

from typing import Any


def resolve_list_path(data: Any, path: str) -> list[Any]:
    if not path:
        return data if isinstance(data, list) else []

    nodes: list[Any] = [data]
    for raw in path.split("."):
        token = raw.strip()
        if not token:
            continue

        next_nodes: list[Any] = []
        is_list_token = token.endswith("[]")
        key = token[:-2] if is_list_token else token

        for node in nodes:
            if key == "*":
                if isinstance(node, dict):
                    vals = list(node.values())
                elif isinstance(node, list):
                    vals = node
                else:
                    vals = []
            elif isinstance(node, dict):
                vals = node.get(key)
            else:
                vals = None

            if is_list_token:
                if isinstance(vals, list):
                    next_nodes.extend(vals)
            else:
                if vals is not None:
                    next_nodes.append(vals)

        nodes = next_nodes

    flattened: list[Any] = []
    for node in nodes:
        if isinstance(node, list):
            flattened.extend(node)
        else:
            flattened.append(node)
    return flattened


def pick_first(obj: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        if key in obj and obj[key] is not None:
            return obj[key]
    return None


def to_num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return round(float(value), 2)
    except (ValueError, TypeError):
        return None


def to_list(value: Any, default: list[str]) -> list[str]:
    if value is None:
        return default
    if isinstance(value, str):
        stripped = value.strip()
        return [stripped] if stripped else default
    if isinstance(value, list):
        out = [str(item).strip() for item in value if str(item).strip()]
        return out or default
    return default


def extract_items_from_payload(payload: Any, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    menu_path = str(cfg.get("menu_list_path") or "")
    src = resolve_list_path(payload, menu_path)
    if not src:
        return []

    field_map = cfg.get("field_map") or {}
    name_keys = field_map.get("name") or ["name"]
    price_keys = field_map.get("price") or ["price"]
    size_keys = field_map.get("size") or ["size"]
    sugar_keys = field_map.get("sugar_opts") or ["sugar_opts"]
    ice_keys = field_map.get("ice_opts") or ["ice_opts"]
    active_keys = field_map.get("is_active") or ["is_active"]
    description_keys = field_map.get("description") or ["description", "major_description", "intro", "remark"]

    defaults = cfg.get("defaults") or {}
    default_sugar = defaults.get("sugar_opts") or []
    default_ice = defaults.get("ice_opts") or []
    default_active = bool(defaults.get("is_active", True))

    brand = str(cfg.get("brand") or "").strip()
    if not brand:
        raise ValueError("config.brand is required")

    out: list[dict[str, Any]] = []
    for item in src:
        if not isinstance(item, dict):
            continue
        name = pick_first(item, name_keys)
        if name is None:
            continue
        name_text = str(name).strip()
        if not name_text:
            continue

        size_raw = pick_first(item, size_keys)
        size = str(size_raw).strip() if size_raw is not None and str(size_raw).strip() else None
        active_raw = pick_first(item, active_keys)
        is_active = default_active if active_raw is None else bool(active_raw)
        description_raw = pick_first(item, description_keys)
        description = str(description_raw).strip() if description_raw is not None and str(description_raw).strip() else None

        out.append(
            {
                "brand": brand,
                "name": name_text,
                "size": size,
                "price": to_num(pick_first(item, price_keys)),
                "description": description,
                "sugar_opts": to_list(pick_first(item, sugar_keys), default_sugar),
                "ice_opts": to_list(pick_first(item, ice_keys), default_ice),
                "is_active": is_active,
            }
        )
    return out
