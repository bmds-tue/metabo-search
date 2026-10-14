"""Shared HTTP transport: pooling, retries, rate limiting, typed errors.

Both source clients use one :class:`HttpClient`. It owns a pooled
``httpx.Client`` (keep-alive across threads), retries transient failures with
exponential backoff and jitter, honours ``Retry-After`` on 429, optionally
rate-limits, and turns HTTP failures into the typed errors in
:mod:`metabo_search.errors`.
"""

from __future__ import annotations

import random
import threading
import time
import warnings
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from metabo_search.errors import (
    ClientError,
    NotFound,
    RateLimited,
    ResponseValidationError,
    TransientError,
)

T = TypeVar("T", bound=BaseModel)

QueryValue = str | int | float | bool | None
"""A scalar query-string value."""


@dataclass(frozen=True)
class RetryPolicy:
    """Retry behaviour for transient failures.

    Attributes:
        max_attempts: Total attempts, including the first.
        base_delay: Delay before the first retry, in seconds.
        max_delay: Cap for the exponential backoff, in seconds.
        jitter: Fraction of the delay added as random jitter, in ``[0, 1]``.
        retry_status: HTTP statuses that are retried.
    """

    max_attempts: int = 4
    base_delay: float = 0.5
    max_delay: float = 20.0
    jitter: float = 0.25
    retry_status: frozenset[int] = frozenset({429, 500, 502, 503, 504})

    def __post_init__(self) -> None:
        assert self.max_attempts >= 1, "max_attempts must be >= 1"
        assert self.base_delay >= 0, "base_delay must be >= 0"
        assert self.max_delay >= self.base_delay, "max_delay must be >= base_delay"
        assert 0 <= self.jitter <= 1, "jitter must be in [0, 1]"


@dataclass(frozen=True)
class RateLimit:
    """Token-bucket rate limit shared by every request of one client.

    Attributes:
        requests_per_second: Sustained rate; ``None`` means unlimited.
        burst: Number of requests allowed immediately.
    """

    requests_per_second: float | None = None
    burst: int = 1

    def __post_init__(self) -> None:
        if self.requests_per_second is not None:
            assert self.requests_per_second > 0, "rate must be > 0"
        assert self.burst >= 1, "burst must be >= 1"


@dataclass(frozen=True)
class HttpRecord:
    """One HTTP attempt, passed to the optional raw sink.

    Attributes:
        method: HTTP method.
        url: Full request URL.
        status: Response status, or ``None`` if the request never completed.
        elapsed_ms: Wall-clock duration of the attempt, in milliseconds.
        attempt: 1-based attempt number.
    """

    method: str
    url: str
    status: int | None
    elapsed_ms: float
    attempt: int


@dataclass(frozen=True)
class HttpStats:
    """Cumulative HTTP attempt statistics for one :class:`HttpClient`.

    Counters accumulate over the client's lifetime. A throttled run is visible
    here (``throttled``/``backoff_seconds``) even though the client retries
    transparently. Subtract an earlier snapshot to measure one batch:
    ``after - before``.

    Attributes:
        requests: Logical operations (one per request/download call).
        attempts: HTTP attempts, including retries.
        ok: Responses that completed successfully (2xx/3xx).
        throttled: HTTP 429 responses.
        transient: Retryable failures: 5xx, timeouts, transport errors.
        backoff_seconds: Total time slept between attempts.
    """

    requests: int = 0
    attempts: int = 0
    ok: int = 0
    throttled: int = 0
    transient: int = 0
    backoff_seconds: float = 0.0

    @property
    def retries(self) -> int:
        """Attempts beyond the first for each logical request."""
        return self.attempts - self.requests

    def __sub__(self, other: HttpStats) -> HttpStats:
        """Return the delta since an earlier snapshot ``other``.

        Raises:
            AssertionError: ``other`` is newer than ``self`` on some counter.
        """
        result = HttpStats(
            requests=self.requests - other.requests,
            attempts=self.attempts - other.attempts,
            ok=self.ok - other.ok,
            throttled=self.throttled - other.throttled,
            transient=self.transient - other.transient,
            backoff_seconds=self.backoff_seconds - other.backoff_seconds,
        )
        assert (
            result.requests >= 0
            and result.attempts >= 0
            and result.ok >= 0
            and result.throttled >= 0
            and result.transient >= 0
            and result.backoff_seconds >= 0.0
        ), "HttpStats delta must not be negative"
        return result


@dataclass
class _StatsAccumulator:
    """Mutable counters behind :class:`HttpStats` (guarded by a lock)."""

    requests: int = 0
    attempts: int = 0
    ok: int = 0
    throttled: int = 0
    transient: int = 0
    backoff_seconds: float = 0.0

    def snapshot(self) -> HttpStats:
        """Return an immutable copy of the current counters."""
        return HttpStats(
            requests=self.requests,
            attempts=self.attempts,
            ok=self.ok,
            throttled=self.throttled,
            transient=self.transient,
            backoff_seconds=self.backoff_seconds,
        )


def warn_if_throttled(
    stats: HttpStats,
    *,
    errors: int = 0,
    workers: int | None = None,
    action: str = "inspect",
    stacklevel: int = 3,
) -> None:
    """Warn when a batch was throttled or lost items; a no-op when clean.

    A transient 5xx that retried and succeeded is deliberately not a warning.

    Args:
        stats: The batch's :class:`HttpStats` delta.
        errors: Number of items that failed (e.g. errored ``StudyDetail``s).
        workers: Worker count, included in the hint when throttled.
        action: Prefix for the message, e.g. ``"MetaboLights.inspect"``.
        stacklevel: ``warnings.warn`` stacklevel.
    """
    assert errors >= 0, "errors must be >= 0"
    if stats.throttled == 0 and errors == 0:
        return
    parts: list[str] = []
    if stats.throttled:
        parts.append(f"{stats.throttled} throttled (429) response(s)")
    if stats.backoff_seconds >= 0.1:
        parts.append(f"{stats.backoff_seconds:.1f}s in retry backoff")
    if errors:
        parts.append(f"{errors} item(s) failed")
    hint = f"; lower workers (currently {workers})" if stats.throttled and workers else ""
    warnings.warn(f"{action}: {', '.join(parts)}{hint}", UserWarning, stacklevel=stacklevel)


class _TokenBucket:
    """A thread-safe token bucket.

    Args:
        rate: Tokens added per second.
        burst: Bucket capacity (tokens available immediately).
    """

    def __init__(self, rate: float, burst: int) -> None:
        self._rate = rate
        self._capacity = float(burst)
        self._tokens = float(burst)
        self._updated = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        """Block until a token is available, then consume one."""
        while True:
            with self._lock:
                now = time.monotonic()
                self._tokens = min(
                    self._capacity, self._tokens + (now - self._updated) * self._rate
                )
                self._updated = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                wait = (1.0 - self._tokens) / self._rate
            time.sleep(wait)


class HttpClient:
    """A pooled HTTP client with retries, rate limiting, and typed errors.

    The client is synchronous and safe to share across threads.

    Args:
        base_url: Prefix for every request path. A trailing slash is added if
            missing; a leading slash on ``path`` is ignored.
        headers: Default headers merged into every request.
        timeout: Read timeout in seconds. The connect timeout is capped at 10 s.
        retry: Retry policy for transient failures.
        rate_limit: Optional token-bucket limiter shared by all calls.
        raw_sink: Optional callback invoked once per attempt with an
            :class:`HttpRecord` (provenance / audit).
        transport: Optional httpx transport, used by tests.
    """

    def __init__(
        self,
        base_url: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float = 30.0,
        retry: RetryPolicy | None = None,
        rate_limit: RateLimit | None = None,
        raw_sink: Callable[[HttpRecord], None] | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        assert base_url, "base_url must be non-empty"
        self._base_url = base_url.rstrip("/") + "/"
        self._retry = retry or RetryPolicy()
        self._bucket = (
            _TokenBucket(rate_limit.requests_per_second, rate_limit.burst)
            if rate_limit is not None and rate_limit.requests_per_second is not None
            else None
        )
        self._raw_sink = raw_sink
        self._stats = _StatsAccumulator()
        self._stats_lock = threading.Lock()
        self._client = httpx.Client(
            headers=headers,
            timeout=httpx.Timeout(timeout, connect=min(10.0, timeout)),
            follow_redirects=True,
            transport=transport,
        )

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, QueryValue] | None = None,
        json: object | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        """Perform a request, retrying transient failures.

        Args:
            method: HTTP method, e.g. ``"GET"``.
            path: Path appended to the client's base URL.
            params: Query-string parameters.
            json: JSON-serialisable request body.
            headers: Per-request headers, merged with the defaults.
            timeout: Per-request read timeout, overriding the client default.

        Returns:
            The successful ``httpx.Response`` (2xx or 3xx).

        Raises:
            NotFound: The server returned 404.
            ClientError: The server returned another non-retryable 4xx.
            RateLimited: The server returned 429 after every attempt.
            TransientError: The server returned 5xx, or the network failed,
                after every attempt.
        """
        url = self._url(path)
        self._add_request()
        for attempt in range(1, self._retry.max_attempts + 1):
            self._acquire()
            started = time.monotonic()
            status: int | None = None
            try:
                response = self._client.request(
                    method, url, params=params, json=json, headers=headers, timeout=timeout
                )
                status = response.status_code
                if status in self._retry.retry_status and attempt < self._retry.max_attempts:
                    self._sleep(attempt, self._retry_after(response))
                    continue
                self._raise_for_status(response)
                return response
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt >= self._retry.max_attempts:
                    raise TransientError(
                        f"{method} {url} failed after {attempt} attempts: {exc}"
                    ) from exc
                self._sleep(attempt, None)
            finally:
                self._emit(
                    method, url, status, (time.monotonic() - started) * 1000.0, attempt
                )
        raise TransientError(f"{method} {url} failed")  # pragma: no cover

    def get_json(
        self,
        path: str,
        *,
        model: type[T],
        params: Mapping[str, QueryValue] | None = None,
    ) -> T:
        """GET ``path`` and validate the JSON body as ``model``.

        Raises:
            ResponseValidationError: The body is not JSON or does not match.
        """
        response = self.request("GET", path, params=params)
        return self._validate(response, model)

    def post_json(
        self,
        path: str,
        *,
        model: type[T],
        body: BaseModel | None = None,
        json: object | None = None,
        params: Mapping[str, QueryValue] | None = None,
    ) -> T:
        """POST to ``path`` and validate the JSON response as ``model``.

        Exactly one of ``body`` (a pydantic model, serialised by alias) or
        ``json`` (a raw mapping) must be given.

        Raises:
            ResponseValidationError: The response body is not JSON or does not
                match ``model``.
        """
        assert (body is None) != (json is None), "provide exactly one of body/json"
        payload: object = (
            body.model_dump(by_alias=True, exclude_none=True) if body is not None else json
        )
        response = self.request("POST", path, json=payload, params=params)
        return self._validate(response, model)

    def get_text(self, path: str, *, params: Mapping[str, QueryValue] | None = None) -> str:
        """GET ``path`` and return the response body as text."""
        return self.request("GET", path, params=params).text

    def download(self, path: str, dest: str | Path) -> int:
        """Stream a GET response to ``dest``, returning the bytes written.

        Retries transient failures, honouring ``Retry-After`` on 429. The
        destination directory is created if needed; a partial file is
        overwritten on retry.

        Raises:
            NotFound: The server returned 404.
            ClientError: The server returned another non-retryable 4xx.
            RateLimited: The server returned 429 after every attempt; the
                exception's ``retry_after`` carries the header when present.
            TransientError: The transfer failed after every attempt.
        """
        url = self._url(path)
        dest_path = Path(dest)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        self._add_request()
        last_exc: Exception | None = None
        for attempt in range(1, self._retry.max_attempts + 1):
            self._acquire()
            status: int | None = None
            try:
                with self._client.stream("GET", url) as response:
                    status = response.status_code
                    if (
                        status in self._retry.retry_status
                        and attempt < self._retry.max_attempts
                    ):
                        self._sleep(attempt, self._retry_after(response))
                        continue
                    self._raise_for_status(response)
                    written = 0
                    with dest_path.open("wb") as handle:
                        for chunk in response.iter_bytes():
                            handle.write(chunk)
                            written += len(chunk)
                    return written
            except TransientError as exc:
                last_exc = exc
                if attempt >= self._retry.max_attempts:
                    break
                self._sleep(attempt, None)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                status = None
                last_exc = exc
                if attempt >= self._retry.max_attempts:
                    break
                self._sleep(attempt, None)
            finally:
                self._record_attempt(status)
        assert last_exc is not None, "no attempt was made"
        if isinstance(last_exc, TransientError):
            raise last_exc
        raise TransientError(f"GET {url} failed: {last_exc}") from last_exc

    @property
    def stats(self) -> HttpStats:
        """Cumulative attempt statistics: attempts, throttling, backoff."""
        with self._stats_lock:
            return self._stats.snapshot()

    def reset_stats(self) -> None:
        """Reset :attr:`stats` to zero."""
        with self._stats_lock:
            self._stats = _StatsAccumulator()

    def close(self) -> None:
        """Close the underlying connection pool."""
        self._client.close()

    def __enter__(self) -> HttpClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # -- internals -------------------------------------------------------

    def _url(self, path: str) -> str:
        """Resolve ``path`` against the base URL (absolute URLs pass through)."""
        if path.startswith(("http://", "https://")):
            return path
        return self._base_url + path.lstrip("/")

    def _acquire(self) -> None:
        if self._bucket is not None:
            self._bucket.acquire()

    def _add_request(self) -> None:
        """Count one logical operation (called once per request/download)."""
        with self._stats_lock:
            self._stats.requests += 1

    def _add_backoff(self, seconds: float) -> None:
        """Record time slept between attempts."""
        assert seconds >= 0.0, "backoff must be >= 0"
        with self._stats_lock:
            self._stats.backoff_seconds += seconds

    def _record_attempt(self, status: int | None) -> None:
        """Count one HTTP attempt by outcome."""
        with self._stats_lock:
            self._stats.attempts += 1
            if status == 429:
                self._stats.throttled += 1
            elif status is None or status in self._retry.retry_status or 500 <= status < 600:
                self._stats.transient += 1
            elif 200 <= status < 400:
                self._stats.ok += 1

    def _sleep(self, attempt: int, retry_after: float | None) -> None:
        assert attempt >= 1, "attempt is 1-based"
        assert retry_after is None or retry_after >= 0, "retry_after must be >= 0"
        if retry_after is not None:
            delay = retry_after
        else:
            delay = min(self._retry.max_delay, self._retry.base_delay * (2 ** (attempt - 1)))
            if self._retry.jitter:
                delay += random.uniform(0.0, self._retry.jitter * delay)
        if delay > 0:
            time.sleep(delay)
            self._add_backoff(delay)

    @staticmethod
    def _retry_after(response: httpx.Response) -> float | None:
        value = response.headers.get("Retry-After")
        if value is None:
            return None
        try:
            return max(0.0, float(value))
        except ValueError:
            # HTTP-date form: fall back to exponential backoff.
            return None

    def _raise_for_status(self, response: httpx.Response) -> None:
        status = response.status_code
        where = f"{response.request.method} {response.request.url}"
        if status == 429:
            raise RateLimited(f"{where} rate limited", retry_after=self._retry_after(response))
        if status in self._retry.retry_status or 500 <= status < 600:
            raise TransientError(f"{where} returned {status}")
        if status == 404:
            raise NotFound(f"{where} not found")
        if status >= 400:
            raise ClientError(f"{where} returned {status}")

    def _emit(
        self, method: str, url: str, status: int | None, elapsed_ms: float, attempt: int
    ) -> None:
        self._record_attempt(status)
        if self._raw_sink is not None:
            self._raw_sink(
                HttpRecord(
                    method=method,
                    url=url,
                    status=status,
                    elapsed_ms=elapsed_ms,
                    attempt=attempt,
                )
            )

    @staticmethod
    def _validate(response: httpx.Response, model: type[T]) -> T:
        try:
            payload = response.json()
        except ValueError as exc:
            raise ResponseValidationError(f"{response.request.url} returned non-JSON") from exc
        try:
            return model.model_validate(payload)
        except ValidationError as exc:
            raise ResponseValidationError(
                f"{response.request.url} did not match {model.__name__}: {exc}"
            ) from exc
