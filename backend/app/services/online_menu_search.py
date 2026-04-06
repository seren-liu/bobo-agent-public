from __future__ import annotations

import asyncio
import html
import json
import os
import re
from collections.abc import Iterable
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from app.core.brands import canonicalize_brand_name

BRAND_WEB_SOURCES: dict[str, tuple[str, ...]] = {
    "霸王茶姬": ("https://www.chagee.com", "https://chagee.com.sg/zh-sg/product"),
    "古茗": ("https://www.gumingnc.com",),
    "喜茶": ("https://www.heytea.com",),
    "蜜雪冰城": ("https://www.mxbc.com",),
}

ONLINE_MENU_RESULT_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string"},
                    "category": {"type": ["string", "null"]},
                    "price": {"type": ["number", "null"]},
                    "reason": {"type": "string"},
                    "source_url": {"type": ["string", "null"]},
                },
                "required": ["name", "category", "price", "reason", "source_url"],
            },
        }
    },
    "required": ["candidates"],
}

ONLINE_MENU_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "online_menu_candidates",
        "strict": True,
        "schema": ONLINE_MENU_RESULT_SCHEMA,
    },
}

_QUERY_TIMEOUT = httpx.Timeout(8.0, connect=3.0)
_HOT_KEYWORDS = ("招牌", "热门", "经典", "人气", "爆款", "推荐", "主打", "畅销")
_G_GENERIC_HINTS = ("菜单", "产品", "官网", "介绍", "品牌", "活动", "资讯", "页面")
_GENERIC_CANDIDATE_NAMES = {
    "官方网站",
    "官网",
    "抖音",
    "小红书",
    "微博",
    "视频",
    "产品",
    "菜单",
    "官方",
    "品牌",
    "首页",
    "详情",
    "门店",
    "外卖",
}
_CATEGORY_HINTS: dict[str, tuple[str, ...]] = {
    "奶茶": ("奶茶", "牛乳茶", "乳茶", "厚乳", "奶香"),
    "果茶": ("果茶", "鲜果", "水果", "柠檬", "果香"),
    "轻乳茶": ("轻乳茶", "乳茶", "奶茶"),
    "纯茶": ("纯茶", "原叶", "茗茶"),
    "柠檬茶": ("柠檬茶", "柠檬", "果茶"),
    "咖啡": ("咖啡", "拿铁", "美式"),
}


def _strip_tags(raw: str) -> str:
    text = re.sub(r"<[^>]+>", "", raw or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _decode_result_url(raw_url: str) -> str:
    if not raw_url:
        return ""
    if raw_url.startswith("//"):
        raw_url = f"https:{raw_url}"
    parsed = urlparse(raw_url)
    if "duckduckgo.com" not in parsed.netloc:
        return raw_url
    target = parse_qs(parsed.query).get("uddg", [""])[0]
    return unquote(target) if target else raw_url


def _extract_meta_description(html_text: str) -> str:
    matched = re.search(
        r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']',
        html_text,
        re.IGNORECASE,
    )
    if not matched:
        return ""
    return _strip_tags(matched.group(1))


def _extract_page_excerpt(html_text: str, limit: int = 2400) -> str:
    text = _strip_tags(html_text)
    return text[:limit]


def _compact_text(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text[:limit]


def _score_text(text: str, query: str | None) -> float:
    score = 0.0
    normalized = text.lower()
    if query:
        # Query can be very short, so give dense direct matches more weight.
        if query in text:
            score += 2.0
        for token in _CATEGORY_HINTS.get(query, (query,)):
            if token and token in text:
                score += 1.0
    for keyword in _HOT_KEYWORDS:
        if keyword in text:
            score += 0.4
    for keyword in _G_GENERIC_HINTS:
        if keyword in text:
            score -= 0.1
    if any(token in normalized for token in ("¥", "元", "价格")):
        score += 0.5
    return score


def _extract_price(text: str) -> float | None:
    matched = re.search(r"(?:¥|￥)\s*(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*(?:元|块)", text)
    if not matched:
        return None
    try:
        value = matched.group(1) or matched.group(2)
        return float(value) if value is not None else None
    except ValueError:
        return None


def _split_candidate_name(text: str) -> str:
    for separator in ("｜", "|", "·", "-", "—", ":", "：", "/"):
        if separator in text:
            parts = [part.strip() for part in text.split(separator) if part.strip()]
            if parts:
                return parts[-1]
    return text.strip()


def _normalize_candidate_name(name: str, brand: str) -> str:
    cleaned = re.sub(r"\s+", "", name or "")
    cleaned = cleaned.replace(brand, "")
    for token in _G_GENERIC_HINTS + _HOT_KEYWORDS:
        cleaned = cleaned.replace(token, "")
    return cleaned.strip("()（）[]【】<>《》,，。；; ")


def _looks_like_product_name(name: str, query: str | None) -> bool:
    value = _normalize_candidate_name(name, "")
    if not value or len(value) < 2:
        return False
    if value in _GENERIC_CANDIDATE_NAMES:
        return False
    if any(hint in value for hint in ("http", ".com", "www", "官方网站", "抖音号", "旗舰店")):
        return False
    if value.endswith(("官网", "官方", "抖音", "首页", "菜单", "产品", "页面", "品牌")):
        return False
    if value.startswith(("官方", "抖音", "品牌", "菜单", "产品")):
        return False
    if query and value == query:
        return False
    return bool(re.search(r"[\u4e00-\u9fff]{2,}", value))


def _extract_category(text: str, query: str | None) -> str | None:
    if query and query in text:
        return query
    for category, hints in _CATEGORY_HINTS.items():
        if any(token in text for token in hints):
            return category
    return query


def _document_candidates(document: dict[str, str], brand: str, query: str | None) -> list[dict[str, object]]:
    text_parts = [document.get("title") or "", document.get("snippet") or "", document.get("excerpt") or ""]
    text = " ".join(part for part in text_parts if part)
    if not text:
        return []

    base_name = _normalize_candidate_name(_split_candidate_name(document.get("title") or text[:24]), brand)
    category = _extract_category(text, query)
    price = _extract_price(text)
    score = 0.0
    score += _score_text(text, query)
    score += 0.5 if base_name else 0.0
    score += 0.3 if price is not None else 0.0
    if document.get("url") and any(domain in str(document.get("url")) for domain in BRAND_WEB_SOURCES.get(brand, ())):
        score += 0.8

    if len(base_name) < 2:
        base_name = ""

    if not base_name:
        fallback = re.findall(r"[\u4e00-\u9fff]{2,10}", text)
        for candidate in fallback:
            normalized = _normalize_candidate_name(candidate, brand)
            if len(normalized) >= 2 and normalized not in _G_GENERIC_HINTS and _looks_like_product_name(normalized, query):
                base_name = normalized
                break

    if not base_name or not _looks_like_product_name(base_name, query):
        return []

    reason_bits: list[str] = []
    if query and query in text:
        reason_bits.append(f"命中{query}")
    if category and category != query:
        reason_bits.append(f"更像{category}")
    if price is not None:
        reason_bits.append(f"提到¥{price:g}")
    for keyword in _HOT_KEYWORDS:
        if keyword in text:
            reason_bits.append(f"包含{keyword}")
            break
    if not reason_bits:
        reason_bits.append("证据来自公开网页")

    return [
        {
            "name": base_name,
            "category": category,
            "price": price,
            "reason": "，".join(reason_bits[:3]),
            "source_url": document.get("url"),
            "score": score,
        }
    ]


def _compact_documents(documents: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    compacted: list[dict[str, str]] = []
    for item in documents:
        compacted.append(
            {
                "title": _compact_text(str(item.get("title") or ""), 80),
                "url": str(item.get("url") or ""),
                "snippet": _compact_text(str(item.get("snippet") or ""), 220),
                "excerpt": _compact_text(str(item.get("excerpt") or ""), 360),
            }
        )
    return compacted


def _heuristic_rank_candidates(
    *,
    brand: str,
    query: str | None,
    user_message: str,
    documents: list[dict[str, str]],
) -> list[dict[str, object]]:
    extracted: list[dict[str, object]] = []
    for document in documents:
        extracted.extend(_document_candidates(document, brand, query))

    if not extracted:
        return []

    user_hint = _score_text(user_message, query)
    for item in extracted:
        item["score"] = float(item.get("score") or 0.0) + user_hint
    extracted.sort(
        key=lambda item: (
            float(item.get("score") or 0.0),
            1.0 if item.get("price") is not None else 0.0,
            len(str(item.get("name") or "")),
        ),
        reverse=True,
    )

    seen: set[tuple[str, str | None]] = set()
    normalized: list[dict[str, object]] = []
    for item in extracted:
        key = (str(item.get("name") or ""), str(item.get("source_url") or ""))
        if key in seen:
            continue
        seen.add(key)
        if not _looks_like_product_name(str(item.get("name") or ""), query):
            continue
        normalized.append(
            {
                "name": item["name"],
                "category": item.get("category"),
                "price": item.get("price"),
                "reason": item.get("reason"),
                "source_url": item.get("source_url"),
            }
        )
        if len(normalized) >= 3:
            break
    return normalized


async def _fetch_search_results(client: httpx.AsyncClient, search_query: str) -> list[dict[str, str]]:
    try:
        response = await client.get("https://html.duckduckgo.com/html/", params={"q": search_query})
        response.raise_for_status()
        return _parse_duckduckgo_results(response.text)
    except Exception:
        return []


async def _fetch_brand_source(client: httpx.AsyncClient, source_url: str) -> dict[str, str] | None:
    try:
        response = await client.get(source_url)
        response.raise_for_status()
    except Exception:
        return None

    title_match = re.search(r"<title>(.*?)</title>", response.text, re.IGNORECASE | re.S)
    title = _strip_tags(title_match.group(1)) if title_match else source_url
    snippet = _extract_meta_description(response.text)
    excerpt = _extract_page_excerpt(response.text)
    if not snippet and not excerpt:
        return None
    return {
        "title": title,
        "snippet": snippet or title,
        "url": source_url,
        "excerpt": excerpt,
        "source_kind": "brand_source",
    }


def _parse_duckduckgo_results(html_text: str) -> list[dict[str, str]]:
    pattern = re.compile(
        r'<a rel="nofollow" class="result__a" href="(?P<url>[^"]+)">(?P<title>.*?)</a>.*?'
        r'<a class="result__snippet" href="[^"]+">(?P<snippet>.*?)</a>',
        re.S,
    )
    results: list[dict[str, str]] = []
    for matched in pattern.finditer(html_text):
        url = _decode_result_url(matched.group("url"))
        title = _strip_tags(matched.group("title"))
        snippet = _strip_tags(matched.group("snippet"))
        if not title or not snippet:
            continue
        results.append({"title": title, "snippet": snippet, "url": url})
        if len(results) >= 6:
            break
    return results


async def search_online_brand_menu(brand: str, query: str | None) -> list[dict[str, str]]:
    normalized_brand = canonicalize_brand_name(brand) or brand
    search_queries = [
        f"{normalized_brand} {query or ''} 菜单".strip(),
        f"{normalized_brand} {query or ''} 产品".strip(),
        f"{normalized_brand} 官网 {query or ''}".strip(),
    ]
    headers = {"User-Agent": "Mozilla/5.0"}
    out: list[dict[str, str]] = []

    async with httpx.AsyncClient(headers=headers, timeout=_QUERY_TIMEOUT, follow_redirects=True) as client:
        tasks = [_fetch_search_results(client, search_query) for search_query in search_queries]
        tasks.extend(_fetch_brand_source(client, source_url) for source_url in BRAND_WEB_SOURCES.get(normalized_brand, ()))
        results = await asyncio.gather(*tasks, return_exceptions=True)

    for result in results:
        if isinstance(result, Exception):
            continue
        if isinstance(result, list):
            out.extend(result)
        elif isinstance(result, dict):
            out.append(result)

    deduped: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in out:
        key = item["url"] or f'{item["title"]}:{item["snippet"]}'
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped[:8]


def _online_model_name() -> str:
    return os.getenv("ONLINE_MENU_MODEL") or os.getenv("FAST_RANKER_MODEL") or "qwen-turbo"


def _create_async_llm_client():
    try:
        from openai import AsyncOpenAI
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("openai package is required for online menu ranking") from exc

    api_key = os.getenv("DASHSCOPE_API_KEY", "") or os.getenv("QWEN_API_KEY", "") or os.getenv("OPENAI_API_KEY", "")
    base_url = os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    if not api_key:
        raise RuntimeError("online menu ranking API key is required")
    return AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=20)


def _extract_text_content(raw: object) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        return "\n".join(
            str(chunk.get("text", ""))
            for chunk in raw
            if isinstance(chunk, dict) and chunk.get("type") == "text"
        )
    return str(raw)


def _strip_code_fence(text: str) -> str:
    content = text.strip()
    if content.startswith("```") and content.endswith("```"):
        lines = content.splitlines()
        if len(lines) >= 2:
            return "\n".join(lines[1:-1]).strip()
    return content


async def rank_online_menu_candidates_async(
    *,
    brand: str,
    query: str | None,
    user_message: str,
    documents: list[dict[str, str]],
) -> list[dict[str, object]]:
    if not documents:
        return []

    client = _create_async_llm_client()
    docs_payload = _compact_documents(documents[:4])
    prompt = (
        "你是奶茶品牌菜单候选抽取与推荐排序器。"
        f"目标品牌：{brand}。目标品类：{query or '饮品'}。用户原话：{user_message}。"
        "先把网页证据抽取成候选，再按相关性排序。"
        "只返回 JSON，不要 markdown。"
        "如果网页里只有品牌/品类信息但没有明确饮品名，则返回空数组。"
        "reason 要简短说明为什么选它，并引用网页证据，不要编造。"
        f"网页证据：{json.dumps(docs_payload, ensure_ascii=False)}"
    )
    response = await client.chat.completions.create(
        model=_online_model_name(),
        messages=[{"role": "user", "content": prompt}],
        response_format=ONLINE_MENU_RESPONSE_FORMAT,
        temperature=0.2,
    )
    text = _strip_code_fence(_extract_text_content(response.choices[0].message.content if response.choices else ""))
    parsed = json.loads(text)
    candidates = parsed.get("candidates") or []
    normalized: list[dict[str, object]] = []
    for item in candidates:
        name = str(item.get("name") or "").strip()
        reason = str(item.get("reason") or "").strip()
        if not name or not reason:
            continue
        normalized.append(
            {
                "name": name,
                "category": item.get("category"),
                "price": item.get("price"),
                "reason": reason,
                "source_url": item.get("source_url"),
            }
        )
    return normalized[:3]


def rank_online_menu_candidates(
    *,
    brand: str,
    query: str | None,
    user_message: str,
    documents: list[dict[str, str]],
) -> list[dict[str, object]]:
    if not documents:
        return []

    heuristic_candidates = _heuristic_rank_candidates(
        brand=brand,
        query=query,
        user_message=user_message,
        documents=documents,
    )
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        try:
            async_candidates = asyncio.run(
                rank_online_menu_candidates_async(
                    brand=brand,
                    query=query,
                    user_message=user_message,
                    documents=documents,
                )
            )
            if async_candidates:
                return async_candidates
        except Exception:
            pass
        return heuristic_candidates

    return heuristic_candidates
