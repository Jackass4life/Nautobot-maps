"""Circuits' answers (#449)."""

from typing import Literal

from pydantic import Field

from app.v1.common import dtos as common


class MappedSite(common.Answer):
    id: str = Field(description="The location to open: the site, or its building on the map.")
    name: str
    on_map: bool = Field(description="False when no location of it there has coordinates.")


class MappedCircuit(common.Circuit):
    group: Literal["several", "none", "one"] = Field(
        description="How many sites its address points to."
    )
    sites: list[MappedSite] = Field(description="By name.")


class MappingSummary(common.Answer):
    total: int
    several: int
    none: int
    one: int


class CircuitMapping(common.Answer):
    summary: MappingSummary
    circuits: list[MappedCircuit] = Field(
        description="Several sites first, then none, then one; by circuit ID."
    )
