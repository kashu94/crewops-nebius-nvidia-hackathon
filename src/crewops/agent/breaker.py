"""Circuit breakers in front of the model services: stop waiting on one that is down.

Every model call already has a firm timeout and a deterministic fallback, so a failure
never costs an answer. But while a service is failing (an outage, throttling, a network
drop), each question would still wait out its timeout first, up to ten seconds or more,
when a voice answer has eight. So after ``threshold`` failures in a row the
breaker opens: model calls fail at once for ``cooldown`` seconds and every answer comes
straight from the roster in DynamoDB through the templates. Then one call is let through
(half open): success closes the breaker, failure opens it again.

Only an unavailable service counts: timeouts, lost connections, throttling and 5xx
errors. A request the service refuses as invalid is our fault, not an outage, and passes
through without tripping anything.

Two breakers serve the whole process, so they remember across the periodic rebuilds of the
pipeline: ``MODEL_BREAKER`` for the Nemotron roles on Nebius Token Factory, and
``BEDROCK_BREAKER`` for the Titan embeddings and the guardrail on Bedrock. Either service
can be down while the other is up.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

from crewops.agent.nebius import NebiusError

OUTAGE_CODES = frozenset(
    {
        "ThrottlingException",
        "TooManyRequestsException",
        "ServiceUnavailableException",
        "ServiceUnavailable",
        "InternalServerException",
        "InternalFailure",
        "ModelTimeoutException",
        "ModelNotReadyException",
        "RequestTimeout",
    }
)
GUARDED_METHODS = ("converse", "invoke_model", "apply_guardrail")


class CircuitOpen(Exception):
    """The model service is paused after repeated failures; answer without it."""


def is_outage(exc: BaseException) -> bool:
    from botocore.exceptions import (
        ClientError,
        ConnectionClosedError,
        ConnectTimeoutError,
        EndpointConnectionError,
        ReadTimeoutError,
    )

    if isinstance(
        exc,
        ReadTimeoutError | ConnectTimeoutError | EndpointConnectionError | ConnectionClosedError,
    ):
        return True
    if isinstance(exc, NebiusError):
        return exc.outage
    if isinstance(exc, ClientError):
        error = exc.response.get("Error", {})
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)
        return error.get("Code") in OUTAGE_CODES or status >= 500
    return isinstance(exc, TimeoutError | ConnectionError)


class CircuitBreaker:
    CLOSED, OPEN, HALF_OPEN = "closed", "open", "half_open"

    def __init__(
        self,
        threshold: int = 3,
        cooldown: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.threshold, self.cooldown, self.clock = threshold, cooldown, clock
        self.state = self.CLOSED
        self.failures = 0
        self.opened_at = 0.0
        self.trips = 0
        self._lock = threading.Lock()

    def before(self) -> None:
        """Raise ``CircuitOpen`` unless a call may go ahead."""
        with self._lock:
            if self.state == self.CLOSED:
                return
            if self.state == self.OPEN and self.clock() - self.opened_at >= self.cooldown:
                self.state = self.HALF_OPEN  # one trial call; the rest still wait
                return
            raise CircuitOpen(f"model calls paused ({self.retry_in():.0f}s before a retry)")

    def succeeded(self) -> None:
        with self._lock:
            self.state, self.failures = self.CLOSED, 0

    def failed(self, exc: BaseException) -> None:
        if not is_outage(exc):
            # The service answered (it refused the request): it is up. A trial call that
            # ends this way must close the breaker, or it would stay half open for good.
            with self._lock:
                if self.state == self.HALF_OPEN:
                    self.state, self.failures = self.CLOSED, 0
            return
        with self._lock:
            self.failures += 1
            if self.state == self.HALF_OPEN or self.failures >= self.threshold:
                if self.state != self.OPEN:
                    self.trips += 1
                self.state, self.opened_at = self.OPEN, self.clock()

    def retry_in(self) -> float:
        return max(0.0, self.cooldown - (self.clock() - self.opened_at))

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "state": self.state,
                "failures": self.failures,
                "trips": self.trips,
                "retry_in_seconds": round(self.retry_in()) if self.state != self.CLOSED else 0,
            }


class GuardedClient:
    """A model client (Nebius or Bedrock) whose model calls go through a breaker."""

    def __init__(self, client: Any, breaker: CircuitBreaker) -> None:
        self._client, self._breaker = client, breaker

    def __getattr__(self, name: str) -> Any:
        target = getattr(self._client, name)
        if name not in GUARDED_METHODS:
            return target

        def call(*args: Any, **kwargs: Any) -> Any:
            self._breaker.before()
            try:
                out = target(*args, **kwargs)
            except Exception as exc:
                self._breaker.failed(exc)
                raise
            self._breaker.succeeded()
            return out

        return call


MODEL_BREAKER = CircuitBreaker()
"""The process's breaker for the chat models on Nebius Token Factory. Reported as "models"."""
BEDROCK_BREAKER = CircuitBreaker()
"""The process's breaker for Bedrock: Titan embeddings and the guardrail."""
