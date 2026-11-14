"""Scopes (#711, the example's app/auth/decorators.py): wildcards, path parameters,
and the roles' scopes as the routes check them."""

import pytest

from app.auth import permissions, scopes
from app.auth.permissions import Permission
from app.core import exceptions
from app.core.settings import get_settings
from app.services import auth_service
from app.services.auth_service import CurrentUser


@pytest.mark.parametrize(
    ("scope", "granted", "covered"),
    [
        ("alerts:ack", ["alerts:ack"], True),
        ("Alerts:ACK", ["alerts:ack"], True),
        ("alerts:ack", ["alerts:*"], True),
        ("alerts:ack", ["*:*"], True),
        ("alerts:ack", ["*"], False),
        ("alerts:ack", ["alerts:unack", "page:*"], False),
        ("status:links:view", ["*:*"], False),
        ("status:links:view", ["status:*:view"], True),
        ("admin", ["*:*", "*:*:*"], False),
        ("admin", ["*"], True),
        ("alerts:ack", [], False),
    ],
)
def test_is_scope_covered(scope, granted, covered):
    assert scopes.is_scope_covered(scope, granted) is covered


def test_fill_from_the_path():
    assert scopes.fill("tenant:{tenant}:view", {"tenant": "Acme"}) == "tenant:Acme:view"
    # A ":" in the value can't make another part (tenant:a:b:view would need *:*:*:*).
    assert scopes.fill("tenant:{tenant}:view", {"tenant": "a:b"}) == "tenant:ab:view"
    # A name the path doesn't have stays: only a wildcard or itself covers it.
    assert scopes.fill("tenant:{tenant}:view", {}) == "tenant:{tenant}:view"
    assert scopes.fill("alerts:ack", {"x": "y"}) == "alerts:ack"
    assert scopes.fill("odd:{", {}) == "odd:{"


def test_admin_holds_every_permission_also_new_ones():
    assert permissions.role_permissions("admin") == permissions.ALL
    assert permissions.ROLES["admin"].scopes == {"*", "*:*", "*:*:*"}
    # A permission added later (two or three parts) reaches admin with no change.
    assert scopes.is_scope_covered("reports:export", permissions.role_scopes("admin"))
    assert permissions.role_permissions("operator") == permissions.ALL - {Permission.ADMIN}


def test_a_route_scope_with_a_path_parameter(configure):
    """``authorize("tenant:{tenant}:view")``: a role's wildcard covers every tenant;
    the refusal names the scope as filled in."""
    configure(AUTH_MODE="header")
    settings = get_settings()
    original = permissions.ROLES["customer"]
    permissions.ROLES["customer"] = permissions.RoleDef(
        original.label, original.landing, original.scopes | {"tenant:acme:view"}
    )
    try:
        user = CurrentUser(is_authenticated=True, username="kam", role="customer")
        ask = ("tenant:{tenant}:view",)
        assert (
            auth_service.permission_problem(user, settings, ask, "GET", params={"tenant": "ACME"})
            is None
        )
        refused, status = auth_service.permission_problem(
            user, settings, ask, "GET", params={"tenant": "Globex"}
        )
        assert status == 403 and refused["required_permission"] == "tenant:Globex:view"
        admin = CurrentUser(is_authenticated=True, username="root", role="admin")
        assert (
            auth_service.permission_problem(admin, settings, ask, "GET", params={"tenant": "x"})
            is None
        )
    finally:
        permissions.ROLES["customer"] = original


def test_the_examples_exceptions():
    assert exceptions.UnauthenticatedException().status == 401
    assert exceptions.UnauthorizedException("Scope required: x").body == {
        "error": "Scope required: x"
    }
    assert exceptions.NotFoundException().status == 404
    assert exceptions.ValidationErrorException("bad").status == 400
