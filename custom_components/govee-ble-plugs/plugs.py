import asyncio
import dataclasses
import logging
import typing as T

from bleak import BleakClient
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from bleak_retry_connector import establish_connection

from homeassistant.exceptions import ConfigEntryError

_LOGGER: logging.Logger = logging.getLogger(__package__)


def _b(s: str):
    return bytes(bytearray.fromhex(s))


def _sign_payload(data):
    checksum = 0
    for b in data:
        checksum ^= b
    return checksum & 0xFF


class GoveePlugApi(T.Protocol):
    MODEL: T.Final[str]

    def __init__(self, device: BLEDevice, token: str) -> None:
        ...

    @property
    def is_on(self) -> bool | None:
        ...

    def handle_bluetooth_event(
        self, device: BLEDevice, adv: AdvertisementData
    ) -> dict[str, T.Any] | None:
        ...

    async def async_turn_on(self):
        ...

    async def async_turn_off(self):
        ...


class GoveePairApi(T.Protocol):
    def __init__(self, device: BLEDevice) -> None:
        ...

    async def begin(self):
        ...

    async def finish(self) -> str | None:
        ...


def get_api_by_model(model: str, device: BLEDevice, token: str) -> GoveePlugApi:
    if model == "H5080":
        return GoveePlugH5080(device, token)

    raise ConfigEntryError(f"Unsupported model {model}")


def get_pair_by_model(model: str, device: BLEDevice) -> GoveePairApi:
    if model == "H5080":
        return GoveePairH5080(device)

    raise ConfigEntryError(f"Unsupported model {model}")


@dataclasses.dataclass
class GoveeAdvertisementData:
    name: str
    address: str
    device: BLEDevice
    model: str


def parse_advertisement_data(
    device: BLEDevice, adv: AdvertisementData
) -> GoveeAdvertisementData | None:
    local_name = adv.local_name
    if not local_name:
        return

    if local_name.startswith("ihoment_H5080_"):
        return GoveeAdvertisementData(
            local_name, device.address, device, GoveePlugH5080.MODEL
        )


class GoveePlugH5080:
    MODEL = "H5080"

    MSG_GET_AUTH_KEY = _b("aab100000000000000000000000000000000001b")
    MSG_TURN_ON = _b("3301ff00000000000000000000000000000000cd")
    MSG_TURN_OFF = _b("3301f000000000000000000000000000000000c2")

    SEND_CHARACTERISTIC_UUID = "00010203-0405-0607-0809-0a0b0c0d2b11"
    RECV_CHARACTERISTIC_UUID = "00010203-0405-0607-0809-0a0b0c0d2b10"

    def __init__(self, device: BLEDevice, token: str) -> None:
        self._device = device
        self._token = token
        self._is_on = None
        self._client = None

    @property
    def is_on(self):
        return self._is_on

    def handle_bluetooth_event(
        self, device: BLEDevice, adv: AdvertisementData
    ) -> dict[str, T.Any] | None:
        for _, mfr_data in adv.manufacturer_data.items():
            self._device = device
            self._is_on = mfr_data[-1] == 0x01
            return {"is_on": self._is_on}

    async def async_turn_on(self):
        await self._set_state(True)

    async def async_turn_off(self):
        await self._set_state(False)

    async def _set_state(self, new_state: bool):
        client = None
        try:
            client = await establish_connection(
                BleakClient,
                self._device,
                f"{self._device.name} ({self._device.address})",
            )

            # events to control execution flow
            on_auth_ready = asyncio.Event()
            on_set_state_ready = asyncio.Event()

            async def recv_handler(c, data):
                if data[0] == 0x33 and data[1] == 0xB2:
                    on_auth_ready.set()
                elif data[0] == 0x33 and data[1] == 0x01:
                    on_set_state_ready.set()

            await client.start_notify(self.RECV_CHARACTERISTIC_UUID, recv_handler)

            ba = bytearray([0x33, 0xB2]) + bytearray.fromhex(self._token).ljust(
                17, b"\0"
            )
            ba.append(_sign_payload(ba))
            await client.write_gatt_char(self.SEND_CHARACTERISTIC_UUID, ba)
            await on_auth_ready.wait()

            ba = self.MSG_TURN_ON if new_state else self.MSG_TURN_OFF
            await client.write_gatt_char(self.SEND_CHARACTERISTIC_UUID, ba)
            await on_set_state_ready.wait()

            await client.stop_notify(self.RECV_CHARACTERISTIC_UUID)

            self._is_on = new_state
        except Exception as e:
            _LOGGER.error("failed to set state: %s", e)
        finally:
            if client is not None:
                await client.disconnect()


class GoveePairH5080:
    def __init__(self, device: BLEDevice) -> None:
        self._device = device
        self._result = asyncio.Future()

    async def begin(self):
        _LOGGER.info(f"%s: connecting to begin pairing", self._device.name)
        self._client = await establish_connection(
            BleakClient,
            self._device,
            f"{self._device.name} ({self._device.address})",
        )

        await self._client.start_notify(
            GoveePlugH5080.RECV_CHARACTERISTIC_UUID, self._recv_handler
        )
        await self._send_get_auth_key()

    async def finish(self) -> str | None:
        token = await self._result
        _LOGGER.info(f"%s: finishing pairing", self._device.name)
        await self._client.stop_notify(GoveePlugH5080.RECV_CHARACTERISTIC_UUID)
        await self._client.disconnect()
        return token

    async def _send_get_auth_key(self):
        _LOGGER.info(f"%s: asking for auth key", self._device.name)
        await self._client.write_gatt_char(
            GoveePlugH5080.SEND_CHARACTERISTIC_UUID, GoveePlugH5080.MSG_GET_AUTH_KEY
        )

    async def _recv_handler(self, _, msg: bytearray):
        if len(msg) != 20:
            return

        # Check for the response type and subtype
        if msg[0] == 0xAA and msg[1] == 0xB1:
            if msg[2] == 0x01:
                auth_key = msg[3:-1]
                _LOGGER.info(f"%s: received authentication key", self._device.name)
                if not self._result.done():
                    self._result.set_result(auth_key.hex())
            else:
                await self._send_get_auth_key()
