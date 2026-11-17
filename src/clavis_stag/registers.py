"""STAG-StrTDC defBusAddressMap/defAGASAController/defBusController map."""


def rbcp_address(module_id: int, local_address: int) -> int:
    if not 0 <= module_id <= 15 or not 0 <= local_address <= 0xFFF:
        raise ValueError("Invalid STAG module/local address")
    return (module_id << 28) | (local_address << 16)


FIRMWARE_BYTES = tuple(rbcp_address(0xE, 0x010 + i) for i in range(4))
ASC_DATA = rbcp_address(0, 0x000)
ASC_BUSY = rbcp_address(0, 0x200)
ASC_START = rbcp_address(0, 0x300)
ASC_SELECT = rbcp_address(0, 0x400)
ASC_TESTPULSE = rbcp_address(0, 0x500)
