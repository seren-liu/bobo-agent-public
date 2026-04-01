from __future__ import annotations

from app.core.config import get_settings
from app.memory import repository


def should_refresh_thread_summary(thread_key: str, message_count: int) -> bool:
    threshold = max(int(get_settings().memory_summary_trigger_count or 20), 4)
    if message_count >= threshold:
        return True
    return message_count >= 6 and message_count % 6 == 0


def build_thread_summary(user_id: str, thread_key: str) -> dict[str, object]:
    messages = repository.list_messages(user_id, thread_key)
    if not messages:
        return {"summary_text": "", "open_slots": [], "covered_message_count": 0, "token_estimate": 0}

    recent = messages[-8:]
    user_topics = [item["content"].strip() for item in recent if item.get("role") == "user" and item.get("content")]
    assistant_topics = [item["content"].strip() for item in recent if item.get("role") == "assistant" and item.get("content")]
    summary_parts: list[str] = []
    if user_topics:
        summary_parts.append(f"用户最近关注：{'; '.join(user_topics[-3:])}")
    if assistant_topics:
        summary_parts.append(f"助手最近回应：{'; '.join(assistant_topics[-2:])}")

    open_slots: list[str] = []
    latest_user = user_topics[-1] if user_topics else ""
    if latest_user and not any(keyword in latest_user for keyword in ["谢谢", "好的", "明白", "结束"]):
        open_slots.append(f"最近用户请求待持续跟进：{latest_user[:80]}")

    summary_text = "；".join(part for part in summary_parts if part)[:600]
    token_estimate = max(len(summary_text) // 2, 1) if summary_text else 0
    return {
        "summary_text": summary_text,
        "open_slots": open_slots,
        "covered_message_count": len(messages),
        "token_estimate": token_estimate,
    }


def save_thread_summary(user_id: str, thread_key: str, summary: dict[str, object], summary_type: str = "rolling") -> dict[str, object]:
    return repository.save_summary(
        user_id=user_id,
        thread_key=thread_key,
        summary_type=summary_type,
        summary_text=str(summary.get("summary_text") or ""),
        open_slots=list(summary.get("open_slots") or []),
        covered_message_count=int(summary.get("covered_message_count") or 0),
        token_estimate=int(summary.get("token_estimate") or 0) or None,
    )


def refresh_thread_summary(user_id: str, thread_key: str, *, force: bool = False) -> dict[str, object] | None:
    thread = repository.get_thread_by_key(user_id, thread_key)
    if not thread:
        return None
    if not force and not should_refresh_thread_summary(thread_key, int(thread.get("message_count") or 0)):
        return repository.latest_summary(user_id, thread_key)
    summary = build_thread_summary(user_id, thread_key)
    if not summary.get("summary_text"):
        return None
    return save_thread_summary(user_id, thread_key, summary)
