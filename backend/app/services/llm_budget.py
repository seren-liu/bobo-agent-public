from __future__ import annotations

import math
import os
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo
from typing import Any

from app.observability import observe_budget_llm_usage, observe_llm_usage

_SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class ModelPricing:
    model: str
    region: str
    input_price_per_million: float
    output_price_per_million: float


_DEFAULT_BUDGET_CNY = 1.0
_DEFAULT_MODEL_REGION = "cn"
_QWEN3_32B_PRICING: dict[str, ModelPricing] = {
    "cn": ModelPricing(model="qwen3-32b", region="cn", input_price_per_million=2.0, output_price_per_million=8.0),
    "global": ModelPricing(model="qwen3-32b", region="global", input_price_per_million=1.174, output_price_per_million=4.697),
    "intl": ModelPricing(model="qwen3-32b", region="global", input_price_per_million=1.174, output_price_per_million=4.697),
}
_TEXT_EMBEDDING_V4_PRICING: dict[str, ModelPricing] = {
    "cn": ModelPricing(model="text-embedding-v4", region="cn", input_price_per_million=0.5, output_price_per_million=0.0),
}
_DEFAULT_MEMORY_EXTRACTION_RESERVE_INPUT_TOKENS = 1200
_DEFAULT_MEMORY_EXTRACTION_RESERVE_OUTPUT_TOKENS = 260
_DEFAULT_MEMORY_EMBED_UPSERT_RESERVE_INPUT_TOKENS = 480


def current_budget_date() -> date:
    return datetime.now(_SHANGHAI_TZ).date()


def daily_budget_cny() -> float:
    raw = os.getenv("BOBO_DAILY_LLM_BUDGET_CNY", "")
    try:
        parsed = float(raw)
    except (TypeError, ValueError):
        return _DEFAULT_BUDGET_CNY
    return parsed if parsed > 0 else _DEFAULT_BUDGET_CNY


def pricing_region() -> str:
    region = (os.getenv("BOBO_LLM_PRICING_REGION") or _DEFAULT_MODEL_REGION).strip().lower()
    return region or _DEFAULT_MODEL_REGION


def resolve_model_pricing(model: str) -> ModelPricing:
    clean_model = (model or "").strip()
    region = pricing_region()

    if clean_model == "qwen3-32b":
        return _QWEN3_32B_PRICING.get(region, _QWEN3_32B_PRICING["cn"])
    if clean_model == "text-embedding-v4":
        return _TEXT_EMBEDDING_V4_PRICING.get(region, _TEXT_EMBEDDING_V4_PRICING["cn"])

    input_override = os.getenv("BOBO_LLM_INPUT_PRICE_PER_MILLION")
    output_override = os.getenv("BOBO_LLM_OUTPUT_PRICE_PER_MILLION")
    if input_override and output_override:
        return ModelPricing(
            model=clean_model or "unknown",
            region=region,
            input_price_per_million=float(input_override),
            output_price_per_million=float(output_override),
        )

    raise ValueError(f"unsupported model pricing for {clean_model or 'unknown'}")


def estimate_tokens(text: str) -> int:
    clean = text.strip()
    if not clean:
        return 0
    # Conservative fallback when provider usage metadata is unavailable.
    return max(1, math.ceil(len(clean.encode("utf-8")) / 3))


def cost_cny_for_tokens(*, pricing: ModelPricing, input_tokens: int, output_tokens: int) -> float:
    input_cost = max(input_tokens, 0) * pricing.input_price_per_million / 1_000_000
    output_cost = max(output_tokens, 0) * pricing.output_price_per_million / 1_000_000
    return round(input_cost + output_cost, 6)


def remaining_output_token_budget(*, pricing: ModelPricing, spent_cost_cny: float, daily_budget: float) -> int:
    remaining_cny = max(daily_budget - spent_cost_cny, 0)
    if pricing.output_price_per_million <= 0 or remaining_cny <= 0:
        return 0
    return max(0, int((remaining_cny * 1_000_000) // pricing.output_price_per_million))


def affordable_output_tokens(*, pricing: ModelPricing, available_cost_cny: float) -> int:
    if pricing.output_price_per_million <= 0 or available_cost_cny <= 0:
        return 0
    return max(0, int((available_cost_cny * 1_000_000) // pricing.output_price_per_million))


def input_cost_cny(*, pricing: ModelPricing, input_tokens: int) -> float:
    return round(max(input_tokens, 0) * pricing.input_price_per_million / 1_000_000, 6)


def supports_pricing(model: str) -> bool:
    try:
        resolve_model_pricing(model)
    except Exception:
        return False
    return True


def record_usage(
    *,
    user_id: str | None,
    model: str,
    input_tokens: int,
    output_tokens: int = 0,
    usage_kind: str = "unknown",
) -> dict[str, Any] | None:
    if not user_id:
        return None
    try:
        pricing = resolve_model_pricing(model)
    except Exception:
        return None

    from app.memory import repository

    estimated_cost = cost_cny_for_tokens(
        pricing=pricing,
        input_tokens=max(int(input_tokens or 0), 0),
        output_tokens=max(int(output_tokens or 0), 0),
    )
    observe_llm_usage(
        model=model,
        input_tokens=max(int(input_tokens or 0), 0),
        output_tokens=max(int(output_tokens or 0), 0),
        estimated_cost_cny=estimated_cost,
    )
    observe_budget_llm_usage(
        model=model,
        usage_kind=usage_kind,
        input_tokens=max(int(input_tokens or 0), 0),
        output_tokens=max(int(output_tokens or 0), 0),
        estimated_cost_cny=estimated_cost,
    )

    return repository.add_daily_llm_usage(
        user_id=user_id,
        usage_date=current_budget_date(),
        model=model,
        input_tokens=max(int(input_tokens or 0), 0),
        output_tokens=max(int(output_tokens or 0), 0),
        estimated_cost_cny=estimated_cost,
    )


def embedding_model() -> str:
    return os.getenv("EMBEDDING_MODEL") or "text-embedding-v4"


def memory_extraction_reserve_tokens() -> tuple[int, int]:
    return (
        _int_env("BOBO_MEMORY_EXTRACTION_RESERVE_INPUT_TOKENS", _DEFAULT_MEMORY_EXTRACTION_RESERVE_INPUT_TOKENS),
        _int_env("BOBO_MEMORY_EXTRACTION_RESERVE_OUTPUT_TOKENS", _DEFAULT_MEMORY_EXTRACTION_RESERVE_OUTPUT_TOKENS),
    )


def memory_embedding_reserve_tokens() -> int:
    return _int_env("BOBO_MEMORY_EMBED_UPSERT_RESERVE_INPUT_TOKENS", _DEFAULT_MEMORY_EMBED_UPSERT_RESERVE_INPUT_TOKENS)


def extract_usage_tokens(payload: Any) -> tuple[int, int] | None:
    prompt = _extract_int(payload, ("prompt_tokens", "input_tokens"))
    completion = _extract_int(payload, ("completion_tokens", "output_tokens"))
    if prompt is None and completion is None:
        return None
    return max(prompt or 0, 0), max(completion or 0, 0)


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return max(int(str(raw).strip()), 0)
    except ValueError:
        return default


def _extract_int(payload: Any, keys: tuple[str, ...]) -> int | None:
    if payload is None:
        return None

    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, (int, float)):
                return int(value)
        for value in payload.values():
            nested = _extract_int(value, keys)
            if nested is not None:
                return nested
        return None

    for key in keys:
        value = getattr(payload, key, None)
        if isinstance(value, (int, float)):
            return int(value)

    usage_metadata = getattr(payload, "usage_metadata", None)
    nested = _extract_int(usage_metadata, keys)
    if nested is not None:
        return nested

    response_metadata = getattr(payload, "response_metadata", None)
    nested = _extract_int(response_metadata, keys)
    if nested is not None:
        return nested

    if isinstance(payload, (list, tuple)):
        for item in payload:
            nested = _extract_int(item, keys)
            if nested is not None:
                return nested

    return None
