from __future__ import annotations

import logging
from typing import Any

from bleak.backends.device import BLEDevice
import voluptuous as vol

from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_ADDRESS, CONF_MODEL

from .const import DOMAIN
from .plugs import (
    parse_advertisement_data,
    GoveeAdvertisementData,
    get_pair_by_model,
    GoveePairApi,
)

_LOGGER: logging.Logger = logging.getLogger(__package__)


class GoveeBlePlugsConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        """Initialize."""
        self._api: GoveePairApi | None = None
        self._errors: dict[str, str] = {}
        self._discovered_adv: GoveeAdvertisementData | None = None
        self._discovered_advs: dict[str, GoveeAdvertisementData] = {}
        self._ble_device: BLEDevice | None = None
        self._name: str | None = None
        self._bdaddr: str | None = None

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle the bluetooth discovery step."""
        _LOGGER.debug("Discovered bluetooth device: %s", discovery_info)
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        self._ble_device = discovery_info.device
        parsed = parse_advertisement_data(
            discovery_info.device, discovery_info.advertisement
        )
        if parsed is None:
            return self.async_abort(reason="not_supported")

        self._discovered_adv = parsed
        self.context["title_placeholders"] = {
            "name": self._discovered_adv.name,
        }
        return await self.async_step_user()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a flow initialized by the user."""
        errors: dict[str, str] = {}

        discovery = self._discovered_adv
        if discovery is not None:
            self._discovered_advs[discovery.address] = discovery
        else:
            current_addresses = self._async_current_ids()
            for discovery_info in async_discovered_service_info(self.hass):
                self._ble_device = discovery_info.device
                address = discovery_info.address
                if address in current_addresses or address in self._discovered_advs:
                    continue
                parsed = parse_advertisement_data(
                    discovery_info.device, discovery_info.advertisement
                )
                if parsed:
                    self._discovered_adv = parsed
                    self._discovered_advs[address] = parsed

        if not self._discovered_advs:
            return self.async_abort(reason="no_devices_found")

        if user_input is not None:
            assert self._discovered_adv is not None
            self._name = self._discovered_adv.name
            self._bdaddr = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(self._bdaddr, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            return await self.async_step_link()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): vol.In(
                        {
                            address: f"{parsed.name} ({address})"
                            for address, parsed in self._discovered_advs.items()
                        }
                    )
                }
            ),
            errors=errors,
            last_step=False,
        )

    async def async_step_link(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Given a configured host, will ask the user to press the button to pair."""

        assert self._discovered_adv is not None
        model = self._discovered_adv.model

        device = self._ble_device
        assert device is not None

        if user_input is None:
            self._api = get_pair_by_model(model, device)
            await self._api.begin()
            return self.async_show_form(step_id="link")

        assert self._api is not None
        token = await self._api.finish()
        if token is None:
            return self.async_show_form(step_id="link", errors={"base": "linking"})

        assert self._name is not None
        return self.async_create_entry(
            title=self._name,
            data=user_input
            | {
                CONF_ADDRESS: self._bdaddr,
                CONF_ACCESS_TOKEN: token,
                CONF_MODEL: model,
            },
        )
