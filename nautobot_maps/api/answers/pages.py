"""What the pages read to draw themselves (#463)."""

from pydantic import Field

from nautobot_maps.api.answers import common


class BarUser(common.Answer):
    name: str = Field(description="The signed-in user, or an API token's name.")
    role: str = Field(description="viewer, operator or admin; empty when unknown.")


class MenuPage(common.Answer):
    key: str = Field(examples=["circuits"])
    href: str = Field(examples=["/circuits"])
    label: str = Field(examples=["Circuits"])
    title: str


class PageConfig(common.Answer):
    user: BarUser | None = Field(description="Who is signed in; null with AUTH_MODE=disabled or no sign-in.")
    nautobot_url: str = Field(description="Nautobot's address, for links to its pages; empty when not set.")
    pages: list[MenuPage] = Field(description="The top bar's menu, in order.")
