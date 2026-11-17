"""SiTCP RBCP byte access. No automatic retries of side-effecting writes."""

import struct
from threading import RLock
import time

from clavis.transport.base import Transport


HEADER = struct.Struct("!BBBBI")
READ, WRITE, ACK, BUS_ERROR = 0xC0, 0x80, 0x08, 0x01


class RbcpError(RuntimeError):
    """Protocol/transport failure; a failed write may have reached hardware."""


class RbcpRegisterClient:
    def __init__(self, transport: Transport, *, timeout: float = 0.1) -> None:
        self.transport = transport
        self.timeout = timeout
        self.is_open = False
        self._packet_id = 0
        self._lock = RLock()

    def open(self) -> None:
        with self._lock:
            if self.is_open:
                return
            try:
                self.transport.open()
            except Exception:
                self.transport.close()
                raise
            self.is_open = True

    def close(self) -> None:
        with self._lock:
            try:
                self.transport.close()
            finally:
                self.is_open = False

    def read_register(self, address: int) -> int:
        return self.read(address, 1)[0]

    def write_register(self, address: int, value: int) -> None:
        if type(value) is not int or not 0 <= value <= 255:
            raise ValueError("RBCP register value must be a byte (0..255)")
        self.write(address, bytes([value]))

    def read(self, address: int, length: int = 1) -> bytes:
        return self._request(READ, address, length, b"")

    def write(self, address: int, data: bytes) -> None:
        payload = bytes(data)
        self._request(WRITE, address, len(payload), payload)

    def _request(self, command: int, address: int, length: int, payload: bytes) -> bytes:
        if type(address) is not int or not 0 <= address <= 0xFFFFFFFF:
            raise ValueError("RBCP address must be an unsigned 32-bit integer")
        if type(length) is not int or not 1 <= length <= 255:
            raise ValueError("RBCP length must be 1..255 bytes")
        if address + length - 1 > 0xFFFFFFFF:
            raise ValueError("RBCP access crosses the address boundary")
        with self._lock:
            if not self.is_open:
                raise RbcpError("RBCP client is not open")
            packet_id = self._packet_id
            self._packet_id = (packet_id + 1) & 0xFF
            request = HEADER.pack(0xFF, command, packet_id, length, address) + payload
            deadline = time.monotonic() + self.timeout
            try:
                self.transport.send(request)
                for _ in range(32):
                    reply = self.transport.receive()
                    if len(reply) < HEADER.size:
                        raise RbcpError("Short RBCP reply")
                    typ, cmd, rid, size, addr = HEADER.unpack(reply[:HEADER.size])
                    if rid != packet_id:
                        if time.monotonic() >= deadline:
                            break
                        continue
                    if typ != 0xFF or addr != address:
                        raise RbcpError("RBCP response type/address mismatch")
                    if cmd == (command | ACK | BUS_ERROR):
                        raise RbcpError(f"RBCP bus error at 0x{address:08x}")
                    if cmd != (command | ACK):
                        raise RbcpError("RBCP command/acknowledgment mismatch")
                    if size != length or len(reply) != HEADER.size + length:
                        raise RbcpError("RBCP response payload length mismatch")
                    data = reply[HEADER.size:]
                    if command == WRITE and data != payload:
                        raise RbcpError("RBCP write echo mismatch")
                    return data
            except OSError as exc:
                raise RbcpError(f"RBCP transport failure at 0x{address:08x}: {exc}") from exc
            raise RbcpError(f"No matching RBCP reply at 0x{address:08x}")
