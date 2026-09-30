"""Diagnosing a payload that does not fit the operation's own input schema.

An operation's input schema carries the path, query and header parameters at
the top level and the request JSON under ``body``. A caller that puts the body's
fields at the top level instead sends an empty body, and the provider answers
with a sentence about its own request model -- which names nothing the caller
can act on, and nothing that says where the JSON was supposed to go. This
compares the payload with the schema the connector published *before* anything
is sent, so the refusal names the field that is missing and where the misplaced
ones belong.

Top level only, deliberately. A misspelled property *inside* ``body`` is the
provider's to judge: it knows its own request model, and a guess here would
refuse payloads the provider accepts.

Names, never values. The message is handed to whoever sent the payload and to a
model that has to correct itself, so it may say which fields are wrong and must
not quote what was in them.

It lives here, in the module's published surface, because the agent toolset is
the other caller: one rule with two doors onto it is the only way the two cannot
drift into saying different things about one payload.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import jsonschema

from app.modules.connectors.domain.errors import OperationExecutionValidationError

__all__ = [
    "MAX_VIOLATIONS",
    "REQUEST_BODY_FIELD",
    "PayloadMismatch",
    "PayloadViolation",
    "describe_payload_mismatch",
    "reject_payload_mismatch",
]

#: How many wrong fields one message names. A payload can be arbitrarily wrong,
#: and this sentence is read in an API error body and in a model's context.
MAX_VIOLATIONS = 10

#: The property the OpenAPI importer gives a request body, and the key the
#: executor reads it from (`openapi_http_bodies.build_body`). Named here because
#: the diagnosis says "belongs under 'body'", and that sentence is only true
#: when this is the name the schema uses.
REQUEST_BODY_FIELD = "body"

#: Lemma's own argument rather than the provider's: where a binary result lands
#: in the pod. `split_output_path` takes it off the payload before the call, so
#: a schema that does not declare it is no reason to refuse one that carries it.
_LEMMA_ARGUMENTS = frozenset({"output_path"})

#: The two keywords this module decides itself. jsonschema's own text for both
#: quotes the payload -- "Additional properties are not allowed ('title',
#: 'head' were unexpected)" and "'body' is a required property" are close, but
#: the first is a sentence and not a list, and neither knows that the request
#: JSON belongs under `body`. Dropped here in favour of the classification below.
_OWN_CLASSIFICATION = frozenset({"required", "additionalProperties"})

_TYPE_LABELS = {
    "array": "a JSON array",
    "boolean": "a boolean",
    "integer": "an integer",
    "null": "null",
    "number": "a number",
    "object": "a JSON object",
    "string": "a string",
}


@dataclass(frozen=True, slots=True)
class PayloadViolation:
    """One way the payload does not fit the schema, naming a field and no value."""

    kind: str
    field: str
    message: str


@dataclass(frozen=True, slots=True)
class PayloadMismatch:
    """What is wrong, as a sentence for the caller and a list for a model."""

    message: str
    violations: tuple[PayloadViolation, ...]


def describe_payload_mismatch(
    input_schema: Mapping[str, object] | None,
    payload: Mapping[str, object] | None,
    *,
    operation_name: str | None = None,
) -> PayloadMismatch | None:
    """What is wrong with ``payload``, or ``None`` when there is nothing to say.

    Nothing to say covers both a payload that fits and an operation whose schema
    constrains nothing about which keys it takes. The second is not a detail: a
    schema with no ``properties`` is a schema that has not said what it accepts,
    and refusing on the strength of it would invent a contract. A schema that
    names no properties and requires none is skipped; one that closes itself
    with ``additionalProperties: false`` is not, because it has said something.
    """
    if not isinstance(input_schema, Mapping) or not input_schema:
        return None
    properties = input_schema.get("properties")
    declared = (
        {str(name) for name in properties} if isinstance(properties, Mapping) else set()
    )
    required = _names(input_schema.get("required"))
    closed = input_schema.get("additionalProperties") is False
    if not declared and not required and not closed:
        return None

    given = {str(key) for key in payload} if isinstance(payload, Mapping) else set()
    violations: list[PayloadViolation] = [
        _missing(name) for name in required if name not in given
    ]
    if closed:
        extras = sorted(given - declared - _LEMMA_ARGUMENTS)
        body_fields = _body_field_names(
            properties if isinstance(properties, Mapping) else {}
        )
        violations.extend(_extra_violations(extras, body_fields))
    violations.extend(_type_violations(input_schema, payload))

    if not violations:
        return None
    shown = tuple(violations[:MAX_VIOLATIONS])
    return PayloadMismatch(
        message=_render(
            operation_name,
            shown,
            omitted=len(violations) - len(shown),
            body_field=REQUEST_BODY_FIELD if REQUEST_BODY_FIELD in declared else None,
        ),
        violations=shown,
    )


def reject_payload_mismatch(
    operation_name: str | None,
    input_schema: Mapping[str, object] | None,
    payload: Mapping[str, object] | None,
) -> None:
    """Refuse a payload that cannot be what this operation asked for.

    Raised before anything leaves for the provider, so the caller is told what
    is wrong with what it sent rather than what a provider made of an empty
    body. ``reason`` and ``operation_name`` are the keys the error's own detail
    allowlist lets through; the fields themselves are in the message.
    """
    mismatch = describe_payload_mismatch(
        input_schema, payload, operation_name=operation_name
    )
    if mismatch is None:
        return
    details: dict[str, object] = {"reason": "payload_schema_mismatch"}
    if operation_name:
        details["operation_name"] = operation_name
    raise OperationExecutionValidationError(mismatch.message, details=details)


def _names(value: object) -> list[str]:
    """A schema's ``required`` list, as strings, ignoring anything else."""
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [str(name) for name in value]


def _body_field_names(properties: Mapping[str, object]) -> frozenset[str]:
    """The fields the request-body property declares, when there is one.

    This is what makes the message actionable rather than merely correct: the
    caller's fields are not unknown, they are one level too high.
    """
    body = properties.get(REQUEST_BODY_FIELD)
    if not isinstance(body, Mapping):
        return frozenset()
    inner = body.get("properties")
    if not isinstance(inner, Mapping):
        return frozenset()
    return frozenset(str(name) for name in inner)


def _missing(name: str) -> PayloadViolation:
    return PayloadViolation(
        kind="missing",
        field=name,
        message=f"required field '{name}' is missing",
    )


def _extra_violations(
    extras: Sequence[str], body_fields: frozenset[str]
) -> list[PayloadViolation]:
    """Fields the schema does not allow, split by where they belong."""
    misplaced = [name for name in extras if name in body_fields]
    unexpected = [name for name in extras if name not in body_fields]
    violations: list[PayloadViolation] = []
    if misplaced:
        violations.append(
            PayloadViolation(
                kind="misplaced",
                field=", ".join(misplaced[:MAX_VIOLATIONS]),
                message=(
                    f"{_quoted(misplaced)} belong under '{REQUEST_BODY_FIELD}' "
                    "rather than at the top level"
                ),
            )
        )
    if unexpected:
        violations.append(
            PayloadViolation(
                kind="unexpected",
                field=", ".join(unexpected[:MAX_VIOLATIONS]),
                message=f"unexpected {_quoted(unexpected)}",
            )
        )
    return violations


def _type_violations(
    input_schema: Mapping[str, object],
    payload: Mapping[str, object] | None,
) -> list[PayloadViolation]:
    """Top-level type mismatches, rendered without quoting the payload.

    jsonschema is the same engine the agent tool validated with, so the two
    paths cannot disagree about what fits. Its messages are not reused: they
    quote the value that was wrong, and a value can be anything the caller sent.
    """
    validator = jsonschema.Draft202012Validator(dict(input_schema))
    violations: list[PayloadViolation] = []
    for error in sorted(
        validator.iter_errors(payload if payload is not None else {}),
        key=lambda item: list(item.absolute_path),
    ):
        if len(error.absolute_path) > 1 or error.validator in _OWN_CLASSIFICATION:
            continue
        field = ".".join(str(part) for part in error.absolute_path) or "(root)"
        violations.append(
            PayloadViolation(
                kind="type",
                field=field,
                message=_type_message(field, error),
            )
        )
    return violations


def _type_message(field: str, error: jsonschema.ValidationError) -> str:
    subject = f"field '{field}'" if error.absolute_path else "the payload"
    if error.validator == "type" and isinstance(error.validator_value, str):
        return f"{subject} must be {_TYPE_LABELS.get(error.validator_value, error.validator_value)}"
    return f"{subject} does not match the schema"


def _quoted(names: Sequence[str]) -> str:
    return ", ".join(f"'{name}'" for name in names[:MAX_VIOLATIONS])


def _render(
    operation_name: str | None,
    violations: Sequence[PayloadViolation],
    *,
    omitted: int,
    body_field: str | None,
) -> str:
    subject = f"'{operation_name}'" if operation_name else "this operation"
    sentences = [
        f"The payload does not match the input schema of {subject}: "
        + "; ".join(violation.message for violation in violations)
        + "."
    ]
    if omitted:
        sentences.append(f"{omitted} further fields are wrong as well.")
    if body_field and any(
        violation.kind in {"missing", "misplaced"} for violation in violations
    ):
        sentences.append(
            f"The request JSON is sent as the '{body_field}' field, alongside the "
            "operation's path, query and header parameters."
        )
    return " ".join(sentences)
