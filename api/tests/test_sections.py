"""The API by page (#623, #624): every route in exactly one section, the spec's tags
in the menu's order, the endpoint list sorted the same way, and one folder per
section with its controller.py and dtos.py."""

import ast
import importlib
from pathlib import Path

from fastapi import APIRouter

from app.main import app
from app.services import apidocs_service as apidocs
from app.services import navigation_service as navigation
from app.v1 import sections


def _operations():
    for path, methods in app.openapi()["paths"].items():
        for method, operation in methods.items():
            yield path, method, operation


def test_the_pages_keep_the_menus_names():
    menu = {key: label for key, _path, label, _title in navigation.PAGES}
    assert sections.BY_KEY["map"].name == menu["map"]
    assert sections.BY_KEY["alert_board"].name == menu["alerts"]
    assert sections.BY_KEY["documentation"].name == menu["help"]
    assert len(set(sections.NAMES)) == len(sections.NAMES)
    # The pages first, then the sections no page owns.
    pages = [section.page for section in sections.SECTIONS]
    assert pages == sorted(pages, reverse=True)


def test_the_spec_lists_every_section_in_order_with_a_description():
    tags = app.openapi()["tags"]
    assert [tag["name"] for tag in tags] == list(sections.NAMES)
    assert all(tag["description"] for tag in tags)


def test_every_operation_has_exactly_one_section():
    for path, method, operation in _operations():
        assert len(operation.get("tags", [])) == 1, (path, method, operation.get("tags"))
        assert operation["tags"][0] in sections.NAMES, (path, method)


def test_every_listed_endpoint_has_exactly_one_section():
    # Pages and the routes outside the spec too (/, /alerts, /help, /api/docs).
    for route in apidocs.routes(None):
        assert len(route["tags"]) == 1, (route["path"], route["tags"])
        assert route["tags"][0] in sections.NAMES, route["path"]


def test_every_page_section_has_an_endpoint():
    used = {route["tags"][0] for route in apidocs.routes(None)}
    assert {section.name for section in sections.SECTIONS} <= used


def test_the_endpoint_list_comes_in_section_order(flask_client):
    listed = flask_client.get("/api/v1/endpoints").get_json()["endpoints"]
    order = [sections.NAMES.index(item["section"]) for item in listed]
    assert order == sorted(order)
    where = {(item["path"], item["methods"][0]): item["section"] for item in listed}
    assert where[("/", "GET")] == "Map"
    assert where[("/alerts", "GET")] == "Alert Board"
    assert where[("/api/v1/alert-cases", "POST")] == "Alert Board"
    assert where[("/api/v1/page-config", "GET")] == "Shared"
    assert where[("/api/v1/app-status", "GET")] == "Shared"
    assert where[("/api/v1/me", "GET")] == "Auth"
    assert where[("/api/v1/tokens", "GET")] == "Administration"
    assert where[("/api/v1/contract", "GET")] == "Administration"
    assert where[("/api/v1/endpoints", "GET")] == "Documentation"
    assert where[("/help/<slug>", "GET")] == "Documentation"
    assert where[("/api/docs", "GET")] == "Documentation"
    assert where[("/healthz", "GET")] == "Monitoring"
    assert where[("/mcp", "POST")] == "Monitoring"
    assert where[("/api/v1/librenms/alert", "POST")] == "Incoming alerts"
    assert where[("/api/v1/items", "GET")] == "Examples"
    # The older grouping stays for callers.
    assert {item["group"] for item in listed} == {"Pages", "API", "Monitoring"}


# --- One folder per section (#624) -------------------------------------------

V1 = Path(__file__).resolve().parent.parent / "app" / "v1"
APP = V1.parent
# Where a router may be defined outside a section: what no section owns (the old
# redirects, /static/), the /api/v1 router that groups the new stack's own, web's files.
ROUTERS_ELSEWHERE = {
    V1 / "legacy" / "controller.py",
    V1 / "__init__.py",
    APP / "frontend" / "controller.py",
}


def test_every_section_is_a_folder_with_a_controller_and_its_dtos():
    for section in sections.SECTIONS:
        folder = V1 / section.key
        assert (folder / "controller.py").is_file(), f"{section.key}/controller.py"
        assert (folder / "dtos.py").is_file(), f"{section.key}/dtos.py"


def test_no_router_outside_a_section_folder():
    folders = {V1 / section.key for section in sections.SECTIONS}
    for path in APP.rglob("*.py"):
        if "APIRouter(" not in path.read_text() or path in ROUTERS_ELSEWHERE:
            continue
        assert path.parent in folders and path.name == "controller.py", path.relative_to(APP)


def test_each_controllers_routes_carry_its_own_section():
    for section in sections.SECTIONS:
        controller = importlib.import_module(f"app.v1.{section.key}.controller")
        routers = [value for value in vars(controller).values() if isinstance(value, APIRouter)]
        assert routers, section.key
        for router in routers:
            for route in router.routes:
                assert route.tags == [section.name], (section.key, route.path, route.tags)


def test_a_sections_dtos_import_no_other_section():
    others = {section.key for section in sections.SECTIONS}
    for section in sections.SECTIONS:
        tree = ast.parse((V1 / section.key / "dtos.py").read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("app.v1."):
                package = node.module.split(".")[2]
                assert package not in others - {section.key}, (section.key, node.module)
