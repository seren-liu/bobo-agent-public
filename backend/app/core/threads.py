from __future__ import annotations


def normalize_session_thread_id(user_id: str, session_id: str) -> str:
    """Normalize a client session id into the authenticated user's thread namespace."""
    clean = (session_id or "").strip()
    if clean.startswith("user-") and ":session-" in clean:
        clean = clean.split(":session-", 1)[1]
    elif clean.startswith("session-"):
        clean = clean[len("session-") :]

    clean = clean.strip()
    return f"user-{user_id}:session-{clean}"
