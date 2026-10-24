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


class MaintenanceLimits(common.Answer):
    recent_days: int = Field(description="How far back the Maintenance page's Ended group reaches.")
    history_days: int = Field(description="The history's period by default, in days up to today.")
    max_sites: int = Field(description="The most sites one request may plan for.")


class AvailabilityLimits(common.Answer):
    today: str = Field(description="Today in UTC, the report's days (2026-10-23).", examples=["2026-10-23"])
    oldest_day: str = Field(description="The oldest day the history still has; empty when nothing is deleted.")
    basis: str = Field(description="AVAILABILITY_BASIS: what counts as down by default.", examples=["any_down"])
    target: float = Field(description="The availability target, in percent.", examples=[99.9])


class MapView(common.Answer):
    latitude: float
    longitude: float
    zoom: int


class MapSettings(common.Answer):
    tile_url: str = Field(description="MAP_TILE_URL: the tile server, Leaflet's {s}/{z}/{x}/{y} form.")
    tile_attribution: str = Field(description="MAP_TILE_ATTRIBUTION, as HTML.")
    view: MapView | None = Field(description="MAP_DEFAULT_VIEW; null: the map frames the sites.")


class BoardSettings(common.Answer):
    case_url_template: str = Field(description="CASE_URL_TEMPLATE: a case's ticket, {case} for its number; empty when not set.")
    levels: dict[str, str] = Field(
        description="What each level and summary chip means, from the configured thresholds (#293): "
        "alarms, critical, medium, low, no_data, ok, total, maintenance."
    )


class PageConfig(common.Answer):
    user: BarUser | None = Field(description="Who is signed in; null with AUTH_MODE=disabled or no sign-in.")
    nautobot_url: str = Field(description="Nautobot's address, for links to its pages; empty when not set.")
    pages: list[MenuPage] = Field(description="The top bar's menu, in order.")
    maintenance: MaintenanceLimits
    availability: AvailabilityLimits
    map: MapSettings
    board: BoardSettings
