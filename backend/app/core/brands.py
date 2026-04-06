from __future__ import annotations

from collections.abc import Iterable

CANONICAL_BRANDS = (
    "喜茶",
    "奈雪",
    "霸王茶姬",
    "茶百道",
    "沪上阿姨",
    "CoCo",
    "1点点",
    "蜜雪冰城",
    "古茗",
    "益禾堂",
)

BRAND_ALIASES: dict[str, str] = {
    brand: brand for brand in CANONICAL_BRANDS
} | {
    "一点点": "1点点",
    "1點點": "1点点",
    "coco": "CoCo",
    "CoCo都可": "CoCo",
    "coco都可": "CoCo",
}


def canonicalize_brand_name(brand: str | None) -> str | None:
    if brand is None:
        return None
    clean = str(brand).strip()
    if not clean:
        return None
    return BRAND_ALIASES.get(clean, clean)


def canonicalize_brand_names(brands: Iterable[str] | None) -> list[str]:
    if not brands:
        return []
    out: list[str] = []
    for brand in brands:
        normalized = canonicalize_brand_name(brand)
        if normalized and normalized not in out:
            out.append(normalized)
    return out


def known_brand_names() -> tuple[str, ...]:
    return tuple(BRAND_ALIASES.keys())
