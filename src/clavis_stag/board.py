"""STAG v2 control through an injected byte-register client."""

from copy import deepcopy
from threading import RLock
import time
from typing import Any

from clavis.boards.base import AcquisitionBoard
from clavis.clients.base import RegisterClient
from clavis.core.errors import (
    BoardAlreadyConnectedError, BoardConnectionError, BoardNotConnectedError,
    ClavisError, InvalidParameterError,
)
from clavis.schemas import BoardMetadata, BoardStatus
from clavis_stag.controls import ChannelState, prepare_updates
from clavis_stag.registers import (
    ASC_BUSY, ASC_DATA, ASC_SELECT, ASC_START, ASC_TESTPULSE, FIRMWARE_BYTES,
)


class UnsupportedOperationError(ClavisError):
    status_code = 501


class StagBoard(AcquisitionBoard):
    def __init__(
        self, metadata: BoardMetadata, client: RegisterClient, *,
        firmware_id: int = 0x5354, spi_timeout: float = 1.0,
    ) -> None:
        self.metadata = metadata
        self.client = client
        self.expected_firmware_id = firmware_id
        self.expected_firmware_version = metadata.firmware_version
        self.spi_timeout = spi_timeout
        self.connected = False
        self.status_fresh = False
        self.status_error: str | None = None
        self._observed: dict[str, Any] = {}
        self._channels = [ChannelState() for _ in range(64)]
        self._applied_asics: set[int] = set()
        self._uncertain_asics: set[int] = set()
        self._programming_blocked = False
        self._programming_error: str | None = None
        self._testpulse_mask: int | None = None
        self._lock = RLock()

    def connect(self) -> None:
        with self._lock:
            if self.connected:
                raise BoardAlreadyConnectedError(f"Board already connected: {self.get_id()}")
            try:
                self.client.open()
                self._refresh_hardware_status()
            except Exception as exc:
                self.connected = False
                self.status_fresh = False
                self.status_error = str(exc)
                try:
                    self.client.close()
                except Exception:
                    pass
                raise BoardConnectionError(f"Failed to connect STAG: {exc}") from exc
            self.connected = True

    def disconnect(self) -> None:
        with self._lock:
            self._require_connected()
            try:
                self.client.close()
            except Exception as exc:
                raise BoardConnectionError(f"Failed to disconnect STAG: {exc}") from exc
            finally:
                self.connected = False
                self.status_fresh = False
                # Write-only control may change while another controller owns it.
                self._testpulse_mask = None

    def power_on(self) -> None:
        raise UnsupportedOperationError("STAG ASIC power is hardwired on; no RBCP power switch exists")

    def power_off(self) -> None:
        raise UnsupportedOperationError("STAG ASIC power is hardwired on; no RBCP power switch exists")

    def start_acquisition(self) -> None:
        raise UnsupportedOperationError("STAG DAQ follows TCP link and heartbeat frame state; RBCP start is unsupported")

    def stop_acquisition(self) -> None:
        raise UnsupportedOperationError("STAG DAQ follows TCP link and heartbeat frame state; RBCP stop is unsupported")

    def set_channel(self, channel_id: str, options: dict[str, Any]) -> None:
        self.set_channels({channel_id: options})

    def set_channels(self, updates: dict[str, dict[str, Any]]) -> None:
        """Merge edits and program each affected ASIC once (plugin extension)."""
        with self._lock:
            self._require_connected()
            if self._programming_blocked:
                raise BoardConnectionError("SPI state is uncertain: reset the board and recreate its Clavis instance before programming")
            try:
                candidate, frames = prepare_updates(self._channels, updates)
            except ValueError as exc:
                raise InvalidParameterError(str(exc)) from exc
            if not frames:
                return
            self._check_hardware()
            for asic, payload in frames.items():
                try:
                    self._program_asic(asic, payload)
                except BaseException as exc:
                    # An interrupt can also leave an incomplete FIFO/frame.
                    self._uncertain_asics.add(asic)
                    self._programming_blocked = True
                    self._programming_error = str(exc) or type(exc).__name__
                    if not isinstance(exc, Exception):
                        raise
                    raise BoardConnectionError(f"ASIC {asic} programming failed; configuration uncertain: {exc}") from exc
                start = asic * 16
                self._channels[start:start + 16] = candidate[start:start + 16]
                self._applied_asics.add(asic)
            self._programming_error = None

    def set_group(self, group_id: str, options: dict[str, Any]) -> None:
        with self._lock:
            self._require_connected()
            if group_id not in {"0", "1", "2", "3"}:
                raise InvalidParameterError("ASIC group ID must be 0..3")
            if not options:
                return
            channel_options = {key: value for key, value in options.items() if key != "testpulse_enabled"}
            updates = {str(i + 1): channel_options for i in range(int(group_id) * 16, (int(group_id) + 1) * 16)}
            try:
                prepare_updates(self._channels, updates)
            except ValueError as exc:
                raise InvalidParameterError(str(exc)) from exc
            mask = None
            if "testpulse_enabled" in options:
                if type(options["testpulse_enabled"]) is not bool:
                    raise InvalidParameterError("testpulse_enabled must be boolean")
                if self._testpulse_mask is None:
                    raise InvalidParameterError("Initialize the write-only testpulse_mask with set-global before editing its group bit")
                bit = 1 << int(group_id)
                mask = (self._testpulse_mask | bit) if options["testpulse_enabled"] else (self._testpulse_mask & ~bit)
            if channel_options:
                self.set_channels(updates)
            if mask is not None:
                self.set_global({"testpulse_mask": mask})

    def set_global(self, options: dict[str, Any]) -> None:
        with self._lock:
            self._require_connected()
            if not options:
                return
            value = options.get("testpulse_mask")
            if set(options) != {"testpulse_mask"} or type(value) is not int or not 0 <= value <= 15:
                raise InvalidParameterError("Global accepts only integer testpulse_mask (0..15)")
            self._check_hardware()
            self._testpulse_mask = None
            try:
                self.client.write_register(ASC_TESTPULSE, value)
            except Exception as exc:
                raise BoardConnectionError(f"Test-pulse write failed; mask is unknown: {exc}") from exc
            self._testpulse_mask = value

    def get_id(self) -> str:
        return self.metadata.board_id

    def get_status(self) -> BoardStatus:
        with self._lock:
            if self.connected:
                try:
                    self._refresh_hardware_status()
                except Exception as exc:
                    self.status_fresh = False
                    self.status_error = str(exc)
            options = {
                **self._observed,
                "status_error": self.status_error,
                "power_known": False,
                "power_control_supported": False,
                "acquisition_known": False,
                "acquisition_control_supported": False,
                "channel_count": 64,
                "asic_count": 4,
                "configuration_readback_supported": False,
                "configuration_source": "remembered; no ASIC readback",
                "spi_error_register_supported": False,
                "applied_asics": sorted(self._applied_asics),
                "uncertain_asics": sorted(self._uncertain_asics),
                "programming_blocked": self._programming_blocked,
                "programming_error": self._programming_error,
            }
            groups = {}
            if self._testpulse_mask is not None:
                options["testpulse_mask"] = self._testpulse_mask
                groups = {str(i): {"testpulse_enabled": bool(self._testpulse_mask & (1 << i))} for i in range(4)}
            return BoardStatus(
                board_id=self.get_id(), connected=self.connected,
                # The common schema requires booleans; known flags above
                # explicitly distinguish these placeholders from measurements.
                powered=False, acquiring=False, status_fresh=self.status_fresh,
                global_options=deepcopy(options), group_options=groups,
                channel_options={str(i + 1): channel.as_options() for i, channel in enumerate(self._channels)
                                 if i // 16 in self._applied_asics - self._uncertain_asics},
            )

    def _require_connected(self) -> None:
        if not self.connected:
            raise BoardNotConnectedError(f"Board not connected: {self.get_id()}")

    def _check_hardware(self) -> None:
        try:
            self._refresh_hardware_status()
        except Exception as exc:
            self.status_fresh = False
            self.status_error = str(exc)
            raise BoardConnectionError(f"STAG hardware check failed: {exc}") from exc

    def _refresh_hardware_status(self) -> None:
        word = sum(self.client.read_register(address) << (8 * i) for i, address in enumerate(FIRMWARE_BYTES))
        firmware_id = word >> 16
        version = f"{(word >> 8) & 255}.{word & 255}"
        if firmware_id != self.expected_firmware_id or version != self.expected_firmware_version:
            raise ValueError(f"Firmware mismatch: got ID 0x{firmware_id:04x} version {version}; expected ID 0x{self.expected_firmware_id:04x} version {self.expected_firmware_version}")
        busy = bool(self.client.read_register(ASC_BUSY) & 1)
        self._observed = {"firmware_id": f"0x{firmware_id:04x}", "firmware_version": version,
                          "firmware_verified": True, "spi_busy": busy}
        self.metadata.firmware_version = version
        self.status_fresh = True
        self.status_error = None

    def _wait_idle(self) -> None:
        deadline = time.monotonic() + self.spi_timeout
        while True:
            if not self.client.read_register(ASC_BUSY) & 1:
                return
            if time.monotonic() >= deadline:
                raise TimeoutError("AGASA SPI engine stayed busy")
            time.sleep(min(0.001, max(0, deadline - time.monotonic())))

    def _program_asic(self, asic: int, payload: bytes) -> None:
        self._wait_idle()
        try:
            self.client.write_register(ASC_SELECT, 1 << asic)
            # Follow tools/rbcp_smoke_test.py: byte-at-a-time SPI cycles,
            # avoiding assumptions about an undocumented FIFO capacity.
            for value in payload:
                self._wait_idle()
                self.client.write_register(ASC_DATA, value)
                self.client.write_register(ASC_START, 1)
                self._wait_idle()
        finally:
            self.client.write_register(ASC_SELECT, 0)
