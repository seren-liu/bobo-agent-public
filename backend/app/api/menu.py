from __future__ import annotations

from functools import lru_cache

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.services.menu_ops import MenuActionError, get_menu_ops_service
from app.observability import observe_menu_search
from app.services.qdrant import QdrantService

router = APIRouter(prefix="/bobo/menu", tags=["menu"])


class MenuSearchItem(BaseModel):
    id: str
    brand: str
    name: str
    size: str | None = None
    price: float | None = None
    description: str | None = None
    score: float


class MenuSearchResponse(BaseModel):
    results: list[MenuSearchItem]


class MenuCreateRequest(BaseModel):
    brand: str
    name: str
    size: str | None = None
    price: float | None = None
    description: str | None = None
    sugar_opts: list[str] = Field(default_factory=list)
    ice_opts: list[str] = Field(default_factory=list)


class MenuCreateResponse(BaseModel):
    id: str
    brand: str
    name: str
    description: str | None = None


class MenuUpdateRequest(BaseModel):
    brand: str | None = None
    name: str | None = None
    size: str | None = None
    price: float | None = None
    description: str | None = None
    sugar_opts: list[str] | None = None
    ice_opts: list[str] | None = None
    is_active: bool | None = None


class MenuUpdateResponse(BaseModel):
    id: str
    brand: str
    name: str
    price: float | None = None
    description: str | None = None
    is_active: bool


@lru_cache(maxsize=1)
def get_qdrant_service() -> QdrantService:
    return QdrantService()


@router.get("/search", response_model=MenuSearchResponse)
async def search_menu(
    q: str = Query(..., min_length=1),
    brand: str | None = Query(default=None),
    top_k: int = Query(default=5, ge=1, le=20),
) -> MenuSearchResponse:
    service = get_qdrant_service()
    results = await service.search(query=q, brand=brand, top_k=top_k)
    observe_menu_search(source="api", brand_filter=bool(brand), outcome="success", result_count=len(results))
    return MenuSearchResponse(results=results)


@router.post("", response_model=MenuCreateResponse, status_code=201)
async def create_menu(payload: MenuCreateRequest) -> MenuCreateResponse:
    try:
        result = await get_menu_ops_service().add_item(payload.model_dump())
    except MenuActionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    item = result.get("item") or {}
    return MenuCreateResponse(id=item["id"], brand=item["brand"], name=item["name"], description=item.get("description"))


@router.put("/{menu_id}", response_model=MenuUpdateResponse)
async def update_menu(menu_id: str, payload: MenuUpdateRequest) -> MenuUpdateResponse:
    fields = payload.model_dump(exclude_unset=True)
    try:
        result = await get_menu_ops_service().update_item({"id": menu_id, **fields})
    except MenuActionError as exc:
        detail = str(exc)
        status = 404 if detail == "menu not found" else 400
        raise HTTPException(status_code=status, detail=detail) from exc

    item = result.get("item") or {}
    return MenuUpdateResponse(
        id=item["id"],
        brand=item["brand"],
        name=item["name"],
        price=item.get("price"),
        description=item.get("description"),
        is_active=bool(item.get("is_active", True)),
    )


@router.delete("/{menu_id}")
async def delete_menu(menu_id: str) -> dict[str, bool]:
    try:
        await get_menu_ops_service().delete_item({"id": menu_id})
    except MenuActionError as exc:
        detail = str(exc)
        status = 404 if detail == "menu not found" else 400
        raise HTTPException(status_code=status, detail=detail) from exc

    return {"ok": True}
