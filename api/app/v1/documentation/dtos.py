"""The documentation's DTOs (#433, #463, #624): the guides and the endpoint list."""

from typing import Any, Literal

from pydantic import Field

from app.v1 import sections
from app.v1.common import dtos as common


class Guide(common.Answer):
    slug: str = Field(examples=["authentication"])
    title: str = Field(examples=["Sign-in and roles"])
    href: str = Field(examples=["/help/authentication"])


class HelpPage(common.Answer):
    slug: str
    title: str
    html: str = Field(
        description="The guide as HTML, from the repository's Markdown (no raw HTML in it)."
    )
    contents: list[Guide] = Field(description="Every guide, in the menu's order.")


class Endpoint(common.Answer):
    path: str = Field(examples=["/api/alerts"])
    methods: list[str]
    section: Literal[*sections.NAMES] = Field(  # type: ignore[valid-type]
        description="The API's section: the page it belongs to, or Auth, Incoming alerts, Shared, Monitoring, Examples."
    )
    group: Literal["Pages", "API", "Monitoring"] = Field(
        description="The older grouping, kept for callers: sort by section instead."
    )
    summary: str
    description: str
    path_params: list[str]
    query_params: list[str]
    required_role: Literal["viewer", "operator", "admin"] | None
    example_body: dict[str, Any] | None
    example_body_text: str = Field(description="example_body, formatted; {} when there is none.")


class Endpoints(common.Answer):
    endpoints: list[Endpoint]
