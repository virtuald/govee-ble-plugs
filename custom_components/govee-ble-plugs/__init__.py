from __future__ import annotations

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_ADDRESS, CONF_MODEL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import DOMAIN
from .coordinator import GoveePlugDataUpdateCoordinator

from .plugs import GoveePlugApi, get_api_by_model

PLATFORMS: list[str] = [Platform.SWITCH]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})
    model: str = entry.data[CONF_MODEL]
    token: str = entry.data[CONF_ACCESS_TOKEN]
    bdaddr: str = entry.data[CONF_ADDRESS]
    ble_device = bluetooth.async_ble_device_from_address(hass, bdaddr, connectable=True)
    if not ble_device:
        raise ConfigEntryNotReady(f"Could not find Govee {model} with address {bdaddr}")

    api: GoveePlugApi = get_api_by_model(model, ble_device, token)

    coordinator = GoveePlugDataUpdateCoordinator(hass, api=api, ble_device=ble_device)

    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(coordinator.async_start())

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Handle removal of an entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
