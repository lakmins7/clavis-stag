# Configuration and operation

The plugin uses a flat Pydantic model and constructs
`UdpTransport` → `RbcpRegisterClient` → `StagBoard`. Creating a board opens no
socket. The board accepts an injected `RegisterClient` for testing.

| Field | Default | Meaning |
| --- | --- | --- |
| `board_id`, `name`, `host` | required | Identity, display name, RBCP hostname/IP |
| `board_type` | `stag` | Clavis plugin type |
| `port` | `4660` | RBCP UDP port |
| `timeout` | `0.1` | Positive, finite UDP timeout, seconds |
| `spi_timeout` | `1.0` | Positive, finite wait for each SPI idle transition, seconds |
| `firmware_id` | `21332` (`0x5354`) | Expected firmware ID |
| `firmware_version` | `1.0` | Expected firmware major.minor |
| `asic` | `AGASA_v3` | ASIC type |
| `fpga` | `Artix_Ultrascale_Plus` | Metadata |

Clavis's generic `unknown` metadata placeholders use STAG defaults.

Example `POST /api/boards` body:

```json
{
  "board_id": "stag-001",
  "board_type": "stag",
  "name": "STAG v2",
  "host": "192.168.10.16",
  "port": 4660,
  "timeout": 0.1,
  "spi_timeout": 1.0
}
```

After installing the plugin, restart `clavisd`. Existing Clavis commands work:

```bash
clavis boards types
clavis boards create board_type=stag board_id=stag-001 name=STAG host=192.168.10.16
clavis boards connect stag-001
clavis boards set-channel stag-001 1 threshold=0.5 testpulse=true
clavis boards set-group stag-001 1 threshold=0.3
clavis boards set-global stag-001 testpulse_mask=1
clavis boards set-group stag-001 0 testpulse_enabled=false
clavis boards status stag-001
clavis boards disconnect stag-001
```

A daemon-held board remembers partial channel edits for its lifetime. Python
callers may use `board.set_channels({"1": {...}, "2": {...}})` to merge several
edits and program each affected ASIC once. Other ASICs are untouched. No
Clavis-core changes or bulk HTTP endpoint are required.

The standalone `clavis-stag` command (also `python -m clavis_stag`) opens a
direct connection without a daemon. Its `set-channel` and `apply` commands
program all four chips using defaults for unspecified channels and settings.
`apply` takes a JSON file mapping channel IDs to option objects. Use one file
for all desired edits, since each invocation starts from defaults. Add
`--dry-run` to preview all four ASIC payloads offline. `--testpulse-mask` is
optional and leaves FPGA pulse generators unchanged when omitted; use `0` to
explicitly disable them. See the README for commands and installation.

| Channel parameter | Default | Allowed values / units |
| --- | --- | --- |
| `analog_output` | `false` | boolean |
| `polarity` | `normal` | `normal`, `inverted` |
| `testpulse` | `false` | boolean, ASIC injection bit |
| `threshold` | `1.8` | -1.8..1.8 V; quantized to a 10-bit DAC |
| `csa_res`, `pzc_res` | `32` | 32..256 in steps of 32 kΩ |
| `shp_res` | `7.5` | 7.5..60 in steps of 7.5 kΩ |
| `csa_cap`, `pzc_cap`, `shp_cap` | `250` | 250..4000 in steps of 250 fF |

Definitions and defaults match `FEE-Control-Software`'s STAG v2 and AGASA v3
JSON files. Every option is validated before any hardware writes. A first
channel update on an ASIC also programs defaults for its other 15 channels;
later updates merge with remembered settings. An empty edit performs no I/O.

Global `testpulse_mask` is an integer 0..15; bit 0 enables ASIC 0 and so on.
Group IDs `0`..`3` also accept all channel parameters: they merge the edit into
all 16 channels of that ASIC and transmit its image once. The additional boolean
`testpulse_enabled` controls that ASIC's FPGA mask bit. Set the global mask
before using `testpulse_enabled` because the FPGA mask is write-only. Group
mask edits preserve its other known bits.
The ASIC channel `testpulse` bit and FPGA generator mask are separate controls;
both must enable the relevant path for generated test pulses.

Connect opens the client and verifies the complete firmware word and readable
SPI status. Failed connections close the client. Connected status calls refresh
hardware; failures retain the last complete observation with
`status_fresh=false` and `status_error`. Channel options are remembered
configuration with no ASIC readback. Only successfully transmitted ASICs appear
in `channel_options`; interrupted or failed ASIC transfers are excluded.

A multi-ASIC edit may partially succeed: completed ASICs retain their new state;
the failing ASIC is uncertain; later ASICs are untouched. Status exposes
`applied_asics`, `uncertain_asics`, `programming_blocked`, and `programming_error`.
After an uncertain SPI transfer, reset the hardware and recreate the board
instance before reapplying a complete configuration. Reconnect alone does not
clear the block, because unread FIFO data cannot be verified or flushed through
the current interface. A failed mask write makes the remembered mask unknown;
write a complete global mask again to restore group editing.

Disconnect closes control only. It leaves test pulses, acquisition, and power
as configured. Power is hardwired and acquisition follows the firmware's TCP /
heartbeat behavior, so software power and RBCP acquisition start/stop raise an
unsupported-operation error. Common status booleans `powered` and `acquiring`
are schema placeholders: consult `power_known=false` and
`acquisition_known=false` instead of interpreting them as measurements.
