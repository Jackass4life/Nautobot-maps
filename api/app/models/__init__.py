"""ORM models. Every model is imported here so `Base.metadata` is complete for Alembic.

Everything but `item` is today's schema (#590), adopted as is.
"""

from app.models.access import ApiToken, StatusLink
from app.models.alerts import (
    AlertCase,
    AlertEvent,
    AlertInstance,
    BoardBuildStat,
    DeviceCriticalityOverride,
    DeviceState,
    NotificationOutbox,
    SiteAlertLevel,
    SiteLevelChange,
    WebhookDeviceAlert,
)
from app.models.base import Base
from app.models.inventory import (
    ContractCheck,
    InventorySyncState,
    NautobotCableCache,
    NautobotCircuitCache,
    NautobotDeviceCache,
    NautobotLocationCache,
    NautobotLocationTenantCache,
    NautobotTenantCache,
)
from app.models.item import Item
from app.models.librenms import LibrenmsDeviceMap, LibrenmsDeviceStatus
from app.models.maintenance import MaintenanceWindow
from app.models.schema import SchemaMigration
from app.models.wireless import WirelessApNeighbour, WirelessApStatus, WirelessPnpStatus

__all__ = [
    "AlertCase",
    "AlertEvent",
    "AlertInstance",
    "ApiToken",
    "Base",
    "BoardBuildStat",
    "ContractCheck",
    "DeviceCriticalityOverride",
    "DeviceState",
    "InventorySyncState",
    "Item",
    "LibrenmsDeviceMap",
    "LibrenmsDeviceStatus",
    "MaintenanceWindow",
    "NautobotCableCache",
    "NautobotCircuitCache",
    "NautobotDeviceCache",
    "NautobotLocationCache",
    "NautobotLocationTenantCache",
    "NautobotTenantCache",
    "NotificationOutbox",
    "SchemaMigration",
    "SiteAlertLevel",
    "SiteLevelChange",
    "StatusLink",
    "WebhookDeviceAlert",
    "WirelessApNeighbour",
    "WirelessApStatus",
    "WirelessPnpStatus",
]
