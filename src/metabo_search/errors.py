"""Exception hierarchy for metabo-search.

Every error raised by the library derives from :class:`MetaboSearchError`, so
callers can catch the whole surface with one ``except``.
"""

from __future__ import annotations

from typing import Any


class MetaboSearchError(Exception):
    """Base class for all metabo-search errors."""


class TransportError(MetaboSearchError):
    """Base class for network-level failures (timeout, connection reset)."""


class TransientError(TransportError):
    """A network or 5xx failure that was retried and still failed."""


class RateLimited(TransientError):
    """HTTP 429 after exhausting retries.

    Attributes:
        retry_after: Seconds the server asked the client to wait, when the
            ``Retry-After`` header was present.
    """

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class ClientError(MetaboSearchError):
    """A non-retryable 4xx response other than 404."""


class NotFound(ClientError):
    """HTTP 404: the requested resource does not exist."""


class APIError(MetaboSearchError):
    """The upstream API returned a well-formed error payload.

    Attributes:
        error_message: The API's ``errorMessage`` field, if any.
        errors: Additional error details returned by the API.
    """

    def __init__(
        self,
        message: str,
        *,
        error_message: str | None = None,
        errors: list[Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.error_message = error_message
        self.errors = errors or []


class ResponseValidationError(MetaboSearchError):
    """A 2xx response body did not match the expected typed model."""


class ParseError(MetaboSearchError):
    """A data file (ISA-Tab or MAF) could not be parsed.

    Raised by the parsers so that a malformed file is reported as a normal
    library error and can be collected per study by ``inspect()`` instead of
    sinking a whole batch.
    """
