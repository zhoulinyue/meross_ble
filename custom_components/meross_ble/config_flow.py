"""Config flow for Meross Bluetooth."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import callback

from .const import (
    CONF_MODEL,
    CONF_RETRY_COUNT,
    DEFAULT_RETRY_COUNT,
    DOMAIN,
    MODEL_FRIENDLY_NAME,
    MerossModel,
)
from .parser import MerossAdvertisement, parse_advertisement_data

MANUAL_SCAN_DURATION = 15


def format_unique_id(address: str) -> str:
    """Format BLE address as unique_id."""
    return address.replace(":", "").replace("-", "").lower()


def short_address(address: str) -> str:
    """Last 4 hex digits for UI titles."""
    parts = address.replace("-", ":").split(":")
    return f"{parts[-2].upper()}{parts[-1].upper()}"[-4:]


def name_from_discovery(discovery: MerossAdvertisement) -> str:
    """Friendly title: model + short address."""
    return f"{discovery.friendly_name} {short_address(discovery.address)}"


def _collect_discovered_service_info(hass) -> list[BluetoothServiceInfoBleak]:
    """Return unique BLE advertisements from HA's connectable/non-connectable caches."""
    seen: set[str] = set()
    results: list[BluetoothServiceInfoBleak] = []
    for connectable in (True, False):
        for info in async_discovered_service_info(hass, connectable):
            if info.address in seen:
                continue
            seen.add(info.address)
            results.append(info)
    return results


class MerossBLEConfigFlow(ConfigFlow, domain=DOMAIN):
    """Config flow for Meross Bluetooth."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovered: MerossAdvertisement | None = None
        self._discovered_devices: dict[str, MerossAdvertisement] = {}

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """HA bluetooth matched manifest rules."""
        await self.async_set_unique_id(format_unique_id(discovery_info.address))
        self._abort_if_unique_id_configured()

        parsed = parse_advertisement_data(
            discovery_info.device, discovery_info.advertisement
        )
        if not parsed:
            return self.async_abort(reason="not_supported")

        self._discovered = parsed
        self.context["title_placeholders"] = {
            "name": parsed.friendly_name,
            "address": short_address(discovery_info.address),
        }
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm setup."""
        assert self._discovered is not None
        if user_input is not None:
            return self._async_create_entry(self._discovered)

        self._set_confirm_only()
        return self.async_show_form(
            step_id="confirm",
            description_placeholders={
                "name": name_from_discovery(self._discovered),
            },
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manual add integration."""
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            discovery = self._discovered_devices[address]
            await self.async_set_unique_id(
                format_unique_id(address), raise_on_progress=False
            )
            self._abort_if_unique_id_configured()
            return self._async_create_entry(discovery)

        await bluetooth.async_request_active_scan(self.hass, MANUAL_SCAN_DURATION)

        if bluetooth.async_scanner_count(self.hass, connectable=False) == 0:
            return self.async_abort(reason="no_bluetooth_adapter")

        current = self._async_current_ids(include_ignore=False)
        for info in _collect_discovered_service_info(self.hass):
            address = info.address
            uid = format_unique_id(address)
            if uid in current or address in self._discovered_devices:
                continue
            parsed = parse_advertisement_data(info.device, info.advertisement)
            if parsed:
                self._discovered_devices[address] = parsed

        if not self._discovered_devices:
            return self.async_abort(reason="no_devices_found")

        if len(self._discovered_devices) == 1:
            discovery = next(iter(self._discovered_devices.values()))
            await self.async_set_unique_id(
                format_unique_id(discovery.address), raise_on_progress=False
            )
            self._abort_if_unique_id_configured()
            self._discovered = discovery
            return await self.async_step_confirm()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): vol.In(
                        {
                            address: name_from_discovery(parsed)
                            for address, parsed in self._discovered_devices.items()
                        }
                    ),
                }
            ),
        )

    def _async_create_entry(self, discovery: MerossAdvertisement) -> ConfigFlowResult:
        return self.async_create_entry(
            title=name_from_discovery(discovery),
            data={
                CONF_ADDRESS: discovery.address,
                CONF_MODEL: discovery.model.value,
            },
            options={CONF_RETRY_COUNT: DEFAULT_RETRY_COUNT},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return MerossBLEOptionsFlowHandler()


class MerossBLEOptionsFlowHandler(OptionsFlow):
    """Options: GATT retry count."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        model = self.config_entry.data.get(CONF_MODEL, MerossModel.MS120)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_RETRY_COUNT,
                        default=self.config_entry.options.get(
                            CONF_RETRY_COUNT, DEFAULT_RETRY_COUNT
                        ),
                    ): vol.All(vol.Coerce(int), vol.Range(min=1, max=10)),
                }
            ),
            description_placeholders={
                "model": MODEL_FRIENDLY_NAME.get(MerossModel(model), str(model)),
            },
        )
