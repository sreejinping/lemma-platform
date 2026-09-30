"""Every `{ENV_VAR}` a catalog URL names is a real, declared setting.

The substitution in `env_system_oauth_config` resolves placeholders by *name*,
straight from the environment -- it has to, because the names are catalog data
rather than fields. That makes the catalog and `ConnectorSettings` two places
naming the same variable with nothing holding them together: rename the field,
or misspell the placeholder, and the connector reports itself unconfigured on
a deployment that set the variable correctly. Nothing else would say why.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.modules.connectors.config import ConnectorSettings
from app.modules.connectors.infrastructure.adapters.env_system_oauth_config import (
    _ENV_PLACEHOLDER,
    _FILLABLE_FIELDS,
)

pytestmark = pytest.mark.unit

CATALOG = Path(__file__).resolve().parents[5] / "scripts" / "lemma_apps_config.json"


def _apps() -> list[dict]:
    return json.loads(CATALOG.read_text())


def _placeholders() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for app in _apps():
        oauth2 = app.get("oauth2_config") or {}
        for field in _FILLABLE_FIELDS:
            value = oauth2.get(field)
            if isinstance(value, str):
                for name in _ENV_PLACEHOLDER.findall(value):
                    found.setdefault(app.get("name", "?"), []).append(name)
    return found


def test_every_placeholder_names_a_declared_setting():
    fields = set(ConnectorSettings.model_fields)
    for connector_id, names in _placeholders().items():
        for name in names:
            assert name.lower() in fields, (
                f"{connector_id} names {name}, which ConnectorSettings does not "
                "declare -- nothing would fill it and the connector would go "
                "quietly unconfigured."
            )


def test_github_connects_through_the_authorize_endpoint_not_the_install_page():
    """Pinned because sending people to the install page does not work twice.

    `/apps/{slug}/installations/new` redirects back with `code` and
    `installation_id` on a *first* install and only then. Someone who already
    has the App -- every reconnect, and every second person in an organization
    where somebody installed it already -- is shown the configure page and never
    redirected anywhere at all. Verified live: the connect request stayed
    PENDING and no account was ever created.

    The authorize endpoint always round-trips a code. The installation is
    resolved from the token afterwards; see `github_installation`.
    """
    github = next(a for a in _apps() if a["name"] == "github")
    assert github["oauth2_config"]["authorization_url"] == (
        "https://github.com/login/oauth/authorize"
    )
    # And nothing needs substituting into it -- the slug identifies the App for
    # the *install* link, which is a different journey.
    assert _placeholders().get("github") is None


def test_no_placeholder_hides_in_a_field_that_is_never_filled():
    """A placeholder outside `_FILLABLE_FIELDS` is dead text that ships as-is."""
    for app in _apps():
        oauth2 = app.get("oauth2_config") or {}
        for field, value in oauth2.items():
            if field in _FILLABLE_FIELDS:
                continue
            assert not re.search(r"\{[A-Z][A-Z0-9_]*\}", json.dumps(value)), (
                f"{app.get('name')}.{field} carries an env placeholder that "
                "nothing substitutes."
            )


def _github_triggers() -> dict[str, dict]:
    github = next(a for a in _apps() if a["name"] == "github")
    return {t["event_type"]: t for t in github["triggers"]}


def _action_defaults() -> dict[str, list[str] | None]:
    return {
        event: (trigger["config_schema"]["properties"].get("actions") or {}).get(
            "default"
        )
        for event, trigger in _github_triggers().items()
    }


def test_the_noisy_events_default_to_one_action():
    """Three GitHub events fire per release, and one per workflow-run state.

    Measured live: publishing a single release delivered `created`, `published`
    and `released`, which without a default woke the agent three times for one
    thing a person did once. A busy repository does the same for `workflow_run`,
    once per run per state change.
    """
    defaults = _action_defaults()
    assert defaults["release"] == ["published"]
    assert defaults["workflow_run"] == ["completed"]
    assert defaults["check_suite"] == ["completed"]
    # The rest are single user actions, where every one is worth firing on.
    for event in ("push", "pull_request", "issues", "issue_comment"):
        assert defaults[event] is None, event


def test_a_review_defaults_to_the_delivery_that_says_something_new():
    """A review is filtered the same way a release is, and for the same reason.

    `submitted` is a reviewer's findings -- a person's or a bot's -- landing on
    the pull request. `edited` is a correction to a review the agent may already
    have read, and `dismissed` is a maintainer withdrawing one; firing on either
    wakes an agent for something it has already acted on. An inline review
    comment is the same shape one level down, where `created` is the new finding
    and a delete withdraws it.
    """
    defaults = _action_defaults()
    assert defaults["pull_request_review"] == ["submitted"]
    assert defaults["pull_request_review_comment"] == ["created"]


def test_the_review_triggers_offer_the_actions_github_sends():
    """The enum is what the connect form and the API accept. An action GitHub
    never sends is a checkbox that silently does nothing."""
    for event, expected in (
        ("pull_request_review", ["submitted", "edited", "dismissed"]),
        ("pull_request_review_comment", ["created", "edited", "deleted"]),
    ):
        actions = _github_triggers()[event]["config_schema"]["properties"]["actions"]
        assert actions["items"]["enum"] == expected, event
