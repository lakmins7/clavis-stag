# Hardware and protocol

Target: STAG v2, four AGASA v3 ASICs, 64 input channels. Configuration values
come from `Repos/FEE-Control-Software/devel/front-end-ui/public/FEE-Config/`:
`STAG_Board_v2.json` and `ASIC-Config/AGASA_v3.json`.
Register semantics come from `Repos/STAG-StrTDC/hdl/stag-common/`:
`defBusAddressMap.vhd`, `defAGASAController.vhd`, and `AGASAController.vhd`.
The byte-at-a-time SPI sequence matches `tools/rbcp_smoke_test.py`.

RBCP uses full 32-bit byte addresses:
`(module_id << 28) | (local_address << 16)`. Consecutive local registers are
0x10000 apart. Do not read the firmware word with an ordinary contiguous
four-byte RBCP read; each byte needs its own mapped address.

| Register | Full RBCP address | Access |
| --- | --- | --- |
| ASC data FIFO | `0x00000000` | write byte |
| ASC busy | `0x02000000` | read bit 0 |
| ASC start cycle | `0x03000000` | write 1 |
| ASC chip select | `0x04000000` | write ASIC mask bits 0..3 |
| ASC test pulse | `0x05000000` | write generator mask bits 0..3 |
| BCT version bytes | `0xE0100000`..`0xE0130000`, stride `0x10000` | read little endian |

The expected BCT word is `0x53540100` (ID `0x5354`, major 1, minor 0).
The module/local mapping lives in `registers.py`; `rbcp.py` handles protocol
headers independently. Its `read_register` / `write_register` methods access one
byte; `read` / `write` buffers use ordinary RBCP contiguous-address semantics.
RBCP checks type, command/ACK, transaction ID, address, payload length, write
echo, and bus-error responses. Old transaction IDs are skipped with a bounded
wait. Writes are never automatically retried, since they may have side effects.

Board channel IDs 1..16 map to ASIC 0 local channels 0..15; 17..32 to ASIC 1;
33..48 to ASIC 2; 49..64 to ASIC 3. Each ASIC image contains 16 channel words
in ascending local-channel order, five bytes per channel, 80 bytes total.

A channel word contains:

```text
0 + CSA resistance[3] + CSA capacitance[4]
0 + SHP resistance[3] + SHP capacitance[4]
0 + PZC resistance[3] + PZC capacitance[4]
000 + testpulse[1] + normal-polarity[1] + analog-output[1] + DAC[10]
```

Discrete components encode their zero-based choice index. DAC code is
`floor((threshold + 1.8) * 1023 / 3.6)`, evaluated with decimal arithmetic.
Words and bytes transmit most significant bit first. A default channel encodes
as `00 00 00 0B FF`; a default ASIC repeats that word 16 times.

Programming waits for idle, selects one ASIC, then for every byte waits for
idle, writes data, writes start=1, and waits for idle again. It deselects the
ASIC in cleanup, including errors and keyboard interrupts. This follows the
current HDL's separate eight-bit SPI cycles and avoids assuming FIFO depth or
queuing a whole frame. Failure blocks further ASIC programming on that board
instance because FIFO/frame state is uncertain. No firmware error register or
ASIC configuration readback exists; diagnostics report busy and software
protocol/programming errors, not a measured ASIC configuration.

Software tests cover known payloads, channel mapping, byte-write sequences,
bulk edits, timeout and interrupt behavior, connection cleanup, stale status,
and malformed RBCP replies. They do not establish physical analog behavior.
For a board check, use `clavis boards status stag-001`, configure channels with
`clavis boards set-channel`, and verify the resulting threshold/pulse behavior
with your measurement setup. For offline inspection, use `prepare_updates`
from `clavis_stag.controls` to build ASIC payloads without a connection. Power
enable is hardwired in the current top-level HDL.
