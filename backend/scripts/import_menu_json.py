from __future__ import annotations

import argparse
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from app.services.menu_typing import infer_menu_taxonomy


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import normalized menu JSON into PostgreSQL menu table.")
    parser.add_argument("--file", required=True, help="Path to normalized menu JSON file.")
    parser.add_argument("--brand", default="", help="Override brand for all items.")
    parser.add_argument("--dry-run", action="store_true", help="Validate and summarize without writing DB.")
    parser.add_argument(
        "--upsert-key",
        default="brand_name_size",
        choices=["brand_name_size", "name_size"],
        help="How to match existing rows when upserting.",
    )
    return parser.parse_args()


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        out: list[str] = []
        for x in value:
            if x is None:
                continue
            s = str(x).strip()
            if s:
                out.append(s)
        return out
    return []


def _as_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _coalesce(*values: Any) -> Any:
    for v in values:
        if v is None:
            continue
        if isinstance(v, str) and not v.strip():
            continue
        return v
    return None


def load_items(path: Path, brand_override: str) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))

    raw_items: Any
    container_brand = ""
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        raw_items = data["items"]
        container_brand = str(data.get("brand") or "").strip()
    elif isinstance(data, list):
        raw_items = data
    else:
        raise ValueError("JSON must be an array of items or object with {'brand','items'}")

    items: list[dict[str, Any]] = []
    for idx, row in enumerate(raw_items, start=1):
        if not isinstance(row, dict):
            continue

        brand = str(_coalesce(brand_override, row.get("brand"), container_brand, "") or "").strip()
        name = str(_coalesce(row.get("name"), row.get("product_name"), row.get("item_name"), "") or "").strip()
        if not brand or not name:
            continue

        size_value = _coalesce(row.get("size"), row.get("spec"), row.get("cup"), None)
        size = str(size_value).strip() if size_value is not None else None
        price = _as_decimal(_coalesce(row.get("price"), row.get("sale_price"), row.get("amount")))
        description_raw = _coalesce(
            row.get("description"),
            row.get("major_description"),
            row.get("intro"),
            row.get("remark"),
        )
        description = str(description_raw).strip() if description_raw is not None and str(description_raw).strip() else None
        sugar_opts = _as_list(_coalesce(row.get("sugar_opts"), row.get("sugar_options"), row.get("sugar")))
        ice_opts = _as_list(_coalesce(row.get("ice_opts"), row.get("ice_options"), row.get("ice")))
        is_active_raw = _coalesce(row.get("is_active"), row.get("available"), True)
        is_active = bool(is_active_raw)

        item = {
            "source_idx": idx,
            "brand": brand,
            "name": name,
            "size": size,
            "price": price,
            "description": description,
            "sugar_opts": sugar_opts,
            "ice_opts": ice_opts,
            "is_active": is_active,
        }
        item.update(infer_menu_taxonomy(item))
        items.append(item)

    return items


def deduplicate(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in items:
        key = (
            item["brand"].strip().lower(),
            item["name"].strip().lower(),
            (item.get("size") or "").strip().lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _lookup_existing(
    cur: Any,
    item: dict[str, Any],
    upsert_key: str,
) -> dict[str, Any] | None:
    if upsert_key == "name_size":
        cur.execute(
            """
            SELECT id::text AS id
            FROM menu
            WHERE name = %s
              AND COALESCE(size, '') = COALESCE(%s, '')
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (item["name"], item.get("size")),
        )
    else:
        cur.execute(
            """
            SELECT id::text AS id
            FROM menu
            WHERE brand = %s
              AND name = %s
              AND COALESCE(size, '') = COALESCE(%s, '')
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (item["brand"], item["name"], item.get("size")),
        )
    return cur.fetchone()


def import_items(items: list[dict[str, Any]], upsert_key: str, dry_run: bool) -> tuple[int, int]:
    if dry_run:
        return len(items), 0

    import psycopg
    from psycopg.rows import dict_row

    from app.core.config import get_settings, to_psycopg_conninfo

    settings = get_settings()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is not configured")
    database_url = to_psycopg_conninfo(settings.database_url)

    inserted = 0
    updated = 0

    with psycopg.connect(database_url, row_factory=dict_row) as conn, conn.cursor() as cur:
        for item in items:
            existing = _lookup_existing(cur, item, upsert_key=upsert_key)
            if existing:
                cur.execute(
                    """
                    UPDATE menu
                    SET brand = %s,
                        name = %s,
                        size = %s,
                        price = %s,
                        description = %s,
                        item_type = %s,
                        drink_category = %s,
                        sugar_opts = %s,
                        ice_opts = %s,
                        is_active = %s,
                        updated_at = NOW()
                    WHERE id = %s::uuid
                    """,
                    (
                        item["brand"],
                        item["name"],
                        item.get("size"),
                        item.get("price"),
                        item.get("description"),
                        item.get("item_type"),
                        item.get("drink_category"),
                        item.get("sugar_opts") or [],
                        item.get("ice_opts") or [],
                        item.get("is_active", True),
                        existing["id"],
                    ),
                )
                updated += 1
            else:
                cur.execute(
                    """
                    INSERT INTO menu (brand, name, size, price, description, item_type, drink_category, sugar_opts, ice_opts, is_active)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        item["brand"],
                        item["name"],
                        item.get("size"),
                        item.get("price"),
                        item.get("description"),
                        item.get("item_type"),
                        item.get("drink_category"),
                        item.get("sugar_opts") or [],
                        item.get("ice_opts") or [],
                        item.get("is_active", True),
                    ),
                )
                inserted += 1

        conn.commit()

    return inserted, updated


def main() -> None:
    args = parse_args()
    input_path = Path(args.file).expanduser().resolve()
    if not input_path.exists():
        raise FileNotFoundError(f"input file not found: {input_path}")

    items = load_items(input_path, args.brand.strip())
    if not items:
        print("No valid menu items found. Nothing to import.")
        return

    deduped = deduplicate(items)
    inserted, updated = import_items(deduped, upsert_key=args.upsert_key, dry_run=args.dry_run)

    print(
        json.dumps(
            {
                "file": str(input_path),
                "raw_items": len(items),
                "deduped_items": len(deduped),
                "inserted": inserted,
                "updated": updated,
                "dry_run": bool(args.dry_run),
                "upsert_key": args.upsert_key,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
