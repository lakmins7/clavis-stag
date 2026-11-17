import pytest

from clavis.core.errors import BoardConnectionError, BoardNotConnectedError, InvalidParameterError
from clavis_stag.board import UnsupportedOperationError
from clavis_stag.controls import ChannelState, encode_asic, update_channel
from clavis_stag.registers import ASC_BUSY, ASC_DATA, ASC_SELECT, ASC_START, ASC_TESTPULSE, FIRMWARE_BYTES


def test_connect_checks_identity_and_disconnect_only_closes(board, client):
    board.connect()
    assert client.reads == [*FIRMWARE_BYTES, ASC_BUSY]
    status = board.get_status()
    assert status.connected and status.status_fresh
    assert status.global_options["firmware_id"] == "0x5354"
    assert status.global_options["spi_busy"] is False
    board.disconnect()
    assert client.closed and not board.get_status().status_fresh
    assert client.writes == []


@pytest.mark.parametrize("failure", ["open", "read", "id", "version"])
def test_failed_connection_closes_client(board, client, failure, monkeypatch):
    if failure == "open":
        def fail():
            raise OSError("open failed")
        monkeypatch.setattr(client, "open", fail)
    elif failure == "read":
        client.fail_read = ASC_BUSY
    else:
        client.registers[FIRMWARE_BYTES[3 if failure == "id" else 0]] ^= 1
    with pytest.raises(BoardConnectionError):
        board.connect()
    assert client.closed and not board.connected and not board.status_fresh
    assert client.writes == []


def test_stale_status_retains_last_complete_observation(board, client):
    board.connect()
    observed = board.get_status().global_options.copy()
    client.registers[ASC_BUSY] = 1
    client.fail_read = FIRMWARE_BYTES[2]
    stale = board.get_status()
    assert stale.connected and not stale.status_fresh
    assert stale.global_options["spi_busy"] == observed["spi_busy"]
    assert "read failed" in stale.global_options["status_error"]
    client.fail_read = None
    fresh = board.get_status()
    assert fresh.status_fresh and fresh.global_options["spi_busy"]


def test_complete_spi_sequence_and_partial_update(board, client):
    board.connect()
    board.set_channel("17", {"threshold": 0.5})
    states = [ChannelState()] * 16
    states[0] = update_channel(states[0], {"threshold": 0.5})
    expected = [(ASC_SELECT, 2)]
    for value in encode_asic(states):
        expected.extend([(ASC_DATA, value), (ASC_START, 1)])
    expected.append((ASC_SELECT, 0))
    assert client.writes == expected
    board.set_channel("18", {"testpulse": True})
    status = board.get_status()
    assert status.channel_options["17"]["threshold"] == 0.5
    assert status.channel_options["18"]["testpulse"]
    assert set(status.channel_options) == {str(i) for i in range(17, 33)}


def test_bulk_edits_program_each_affected_asic_once(board, client):
    board.connect()
    board.set_channels({"1": {"threshold": 0}, "2": {"threshold": 0.2}, "64": {"csa_res": 64}})
    assert [v for a, v in client.writes if a == ASC_SELECT] == [1, 0, 8, 0]
    assert sum(a == ASC_START for a, _ in client.writes) == 160


def test_validation_happens_before_any_write(board, client):
    board.connect()
    with pytest.raises(InvalidParameterError):
        board.set_channels({"1": {"threshold": 0}, "64": {"csa_res": 33}})
    assert client.writes == []
    assert board.get_status().channel_options == {}


def test_failed_transfer_marks_unknown_and_deselects(board, client):
    board.connect()
    board.set_channel("1", {"threshold": 0.1})
    client.fail_write = ASC_START
    with pytest.raises(BoardConnectionError, match="uncertain"):
        board.set_channel("1", {"threshold": 0.2})
    assert client.writes[-1] == (ASC_SELECT, 0)
    status = board.get_status()
    assert status.global_options["uncertain_asics"] == [0]
    assert status.global_options["programming_blocked"]
    assert status.channel_options == {}
    assert board._channels[0].threshold == 0.1
    before = len(client.writes)
    with pytest.raises(BoardConnectionError, match="reset"):
        board.set_channel("17", {"threshold": 0})
    assert len(client.writes) == before


def test_bulk_failure_preserves_successfully_applied_asics(board, client, monkeypatch):
    board.connect()
    original = client.write_register
    def fail_second_chip(address, value):
        if address == ASC_SELECT and value == 2:
            raise OSError("second chip failed")
        original(address, value)
    monkeypatch.setattr(client, "write_register", fail_second_chip)
    with pytest.raises(BoardConnectionError):
        board.set_channels({"1": {"threshold": 0.1}, "17": {"threshold": 0.2}})
    status = board.get_status()
    assert status.channel_options["1"]["threshold"] == 0.1
    assert "17" not in status.channel_options
    assert status.global_options["uncertain_asics"] == [1]


def test_busy_timeout(board, client):
    board.connect()
    client.registers[ASC_BUSY] = 1
    with pytest.raises(BoardConnectionError, match="stayed busy"):
        board.set_channel("1", {"threshold": 0})
    assert client.writes == []


def test_interrupted_transfer_is_uncertain(board, client, monkeypatch):
    board.connect()
    original = client.write_register
    def interrupt(address, value):
        if address == ASC_START:
            raise KeyboardInterrupt()
        original(address, value)
    monkeypatch.setattr(client, "write_register", interrupt)
    with pytest.raises(KeyboardInterrupt):
        board.set_channel("1", {"threshold": 0})
    assert client.writes[-1] == (ASC_SELECT, 0)
    assert board.get_status().global_options["programming_blocked"]


def test_busy_timeout_after_start_deselects_and_marks_uncertain(board, client, monkeypatch):
    board.connect()
    original = client.write_register
    def start_busy(address, value):
        original(address, value)
        if address == ASC_START:
            client.registers[ASC_BUSY] = 1
    monkeypatch.setattr(client, "write_register", start_busy)
    with pytest.raises(BoardConnectionError, match="stayed busy"):
        board.set_channel("1", {"threshold": 0})
    assert client.writes[-1] == (ASC_SELECT, 0)
    assert sum(a == ASC_START for a, _ in client.writes) == 1
    status = board.get_status()
    assert status.global_options["uncertain_asics"] == [0]
    assert status.channel_options == {}


def test_write_only_testpulse_global_and_group(board, client):
    board.connect()
    with pytest.raises(InvalidParameterError, match="Initialize"):
        board.set_group("0", {"testpulse_enabled": True})
    board.set_global({"testpulse_mask": 4})
    board.set_group("0", {"testpulse_enabled": True})
    board.set_group("2", {"testpulse_enabled": False})
    assert client.writes == [(ASC_TESTPULSE, 4), (ASC_TESTPULSE, 5), (ASC_TESTPULSE, 1)]
    assert board.get_status().group_options["0"]["testpulse_enabled"]
    client.fail_write = ASC_TESTPULSE
    with pytest.raises(BoardConnectionError):
        board.set_global({"testpulse_mask": 0})
    assert "testpulse_mask" not in board.get_status().global_options


def test_unsupported_operations_and_disconnected_write(board):
    for operation in (board.power_on, board.power_off, board.start_acquisition, board.stop_acquisition):
        with pytest.raises(UnsupportedOperationError):
            operation()
    with pytest.raises(BoardNotConnectedError):
        board.set_channel("1", {"threshold": 0})


def test_group_programs_all_sixteen_channels_once(board, client):
    board.connect()
    board.set_channel("33", {"analog_output": True})
    client.writes.clear()
    board.set_group("2", {"threshold": 0.5})
    assert [v for a, v in client.writes if a == ASC_SELECT] == [4, 0]
    assert sum(a == ASC_START for a, _ in client.writes) == 80
    status = board.get_status()
    assert all(status.channel_options[str(i)]["threshold"] == 0.5 for i in range(33, 49))
    assert status.channel_options["33"]["analog_output"]


def test_group_validates_mask_and_channels_before_spi(board, client):
    board.connect()
    with pytest.raises(InvalidParameterError):
        board.set_group("0", {"threshold": 0.5, "testpulse_enabled": True})
    board.set_global({"testpulse_mask": 0})
    client.writes.clear()
    with pytest.raises(InvalidParameterError):
        board.set_group("0", {"threshold": 2, "testpulse_enabled": True})
    assert client.writes == []
