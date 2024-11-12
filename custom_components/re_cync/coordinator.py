"""ReCync Hub."""

from __future__ import annotations

import logging

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .auth import ReCyncSession
from .const import DOMAIN
from .event import EventStream

_LOGGER = logging.getLogger(__name__)

API_DEVICE_LIST = "https://api.gelighting.com/v2/user/{user_id}/subscribe/devices"
API_DEVICE_PROPS = (
    "https://api.gelighting.com/v2/product/{product_id}/device/{device_id}/property"
)


DEVICE_TYPES_SWITCHES = {55, 68}
DEVICE_TYPES_BULBS = {57, 146}


class ApiError(Exception):
    pass


class AuthError(ApiError):
    pass


class ReCyncCoordinator(DataUpdateCoordinator):
    """Cync's cloud "hub" that works over IP."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
    ) -> None:
        """Create the SIAHub."""
        _LOGGER.debug("Hub init")
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
        )

        self._entry: ConfigEntry = entry
        self._rcs = ReCyncSession(entry.data[CONF_TOKEN])
        self._bulbs = []
        self._event_stream = EventStream(self._rcs.binary_token)
        self._seq: int = 0

        self.data = {}

    async def start_cloud(self):
    """Check cloud."""
    _LOGGER.info("Cloud start %s", self._rcs.user_id)

    url = API_DEVICE_LIST.format(user_id=self._rcs.user_id)
    devices = await self._get_url(url)
    for d in devices:
        # Log the name, ID, and product ID of each device
        _LOGGER.debug("Discovered device - Name: %s, ID: %s, Product ID: %s", 
                      d.get("name"), d.get("id"), d.get("product_id"))

        sku_type = int(d["product_id"], 16) % 1000
        match sku_type:
            case 713, 721:
                _LOGGER.debug("Ignoring switch/dimmer (?) SKU type %d", sku_type)
            case 897:
                await self._discover_home(d)
            case _:
                _LOGGER.debug("Ignoring SKU type %d (%s)", sku_type, d.get("name"))
    self._event_stream.set_update_callback(self.async_handle_status)
    await self._event_stream.initialize()
    _LOGGER.info("Cloud started")


    async def async_handle_status(self, switch_id, status) -> None:
        _LOGGER.debug("Got status %s %s", switch_id, status)
        new_data = self.data.copy()
        new_data[switch_id] = status
        _LOGGER.debug("New data %s", new_data)
        self.async_set_updated_data(new_data)

    @property
    def bulbs(self):
        return filter(lambda b: b["deviceType"] in DEVICE_TYPES_BULBS, self._bulbs)

    @property
    def switches(self):
        return filter(lambda b: b["deviceType"] in DEVICE_TYPES_SWITCHES, self._bulbs)

    async def turn_on(self, switch_id):
        self._seq += 1
        mesh_id = bytes.fromhex("0000")  # FIXME not real
        packet = (
            mesh_id
            + bytes.fromhex("d00000010000")
            + ((430 + int(mesh_id[0]) + int(mesh_id[1])) % 256).to_bytes(1, "big")
            + bytes.fromhex("7e")
        )
        # packet = bytes([0x73, 0x00, 0x00, 0x00, 0x1F])
        # packet += int(switch_id).to_bytes(4, "big") + int(self._seq).to_bytes(2, "big")

        await self._event_stream.async_command(
            bytes.fromhex("730000001f"), switch_id, packet
        )

    async def turn_off(self, switch_id):
        self._seq += 1
        mesh_id = bytes.fromhex("0000")  # FIXME not real

        packet = (
            mesh_id
            + bytes.fromhex("d00000000000")
            + ((429 + int(mesh_id[0]) + int(mesh_id[1])) % 256).to_bytes(1, "big")
            + bytes.fromhex("7e")
        )

        await self._event_stream.async_command(
            bytes.fromhex("730000001f"), switch_id, packet
        )

    async def _discover_home(self, device):
    url = API_DEVICE_PROPS.format(
        product_id=device["product_id"], device_id=device["id"]
    )
    info = await self._get_url(url)
    for bulb in info["bulbsArray"]:
        # Log details of each bulb discovered
        _LOGGER.debug("Bulb found - Name: %s, ID: %s, Type: %s", 
                      bulb.get("displayName"), bulb.get("deviceID"), bulb.get("deviceType"))
        self._bulbs.append(bulb)

