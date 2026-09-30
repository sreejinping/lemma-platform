"""The payload gate at the resolve/execute seam.

An operation's input schema is checked before anything is sent, so a caller that
put the request JSON at the top level is told which field is missing rather than
being handed whatever the provider made of an empty body. The other half of the
same decision is here too: a provider that refuses a request the schema allowed
speaks for itself, in its own words.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.connectors.domain.connector import (
    ConnectorEntity,
    ConnectorKind,
)
from app.modules.connectors.domain.connector_operation import (
    ConnectorOperationEntity,
)
from app.modules.connectors.domain.errors import OperationExecutionValidationError
from app.modules.connectors.services.connector_operation_service import (
    ConnectorOperationService,
)

pytestmark = pytest.mark.asyncio


def _install_double():
    """A `connector_service` double whose install is Composio-kind, which is the
    kind that routes the call to the injected `operation_gateway` -- where
    "nothing reached the provider" can be asserted."""
    return SimpleNamespace(
        auth_config_repository=AsyncMock(
            get=AsyncMock(
                return_value=SimpleNamespace(
                    id=uuid4(), kind=ConnectorKind.COMPOSIO, config=None
                )
            )
        )
    )


def _http_service(operation, gateway):
    """The service with one catalog operation and a gateway standing in for the
    provider. `_composio_install_service` routes the call to that gateway, which
    is where "nothing reached the provider" can be asserted."""
    operation_repository = AsyncMock()
    operation_repository.get_by_connector_and_name.return_value = operation
    operation_repository.get_by_connector_kind_and_name.return_value = operation
    account = SimpleNamespace(
        id=uuid4(), auth_config_id=uuid4(), credentials={"access_token": "token"}
    )
    return ConnectorOperationService(
        connector_repository=AsyncMock(
            get=AsyncMock(
                return_value=ConnectorEntity(
                    id="github",
                    auth_kind=ConnectorKind.COMPOSIO,
                )
            )
        ),
        operation_repository=operation_repository,
        operation_gateway=gateway,
        account_resolution_service=AsyncMock(
            resolve_account=AsyncMock(return_value=account)
        ),
        connector_service=_install_double(),
    )


def _pulls_create() -> ConnectorOperationEntity:
    """GitHub's `pulls_create`: path parameters at the top level, the request
    JSON under `body`, and nothing else allowed."""
    return ConnectorOperationEntity(
        id="github:pulls_create",
        connector_id="github",
        name="pulls_create",
        provider_operation_name="pulls_create",
        input_schema={
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
        },
    )


async def test_a_top_level_body_is_refused_without_reaching_the_provider():
    """The write that produced the report. GitHub answered 422 about its own
    request model; now the refusal names the field that is missing, the fields
    that are one level too high, and where they belong -- and the provider is
    never called."""
    gateway = AsyncMock(execute_operation=AsyncMock())
    service = _http_service(_pulls_create(), gateway)

    with pytest.raises(OperationExecutionValidationError) as caught:
        await service.execute_operation(
            connector_id="github",
            operation_name="pulls_create",
            payload={
                "owner": "octocat-owner",
                "repo": "hello-world-repo",
                "title": "Add a pull request title",
                "head": "feature-branch-name",
                "base": "main-branch-name",
            },
            user_id=uuid4(),
        )

    assert caught.value.status_code == 422
    assert caught.value.code == "OPERATION_EXECUTION_VALIDATION_ERROR"
    assert "'body'" in caught.value.message
    for field in ("title", "head", "base"):
        assert f"'{field}'" in caught.value.message
    for value in ("octocat-owner", "feature-branch-name", "main-branch-name"):
        assert value not in caught.value.message
    gateway.execute_operation.assert_not_awaited()


async def test_a_correct_payload_still_reaches_the_provider():
    gateway = AsyncMock(execute_operation=AsyncMock(return_value={"number": 1}))
    service = _http_service(_pulls_create(), gateway)

    response = await service.execute_operation(
        connector_id="github",
        operation_name="pulls_create",
        payload={
            "owner": "octocat-owner",
            "repo": "hello-world-repo",
            "body": {"title": "Add a pull request title"},
        },
        user_id=uuid4(),
    )

    assert response.result == {"number": 1}
    gateway.execute_operation.assert_awaited_once()


async def test_a_provider_that_refuses_the_call_speaks_for_itself():
    """A provider 422 that is not the schema's fault -- a body it dislikes, a
    state it will not allow -- reaches the caller as the provider wrote it,
    with its status. The sentence is the whole reason the call failed."""
    sentence = (
        '{"message":"Validation Failed","errors":[{"resource":"PullRequest",'
        '"field":"head","code":"invalid"}]}'
    )

    class _ProviderRefusal(Exception):
        def __init__(self):
            super().__init__(sentence)
            self.status_code = 422

    gateway = AsyncMock(execute_operation=AsyncMock(side_effect=_ProviderRefusal()))
    service = _http_service(_pulls_create(), gateway)

    with pytest.raises(OperationExecutionValidationError) as caught:
        await service.execute_operation(
            connector_id="github",
            operation_name="pulls_create",
            payload={
                "owner": "octocat-owner",
                "repo": "hello-world-repo",
                "body": {"title": "Add a pull request title"},
            },
            user_id=uuid4(),
        )

    assert caught.value.status_code == 422
    assert caught.value.message == sentence
    assert caught.value.details["upstream_status"] == 422
    assert caught.value.details["upstream_message"] == sentence
    gateway.execute_operation.assert_awaited_once()
