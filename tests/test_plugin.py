"""Configuration and factory checks; no physical board is required."""

import socket
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from clavis.core.factory import BoardFactory
from clavis.parameters.validation import validate_parameter_definitions
from clavis.plugins import BoardPluginRegistry
from clavis.schemas import BoardConfig
from clavis.transport.udp import UdpTransport
from clavis_stag.config import StagConfig
from clavis_stag.plugin import create_board, load_parameters, plugin
from clavis_stag.rbcp import RbcpRegisterClient


def test_factory_uses_defaults_without_network_access(monkeypatch) -> None:
    def unexpected_network(*args, **kwargs):
        pytest.fail("Board construction must not access the network")

    monkeypatch.setattr(socket, "socket", unexpected_network)
    monkeypatch.setattr(socket, "getaddrinfo", unexpected_network)
    board = create_board(
        StagConfig(board_id="stag-001", name="STAG v2", host="192.0.2.1")
    )
    assert isinstance(board.client, RbcpRegisterClient)
    assert isinstance(board.client.transport, UdpTransport)
    assert board.client.transport.host == "192.0.2.1"
    assert board.client.transport.port == 4660
    assert board.client.transport.timeout == 0.1
    assert board.spi_timeout == 1.0
    assert board.expected_firmware_id == 0x5354
    assert board.expected_firmware_version == "1.0"
    assert board.metadata.firmware_version == "1.0"
    assert board.metadata.board_type == "stag"


def test_clavis_factory_preserves_flat_plugin_settings() -> None:
    registry = BoardPluginRegistry()
    registry.register(plugin())
    config = BoardConfig.model_validate({
        "board_id": "stag-002",
        "board_type": "stag",
        "name": "Custom STAG",
        "host": "192.0.2.2",
        "port": 4661,
        "timeout": 0.5,
        "spi_timeout": 2.0,
        "firmware_id": 0x5354,
    })
    board = BoardFactory(registry).create(config)
    assert board.client.transport.host == "192.0.2.2"
    assert board.client.transport.port == 4661
    assert board.client.transport.timeout == 0.5
    assert board.spi_timeout == 2.0
    assert board.expected_firmware_id == 0x5354
    assert board.metadata.name == "Custom STAG"
    assert board.metadata.firmware_version == "1.0"
    assert board.metadata.asic == "AGASA_v3"
    assert board.metadata.fpga == "Artix_Ultrascale_Plus"


def test_explicit_firmware_expectation_is_preserved() -> None:
    board = create_board(StagConfig(
        board_id="stag-future", name="Future STAG", host="192.0.2.3",
        firmware_id=0x5355, firmware_version="2.0",
    ))
    assert board.expected_firmware_id == 0x5355
    assert board.expected_firmware_version == "2.0"


@pytest.mark.parametrize("options", [
    {"port": 0},
    {"port": 65536},
    {"timeout": 0},
    {"timeout": -1},
    {"timeout": float("inf")},
    {"spi_timeout": 0},
    {"spi_timeout": float("nan")},
    {"firmware_id": -1},
    {"firmware_id": 0x10000},
    {"host": ""},
    {"board_type": "samidare"},
])
def test_config_rejects_invalid_settings(options) -> None:
    values = {"board_id": "stag-001", "name": "STAG v2", "host": "192.0.2.1"}
    with pytest.raises(ValidationError):
        StagConfig.model_validate({**values, **options})


def test_parameters_are_valid_and_match_driver_defaults() -> None:
    from clavis_stag.controls import ChannelState

    definitions = load_parameters()
    validate_parameter_definitions(definitions)
    assert {p.name: p.defaultValue for p in definitions.channelParameters} == ChannelState().as_options()
    assert [p.name for p in definitions.generalParameters] == ["testpulse_mask"]
    assert {p.name for p in definitions.groupParameters} == {"testpulse_enabled", *ChannelState().as_options()}


def test_parameters_match_fee_control_software_reference() -> None:
    reference_root = Path(__file__).resolve().parents[2] / "FEE-Control-Software/devel/front-end-ui/public/FEE-Config"
    if not reference_root.exists():
        pytest.skip("FEE-Control-Software reference is only present in the Firmware workspace")
    board = json.loads((reference_root / "STAG_Board_v2.json").read_text())
    asic = json.loads((reference_root / "ASIC-Config/AGASA_v3.json").read_text())
    assert board["input_channels"] == 64
    assert board["ASIC"] == "AGASA_v3"
    assert asic["generalParameters"]["numberOfChannels"] == 16
    definitions = {p.name: p for p in load_parameters().channelParameters}
    assert set(definitions) == {p["name"] for p in asic["channelParameters"]}
    for expected in asic["channelParameters"]:
        actual = definitions[expected["name"]]
        assert actual.type == expected["type"]
        assert actual.defaultValue == expected["defaultValue"]
        assert actual.options == expected.get("options", [])
        assert actual.minimum == expected.get("min")
        assert actual.maximum == expected.get("max")
