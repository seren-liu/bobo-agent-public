from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from collections.abc import Iterable
from typing import Any

import psycopg
from psycopg.rows import dict_row

from app.core.config import get_settings, to_psycopg_conninfo
from app.services.menu_typing import infer_menu_taxonomy


def _ensure_menu_columns(conn: psycopg.Connection[Any]) -> None:
    with conn.cursor() as cur:
        cur.execute("ALTER TABLE menu ADD COLUMN IF NOT EXISTS item_type VARCHAR(24)")
        cur.execute("ALTER TABLE menu ADD COLUMN IF NOT EXISTS drink_category VARCHAR(32)")
    conn.commit()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill menu taxonomy and optionally deactivate non-drink items.")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without writing DB/Qdrant.")
    parser.add_argument(
        "--deactivate-non-drink",
        action="store_true",
        help="Set is_active=false for items whose inferred item_type is not drink.",
    )
    return parser.parse_args()


def _db_url() -> str:
    database_url = get_settings().database_url or os.getenv("DATABASE_URL") or ""
    if not database_url:
        raise RuntimeError("DATABASE_URL is not configured")
    return to_psycopg_conninfo(database_url)


def _fetch_menu_rows(conn: psycopg.Connection[Any]) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id::text AS id, brand, name, size, price, description, is_active, item_type, drink_category
            FROM menu
            ORDER BY brand, name
            """
        )
        return list(cur.fetchall() or [])


def _update_row(
    conn: psycopg.Connection[Any],
    *,
    row_id: str,
    item_type: str,
    drink_category: str | None,
    is_active: bool,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE menu
            SET item_type = %s,
                drink_category = %s,
                is_active = %s,
                updated_at = NOW()
            WHERE id = %s::uuid
            """,
            (item_type, drink_category, is_active, row_id),
        )


def _serialize_price(value: Any) -> float:
    if value is None:
        return 0.0
    return float(value)


def _chunked(values: Iterable[str], size: int = 64) -> list[list[str]]:
    chunk: list[str] = []
    chunks: list[list[str]] = []
    for value in values:
        chunk.append(value)
        if len(chunk) >= size:
            chunks.append(chunk)
            chunk = []
    if chunk:
        chunks.append(chunk)
    return chunks


def _prepare_rows(
    rows: list[dict[str, Any]],
    *,
    deactivate_non_drink: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], Counter[str], int, int]:
    changed_rows: list[dict[str, Any]] = []
    sync_rows: list[dict[str, Any]] = []
    item_type_counts: Counter[str] = Counter()
    keep_count = 0
    deactivate_count = 0

    for row in rows:
        taxonomy = infer_menu_taxonomy(row)
        item_type = str(taxonomy["item_type"] or "other")
        drink_category = taxonomy["drink_category"]
        is_active = bool(row.get("is_active", True))
        if deactivate_non_drink and item_type != "drink":
            is_active = False

        item_type_counts[item_type] += 1
        if item_type == "drink" and is_active:
            keep_count += 1
        if not is_active:
            deactivate_count += 1

        enriched = dict(row)
        enriched["item_type"] = item_type
        enriched["drink_category"] = drink_category
        enriched["is_active"] = is_active
        sync_rows.append(enriched)

        if (
            row.get("item_type") != item_type
            or row.get("drink_category") != drink_category
            or bool(row.get("is_active", True)) != is_active
        ):
            changed_rows.append(enriched)

    return changed_rows, sync_rows, item_type_counts, keep_count, deactivate_count


async def _sync_qdrant(rows: list[dict[str, Any]], *, dry_run: bool) -> dict[str, int]:
    from qdrant_client import AsyncQdrantClient
    from qdrant_client.http import models

    qdrant_url = os.getenv("QDRANT_URL") or "http://127.0.0.1:6333"
    qdrant_api_key = os.getenv("QDRANT_API_KEY")
    client = AsyncQdrantClient(url=qdrant_url, api_key=qdrant_api_key)
    collection_name = "menu_vectors"
    deleted = 0
    payload_updated = 0

    delete_ids = [
        str(row["id"])
        for row in rows
        if row.get("item_type") != "drink" or not bool(row.get("is_active", True))
    ]
    drink_rows = [
        row
        for row in rows
        if row.get("item_type") == "drink" and bool(row.get("is_active", True))
    ]

    if not dry_run:
        for chunk in _chunked(delete_ids):
            await client.delete(
                collection_name=collection_name,
                points_selector=models.PointIdsList(points=chunk),
                wait=True,
            )
        for row in drink_rows:
            await client.set_payload(
                collection_name=collection_name,
                payload={
                    "item_type": str(row.get("item_type") or "drink"),
                    "drink_category": row.get("drink_category"),
                    "is_active": True,
                },
                points=[str(row["id"])],
                wait=True,
            )

    deleted = len(delete_ids)
    payload_updated = len(drink_rows)
    await client.close()
    return {"qdrant_deleted": deleted, "qdrant_payload_updated": payload_updated}


async def main() -> None:
    args = parse_args()
    with psycopg.connect(_db_url(), row_factory=dict_row) as conn:
        _ensure_menu_columns(conn)
        rows = _fetch_menu_rows(conn)
        changed_rows, sync_rows, item_type_counts, keep_count, deactivate_count = _prepare_rows(
            rows,
            deactivate_non_drink=bool(args.deactivate_non_drink),
        )

        if not args.dry_run:
            for row in changed_rows:
                _update_row(
                    conn,
                    row_id=str(row["id"]),
                    item_type=str(row["item_type"]),
                    drink_category=row.get("drink_category"),
                    is_active=bool(row["is_active"]),
                )
            conn.commit()

        sync_stats = await _sync_qdrant(sync_rows, dry_run=args.dry_run)

        preview = [
            {
                "id": row["id"],
                "brand": row["brand"],
                "name": row["name"],
                "item_type": row["item_type"],
                "drink_category": row["drink_category"],
                "is_active": row["is_active"],
            }
            for row in changed_rows[:20]
        ]

        print(
            json.dumps(
                {
                    "dry_run": bool(args.dry_run),
                    "deactivate_non_drink": bool(args.deactivate_non_drink),
                    "total_rows": len(rows),
                    "changed_rows": len(changed_rows),
                    "active_drink_rows": keep_count,
                    "inactive_rows": deactivate_count,
                    "item_type_counts": dict(item_type_counts),
                    **sync_stats,
                    "preview": preview,
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
