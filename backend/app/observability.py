from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest


HTTP_REQUESTS_TOTAL = Counter(
    "bobo_http_requests_total",
    "Total HTTP requests served by the API.",
    ("method", "route", "status_class"),
)

HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "bobo_http_request_duration_seconds",
    "HTTP request latency in seconds.",
    ("method", "route"),
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
)

RECORDS_CONFIRM_REQUESTS_TOTAL = Counter(
    "bobo_records_confirm_requests_total",
    "Total record confirmation requests.",
)

RECORDS_CONFIRM_ITEMS_TOTAL = Counter(
    "bobo_records_confirm_items_total",
    "Total number of drink records confirmed by users.",
)

RECORDS_DELETE_TOTAL = Counter(
    "bobo_records_delete_total",
    "Total delete record attempts.",
    ("outcome",),
)

VISION_REQUESTS_TOTAL = Counter(
    "bobo_vision_requests_total",
    "Total vision recognition requests.",
    ("source_type", "outcome"),
)

VISION_ITEMS_TOTAL = Counter(
    "bobo_vision_items_total",
    "Total items returned by vision recognition.",
    ("source_type",),
)

VISION_LOW_CONFIDENCE_ITEMS_TOTAL = Counter(
    "bobo_vision_low_confidence_items_total",
    "Total low-confidence items returned by vision recognition.",
    ("source_type",),
)

MENU_SEARCH_REQUESTS_TOTAL = Counter(
    "bobo_menu_search_requests_total",
    "Total menu search requests.",
    ("source", "brand_filter", "outcome"),
)

MENU_SEARCH_RESULTS = Histogram(
    "bobo_menu_search_results",
    "Distribution of menu search result counts.",
    ("source", "brand_filter"),
    buckets=(0, 1, 3, 5, 10, 20),
)

QDRANT_SEARCH_REQUESTS_TOTAL = Counter(
    "bobo_qdrant_search_requests_total",
    "Total Qdrant search requests.",
    ("collection", "brand_filter", "outcome"),
)

QDRANT_SEARCH_DURATION_SECONDS = Histogram(
    "bobo_qdrant_search_duration_seconds",
    "Qdrant search duration in seconds.",
    ("collection", "brand_filter"),
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
)

AGENT_CHAT_REQUESTS_TOTAL = Counter(
    "bobo_agent_chat_requests_total",
    "Total agent chat requests.",
    ("mode", "outcome"),
)

AGENT_CHAT_DURATION_SECONDS = Histogram(
    "bobo_agent_chat_duration_seconds",
    "End-to-end agent chat duration in seconds.",
    ("mode", "outcome"),
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20, 30, 60),
)

AGENT_CHAT_FIRST_TOKEN_SECONDS = Histogram(
    "bobo_agent_chat_first_token_seconds",
    "Time to first token for agent chat responses.",
    ("mode",),
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)

AGENT_TOOL_CALLS_TOTAL = Counter(
    "bobo_agent_tool_calls_total",
    "Total agent tool calls.",
    ("tool", "outcome"),
)

AGENT_TOOL_DURATION_SECONDS = Histogram(
    "bobo_agent_tool_duration_seconds",
    "Agent tool execution duration in seconds.",
    ("tool", "outcome"),
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20),
)

AGENT_BUDGET_CHECKS_TOTAL = Counter(
    "bobo_agent_budget_checks_total",
    "Total agent budget gate evaluations.",
    ("outcome",),
)

AGENT_BUDGET_REMAINING_CNY = Histogram(
    "bobo_agent_budget_remaining_cny",
    "Remaining daily budget (CNY) observed at agent request start.",
    buckets=(0, 0.01, 0.05, 0.1, 0.25, 0.5, 0.75, 1, 2, 5),
)

AGENT_BUDGET_RESERVED_COST_CNY = Histogram(
    "bobo_agent_budget_reserved_cost_cny",
    "Reserved budget cost (CNY) for a new agent request.",
    buckets=(0, 0.001, 0.0025, 0.005, 0.01, 0.02, 0.05, 0.1, 0.25, 0.5),
)

AGENT_BUDGET_AVAILABLE_OUTPUT_TOKENS = Histogram(
    "bobo_agent_budget_available_output_tokens",
    "Available output token budget after applying request reserve.",
    buckets=(0, 64, 128, 256, 512, 1024, 2048, 4096, 8192, 16384, 65536),
)

LLM_TOKENS_TOTAL = Counter(
    "bobo_llm_tokens_total",
    "Total LLM tokens consumed.",
    ("model", "direction"),
)

LLM_COST_CNY_TOTAL = Counter(
    "bobo_llm_cost_cny_total",
    "Total estimated LLM cost in CNY.",
    ("model",),
)

BUDGET_LLM_TOKENS_TOTAL = Counter(
    "bobo_budget_llm_tokens_total",
    "Total LLM tokens counted by the low-cost budget system.",
    ("model", "usage_kind", "direction"),
)

BUDGET_LLM_COST_CNY_TOTAL = Counter(
    "bobo_budget_llm_cost_cny_total",
    "Total estimated LLM cost counted by the low-cost budget system.",
    ("model", "usage_kind"),
)


def metrics_payload() -> bytes:
    return generate_latest()


def metrics_content_type() -> str:
    return CONTENT_TYPE_LATEST


def observe_http_request(*, method: str, route: str, status_code: int, duration_seconds: float) -> None:
    safe_route = route or "unknown"
    HTTP_REQUESTS_TOTAL.labels(method=method.upper(), route=safe_route, status_class=_status_class(status_code)).inc()
    HTTP_REQUEST_DURATION_SECONDS.labels(method=method.upper(), route=safe_route).observe(max(duration_seconds, 0.0))


def observe_records_confirm(*, item_count: int) -> None:
    RECORDS_CONFIRM_REQUESTS_TOTAL.inc()
    RECORDS_CONFIRM_ITEMS_TOTAL.inc(max(item_count, 0))


def observe_record_delete(*, outcome: str) -> None:
    RECORDS_DELETE_TOTAL.labels(outcome=outcome).inc()


def observe_vision_request(*, source_type: str, outcome: str, item_count: int, low_confidence_count: int) -> None:
    VISION_REQUESTS_TOTAL.labels(source_type=source_type, outcome=outcome).inc()
    VISION_ITEMS_TOTAL.labels(source_type=source_type).inc(max(item_count, 0))
    VISION_LOW_CONFIDENCE_ITEMS_TOTAL.labels(source_type=source_type).inc(max(low_confidence_count, 0))


def observe_menu_search(*, source: str, brand_filter: bool, outcome: str, result_count: int) -> None:
    brand_label = "yes" if brand_filter else "no"
    MENU_SEARCH_REQUESTS_TOTAL.labels(source=source, brand_filter=brand_label, outcome=outcome).inc()
    MENU_SEARCH_RESULTS.labels(source=source, brand_filter=brand_label).observe(max(result_count, 0))


def observe_qdrant_search(*, collection: str, brand_filter: bool, outcome: str, duration_seconds: float) -> None:
    brand_label = "yes" if brand_filter else "no"
    QDRANT_SEARCH_REQUESTS_TOTAL.labels(collection=collection, brand_filter=brand_label, outcome=outcome).inc()
    QDRANT_SEARCH_DURATION_SECONDS.labels(collection=collection, brand_filter=brand_label).observe(max(duration_seconds, 0.0))


def observe_agent_chat(*, mode: str, outcome: str, duration_seconds: float) -> None:
    AGENT_CHAT_REQUESTS_TOTAL.labels(mode=mode, outcome=outcome).inc()
    AGENT_CHAT_DURATION_SECONDS.labels(mode=mode, outcome=outcome).observe(max(duration_seconds, 0.0))


def observe_agent_first_token(*, mode: str, duration_seconds: float) -> None:
    AGENT_CHAT_FIRST_TOKEN_SECONDS.labels(mode=mode).observe(max(duration_seconds, 0.0))


def observe_agent_tool_call(*, tool: str, outcome: str, duration_seconds: float | None = None) -> None:
    tool_name = tool or "unknown"
    AGENT_TOOL_CALLS_TOTAL.labels(tool=tool_name, outcome=outcome).inc()
    if duration_seconds is not None:
        AGENT_TOOL_DURATION_SECONDS.labels(tool=tool_name, outcome=outcome).observe(max(duration_seconds, 0.0))


def observe_agent_budget_check(*, outcome: str, remaining_cny: float, reserved_cost_cny: float, available_output_tokens: int) -> None:
    AGENT_BUDGET_CHECKS_TOTAL.labels(outcome=outcome).inc()
    AGENT_BUDGET_REMAINING_CNY.observe(max(remaining_cny, 0.0))
    AGENT_BUDGET_RESERVED_COST_CNY.observe(max(reserved_cost_cny, 0.0))
    AGENT_BUDGET_AVAILABLE_OUTPUT_TOKENS.observe(max(available_output_tokens, 0))


def observe_llm_usage(*, model: str, input_tokens: int, output_tokens: int, estimated_cost_cny: float) -> None:
    clean_model = model or "unknown"
    LLM_TOKENS_TOTAL.labels(model=clean_model, direction="input").inc(max(input_tokens, 0))
    LLM_TOKENS_TOTAL.labels(model=clean_model, direction="output").inc(max(output_tokens, 0))
    LLM_COST_CNY_TOTAL.labels(model=clean_model).inc(max(estimated_cost_cny, 0.0))


def observe_budget_llm_usage(*, model: str, usage_kind: str, input_tokens: int, output_tokens: int, estimated_cost_cny: float) -> None:
    clean_model = model or "unknown"
    clean_usage_kind = usage_kind or "unknown"
    BUDGET_LLM_TOKENS_TOTAL.labels(model=clean_model, usage_kind=clean_usage_kind, direction="input").inc(max(input_tokens, 0))
    BUDGET_LLM_TOKENS_TOTAL.labels(model=clean_model, usage_kind=clean_usage_kind, direction="output").inc(max(output_tokens, 0))
    BUDGET_LLM_COST_CNY_TOTAL.labels(model=clean_model, usage_kind=clean_usage_kind).inc(max(estimated_cost_cny, 0.0))


def _status_class(status_code: int) -> str:
    if status_code < 100:
        return "unknown"
    return f"{status_code // 100}xx"
