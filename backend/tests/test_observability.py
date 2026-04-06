from __future__ import annotations

import re
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.api import agent as agent_api
from app.api import menu as menu_api
from app.api import vision as vision_api
from app.core.security import create_access_token
from app.main import app
from app.models.vision import DrinkItem, VisionResult


client = TestClient(app)


def _auth_header() -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token('u-observability')}"}


def _metric_value(body: str, metric_name: str, labels: dict[str, str] | None = None) -> float:
    if labels:
        label_str = ",".join(f'{key}="{labels[key]}"' for key in sorted(labels))
        pattern = rf"^{re.escape(metric_name)}\{{{re.escape(label_str)}\}}\s+([0-9.eE+-]+)$"
    else:
        pattern = rf"^{re.escape(metric_name)}\s+([0-9.eE+-]+)$"
    match = re.search(pattern, body, re.MULTILINE)
    if not match:
        return 0.0
    return float(match.group(1))


def test_metrics_endpoint_records_business_signals(monkeypatch):
    baseline = client.get("/metrics")
    assert baseline.status_code == 200
    before = baseline.text

    monkeypatch.setattr(
        "app.api.records.insert_records",
        lambda _user_id, _items: [
            {
                "id": "rec-1",
                "brand": "喜茶",
                "name": "多肉葡萄",
                "size": "大杯",
                "sugar": "少糖",
                "ice": "少冰",
                "mood": None,
                "price": 19,
                "photo_url": None,
                "photos": [],
                "source": "manual",
                "notes": None,
                "consumed_at": "2026-04-04T12:00:00Z",
                "created_at": "2026-04-04T12:00:00Z",
            }
        ],
    )
    monkeypatch.setattr(vision_api._cos_service, "get_presigned_read_url", lambda url: url)
    monkeypatch.setattr(
        vision_api._vision_service,
        "recognize",
        lambda **_kwargs: VisionResult(
            items=[
                DrinkItem(brand="喜茶", name="多肉葡萄", size="大杯", sugar="少糖", ice="少冰", price=19, confidence=0.92),
                DrinkItem(brand=None, name=None, size=None, sugar=None, ice=None, price=None, confidence=0.22),
            ],
            source_type="photo",
            order_time=None,
            error=None,
        ),
    )
    async def _fake_search(**_kwargs):
        return [{"id": "menu-1", "brand": "heytea", "name": "多肉葡萄", "score": 0.91}]

    monkeypatch.setattr(menu_api, "get_qdrant_service", lambda: SimpleNamespace(search=_fake_search))

    response = client.post(
        "/bobo/records/confirm",
        headers=_auth_header(),
        json={
            "items": [
                {
                    "brand": "喜茶",
                    "name": "多肉葡萄",
                    "size": "大杯",
                    "sugar": "少糖",
                    "ice": "少冰",
                    "price": 19,
                    "source": "manual",
                    "consumed_at": "2026-04-04T12:00:00Z",
                }
            ]
        },
    )
    assert response.status_code == 201

    response = client.post(
        "/bobo/vision/recognize",
        headers=_auth_header(),
        json={"image_url": "https://example.com/x.jpg", "source_type": "photo"},
    )
    assert response.status_code == 200

    response = client.get("/bobo/menu/search?q=葡萄&brand=heytea", headers=_auth_header())
    assert response.status_code == 200

    after = client.get("/metrics")
    assert after.status_code == 200
    body = after.text

    assert _metric_value(body, "bobo_records_confirm_requests_total") >= _metric_value(before, "bobo_records_confirm_requests_total") + 1
    assert _metric_value(body, "bobo_records_confirm_items_total") >= _metric_value(before, "bobo_records_confirm_items_total") + 1
    assert _metric_value(
        body,
        "bobo_vision_requests_total",
        {"source_type": "photo", "outcome": "success"},
    ) >= _metric_value(before, "bobo_vision_requests_total", {"source_type": "photo", "outcome": "success"}) + 1
    assert _metric_value(
        body,
        "bobo_menu_search_requests_total",
        {"source": "api", "brand_filter": "yes", "outcome": "success"},
    ) >= _metric_value(before, "bobo_menu_search_requests_total", {"source": "api", "brand_filter": "yes", "outcome": "success"}) + 1
    assert 'route="/bobo/records/confirm"' in body
    assert 'route="/bobo/vision/recognize"' in body
    assert 'route="/bobo/menu/search"' in body


def test_metrics_endpoint_tracks_agent_chat(monkeypatch):
    async def _fake_stream(**_kwargs):
        yield {"event": "on_tool_start", "name": "search_menu", "run_id": "tool-1", "data": {"input": {"query": "葡萄"}}}
        yield {"event": "on_tool_end", "name": "search_menu", "run_id": "tool-1", "data": {"output": {"results": [{"id": "menu-1"}]}}}
        yield {"event": "on_chat_model_stream", "data": {"chunk": SimpleNamespace(content="你好，推荐多肉葡萄")}}
        yield {"event": "on_chat_model_end", "data": {"output": {"usage_metadata": {"input_tokens": 12, "output_tokens": 8}}}}

    async def _fake_memory_jobs(_user_id: str, _thread_id: str) -> None:
        return None

    baseline = client.get("/metrics").text

    monkeypatch.setattr(agent_api, "stream_agent_events", _fake_stream)
    monkeypatch.setattr(agent_api.repository, "create_thread", lambda *_args, **_kwargs: {"id": "thread-1"})
    monkeypatch.setattr(agent_api.repository, "append_message", lambda *_args, **_kwargs: {"id": "msg-1"})
    monkeypatch.setattr(agent_api.repository, "add_daily_llm_usage", lambda **kwargs: kwargs)
    monkeypatch.setattr(agent_api, "_run_memory_jobs_after_response", _fake_memory_jobs)
    monkeypatch.setattr(
        agent_api,
        "_daily_budget_snapshot",
        lambda **_kwargs: {
            "remaining_cny": 1.0,
            "remaining_output_tokens": 99999,
            "spent_cost_cny": 0.0,
            "budget_cny": 1.0,
            "pricing": SimpleNamespace(model="qwen3-32b", input_price_per_million=2.0, output_price_per_million=8.0),
        },
    )

    with client.stream(
        "POST",
        "/bobo/agent/chat",
        headers=_auth_header(),
        json={"message": "帮我推荐一杯奶茶", "thread_id": "obs-thread"},
    ) as response:
        body = "".join(response.iter_text())

    assert response.status_code == 200
    assert '"type": "done"' in body

    metrics = client.get("/metrics").text
    assert _metric_value(
        metrics,
        "bobo_agent_chat_requests_total",
        {"mode": "agent_graph", "outcome": "success"},
    ) >= _metric_value(baseline, "bobo_agent_chat_requests_total", {"mode": "agent_graph", "outcome": "success"}) + 1
    assert _metric_value(
        metrics,
        "bobo_agent_tool_calls_total",
        {"tool": "search_menu", "outcome": "success"},
    ) >= _metric_value(baseline, "bobo_agent_tool_calls_total", {"tool": "search_menu", "outcome": "success"}) + 1
    assert _metric_value(
        metrics,
        "bobo_llm_tokens_total",
        {"model": "qwen3-32b", "direction": "input"},
    ) >= _metric_value(baseline, "bobo_llm_tokens_total", {"model": "qwen3-32b", "direction": "input"}) + 12
    assert _metric_value(
        metrics,
        "bobo_agent_budget_checks_total",
        {"outcome": "allowed"},
    ) >= _metric_value(baseline, "bobo_agent_budget_checks_total", {"outcome": "allowed"}) + 1
    assert _metric_value(
        metrics,
        "bobo_budget_llm_cost_cny_total",
        {"model": "qwen3-32b", "usage_kind": "chat_main"},
    ) > _metric_value(baseline, "bobo_budget_llm_cost_cny_total", {"model": "qwen3-32b", "usage_kind": "chat_main"})


def test_metrics_endpoint_tracks_budget_rejections(monkeypatch):
    baseline = client.get("/metrics").text

    monkeypatch.setattr(
        agent_api,
        "_daily_budget_snapshot",
        lambda **_kwargs: {
            "remaining_cny": 0.0,
            "remaining_output_tokens": 0,
            "spent_cost_cny": 1.0,
            "budget_cny": 1.0,
            "pricing": SimpleNamespace(model="qwen3-32b", input_price_per_million=2.0, output_price_per_million=8.0),
        },
    )

    response = client.post(
        "/bobo/agent/chat",
        headers=_auth_header(),
        json={"message": "帮我推荐一杯奶茶", "thread_id": "obs-budget-blocked"},
    )

    assert response.status_code == 429

    metrics = client.get("/metrics").text
    assert _metric_value(
        metrics,
        "bobo_agent_budget_checks_total",
        {"outcome": "budget_exhausted"},
    ) >= _metric_value(baseline, "bobo_agent_budget_checks_total", {"outcome": "budget_exhausted"}) + 1
