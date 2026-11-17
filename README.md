# clavis-stag

STAG v2 slow control plugin for Clavis. Verifies firmware, programs four
AGASA v3 ASICs (64 input channels), and controls FPGA test pulses through RBCP.
Use the standalone `clavis-stag` CLI or Clavis's shared CLI and daemon to control
the board.

The package follows
[clavis-samidare](https://github.com/claus-92/clavis-samidare):

```text
src/clavis_stag/
├── __init__.py
├── __main__.py
├── board.py
├── cli.py
├── config.py
├── controls.py
├── plugin.py
├── registers.py
├── rbcp.py
└── parameter_definitions/
    └── stag.json
```

`board.py` implements board operations; `config.py` validates connection settings;
`controls.py` validates and encodes AGASA settings; `plugin.py` registers the
board and constructs its client; `registers.py` defines the firmware address map.
The JSON file supplies parameter definitions to Clavis.

`rbcp.py` is the additional protocol client STAG needs. SAMIDARE uses the
Fakernet client supplied by Clavis; STAG uses SiTCP RBCP, which the local Clavis
package does not provide. The protocols are not interchangeable.

From the Firmware workspace, install into your Python environment:

```bash
python -m pip install -e Repos/clavis -e 'Repos/clavis-stag[test]'
```

Start `clavisd` in a separate terminal (restart it after installing the plugin).
Then replace the example IP with the board's IP:

```bash
clavis boards types
clavis boards create board_type=stag board_id=stag-001 name=STAG host=192.168.10.16
clavis boards connect stag-001
clavis boards status stag-001
clavis boards set-channel stag-001 1 threshold=0.5 testpulse=true
clavis boards set-global stag-001 testpulse_mask=1
clavis boards set-global stag-001 testpulse_mask=0
clavis boards disconnect stag-001
```

Channels are **1..64**, ASIC groups **0..3**. The daemon remembers successful
channel edits for the board instance's lifetime. The first update to an ASIC
programs defaults for its other channels; later edits preserve remembered
settings. ASIC configuration has no readback. Test pulses continue after
disconnect; set `testpulse_mask=0` to disable the FPGA generator. ASIC power
is hardwired; software power and RBCP acquisition start/stop are unsupported.

The standalone CLI needs no daemon:

```bash
clavis-stag --host 192.168.10.16 status
clavis-stag set-channel 1 threshold=0.5 testpulse=true --dry-run
clavis-stag --host 192.168.10.16 set-channel 1 threshold=0.5 testpulse=true --testpulse-mask 1
clavis-stag --host 192.168.10.16 apply channels.json --testpulse-mask 0
```

`python -m clavis_stag` accepts the same arguments. `apply` reads a JSON object
mapping channel IDs to parameter objects, for example
`{"1": {"threshold": 0.02, "testpulse": false}}`.
Standalone `set-channel` and `apply` explicitly program all four chips, resetting
unspecified channels and settings to defaults. Keep all desired channel edits
in one JSON file. `--dry-run` previews all four images without contacting the
board. Omitting `--testpulse-mask` leaves the FPGA generators unchanged; pass
`0` to disable them. Shared daemon commands retain their incremental behavior.

After updating the source, reinstall the editable package to register its
console command, then refresh your shell's command cache (`rehash` in zsh).

Run the automated tests without a board:

```bash
python -m pytest Repos/clavis-stag/tests -q
```

See [configuration](docs/configuration.md) and [hardware](docs/hardware.md) for
parameter units, SPI behavior, firmware references, and recovery after a failure.
