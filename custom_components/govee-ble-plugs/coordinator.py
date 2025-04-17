from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any


from homeassistant.components import bluetooth
from homeassistant.components.bluetooth.passive_update_coordinator import (
    PassiveBluetoothDataUpdateCoordinator,
)
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback

from .plugs import GoveePlugApi

if TYPE_CHECKING:
    from bleak.backends.device import BLEDevice

_LOGGER: logging.Logger = logging.getLogger(__package__)
PLATFORMS: list[str] = [Platform.SWITCH]


class GoveePlugDataUpdateCoordinator(PassiveBluetoothDataUpdateCoordinator):
    """Class to manage fetching data from the plug."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: GoveePlugApi,
        ble_device: BLEDevice,
    ) -> None:
        """Initialize."""
        self.api: GoveePlugApi = api
        self.ble_device = ble_device
        super().__init__(
            hass,
            _LOGGER,
            ble_device.address,
            bluetooth.BluetoothScanningMode.PASSIVE,
        )

    @callback
    def _async_handle_bluetooth_event(
        self,
        service_info: bluetooth.BluetoothServiceInfoBleak,
        change: bluetooth.BluetoothChange,
    ) -> None:
        """Handle a Bluetooth event."""
        self.api.handle_bluetooth_event(service_info.device, service_info.advertisement)
        super()._async_handle_bluetooth_event(service_info, change)
