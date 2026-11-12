"""The pages' security headers are the same from the API and from the web image's server
(#672): both are checked against web/tests/csp-cases.json (web/tests/security.test.ts
checks the web side)."""

import json
import pathlib

import pytest

from app.core.settings import Settings
from app.frontend import security

CASES = json.loads(
    (pathlib.Path(__file__).resolve().parents[2] / "web" / "tests" / "csp-cases.json").read_text()
)


@pytest.mark.parametrize("case", CASES["cases"], ids=lambda case: case["map_tile_url"])
def test_the_csp_per_tile_server(case):
    settings = Settings(map_tile_url=case["map_tile_url"])
    assert security.content_security_policy(settings) == case["csp"]


def test_the_other_headers():
    headers = dict(security.security_headers(Settings()))
    headers.pop("content-security-policy")
    assert headers == CASES["headers"]
