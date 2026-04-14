from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from collections.abc import AsyncGenerator
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import agent as agent_api
from app.memory import retrieval


@dataclass
class ReplayRunSummary:
    suite_name: str
    total_cases: int
    passed_cases: int
    failed_cases: int
    results: list[dict[str, Any]]

    @property
    def ok(self) -> bool:
        return self.failed_cases == 0


def load_suite(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if str(payload.get("schema_version") or "").strip() != "bobo-agent-business-eval.v1":
        raise ValueError("unsupported eval schema_version")
    if not isinstance(payload.get("cases"), list) or not payload["cases"]:
        raise ValueError("eval suite must contain non-empty cases")
    return payload


def _build_client(user_id: str) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def add_user_id(request, call_next):
        request.state.user_id = user_id
        request.state.request_id = f"req-{user_id}"
        return await call_next(request)

    app.include_router(agent_api.router)
    return TestClient(app)


def _parse_sse(body: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    current_event: str | None = None
    current_data: list[str] = []

    def _flush() -> None:
        nonlocal current_event, current_data
        if not current_event and not current_data:
            return
        payload = {}
        if current_data:
            try:
                payload = json.loads("\n".join(current_data))
            except json.JSONDecodeError:
                payload = {"raw": "\n".join(current_data)}
        events.append({"event": current_event or "message", "payload": payload})
        current_event = None
        current_data = []

    for raw_line in body.splitlines():
        line = raw_line.rstrip("\n")
        if not line.strip():
            _flush()
            continue
        if line.startswith("event: "):
            current_event = line[7:].strip()
        elif line.startswith("data: "):
            current_data.append(line[6:])
    _flush()
    return events


def _tool_name_from_event(event: dict[str, Any]) -> str | None:
    payload = event.get("payload") or {}
    if payload.get("type") in {"tool_call", "tool_result"}:
        return str(payload.get("tool") or "").strip() or None
    return None


def _text_from_events(events: list[dict[str, Any]]) -> str:
    chunks: list[str] = []
    for event in events:
        payload = event.get("payload") or {}
        if payload.get("type") == "text":
            chunks.append(str(payload.get("content") or ""))
    return "".join(chunks)


def _results_from_search_spec(specs: list[dict[str, Any]], query: str | None, brand: str | None) -> list[dict[str, Any]]:
    for spec in specs:
        if spec.get("query") not in {None, query}:
            continue
        if spec.get("brand") not in {None, brand}:
            continue
        return list(spec.get("results") or [])
    return []


def _assert_contains_all(haystack: str, needles: list[str]) -> list[str]:
    failures: list[str] = []
    for needle in needles:
        if needle not in haystack:
            failures.append(f"missing expected text: {needle}")
    return failures


def _assert_excludes_all(haystack: str, needles: list[str]) -> list[str]:
    failures: list[str] = []
    for needle in needles:
        if needle in haystack:
            failures.append(f"unexpected text present: {needle}")
    return failures


def _apply_common_patches(stack: ExitStack, case: dict[str, Any], traces: dict[str, Any]) -> None:
    budget_snapshot = {
        "remaining_cny": 1.0,
        "remaining_output_tokens": 100000,
        "spent_cost_cny": 0.0,
        "budget_cny": 1.0,
        "pricing": SimpleNamespace(model="qwen3-32b", input_price_per_million=2.0, output_price_per_million=8.0),
    }

    async def _fake_memory_jobs(_user_id: str, _thread_id: str) -> None:
        traces["calls"]["memory_jobs_enqueued"] += 1
        return None

    async def _fake_budget_snapshot_async(*, user_id: str, model: str) -> dict[str, Any]:
        traces["calls"]["daily_budget_snapshot_async"] += 1
        return budget_snapshot

    stack.enter_context(patch.object(agent_api, "_daily_budget_snapshot_async", _fake_budget_snapshot_async))
    stack.enter_context(patch.object(agent_api, "_run_memory_jobs_after_response", _fake_memory_jobs))
    stack.enter_context(patch.object(agent_api.repository, "create_thread", lambda *_args, **_kwargs: {"id": "thread-1"}))
    stack.enter_context(patch.object(agent_api.repository, "append_message", lambda *_args, **_kwargs: {"id": "msg-1"}))
    stack.enter_context(patch.object(agent_api.repository, "add_daily_llm_usage", lambda **kwargs: kwargs))

    profile = dict((case.get("setup") or {}).get("profile") or {})
    memories = list((case.get("setup") or {}).get("memories") or [])
    thread_summary = str(((case.get("setup") or {}).get("thread_summary")) or "")

    stack.enter_context(patch.object(retrieval.repository, "get_profile", lambda _user_id: profile))
    stack.enter_context(patch.object(retrieval, "load_latest_thread_summary", lambda _user_id, _thread_id: thread_summary))
    stack.enter_context(
        patch.object(
            retrieval,
            "search_relevant_memories",
            lambda _user_id, query, scope=None, top_k=None: memories,
        )
    )


def _run_chat_case(case: dict[str, Any]) -> dict[str, Any]:
    traces: dict[str, Any] = {"calls": defaultdict(int)}
    client = _build_client(str(case.get("user_id") or "u-eval"))
    stubs = dict(case.get("stubs") or {})
    assertions = dict(case.get("assertions") or {})

    async def _fake_search_menu_impl(**kwargs):
        traces["calls"]["search_menu_impl"] += 1
        traces.setdefault("search_menu_args", []).append(kwargs)
        results = _results_from_search_spec(list(stubs.get("search_menu_results") or []), kwargs.get("query"), kwargs.get("brand"))
        return {"results": results, "query": kwargs.get("query"), "brand": kwargs.get("brand")}

    async def _fake_stream_agent_events(**kwargs) -> AsyncGenerator[dict[str, Any], None]:
        traces["calls"]["stream_agent_events"] += 1
        traces["stream_kwargs"] = kwargs
        for event in list(stubs.get("graph_events") or []):
            yield dict(event)

    def _fake_stats_impl(**kwargs):
        traces["calls"]["get_stats_impl"] += 1
        traces["stats_args"] = kwargs
        return dict(stubs.get("stats_payload") or {})

    def _fake_recent_impl(**kwargs):
        traces["calls"]["get_recent_records_impl"] += 1
        traces["recent_args"] = kwargs
        return {"records": list(stubs.get("recent_records_payload") or [])}

    def _fake_day_impl(**kwargs):
        traces["calls"]["get_day_impl"] += 1
        traces["day_args"] = kwargs
        return {"records": list(stubs.get("day_records_payload") or [])}

    async def _fake_unstructured_menu_reply(**kwargs):
        traces["calls"]["generate_unstructured_menu_reply"] += 1
        traces["unstructured_args"] = kwargs
        return str(stubs.get("unstructured_menu_reply") or "暂时没有现成菜单，但可以先按相近口味推荐。"), None

    with ExitStack() as stack:
        _apply_common_patches(stack, case, traces)
        stack.enter_context(patch.object(agent_api, "search_menu_impl", _fake_search_menu_impl))
        stack.enter_context(patch.object(agent_api, "get_stats_impl", _fake_stats_impl))
        stack.enter_context(patch.object(agent_api, "get_recent_records_impl", _fake_recent_impl))
        stack.enter_context(patch.object(agent_api, "get_day_impl", _fake_day_impl))
        stack.enter_context(patch.object(agent_api, "stream_agent_events", _fake_stream_agent_events))
        stack.enter_context(patch.object(agent_api, "_generate_unstructured_menu_reply", _fake_unstructured_menu_reply))
        if "brand_coverage" in stubs:
            stack.enter_context(patch.object(agent_api, "get_menu_brand_coverage_impl", lambda **_kwargs: stubs["brand_coverage"]))

        with client.stream(
            "POST",
            "/bobo/agent/chat",
            json={"message": case["message"], "thread_id": case["thread_id"]},
        ) as resp:
            body = "".join(resp.iter_text())
            status_code = resp.status_code

    events = _parse_sse(body)
    tools = [tool for tool in (_tool_name_from_event(event) for event in events) if tool]
    text = _text_from_events(events)
    failures: list[str] = []

    if status_code != int(assertions.get("expected_status", 200)):
        failures.append(f"unexpected status: {status_code}")

    failures.extend(_assert_contains_all(body, list(assertions.get("body_contains_all") or [])))
    failures.extend(_assert_contains_all(text, list(assertions.get("text_contains_all") or [])))
    failures.extend(_assert_excludes_all(text, list(assertions.get("text_excludes_all") or [])))

    expected_event_types = list(assertions.get("event_types_include") or [])
    observed_event_types = [str(event["event"]) for event in events]
    for event_type in expected_event_types:
        if event_type not in observed_event_types:
            failures.append(f"missing expected event type: {event_type}")

    expected_tools = list(assertions.get("tools_include") or [])
    for tool in expected_tools:
        if tool not in tools:
            failures.append(f"missing expected tool: {tool}")

    for call_name, expected in dict(assertions.get("call_expectations") or {}).items():
        actual = int(traces["calls"].get(call_name, 0))
        if actual != int(expected):
            failures.append(f"unexpected call count for {call_name}: expected {expected}, got {actual}")

    return {
        "id": case["id"],
        "kind": "chat",
        "passed": not failures,
        "failures": failures,
        "status_code": status_code,
        "observed_event_types": observed_event_types,
        "observed_tools": tools,
        "assistant_text": text,
        "call_counts": dict(traces["calls"]),
    }


def _run_memory_context_case(case: dict[str, Any]) -> dict[str, Any]:
    setup = dict(case.get("setup") or {})
    assertions = dict(case.get("assertions") or {})

    with ExitStack() as stack:
        stack.enter_context(patch.object(retrieval.repository, "get_profile", lambda _user_id: dict(setup.get("profile") or {})))
        stack.enter_context(patch.object(retrieval, "load_latest_thread_summary", lambda _user_id, _thread_id: str(setup.get("thread_summary") or "")))
        stack.enter_context(
            patch.object(
                retrieval,
                "search_relevant_memories",
                lambda _user_id, query, scope=None, top_k=None: list(setup.get("memories") or []),
            )
        )

        bundle = retrieval.build_agent_prompt_context(
            str(case.get("user_id") or "u-eval"),
            str(case.get("thread_id") or "eval-thread"),
            [("user", str(case.get("message") or ""))],
            include_metadata=True,
        )

    rendered = str(bundle.get("rendered_text") or "")
    diagnostics = dict(bundle.get("diagnostics") or {})
    failures: list[str] = []
    failures.extend(_assert_contains_all(rendered, list(assertions.get("rendered_contains_all") or [])))
    failures.extend(_assert_excludes_all(rendered, list(assertions.get("rendered_excludes_all") or [])))

    max_chars = assertions.get("diagnostics_char_count_lte")
    if max_chars is not None and int(diagnostics.get("char_count") or 0) > int(max_chars):
        failures.append(f"diagnostics.char_count exceeded {max_chars}")

    truncated = assertions.get("diagnostics_truncated")
    if truncated is not None and bool(diagnostics.get("truncated")) is not bool(truncated):
        failures.append(f"diagnostics.truncated expected {truncated}, got {diagnostics.get('truncated')}")

    return {
        "id": case["id"],
        "kind": "memory_context",
        "passed": not failures,
        "failures": failures,
        "rendered_text": rendered,
        "diagnostics": diagnostics,
    }


def run_case(case: dict[str, Any]) -> dict[str, Any]:
    kind = str(case.get("kind") or "chat")
    if kind == "chat":
        return _run_chat_case(case)
    if kind == "memory_context":
        return _run_memory_context_case(case)
    raise ValueError(f"unsupported eval case kind: {kind}")


def run_suite(path: str | Path) -> ReplayRunSummary:
    suite = load_suite(path)
    results = [run_case(case) for case in suite["cases"]]
    passed = sum(1 for item in results if item["passed"])
    failed = len(results) - passed
    return ReplayRunSummary(
        suite_name=str(suite.get("suite_name") or Path(path).stem),
        total_cases=len(results),
        passed_cases=passed,
        failed_cases=failed,
        results=results,
    )


def render_summary_markdown(summary: ReplayRunSummary) -> str:
    lines = [
        f"# {summary.suite_name}",
        "",
        f"- total_cases: {summary.total_cases}",
        f"- passed_cases: {summary.passed_cases}",
        f"- failed_cases: {summary.failed_cases}",
        "",
        "| case_id | kind | passed | failures |",
        "|---|---|---|---|",
    ]
    for result in summary.results:
        failures = "; ".join(result.get("failures") or [])
        lines.append(f"| {result['id']} | {result['kind']} | {result['passed']} | {failures or ''} |")
    return "\n".join(lines)
