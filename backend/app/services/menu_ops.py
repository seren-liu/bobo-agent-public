from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from typing import Any

import psycopg
from psycopg.rows import dict_row

from app.core.config import get_settings, to_psycopg_conninfo
from app.services.menu_search import invalidate_menu_search_cache
from app.services.qdrant import QdrantService
from app.services.menu_typing import infer_menu_taxonomy


class MenuActionError(ValueError):
    pass


def _db_url() -> str:
    database_url = get_settings().database_url
    if not database_url:
        raise MenuActionError("database_url is not configured")
    return to_psycopg_conninfo(database_url)


def _to_float(value: Decimal | float | int | None) -> float | None:
    if value is None:
        return None
    return float(value)


@lru_cache(maxsize=1)
def get_menu_ops_service() -> "MenuOpsService":
    return MenuOpsService()


class MenuOpsService:
    def __init__(self, qdrant_service: QdrantService | None = None):
        self.qdrant = qdrant_service or QdrantService()

    async def apply_action(self, action: str, item: dict[str, Any]) -> dict[str, Any]:
        act = (action or "").strip().lower()
        if act not in {"add", "update", "delete"}:
            raise MenuActionError("action must be one of add/update/delete")

        if act == "add":
            return await self.add_item(item)
        if act == "update":
            return await self.update_item(item)
        return await self.delete_item(item)

    async def add_item(self, item: dict[str, Any]) -> dict[str, Any]:
        brand = str(item.get("brand", "")).strip()
        name = str(item.get("name", "")).strip()
        if not brand or not name:
            raise MenuActionError("add requires non-empty brand and name")

        size = item.get("size")
        price = item.get("price")
        description = item.get("description")
        sugar_opts = item.get("sugar_opts") or []
        ice_opts = item.get("ice_opts") or []
        taxonomy = infer_menu_taxonomy({"name": name, "description": description})

        sql = """
        INSERT INTO menu (brand, name, size, price, description, item_type, drink_category, sugar_opts, ice_opts, is_active)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE)
        RETURNING id::text AS id, brand, name, size, price, description, item_type, drink_category, is_active
        """
        with psycopg.connect(_db_url(), row_factory=dict_row) as conn, conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    brand,
                    name,
                    size,
                    price,
                    description,
                    taxonomy["item_type"],
                    taxonomy["drink_category"],
                    sugar_opts,
                    ice_opts,
                ),
            )
            row = cur.fetchone()
            conn.commit()

        if not row:
            raise MenuActionError("failed to create menu item")

        invalidate_menu_search_cache()

        warnings: list[str] = []
        vector_updated = False
        try:
            await self.qdrant.upsert(
                menu_id=row["id"],
                brand=row["brand"],
                name=row["name"],
                price=_to_float(row.get("price")) or 0.0,
                size=row.get("size") or "",
                description=row.get("description"),
                item_type=row.get("item_type"),
                drink_category=row.get("drink_category"),
                is_active=bool(row.get("is_active", True)),
            )
            vector_updated = True
        except Exception as exc:  # pragma: no cover
            warnings.append(f"qdrant_upsert_failed:{exc}")

        return {
            "ok": True,
            "action": "add",
            "menu_id": row["id"],
            "db_updated": True,
            "vector_updated": vector_updated,
            "warnings": warnings,
            "item": {
                "id": row["id"],
                "brand": row["brand"],
                "name": row["name"],
                "size": row.get("size"),
                "price": _to_float(row.get("price")),
                "description": row.get("description"),
                "item_type": row.get("item_type"),
                "drink_category": row.get("drink_category"),
                "is_active": bool(row.get("is_active", True)),
            },
        }

    async def update_item(self, item: dict[str, Any]) -> dict[str, Any]:
        menu_id = str(item.get("id") or item.get("menu_id") or "").strip()
        if not menu_id:
            raise MenuActionError("update requires id/menu_id")

        updatable = {
            "brand",
            "name",
            "size",
            "price",
            "description",
            "item_type",
            "drink_category",
            "sugar_opts",
            "ice_opts",
            "is_active",
        }
        fields = {k: v for k, v in item.items() if k in updatable}
        if not fields:
            raise MenuActionError("update requires at least one updatable field")
        if "name" in fields or "description" in fields:
            taxonomy = infer_menu_taxonomy(
                {
                    "name": fields.get("name") or item.get("name") or "",
                    "description": fields.get("description") if "description" in fields else item.get("description"),
                }
            )
            fields["item_type"] = fields.get("item_type") or taxonomy["item_type"]
            fields["drink_category"] = fields.get("drink_category") or taxonomy["drink_category"]

        updates: list[str] = []
        values: list[Any] = []
        for key, value in fields.items():
            updates.append(f"{key} = %s")
            values.append(value)
        values.append(menu_id)

        sql = f"""
        UPDATE menu
        SET {", ".join(updates)}, updated_at = NOW()
        WHERE id = %s
        RETURNING id::text AS id, brand, name, size, price, description, item_type, drink_category, is_active
        """
        with psycopg.connect(_db_url(), row_factory=dict_row) as conn, conn.cursor() as cur:
            cur.execute(sql, tuple(values))
            row = cur.fetchone()
            conn.commit()

        if not row:
            raise MenuActionError("menu not found")

        invalidate_menu_search_cache()

        warnings: list[str] = []
        vector_updated = False
        try:
            if bool(row.get("is_active", True)):
                await self.qdrant.upsert(
                    menu_id=row["id"],
                    brand=row["brand"],
                    name=row["name"],
                    price=_to_float(row.get("price")) or 0.0,
                    size=row.get("size") or "",
                    description=row.get("description"),
                    item_type=row.get("item_type"),
                    drink_category=row.get("drink_category"),
                    is_active=True,
                )
            else:
                await self.qdrant.delete(row["id"])
            vector_updated = True
        except Exception as exc:  # pragma: no cover
            warnings.append(f"qdrant_sync_failed:{exc}")

        return {
            "ok": True,
            "action": "update",
            "menu_id": row["id"],
            "db_updated": True,
            "vector_updated": vector_updated,
            "warnings": warnings,
            "item": {
                "id": row["id"],
                "brand": row["brand"],
                "name": row["name"],
                "size": row.get("size"),
                "price": _to_float(row.get("price")),
                "description": row.get("description"),
                "item_type": row.get("item_type"),
                "drink_category": row.get("drink_category"),
                "is_active": bool(row.get("is_active", True)),
            },
        }

    async def delete_item(self, item: dict[str, Any]) -> dict[str, Any]:
        menu_id = str(item.get("id") or item.get("menu_id") or "").strip()
        if not menu_id:
            raise MenuActionError("delete requires id/menu_id")

        sql = "DELETE FROM menu WHERE id = %s RETURNING id::text AS id"
        with psycopg.connect(_db_url(), row_factory=dict_row) as conn, conn.cursor() as cur:
            cur.execute(sql, (menu_id,))
            row = cur.fetchone()
            conn.commit()

        if not row:
            raise MenuActionError("menu not found")

        invalidate_menu_search_cache()

        warnings: list[str] = []
        vector_updated = False
        try:
            await self.qdrant.delete(menu_id)
            vector_updated = True
        except Exception as exc:  # pragma: no cover
            warnings.append(f"qdrant_delete_failed:{exc}")

        return {
            "ok": True,
            "action": "delete",
            "menu_id": menu_id,
            "db_updated": True,
            "vector_updated": vector_updated,
            "warnings": warnings,
        }
