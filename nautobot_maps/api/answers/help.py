"""The documentation pages' answers (#433, #463)."""

from pydantic import Field

from nautobot_maps.api.answers import common


class Guide(common.Answer):
    slug: str = Field(examples=["authentication"])
    title: str = Field(examples=["Sign-in and roles"])
    href: str = Field(examples=["/help/authentication"])


class HelpPage(common.Answer):
    slug: str
    title: str
    html: str = Field(description="The guide as HTML, from the repository's Markdown (no raw HTML in it).")
    contents: list[Guide] = Field(description="Every guide, in the menu's order.")
