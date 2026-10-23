"""Circuits' answers (#449)."""

from typing import Literal

from pydantic import Field

from nautobot_maps.api.answers import common


class Circuit(common.Answer):
    """A circuit as Nautobot has it; empty text where Nautobot has nothing."""

    id: str
    cid: str = Field(description='"Unnamed circuit" when it has no circuit ID.')
    provider: str
    circuit_type: str
    status: str
    tenant: str
    commit_rate: str = Field(examples=["10 Gbps"], description="Empty when Nautobot has no speed.")
    description: str


class MappedSite(common.Answer):
    id: str = Field(description="The location to open: the site, or its building on the map.")
    name: str
    on_map: bool = Field(description="False when no location of it there has coordinates.")


class MappedCircuit(Circuit):
    group: Literal["several", "none", "one"] = Field(description="How many sites its address points to.")
    sites: list[MappedSite] = Field(description="By name.")


class MappingSummary(common.Answer):
    total: int
    several: int
    none: int
    one: int


class CircuitMapping(common.Answer):
    summary: MappingSummary
    circuits: list[MappedCircuit] = Field(description="Several sites first, then none, then one; by circuit ID.")
