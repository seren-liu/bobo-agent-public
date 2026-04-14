from __future__ import annotations

from app.core.resilience import DependencyError, classify_dependency_error
from app.tooling.validation import ToolValidationError


def build_tool_error_payload(tool_name: str, exc: Exception) -> dict[str, object]:
    if isinstance(exc, ToolValidationError):
        category = f"{exc.phase}_validation"
        return {
            "ok": False,
            "error": str(exc),
            "error_category": category,
            "error_type": category,
            "retryable": False,
            "dependency": f"tool:{tool_name}",
        }
    error = classify_dependency_error(exc, f"tool:{tool_name}")
    return {
        "ok": False,
        "error": str(error),
        "error_category": error.category,
        "error_type": error.category,
        "retryable": error.retryable,
        "dependency": error.dependency,
    }
