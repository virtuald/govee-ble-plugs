import asyncio
import dataclasses
import os
import logging
import queue
import typing as T

from bleak import BleakClient
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from bleak_retry_connector import establish_connection

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

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

    def __init__(self, device: BLEDevice, token: str) -> None: ...

    def port_names(self) -> T.List[T.Tuple[T.Optional[int], T.Optional[str]]]: ...

    def is_on(self, port: int) -> bool | None: ...

    def handle_bluetooth_event(self, device: BLEDevice, adv: AdvertisementData): ...

    async def async_turn_on(self, port: int): ...

    async def async_turn_off(self, port: int): ...


class GoveePairApi(T.Protocol):

    async def begin(self): ...

    async def finish(self) -> str | None: ...


def get_api_by_model(model: str, device: BLEDevice, token: str) -> GoveePlugApi:
    if model == "H5080":
        return GoveePlugH5080(device, token)

    if model == "H5082":
        return GoveePlugH5082(device, token)

    if model == "H5086":
        return GoveePlugH5086(device, token)

    if model == "H5083":
        return GoveePlugH5083(device, token)

    raise ConfigEntryError(f"Unsupported model {model}")


def get_pair_by_model(model: str, device: BLEDevice) -> GoveePairApi:
    if model == "H5080":
        return GoveePlugPairer(
            device,
            GoveePlugH5080.RECV_CHARACTERISTIC_UUID,
            GoveePlugH5080.SEND_CHARACTERISTIC_UUID,
            GoveePlugH5080.MSG_GET_AUTH_KEY,
        )

    if model == "H5082":
        return GoveePlugPairer(
            device,
            GoveePlugH5082.RECV_CHARACTERISTIC_UUID,
            GoveePlugH5082.SEND_CHARACTERISTIC_UUID,
            GoveePlugH5082.MSG_GET_AUTH_KEY,
        )

    if model == "H5086":
        return GoveePlugPairer(
            device,
            GoveePlugH5086.RECV_CHARACTERISTIC_UUID,
            GoveePlugH5086.SEND_CHARACTERISTIC_UUID,
            GoveePlugH5086.MSG_GET_AUTH_KEY,
        )

    if model == "H5083":
        return GoveePlugH5083Pairer(device)

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

    if local_name.startswith("ihoment_H5082_"):
        return GoveeAdvertisementData(
            local_name, device.address, device, GoveePlugH5082.MODEL
        )

    if local_name.startswith("ihoment_H5083_"):
        return GoveeAdvertisementData(
            local_name, device.address, device, GoveePlugH5083.MODEL
        )

    if local_name.startswith("GVH5086"):
        return GoveeAdvertisementData(
            local_name, device.address, device, GoveePlugH5086.MODEL
        )


class GoveePlugH508x:

    def __init__(
        self,
        device: BLEDevice,
        token: str,
        RECV_CHARACTERISTIC_UUID: str,
        SEND_CHARACTERISTIC_UUID: str,
    ) -> None:
        self._device = device
        self._token = token
        self._RECV_CHARACTERISTIC_UUID = RECV_CHARACTERISTIC_UUID
        self._SEND_CHARACTERISTIC_UUID = SEND_CHARACTERISTIC_UUID

        self._connection_task: T.Optional[asyncio.Task] = None
        self._msgqueue = asyncio.Queue[T.Tuple[bytes, asyncio.Future[bool]]]()

    async def _send_message(self, msg: bytes) -> bool:
        f = asyncio.Future[bool]()
        self._msgqueue.put_nowait((msg, f))
        self._ensure_message_task()
        return await f

    def _ensure_message_task(self):
        if not self._connection_task:
            self._connection_task = asyncio.create_task(self._message_task_fn())
            self._connection_task.add_done_callback(self._message_task_done)

    def _message_task_done(self, task: asyncio.Task):
        try:
            task.result()
        except Exception:
            # if this failed, it was logged or failed while disconnecting
            pass

        if self._connection_task is task:
            self._connection_task = None

        if self._connection_task is None and not self._msgqueue.empty():
            self._ensure_message_task()

    async def _message_task_fn(self):
        client = None
        must_process = queue.Queue[T.Tuple[bytes, asyncio.Future]]()

        try:
            # Pull anything on the message queue directly off, these must
            # be processed one way or another
            while not self._msgqueue.empty():
                must_process.put(self._msgqueue.get_nowait())

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

            await client.start_notify(self._RECV_CHARACTERISTIC_UUID, recv_handler)

            ba = bytearray([0x33, 0xB2]) + bytearray.fromhex(self._token).ljust(
                17, b"\0"
            )
            ba.append(_sign_payload(ba))
            await client.write_gatt_char(self._SEND_CHARACTERISTIC_UUID, ba)
            await on_auth_ready.wait()

            #
            # Send messages after authentication occurs
            #

            async def _send_msg(msg: bytes, f: asyncio.Future):
                try:
                    await client.write_gatt_char(self._SEND_CHARACTERISTIC_UUID, msg)
                    await on_set_state_ready.wait()
                except Exception:
                    f.set_result(False)
                    raise
                else:
                    f.set_result(True)

            # Process must process entries first
            while not must_process.empty():
                msg, f = must_process.get_nowait()
                await _send_msg(msg, f)

            # Then process anything else that might be in the queue
            while True:
                try:
                    msg, f = await asyncio.wait_for(self._msgqueue.get(), timeout=1)
                except TimeoutError:
                    break
                else:
                    await _send_msg(msg, f)

            await client.stop_notify(self._RECV_CHARACTERISTIC_UUID)

        except Exception as e:
            _LOGGER.error("failed to set state: %s", e)
        finally:
            # We only force clearing the must process queue. Anything that
            # was queued while the connection was failing deserves another try
            # and will be requeued when this task's done callback is called
            while not must_process.empty():
                _, f = must_process.get_nowait()
                f.set_result(False)

            if client is not None:
                await client.disconnect()


class GoveePlugH5080(GoveePlugH508x):
    MODEL = "H5080"

    MSG_GET_AUTH_KEY = _b("aab100000000000000000000000000000000001b")
    MSG_TURN_ON = _b("3301ff00000000000000000000000000000000cd")
    MSG_TURN_OFF = _b("3301f000000000000000000000000000000000c2")

    SEND_CHARACTERISTIC_UUID = "00010203-0405-0607-0809-0a0b0c0d2b11"
    RECV_CHARACTERISTIC_UUID = "00010203-0405-0607-0809-0a0b0c0d2b10"

    def __init__(self, device: BLEDevice, token: str) -> None:
        super().__init__(
            device, token, self.RECV_CHARACTERISTIC_UUID, self.SEND_CHARACTERISTIC_UUID
        )
        self._is_on = None

    def port_names(self) -> T.List[T.Tuple[T.Optional[int], T.Optional[str]]]:
        return [(None, None)]

    def is_on(self, port: int):
        return self._is_on

    def handle_bluetooth_event(self, device: BLEDevice, adv: AdvertisementData):
        for _, mfr_data in adv.manufacturer_data.items():
            self._device = device
            self._is_on = mfr_data[-1] == 0x01

    async def async_turn_on(self, port: int):
        assert port == 0
        if await self._send_message(self.MSG_TURN_ON):
            self._is_on = True

    async def async_turn_off(self, port: int):
        assert port == 0
        if await self._send_message(self.MSG_TURN_OFF):
            self._is_on = False


class GoveePlugH5082(GoveePlugH508x):
    MODEL = "H5082"

    MSG_GET_AUTH_KEY = _b("aab100000000000000000000000000000000001b")

    MSG_LEFT_ON = _b("3301220000000000000000000000000000000010")
    MSG_LEFT_OFF = _b("3301200000000000000000000000000000000012")
    MSG_RIGHT_ON = _b("3301110000000000000000000000000000000023")
    MSG_RIGHT_OFF = _b("3301100000000000000000000000000000000022")

    SEND_CHARACTERISTIC_UUID = "00010203-0405-0607-0809-0a0b0c0d2b11"
    RECV_CHARACTERISTIC_UUID = "00010203-0405-0607-0809-0a0b0c0d2b10"

    def __init__(self, device: BLEDevice, token: str) -> None:
        super().__init__(
            device, token, self.RECV_CHARACTERISTIC_UUID, self.SEND_CHARACTERISTIC_UUID
        )
        self._is_on: T.List[T.Optional[bool]] = [None, None]

    def port_names(self) -> T.List[T.Tuple[T.Optional[int], T.Optional[str]]]:
        return [(0, "Left Power"), (1, "Right Power")]

    def is_on(self, port: int):
        return self._is_on[port]

    def handle_bluetooth_event(self, device: BLEDevice, adv: AdvertisementData):
        for _, mfr_data in adv.manufacturer_data.items():
            self._device = device
            self._is_on[0] = (mfr_data[-1] & 0x2) == 0x2
            self._is_on[1] = (mfr_data[-1] & 0x1) == 0x1

    async def async_turn_on(self, port: int):
        if port == 0:
            msg = self.MSG_LEFT_ON
        elif port == 1:
            msg = self.MSG_RIGHT_ON
        else:
            assert False

        if await self._send_message(msg):
            self._is_on[port] = True

    async def async_turn_off(self, port: int):
        if port == 0:
            msg = self.MSG_LEFT_OFF
        elif port == 1:
            msg = self.MSG_RIGHT_OFF
        else:
            assert False

        if await self._send_message(msg):
            self._is_on[port] = False


class GoveePlugH5086(GoveePlugH508x):
    MODEL = "H5086"

    MSG_GET_AUTH_KEY = _b("aab100000000000000000000000000000000001b")
    MSG_TURN_ON = _b("3301010000000000000000000000000000000033")
    MSG_TURN_OFF = _b("3301000000000000000000000000000000000032")

    SEND_CHARACTERISTIC_UUID = "00010203-0405-0607-0809-0a0b0c0d2b11"
    RECV_CHARACTERISTIC_UUID = "00010203-0405-0607-0809-0a0b0c0d2b10"

    def __init__(self, device: BLEDevice, token: str) -> None:
        super().__init__(
            device, token, self.RECV_CHARACTERISTIC_UUID, self.SEND_CHARACTERISTIC_UUID
        )
        self._is_on = None

    def port_names(self) -> T.List[T.Tuple[T.Optional[int], T.Optional[str]]]:
        return [(None, None)]

    def is_on(self, port: int):
        return self._is_on

    def handle_bluetooth_event(self, device: BLEDevice, adv: AdvertisementData):
        for _, mfr_data in adv.manufacturer_data.items():
            self._device = device
            self._is_on = mfr_data[-1] == 0x01

    async def async_turn_on(self, port: int):
        assert port == 0
        if await self._send_message(self.MSG_TURN_ON):
            self._is_on = True

    async def async_turn_off(self, port: int):
        assert port == 0
        if await self._send_message(self.MSG_TURN_OFF):
            self._is_on = False


class GoveePlugPairer:
    # At least H5080, H5082, and H5086 all have the same pairing procedure
    # as implemented here

    def __init__(
        self, device: BLEDevice, recv_uuid: str, send_uuid: str, auth_msg: bytes
    ) -> None:
        self._device = device
        self._recv_uuid = recv_uuid
        self._send_uuid = send_uuid
        self._auth_msg = auth_msg
        self._result = asyncio.Future()

    async def begin(self):
        _LOGGER.info(f"%s: connecting to begin pairing", self._device.name)
        self._client = await establish_connection(
            BleakClient,
            self._device,
            f"{self._device.name} ({self._device.address})",
        )

        await self._client.start_notify(self._recv_uuid, self._recv_handler)
        await self._send_get_auth_key()

    async def finish(self) -> str | None:
        token = await self._result
        _LOGGER.info(f"%s: finishing pairing", self._device.name)
        await self._client.stop_notify(self._recv_uuid)
        await self._client.disconnect()
        return token

    async def _send_get_auth_key(self):
        _LOGGER.info(f"%s: asking for auth key", self._device.name)
        await self._client.write_gatt_char(self._send_uuid, self._auth_msg)

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


#
# H5083: same 20-byte Govee frames, but every frame is encrypted (first 16 bytes AES-128-ECB,
# last 4 bytes RC4). An "e7 01 / e7 02" handshake under a fixed key yields a per-connection
# session key (the plug's 16 random bytes), which encrypts everything after it.
#

H5083_STATIC_KEY = b"MakingLifeSmarte"


def _rc4(key: bytes, data: bytes) -> bytes:
    S = list(range(256))
    j = 0
    for i in range(256):
        j = (j + S[i] + key[i % len(key)]) % 256
        S[i], S[j] = S[j], S[i]
    i = j = 0
    out = bytearray()
    for c in data:
        i = (i + 1) % 256
        j = (j + S[i]) % 256
        S[i], S[j] = S[j], S[i]
        out.append(c ^ S[(S[i] + S[j]) % 256])
    return bytes(out)


def _h5083_encrypt(key: bytes, p: bytes) -> bytes:
    return Cipher(algorithms.AES(key), modes.ECB()).encryptor().update(p[:16]) + _rc4(
        key, p[16:]
    )


def _h5083_decrypt(key: bytes, c: bytes) -> bytes:
    return Cipher(algorithms.AES(key), modes.ECB()).decryptor().update(c[:16]) + _rc4(
        key, c[16:]
    )


def _frame(*head: int) -> bytes:
    ba = bytearray(head).ljust(19, b"\0")
    ba.append(_sign_payload(ba))
    return bytes(ba)


def _valid(p: bytes) -> bool:
    return len(p) == 20 and _sign_payload(p[:-1]) == p[-1]


class _H5083Session:
    """Connects, runs the e7 handshake and holds the session key."""

    SEND = "00010203-0405-0607-0809-0a0b0c0d2b11"
    RECV = "00010203-0405-0607-0809-0a0b0c0d2b10"

    def __init__(self, client: BleakClient) -> None:
        self.client = client
        self.key: bytes | None = None
        self._waiters: dict[bytes, asyncio.Future] = {}
        # async callback(sender, msg) for decrypted messages nobody is waiting for
        self.on_message = None

    def _decode(self, raw: bytes) -> bytes | None:
        for k in ([self.key] if self.key else []) + [H5083_STATIC_KEY]:
            p = _h5083_decrypt(k, raw)
            if _valid(p):
                return p
        return None

    def _on_notify(self, _, data: bytearray):
        p = self._decode(bytes(data))
        if p is None:
            _LOGGER.debug("H5083: undecodable notify %s", bytes(data).hex())
            return
        _LOGGER.debug("H5083 <- %s", p.hex())
        f = self._waiters.pop(p[:2], None)
        if f is not None and not f.done():
            f.set_result(p)
        elif f is None and self.on_message is not None:
            asyncio.create_task(self.on_message(None, bytearray(p)))

    async def _request(self, p: bytes, key: bytes, timeout: float = 5) -> bytes:
        f = asyncio.get_running_loop().create_future()
        self._waiters[p[:2]] = f
        _LOGGER.debug("H5083 -> %s", p.hex())
        await self.client.write_gatt_char(
            self.SEND, _h5083_encrypt(key, p), response=False
        )
        return await asyncio.wait_for(f, timeout)

    async def start(self):
        await self.client.start_notify(self.RECV, self._on_notify)
        reply = await self._request(
            _frame(0xE7, 0x01, *os.urandom(17)), H5083_STATIC_KEY
        )
        plug_rand = reply[2:18]
        await self._request(_frame(0xE7, 0x02, *os.urandom(17)), H5083_STATIC_KEY)
        self.key = plug_rand

    async def auth(self, token: str):
        ba = bytearray([0x33, 0xB2]) + bytearray.fromhex(token).ljust(17, b"\0")
        ba.append(_sign_payload(ba))
        await self._request(bytes(ba), self.key)

    async def command(self, p: bytes):
        await self._request(p, self.key)


class GoveePlugH5083(GoveePlugH508x):
    MODEL = "H5083"

    SEND_CHARACTERISTIC_UUID = _H5083Session.SEND
    RECV_CHARACTERISTIC_UUID = _H5083Session.RECV

    MSG_TURN_ON = _frame(0x33, 0x01, 0x11)
    MSG_TURN_OFF = _frame(0x33, 0x01, 0x10)

    def __init__(self, device: BLEDevice, token: str) -> None:
        super().__init__(
            device, token, self.RECV_CHARACTERISTIC_UUID, self.SEND_CHARACTERISTIC_UUID
        )
        self._is_on = None

    def port_names(self) -> T.List[T.Tuple[T.Optional[int], T.Optional[str]]]:
        return [(None, None)]

    def is_on(self, port: int):
        return self._is_on

    def handle_bluetooth_event(self, device: BLEDevice, adv: AdvertisementData):
        for _, mfr_data in adv.manufacturer_data.items():
            self._device = device
            self._is_on = mfr_data[-1] == 0x01

    async def async_turn_on(self, port: int):
        if await self._send_message(self.MSG_TURN_ON):
            self._is_on = True

    async def async_turn_off(self, port: int):
        if await self._send_message(self.MSG_TURN_OFF):
            self._is_on = False

    async def _message_task_fn(self):
        client = None
        must_process = queue.Queue[T.Tuple[bytes, asyncio.Future]]()
        try:
            while not self._msgqueue.empty():
                must_process.put(self._msgqueue.get_nowait())

            client = await establish_connection(
                BleakClient,
                self._device,
                f"{self._device.name} ({self._device.address})",
            )
            session = _H5083Session(client)
            await session.start()
            await session.auth(self._token)

            async def _send_msg(msg: bytes, f: asyncio.Future):
                try:
                    await session.command(msg)
                except Exception:
                    f.set_result(False)
                    raise
                else:
                    f.set_result(True)

            while not must_process.empty():
                msg, f = must_process.get_nowait()
                await _send_msg(msg, f)

            while True:
                try:
                    msg, f = await asyncio.wait_for(self._msgqueue.get(), timeout=1)
                except TimeoutError:
                    break
                else:
                    await _send_msg(msg, f)

            await client.stop_notify(self.RECV_CHARACTERISTIC_UUID)

        except Exception as e:
            _LOGGER.error("H5083: failed to set state: %r", e)
        finally:
            while not must_process.empty():
                _, f = must_process.get_nowait()
                f.set_result(False)
            if client is not None:
                await client.disconnect()


class GoveePlugH5083Pairer(GoveePlugPairer):
    """Same pairing procedure as GoveePlugPairer (send aa b1 until the plug answers
    aa b1 01 <key>), but the H5083 needs the e7 handshake first and every frame is
    encrypted. The plug's light starts flashing once the handshake completes."""

    def __init__(self, device: BLEDevice) -> None:
        super().__init__(
            device,
            _H5083Session.RECV,
            _H5083Session.SEND,
            GoveePlugH5080.MSG_GET_AUTH_KEY,
        )

    async def begin(self):
        _LOGGER.info(f"%s: connecting to begin pairing", self._device.name)
        self._client = await establish_connection(
            BleakClient,
            self._device,
            f"{self._device.name} ({self._device.address})",
        )
        self._session = _H5083Session(self._client)
        await self._session.start()
        self._session.on_message = self._recv_handler
        await self._send_get_auth_key()

    async def _send_get_auth_key(self):
        # the plug answers aa b1 00 until its button is pressed; don't spin
        await asyncio.sleep(1)
        await self._client.write_gatt_char(
            self._send_uuid,
            _h5083_encrypt(self._session.key, self._auth_msg),
            response=False,
        )
