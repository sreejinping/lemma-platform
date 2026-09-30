"""What a caller is told when a payload does not fit the operation's schema.

A GitHub write called with the request JSON at the top level instead of under
``body`` sends an empty body, and the provider answers with a sentence about its
own request model. The caller read "Connector rejected the operation request."
and could not tell which field was missing, which ones were in the wrong place,
or that either was the problem. Every one of those is decidable from the schema
the connector published, before anything is sent.

The other half is what this must *not* say. The message is handed to whoever
sent the payload and, through the agent tool, to a model that has to correct
itself -- so it names fields and never quotes them.
"""

from __future__ import annotations

import pytest

from app.modules.connectors.domain.errors import OperationExecutionValidationError
from app.modules.connectors.contracts.operation_payload_validation import (
    MAX_VIOLATIONS,
    describe_payload_mismatch,
    reject_payload_mismatch,
)

#: GitHub's `pulls_create` shape, abbreviated: the path parameters at the top
#: level, the request JSON under `body`, and nothing else allowed.
PULLS_CREATE = {
    "type": "object",
    "title": "pulls_create",
    "properties": {
        "owner": {"type": "string"},
        "repo": {"type": "string"},
        "body": {
            "type": "object",
            "properties": {"title": {}, "head": {}, "base": {}},
        },
    },
    "required": ["owner", "repo", "body"],
    "additionalProperties": False,
}

#: The payload that produced the report, with values nothing else in the
#: message could coincidentally contain.
TOP_LEVEL_BODY = {
    "owner": "octocat-owner",
    "repo": "hello-world-repo",
    "title": "Add a pull request title",
    "head": "feature-branch-name",
    "base": "main-branch-name",
}


def test_a_body_sent_at_the_top_level_names_body_and_every_misplaced_field():
    mismatch = describe_payload_mismatch(
        PULLS_CREATE, TOP_LEVEL_BODY, operation_name="pulls_create"
    )

    assert mismatch is not None
    assert "'body'" in mismatch.message
    for field in ("title", "head", "base"):
        assert f"'{field}'" in mismatch.message
    assert "pulls_create" in mismatch.message


def test_the_diagnosis_names_fields_and_no_value_from_the_payload():
    mismatch = describe_payload_mismatch(PULLS_CREATE, TOP_LEVEL_BODY)

    assert mismatch is not None
    for value in TOP_LEVEL_BODY.values():
        assert value not in mismatch.message
    for violation in mismatch.violations:
        for value in TOP_LEVEL_BODY.values():
            assert value not in violation.message


def test_a_payload_that_fits_is_not_described():
    payload = {"owner": "octocat-owner", "repo": "hello-world-repo", "body": {}}

    assert describe_payload_mismatch(PULLS_CREATE, payload) is None


def test_a_misspelling_inside_the_body_is_left_to_the_provider():
    """Depth is deliberate. The provider judges its own request model better than
    we can, and a guess here would refuse payloads it accepts."""
    payload = {
        "owner": "octocat-owner",
        "repo": "hello-world-repo",
        "body": {"titel": "Add a pull request title"},
    }

    assert describe_payload_mismatch(PULLS_CREATE, payload) is None


def test_an_operation_with_no_schema_is_not_checked():
    """A schema that has not said which keys it takes cannot be violated by one
    of them, and refusing on its silence would invent a contract."""
    assert describe_payload_mismatch(None, {"anything": 1}) is None
    assert describe_payload_mismatch({}, {"anything": 1}) is None
    assert describe_payload_mismatch({"type": "object"}, {"anything": 1}) is None
    assert (
        describe_payload_mismatch({"type": "object", "properties": {}}, {"anything": 1})
        is None
    )


def test_a_closed_schema_with_no_properties_refuses_every_field():
    """The other side of the same rule: `additionalProperties: false` with no
    properties is a schema that has said something, and it said no keys."""
    mismatch = describe_payload_mismatch(
        {"type": "object", "properties": {}, "additionalProperties": False},
        {"anything": 1},
    )

    assert mismatch is not None
    assert "'anything'" in mismatch.message


def test_an_open_schema_allows_a_field_it_does_not_declare():
    schema = {
        "type": "object",
        "properties": {"a": {"type": "string"}},
        "additionalProperties": True,
    }

    assert describe_payload_mismatch(schema, {"a": "x", "b": 1}) is None


def test_output_path_is_allowed_where_the_schema_does_not_declare_it():
    """Lemma's own argument, not the provider's: `split_output_path` takes it off
    the payload before the call, so a schema without it is no reason to refuse."""
    payload = {
        "owner": "octocat-owner",
        "repo": "hello-world-repo",
        "body": {},
        "output_path": "/me/report.pdf",
    }

    assert describe_payload_mismatch(PULLS_CREATE, payload) is None


def test_a_wrong_type_is_named_without_the_value_that_was_wrong():
    schema = {
        "type": "object",
        "properties": {"amount": {"type": "number"}},
        "required": ["amount"],
    }

    mismatch = describe_payload_mismatch(schema, {"amount": "twenty-one"})

    assert mismatch is not None
    assert "'amount'" in mismatch.message
    assert "a number" in mismatch.message
    assert "twenty-one" not in mismatch.message


def test_at_most_ten_violations_are_reported():
    schema = {
        "type": "object",
        "properties": {"kept": {}},
        "required": [f"field_{index}" for index in range(40)],
    }

    mismatch = describe_payload_mismatch(schema, {})

    assert mismatch is not None
    assert len(mismatch.violations) == MAX_VIOLATIONS
    assert "30 further fields are wrong as well" in mismatch.message


def test_the_refusal_is_a_422_naming_the_operation_and_the_reason():
    with pytest.raises(OperationExecutionValidationError) as caught:
        reject_payload_mismatch("pulls_create", PULLS_CREATE, TOP_LEVEL_BODY)

    assert caught.value.status_code == 422
    assert caught.value.code == "OPERATION_EXECUTION_VALIDATION_ERROR"
    assert "'body'" in caught.value.message
    assert caught.value.details == {
        "reason": "payload_schema_mismatch",
        "operation_name": "pulls_create",
    }


def test_a_payload_that_fits_raises_nothing():
    reject_payload_mismatch(
        "pulls_create",
        PULLS_CREATE,
        {"owner": "octocat-owner", "repo": "hello-world-repo", "body": {}},
    )
