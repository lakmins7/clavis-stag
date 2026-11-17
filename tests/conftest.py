import pytest

from clavis.schemas import BoardMetadata
from clavis_stag.board import StagBoard
from clavis_stag.registers import ASC_BUSY, FIRMWARE_BYTES


class RecordingClient:
    def __init__(self):
        self.registers = dict(zip(FIRMWARE_BYTES, bytes.fromhex("00015453")))
        self.registers[ASC_BUSY] = 0
        self.writes = []
        self.reads = []
        self.opened = False
        self.closed = False
        self.fail_read = None
        self.fail_write = None

    def open(self):
        self.opened = True

    def close(self):
        self.closed = True

    def read_register(self, address):
        self.reads.append(address)
        if address == self.fail_read:
            raise OSError("read failed")
        return self.registers[address]

    def write_register(self, address, value):
        self.writes.append((address, value))
        if address == self.fail_write:
            raise OSError("write failed")


@pytest.fixture
def client():
    return RecordingClient()


@pytest.fixture
def board(client):
    return StagBoard(BoardMetadata(board_id="stag-test", name="Test STAG", board_type="stag",
                                   asic="AGASA_v3", fpga="Artix_Ultrascale_Plus", firmware_version="1.0"),
                     client, spi_timeout=0.002)
