"""Clavis entry point and packaged parameter discovery."""

import json
from importlib.resources import files

from pydantic import BaseModel

from clavis.plugins import BoardPlugin
from clavis.schemas import BoardMetadata, ParameterDefinitions
from clavis.transport.udp import UdpTransport
from clavis_stag.board import StagBoard
from clavis_stag.config import StagConfig
from clavis_stag.rbcp import RbcpRegisterClient


def create_board(config: BaseModel) -> StagBoard:
    validated = StagConfig.model_validate(config.model_dump())
    transport = UdpTransport(
        host=validated.host,
        port=validated.port,
        timeout=validated.timeout,
    )
    client = RbcpRegisterClient(transport, timeout=validated.timeout)
    metadata = BoardMetadata(
        board_id=validated.board_id,
        name=validated.name,
        asic=validated.asic,
        fpga=validated.fpga,
        firmware_version=validated.firmware_version,
        board_type=validated.board_type,
    )
    return StagBoard(
        metadata,
        client,
        firmware_id=validated.firmware_id,
        spi_timeout=validated.spi_timeout,
    )


def load_parameters() -> ParameterDefinitions:
    path = files("clavis_stag") / "parameter_definitions" / "stag.json"
    return ParameterDefinitions.model_validate(
        json.loads(path.read_text(encoding="utf-8"))
    )


def plugin() -> BoardPlugin:
    return BoardPlugin(
        board_type="stag",
        display_name="STAG",
        config_model=StagConfig,
        create_board=create_board,
        load_parameters=load_parameters,
    )
