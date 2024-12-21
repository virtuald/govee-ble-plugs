from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import (
    AddEntitiesCallback,
)

from .const import DOMAIN
from .coordinator import GoveePlugDataUpdateCoordinator
from .entity import GoveePlugEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up MicroBot based on a config entry."""
    coordinator: GoveePlugDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([GoveePlugSwitch(coordinator, entry)])


class GoveePlugSwitch(GoveePlugEntity, SwitchEntity):
    """Govee switch class."""

    _attr_device_class = SwitchDeviceClass.OUTLET
    _attr_translation_key = "power"

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the switch."""
        await self.coordinator.api.async_turn_on()
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the switch."""
        await self.coordinator.api.async_turn_off()
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool | None:
        """Return true if the switch is on."""
        return self.coordinator.api.is_on