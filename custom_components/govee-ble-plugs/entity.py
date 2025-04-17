from __future__ import annotations

from typing import Any, Optional

from homeassistant.components.bluetooth.passive_update_coordinator import (
    PassiveBluetoothCoordinatorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo

from .const import MANUFACTURER
from .coordinator import GoveePlugDataUpdateCoordinator


class GoveePlugEntity(
    PassiveBluetoothCoordinatorEntity[GoveePlugDataUpdateCoordinator]
):
    """Generic entity for all plugs."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator,
        config_entry: ConfigEntry,
        port: Optional[int],
        port_name: Optional[str],
    ):
        """Initialise the entity."""
        super().__init__(coordinator)
        self._address = self.coordinator.ble_device.address
        self._port = port or 0

        # backwards compat -- original H5080 entity
        if port is None:
            self._attr_unique_id = self._address
        else:
            self._attr_unique_id = f"{self._address}-{port}"
        self._attr_name = port_name
        self._attr_device_info = DeviceInfo(
            connections={(dr.CONNECTION_BLUETOOTH, self._address)},
            manufacturer=MANUFACTURER,
            model=self.coordinator.api.MODEL,
            name=config_entry.title,
        )
