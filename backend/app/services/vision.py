from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any, Literal

from app.models.vision import DrinkItem, VisionResult

logger = logging.getLogger("bobo.vision.service")

VISION_PROMPT = (
    "你是专业奶茶订单识别助手。从图片中提取所有饮品信息，"
    "返回纯 JSON，不含任何解释或 markdown："
    '{"items":[{"brand":"","name":"","size":"","sugar":"","ice":"",'
    '"price":null,"confidence":0.0}],"source_type":"photo","order_time":null}'
    "字段无法识别填 null，置信度 0~1。"
)

VISION_RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "brand": {"type": ["string", "null"]},
                    "name": {"type": ["string", "null"]},
                    "size": {"type": ["string", "null"]},
                    "sugar": {"type": ["string", "null"]},
                    "ice": {"type": ["string", "null"]},
                    "price": {"type": ["number", "null"]},
                    "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                },
                "required": ["brand", "name", "size", "sugar", "ice", "price", "confidence"],
            },
        },
        "source_type": {"type": "string", "enum": ["photo", "screenshot"]},
        "order_time": {"type": ["string", "null"]},
    },
    "required": ["items", "source_type", "order_time"],
}

STRICT_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "vision_result",
        "strict": True,
        "schema": VISION_RESULT_SCHEMA,
    },
}


class VisionService:
    def __init__(self) -> None:
        self.api_key = os.getenv("DASHSCOPE_API_KEY", "") or os.getenv("QWEN_API_KEY", "") or os.getenv("OPENAI_API_KEY", "")
        self.base_url = os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        self.model = os.getenv("VISION_MODEL", "qwen-vl-max")

    def _create_client(self):
        try:
            from openai import OpenAI
        except Exception as exc:  # pragma: no cover
            raise RuntimeError("openai package is required for vision recognition") from exc

        if not self.api_key:
            raise RuntimeError("vision API key is required")
        return OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=30)

    @staticmethod
    def _extract_text_content(raw: Any) -> str:
        if raw is None:
            return ""
        if isinstance(raw, str):
            return raw
        if isinstance(raw, list):
            texts: list[str] = []
            for chunk in raw:
                if isinstance(chunk, dict) and chunk.get("type") == "text":
                    texts.append(str(chunk.get("text", "")))
            return "\n".join(texts)
        return str(raw)

    @staticmethod
    def _strip_code_fence(text: str) -> str:
        content = text.strip()
        if content.startswith("```") and content.endswith("```"):
            lines = content.splitlines()
            if len(lines) >= 2:
                return "\n".join(lines[1:-1]).strip()
        return content

    @staticmethod
    def _normalize_items(items: list[DrinkItem]) -> list[DrinkItem]:
        normalized: list[DrinkItem] = []
        for item in items:
            if item.confidence < 0.5:
                normalized.append(
                    DrinkItem(
                        brand=None,
                        name=None,
                        size=None,
                        sugar=None,
                        ice=None,
                        price=None,
                        confidence=item.confidence,
                    )
                )
            else:
                normalized.append(item)
        return normalized

    def _build_messages(self, image_url: str) -> list[dict[str, Any]]:
        return [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": VISION_PROMPT},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            }
        ]

    def _create_completion(self, client: Any, image_url: str, response_format: dict[str, Any]) -> Any:
        return client.chat.completions.create(
            model=self.model,
            messages=self._build_messages(image_url),
            response_format=response_format,
            temperature=0,
        )

    def _log(self, event: str, **fields: Any) -> None:
        logger.info(json.dumps({"event": event, **fields}, ensure_ascii=False, default=str))

    def recognize(self, image_url: str, source_type: Literal["photo", "screenshot"], request_id: str | None = None) -> VisionResult:
        try:
            client = self._create_client()
        except Exception as exc:
            self._log("vision_recognize_client_error", request_id=request_id, source_type=source_type, error=str(exc))
            return VisionResult(items=[], source_type=source_type, order_time=None, error="recognition_failed")

        last_exc: Exception | None = None
        response = None
        response_strategy = "strict_json_schema"
        for response_format in (STRICT_RESPONSE_FORMAT, {"type": "json_object"}, None):
            try:
                if response_format is None:
                    # Plain text fallback – no response_format param
                    response = client.chat.completions.create(
                        model=self.model,
                        messages=self._build_messages(image_url),
                        temperature=0,
                    )
                    response_strategy = "plain_text_fallback"
                else:
                    response = self._create_completion(client, image_url, response_format)
                    response_strategy = "strict_json_schema" if response_format == STRICT_RESPONSE_FORMAT else "json_object_fallback"
                break
            except Exception as exc:
                last_exc = exc

        if response is None:
            self._log(
                "vision_recognize_failed",
                request_id=request_id,
                source_type=source_type,
                model=self.model,
                strategy=response_strategy,
                error=str(last_exc) if last_exc else "unknown",
            )
            return VisionResult(items=[], source_type=source_type, order_time=None, error="recognition_failed")

        raw_content = response.choices[0].message.content if response.choices else ""
        text = self._strip_code_fence(self._extract_text_content(raw_content))

        try:
            parsed = json.loads(text)
        except Exception as exc:
            self._log(
                "vision_recognize_parse_error",
                request_id=request_id,
                source_type=source_type,
                model=self.model,
                strategy=response_strategy,
                error=str(exc),
                preview=text[:200],
            )
            return VisionResult(items=[], source_type=source_type, order_time=None, error="parse_error")

        try:
            raw_items = parsed.get("items", [])
            parsed_items = [DrinkItem(**item) for item in raw_items]
        except Exception as exc:
            self._log(
                "vision_recognize_parse_error",
                request_id=request_id,
                source_type=source_type,
                model=self.model,
                strategy=response_strategy,
                error=str(exc),
            )
            return VisionResult(items=[], source_type=source_type, order_time=None, error="parse_error")

        order_time = parsed.get("order_time")
        order_time_parsed = None
        if order_time is not None:
            if not isinstance(order_time, str):
                self._log(
                    "vision_recognize_parse_error",
                    request_id=request_id,
                    source_type=source_type,
                    model=self.model,
                    strategy=response_strategy,
                    error="invalid order_time type",
                )
                return VisionResult(items=[], source_type=source_type, order_time=None, error="parse_error")
            try:
                order_time_parsed = datetime.fromisoformat(order_time.replace("Z", "+00:00"))
            except ValueError as exc:
                self._log(
                    "vision_recognize_parse_error",
                    request_id=request_id,
                    source_type=source_type,
                    model=self.model,
                    strategy=response_strategy,
                    error=str(exc),
                )
                return VisionResult(items=[], source_type=source_type, order_time=None, error="parse_error")

        result = VisionResult(
            items=self._normalize_items(parsed_items),
            source_type=source_type,
            order_time=order_time_parsed,
            error=None,
        )
        self._log(
            "vision_recognize_success",
            request_id=request_id,
            source_type=source_type,
            model=self.model,
            strategy=response_strategy,
            items_count=len(result.items),
            low_confidence_count=sum(1 for item in result.items if item.brand is None and item.name is None),
        )
        return result
