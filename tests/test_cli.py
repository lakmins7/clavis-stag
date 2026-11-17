import json
import socket
import subprocess
import sys

import pytest

from clavis_stag.cli import main
from clavis_stag.controls import ChannelState, encode_channel, update_channel
from clavis_stag.registers import ASC_DATA, ASC_SELECT, ASC_START, ASC_TESTPULSE


def test_module_entry_point():
    result = subprocess.run([sys.executable, "-m", "clavis_stag", "--help"],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert all(command in result.stdout for command in ("status", "set-channel", "apply", "set-testpulse"))


def test_dry_run_is_offline_and_contains_all_four_chips(monkeypatch, capsys):
    def fail(*args, **kwargs):
        pytest.fail("dry run must not open a socket")
    monkeypatch.setattr(socket, "socket", fail)
    assert main(["set-channel", "1", "threshold=0.5", "testpulse=true", "--dry-run"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert set(result["asic_payloads"]) == {"0", "1", "2", "3"}
    assert all(len(bytes.fromhex(frame)) == 80 for frame in result["asic_payloads"].values())
    assert result["testpulse_mask"] is None
    for asic in ("1", "2", "3"):
        assert bytes.fromhex(result["asic_payloads"][asic]) == bytes.fromhex("0000000bff") * 16


def test_status_only_reads_and_closes(board, client, monkeypatch, capsys):
    monkeypatch.setattr("clavis_stag.cli.create_board", lambda config: board)
    assert main(["--host", "192.0.2.1", "status"]) == 0
    assert json.loads(capsys.readouterr().out)["status_fresh"]
    assert client.closed and client.writes == []


@pytest.mark.parametrize("channel", ["1", "17", "33", "64"])
def test_channel_command_programs_defaults_on_other_chips(channel, board, client, monkeypatch, capsys):
    monkeypatch.setattr("clavis_stag.cli.create_board", lambda config: board)
    args = ["set-channel", channel, "threshold=0.5", "testpulse=true", "analog_output=true"]
    assert main([*args, "--dry-run"]) == 0
    preview = json.loads(capsys.readouterr().out)["asic_payloads"]
    assert main(["--host", "192.0.2.1", *args]) == 0
    status = json.loads(capsys.readouterr().out)
    assert [v for a, v in client.writes if a == ASC_SELECT] == [1, 0, 2, 0, 4, 0, 8, 0]
    assert sum(a == ASC_START for a, _ in client.writes) == 320
    expected = [ChannelState()] * 64
    expected[int(channel) - 1] = update_channel(ChannelState(), {
        "threshold": 0.5, "testpulse": True, "analog_output": True,
    })
    payload = bytes(v for a, v in client.writes if a == ASC_DATA)
    assert payload == b"".join(encode_channel(state) for state in expected)
    assert payload == b"".join(bytes.fromhex(preview[str(i)]) for i in range(4))
    assert status["channel_options"] == {str(i + 1): state.as_options() for i, state in enumerate(expected)}
    assert not any(a == ASC_TESTPULSE for a, _ in client.writes)
    assert client.closed


def test_apply_json_and_explicitly_disable_pulses(tmp_path, board, client, monkeypatch, capsys):
    path = tmp_path / "channels.json"
    path.write_text(json.dumps({str(i): {"threshold": 0.02, "testpulse": False} for i in range(1, 65)}))
    monkeypatch.setattr("clavis_stag.cli.create_board", lambda config: board)
    assert main(["--host", "192.0.2.1", "apply", str(path), "--testpulse-mask", "0"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert len(status["channel_options"]) == 64
    assert all(ch["threshold"] == 0.02 and not ch["testpulse"] for ch in status["channel_options"].values())
    assert client.writes[-1] == (ASC_TESTPULSE, 0)
    assert client.closed


def test_set_testpulse(board, client, monkeypatch, capsys):
    monkeypatch.setattr("clavis_stag.cli.create_board", lambda config: board)
    assert main(["--host", "192.0.2.1", "set-testpulse", "15"]) == 0
    assert client.writes == [(ASC_TESTPULSE, 15)]
    assert json.loads(capsys.readouterr().out)["global_options"]["testpulse_mask"] == 15


@pytest.mark.parametrize("args", [["status"], ["set-channel", "65", "threshold=0"],
                                  ["set-channel", "1", "testpulse=1"],
                                  ["set-channel", "1", "threshold=0", "threshold=1"],
                                  ["set-channel", "1", "threshold"]])
def test_invalid_input_does_not_connect(args, monkeypatch, capsys):
    def fail(config):
        pytest.fail("invalid input must not construct a board")
    monkeypatch.setattr("clavis_stag.cli.create_board", fail)
    assert main(args) == 1
    assert "clavis-stag:" in capsys.readouterr().err


def test_connection_error_is_reported_without_traceback(board, client, monkeypatch, capsys):
    client.fail_read = next(iter(client.registers))
    monkeypatch.setattr("clavis_stag.cli.create_board", lambda config: board)
    assert main(["--host", "192.0.2.1", "status"]) == 1
    assert "Failed to connect" in capsys.readouterr().err
    assert client.closed
