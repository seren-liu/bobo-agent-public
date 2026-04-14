from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass
from typing import Any

from app.observability import observe_dependency_call, set_dependency_circuit_state


@dataclass(slots=True)
class DependencyError(RuntimeError):
    dependency: str
    category: str
    detail: str
    retryable: bool = True

    def __str__(self) -> str:
        return self.detail


def classify_dependency_error(exc: Exception, dependency: str) -> DependencyError:
    if isinstance(exc, DependencyError):
        return exc

    text = str(exc).strip()
    lowered = text.lower()

    if isinstance(exc, asyncio.TimeoutError) or "timeout" in lowered or "timed out" in lowered:
        return DependencyError(dependency=dependency, category="timeout", detail=text or f"{dependency} timed out", retryable=True)
    if "429" in lowered or "rate limit" in lowered or "too many requests" in lowered:
        return DependencyError(dependency=dependency, category="rate_limit", detail=text or f"{dependency} rate limited", retryable=True)
    if "401" in lowered or "403" in lowered or "unauthorized" in lowered or "forbidden" in lowered:
        return DependencyError(dependency=dependency, category="auth", detail=text or f"{dependency} auth failed", retryable=False)
    if any(token in lowered for token in ("connection", "refused", "unavailable", "temporarily", "server disconnected", "service down")):
        return DependencyError(dependency=dependency, category="unavailable", detail=text or f"{dependency} unavailable", retryable=True)
    if isinstance(exc, (ValueError, TypeError, KeyError)) or "invalid" in lowered or "schema" in lowered:
        return DependencyError(dependency=dependency, category="invalid_response", detail=text or f"{dependency} returned invalid response", retryable=False)
    return DependencyError(dependency=dependency, category="upstream_error", detail=text or f"{dependency} failed", retryable=True)


class CircuitBreaker:
    def __init__(
        self,
        name: str,
        *,
        failure_threshold: int = 3,
        recovery_timeout_seconds: float = 30.0,
        half_open_max_calls: int = 1,
    ) -> None:
        self.name = name
        self.failure_threshold = max(int(failure_threshold or 0), 1)
        self.recovery_timeout_seconds = max(float(recovery_timeout_seconds or 0), 1.0)
        self.half_open_max_calls = max(int(half_open_max_calls or 0), 1)
        self._lock = threading.Lock()
        self._state = "closed"
        self._failure_count = 0
        self._opened_at = 0.0
        self._half_open_in_flight = 0
        self._publish_state("closed")

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    def before_call(self) -> None:
        with self._lock:
            now = time.monotonic()
            if self._state == "open":
                if now - self._opened_at >= self.recovery_timeout_seconds:
                    self._state = "half_open"
                    self._half_open_in_flight = 0
                    self._publish_state("half_open")
                else:
                    raise DependencyError(
                        dependency=self.name,
                        category="circuit_open",
                        detail=f"{self.name} circuit is open",
                        retryable=True,
                    )

            if self._state == "half_open":
                if self._half_open_in_flight >= self.half_open_max_calls:
                    raise DependencyError(
                        dependency=self.name,
                        category="circuit_open",
                        detail=f"{self.name} circuit is probing",
                        retryable=True,
                    )
                self._half_open_in_flight += 1

    def on_success(self) -> None:
        with self._lock:
            self._failure_count = 0
            self._half_open_in_flight = 0
            if self._state != "closed":
                self._state = "closed"
                self._publish_state("closed")

    def on_failure(self) -> None:
        with self._lock:
            self._failure_count += 1
            self._half_open_in_flight = 0
            if self._failure_count >= self.failure_threshold:
                self._state = "open"
                self._opened_at = time.monotonic()
                self._publish_state("open")

    def _publish_state(self, active_state: str) -> None:
        for state in ("closed", "open", "half_open"):
            set_dependency_circuit_state(dependency=self.name, state=state, value=1 if state == active_state else 0)


_BREAKERS: dict[tuple[str, int, float, int], CircuitBreaker] = {}
_BREAKERS_LOCK = threading.Lock()


def get_circuit_breaker(
    name: str,
    *,
    failure_threshold: int = 3,
    recovery_timeout_seconds: float = 30.0,
    half_open_max_calls: int = 1,
) -> CircuitBreaker:
    key = (name, max(int(failure_threshold or 0), 1), max(float(recovery_timeout_seconds or 0), 1.0), max(int(half_open_max_calls or 0), 1))
    with _BREAKERS_LOCK:
        breaker = _BREAKERS.get(key)
        if breaker is None:
            breaker = CircuitBreaker(
                name,
                failure_threshold=key[1],
                recovery_timeout_seconds=key[2],
                half_open_max_calls=key[3],
            )
            _BREAKERS[key] = breaker
        return breaker


async def call_with_resilience(
    dependency: str,
    operation: Any,
    *,
    timeout_seconds: float | None = None,
    breaker: CircuitBreaker | None = None,
) -> Any:
    active_breaker = breaker
    if active_breaker is not None:
        active_breaker.before_call()

    start = time.perf_counter()
    try:
        if timeout_seconds and timeout_seconds > 0:
            result = await asyncio.wait_for(operation(), timeout=timeout_seconds)
        else:
            result = await operation()
    except Exception as exc:
        error = classify_dependency_error(exc, dependency)
        if active_breaker is not None:
            active_breaker.on_failure()
        observe_dependency_call(
            dependency=dependency,
            outcome="error",
            category=error.category,
            duration_seconds=time.perf_counter() - start,
        )
        raise error

    if active_breaker is not None:
        active_breaker.on_success()
    observe_dependency_call(
        dependency=dependency,
        outcome="success",
        category="none",
        duration_seconds=time.perf_counter() - start,
    )
    return result
