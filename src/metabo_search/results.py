"""Typed result containers.

:class:`Results` is a ``list`` subclass: it behaves exactly like a list and
adds a few convenience helpers. Domain-specific formatting lives in the
clients, not here.
"""

from __future__ import annotations

from typing import Any, TypeVar, cast

T = TypeVar("T")


class Results(list[T]):
    """A list of typed records with convenience helpers.

    It is a ``list`` in every respect — indexing, slicing, iteration, ``len`` —
    so it composes with anything that expects a list.
    """

    def ids(self, attribute: str = "study_id") -> list[str]:
        """Return one attribute from every item, as strings.

        Args:
            attribute: Attribute name (for objects) or key (for mappings).

        Returns:
            The values in item order.

        Raises:
            AttributeError: An item has neither the attribute nor the key.
        """
        values: list[str] = []
        for item in self:
            if isinstance(item, dict):
                mapping = cast("dict[str, Any]", item)
                values.append(str(mapping[attribute]))
            else:
                values.append(str(getattr(item, attribute)))
        return values

    def records(self) -> list[dict[str, Any]]:
        """Return the items as plain dictionaries.

        Pydantic models are dumped with ``model_dump()``; mappings are copied.

        Raises:
            TypeError: An item is neither a pydantic model nor a mapping.
        """
        return [_as_dict(item) for item in self]

    def fmt(self, limit: int = 10) -> str:
        """Return a short human-readable summary.

        Args:
            limit: Maximum number of items to show.
        """
        lines = [f"{len(self)} result(s)"]
        for item in self[:limit]:
            lines.append(f"  {item!r}")
        if len(self) > limit:
            lines.append(f"  ... {len(self) - limit} more")
        return "\n".join(lines)


def _as_dict(item: Any) -> dict[str, Any]:
    """Convert a pydantic model or mapping to a plain dictionary."""
    dump = getattr(item, "model_dump", None)
    if callable(dump):
        dumped = dump()
        assert isinstance(dumped, dict), "model_dump() must return a dict"
        return cast("dict[str, Any]", dumped)
    if isinstance(item, dict):
        mapping = cast("dict[object, object]", item)
        return {str(key): value for key, value in mapping.items()}
    raise TypeError(f"cannot convert {type(item).__name__} to a row")
