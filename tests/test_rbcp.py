import socket

import pytest

from clavis_stag.rbcp import ACK, BUS_ERROR, HEADER, READ, WRITE, RbcpError, RbcpRegisterClient
from clavis_stag.registers import FIRMWARE_BYTES, rbcp_address


class ReplyTransport:
    def __init__(self):
        self.requests = []
        self.closed = False
        self.replies = []
        self.change_reply = lambda reply: reply

    def open(self):
        pass

    def close(self):
        self.closed = True

    def send(self, data):
        self.requests.append(data)

    def receive(self):
        if self.replies:
            return self.replies.pop(0)
        typ, cmd, ident, size, address = HEADER.unpack(self.requests[-1][:8])
        payload = self.requests[-1][8:] if cmd == WRITE else bytes(range(size))
        return self.change_reply(HEADER.pack(typ, cmd | ACK, ident, size, address) + payload)


@pytest.fixture
def rbcp():
    transport = ReplyTransport()
    client = RbcpRegisterClient(transport)
    client.open()
    return client, transport


def test_read_write_and_full_address_mapping(rbcp):
    client, transport = rbcp
    assert FIRMWARE_BYTES == (0xE0100000, 0xE0110000, 0xE0120000, 0xE0130000)
    assert client.read_register(FIRMWARE_BYTES[0]) == 0
    assert transport.requests[-1] == bytes.fromhex("ffc00001e0100000")
    client.write_register(rbcp_address(0, 0x400), 8)
    assert transport.requests[-1] == bytes.fromhex("ff8001010400000008")
    assert client.read(0, 3) == b"\x00\x01\x02"
    client.write(0, b"\x12\x34")
    client.close()
    assert transport.closed and not client.is_open
    with pytest.raises(RbcpError, match="not open"):
        client.read_register(0)


@pytest.mark.parametrize("change, match", [
    (lambda r: r[:7], "Short"),
    (lambda r: bytes([0]) + r[1:], "type/address"),
    (lambda r: r[:7] + bytes([r[7] ^ 1]) + r[8:], "type/address"),
    (lambda r: r[:1] + bytes([r[1] & ~ACK]) + r[2:], "acknowledgment"),
    (lambda r: r[:3] + bytes([2]) + r[4:], "length"),
    (lambda r: r[:-1], "length"),
    (lambda r: r + b"\x00", "length"),
    (lambda r: r[:1] + bytes([r[1] | BUS_ERROR]) + r[2:], "bus error"),
])
def test_reject_malformed_replies(rbcp, change, match):
    client, transport = rbcp
    transport.change_reply = change
    with pytest.raises(RbcpError, match=match):
        client.read_register(0)


def test_write_echo_validation_and_no_retry(rbcp):
    client, transport = rbcp
    transport.change_reply = lambda r: r[:8] + b"\xff"
    with pytest.raises(RbcpError, match="echo"):
        client.write_register(0, 1)
    assert len(transport.requests) == 1


def test_stale_packet_is_ignored_and_ids_wrap(rbcp):
    client, transport = rbcp
    transport.replies.append(HEADER.pack(0xFF, READ | ACK, 250, 1, 0) + b"\xff")
    assert client.read_register(0) == 0
    for _ in range(256):
        client.read_register(0)
    assert HEADER.unpack(transport.requests[-1][:8])[2] == 0


def test_timeout_and_unmatched_packets_never_retry_write(rbcp, monkeypatch):
    client, transport = rbcp
    def fail():
        raise socket.timeout("timed out")
    monkeypatch.setattr(transport, "receive", fail)
    with pytest.raises(RbcpError, match="transport failure"):
        client.write_register(0, 1)
    assert len(transport.requests) == 1
    monkeypatch.setattr(transport, "receive", lambda: HEADER.pack(0xFF, READ | ACK, 200, 1, 0) + b"\0")
    with pytest.raises(RbcpError, match="No matching"):
        client.read_register(0)


@pytest.mark.parametrize("address,length", [(-1, 1), (2**32, 1), (True, 1), (0, 0), (0, 256),
                                          (0xFFFFFFFF, 2), (0, True)])
def test_invalid_access_is_rejected_before_send(rbcp, address, length):
    client, transport = rbcp
    with pytest.raises(ValueError):
        client.read(address, length)
    assert transport.requests == []


@pytest.mark.parametrize("value", [-1, 256, True, 1.0])
def test_register_values_are_bytes(rbcp, value):
    client, transport = rbcp
    with pytest.raises(ValueError):
        client.write_register(0, value)
    assert transport.requests == []


def test_open_failure_closes_transport(monkeypatch):
    transport = ReplyTransport()
    def fail():
        raise OSError("open failed")
    monkeypatch.setattr(transport, "open", fail)
    client = RbcpRegisterClient(transport)
    with pytest.raises(OSError):
        client.open()
    assert transport.closed and not client.is_open
