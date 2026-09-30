"""An operation's result, in a shape JSON can carry.

An executor hands back whatever the provider or the connector's own client
produced: pydantic models, tuples, raw bytes. This is the one pass that turns
that into something a response can serialize.

Split out of ``connector_operation_service``, which is over the file-size
ratchet and could not take the line this module's caller needed. It is pure, it
holds no state, and it was only ever a method because everything else in that
class was one.
"""

from __future__ import annotations

import base64

__all__ = ["normalize_execution_result"]


def normalize_execution_result(value: object) -> object:
    """A JSON-safe copy of ``value``.

    Typed ``object`` rather than ``Any``: this is a provider's payload, whose
    shape is not known until the provider is read, and ``object`` says that
    without also switching off the checker for every call site.
    """
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return normalize_execution_result(
            model_dump(by_alias=True, exclude_none=True, mode="json")
        )
    if isinstance(value, dict):
        return {key: normalize_execution_result(item) for key, item in value.items()}
    if isinstance(value, list):
        return [normalize_execution_result(item) for item in value]
    if isinstance(value, tuple):
        return [normalize_execution_result(item) for item in value]
    if isinstance(value, (bytes, bytearray)):
        return {
            "type": "binary_content",
            "content_base64": base64.b64encode(bytes(value)).decode("ascii"),
            "media_type": "application/octet-stream",
            "size_bytes": len(value),
        }
    return value
