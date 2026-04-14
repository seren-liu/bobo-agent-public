from __future__ import annotations

import re
from typing import Any

_DRINK_CATEGORY_HINTS: list[tuple[str, tuple[str, ...]]] = [
    ("fruit_tea", ("果茶", "水果茶", "鲜果茶", "果粒茶", "柠檬茶", "轻柠茶", "果饮")),
    ("milk_tea", ("奶茶", "厚乳", "奶绿", "奶青", "奶乌", "奶乌龙", "牛乳茶", "牛乳", "乳茶", "玛奇朵")),
    ("light_milk_tea", ("轻乳茶",)),
    ("pure_tea", ("纯茶", "乌龙茶", "绿茶", "红茶", "茉莉绿", "单丛", "茗茶", "茶汤")),
]

_FRUIT_HINTS = ("葡萄", "芭乐", "柠檬", "桃", "芒", "百香果", "红柚", "青柚", "柚", "莓", "苹果", "枇杷", "菠萝", "橙", "橘")
_COFFEE_HINTS = ("咖啡", "拿铁", "美式", "浓缩")

_DRINK_HINTS = (
    "茶",
    "饮",
    "奶茶",
    "牛乳",
    "乳茶",
    "咖啡",
    "拿铁",
    "美式",
    "奶昔",
    "摇摇",
    "柠檬",
    "果饮",
    "冰沙",
    "鲜萃",
    "玛奇朵",
)
_ADDON_HINTS = ("波波", "珍珠", "椰果", "布丁", "奶盖", "芝士", "脆啵", "寒天", "小料")
_SNACK_HINTS = ("薯片", "饼干", "零食", "蛋糕", "面包", "巧克力", "豆腐", "豆干", "小鱼仔", "鱼豆腐", "面筋", "玉米片", "脆片", "酥")
_PACKAGED_NAME_HINTS = ("盒装", "礼盒", "礼包", "礼品卡")
_PACKAGED_DESC_HINTS = ("保质期", "净含量", "规格", "即食", "开袋")
_DESSERT_HINTS = ("圣代", "冰淇淋", "雪糕", "甜筒", "果冻", "双炫")
_PACKAGED_PATTERN = re.compile(r"(\d+\s*[袋包盒罐支杯]|\*\s*\d+\s*[袋包盒罐支杯]|[0-9]+g/[袋包盒]|[0-9]+ml/[瓶罐])")


def _text(item: dict[str, Any]) -> str:
    return " ".join(
        str(part or "").strip()
        for part in (item.get("name"), item.get("description"))
        if str(part or "").strip()
    )


def _name(item: dict[str, Any]) -> str:
    return str(item.get("name") or "").strip()


def _description(item: dict[str, Any]) -> str:
    return str(item.get("description") or "").strip()


def _looks_like_packaged(item: dict[str, Any]) -> bool:
    name = _name(item)
    description = _description(item)
    text = f"{name} {description}".strip()
    if any(hint in name for hint in _PACKAGED_NAME_HINTS):
        return True
    if any(hint in description for hint in _PACKAGED_DESC_HINTS):
        return True
    return bool(_PACKAGED_PATTERN.search(text))


def _looks_like_snack(item: dict[str, Any]) -> bool:
    text = _text(item)
    return any(hint in text for hint in _SNACK_HINTS)


def _looks_like_dessert(item: dict[str, Any]) -> bool:
    text = _text(item)
    return any(hint in text for hint in _DESSERT_HINTS)


def _looks_like_drink(item: dict[str, Any]) -> bool:
    name = _name(item)
    description = _description(item)
    text = f"{name} {description}".strip()
    if any(hint in name for hint in _COFFEE_HINTS):
        return True
    if any(hint in text for hint in _DRINK_HINTS):
        return True
    if any(hint in name for hint in _FRUIT_HINTS) and any(hint in text for hint in ("茶", "饮", "冰", "摇")):
        return True
    return False


def infer_drink_category(item: dict[str, Any]) -> str | None:
    name = _name(item)
    description = _description(item)
    text = f"{name} {description}".strip()

    if _looks_like_packaged(item):
        return None
    if (_looks_like_snack(item) or _looks_like_dessert(item)) and not any(
        hint in text for hint in ("奶茶", "牛乳茶", "乳茶", "咖啡", "拿铁", "美式", "奶昔", "饮", "喝")
    ):
        return None
    if any(hint in name for hint in _COFFEE_HINTS):
        return "coffee"
    if any(hint in text for hint in ("果茶", "水果茶", "鲜果茶", "果饮", "柠檬茶")):
        return "fruit_tea"
    if any(hint in text for hint in ("奶茶", "牛乳茶", "乳茶", "厚乳", "奶绿", "奶乌", "牛乳", "玛奇朵")):
        return "milk_tea"
    if any(hint in text for hint in ("轻乳茶",)):
        return "light_milk_tea"
    if any(hint in name for hint in _FRUIT_HINTS) and any(
        hint in text for hint in ("茶", "绿妍", "茉莉绿", "乌龙", "单丛", "饮", "鲜果", "果肉", "榨汁", "咖啡因")
    ):
        return "fruit_tea"
    for category, hints in _DRINK_CATEGORY_HINTS:
        if any(hint in text for hint in hints):
            return category
    return None


def infer_item_type(item: dict[str, Any]) -> str:
    if infer_drink_category(item):
        return "drink"
    if _looks_like_packaged(item):
        return "packaged"
    if _looks_like_dessert(item) and not _looks_like_drink(item):
        return "dessert"
    if _looks_like_snack(item):
        return "snack"
    if any(hint in _text(item) for hint in _ADDON_HINTS) and not _looks_like_drink(item):
        return "addon"
    if _looks_like_drink(item):
        return "drink"
    return "other"


def infer_menu_taxonomy(item: dict[str, Any]) -> dict[str, str | None]:
    category = infer_drink_category(item)
    item_type = infer_item_type(item)
    return {
        "item_type": item_type,
        "drink_category": category if item_type == "drink" else None,
    }
