# clavis-stag

STAG v2 slow control plugin for Clavis. Verifies firmware, programs four
AGASA v3 ASICs (64 input channels), and controls FPGA test pulses through RBCP.
Use Clavis's shared CLI and daemon to control the board.

The package follows
[clavis-samidare](https://github.com/claus-92/clavis-samidare):

```text
src/clavis_stag/
├── __init__.py
├── board.py
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

## Initial installation

Install Git and Python 3.10 or newer. From a directory of your choice, create a
workspace named `clavis-slowcontrol` and clone both repositories into it:

```bash
mkdir clavis-slowcontrol
cd clavis-slowcontrol
git clone https://github.com/claus-92/clavis.git
git clone https://github.com/lakmins7/clavis-stag.git
```

Create one virtual environment for both packages, then install Clavis first and
the STAG plugin second:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ./clavis
python -m pip install -e './clavis-stag[test]'
python -m pip show clavis clavis-stag
```

The workspace now has this layout:

```text
clavis-slowcontrol/
├── .venv/
├── clavis/
└── clavis-stag/
```

The `[test]` extra installs pytest. Editable installs (`-e`) use the local source
directly. Reinstall after changes to `pyproject.toml` or plugin entry points.
Update both checkouts to get the compact shared CLI output described below.

## Start the daemon and CLI

In terminal 1, open the `clavis-slowcontrol` directory, activate the environment,
and start the daemon. Leave it running:

```bash
source .venv/bin/activate
clavisd --uds /tmp/clavisd.sock
```

In terminal 2, open the same `clavis-slowcontrol` directory, activate the
environment, and configure the same socket:

```bash
source .venv/bin/activate
export CLAVIS_UDS=/tmp/clavisd.sock
clavis status
clavis boards types
```

`boards types` should include `stag`. Restart the daemon after installing or
updating the plugin. Set `CLAVIS_UDS` in each new command terminal; otherwise
Clavis tries its default socket `/run/clavis/clavisd.sock`. Alternatively, put
`--uds /tmp/clavisd.sock` directly after `clavis` in each command. Clavis's
`--host` option refers to an HTTP daemon URL; the STAG IP belongs in the board's
`host=...` setting below.

## Create and initialize STAG

Register the board and connect (replace `192.168.10.16` with its IP):

```bash
clavis boards create board_type=stag board_id=stag-001 name=STAG host=192.168.10.16
clavis boards connect stag-001
clavis boards status stag-001
```

Initialize all four ASICs before editing individual channels. Specify every
channel parameter so this sequence also restores defaults after earlier edits:

```bash
clavis boards set-global stag-001 testpulse_mask=0
for chip in 0 1 2 3; do
  clavis boards set-group stag-001 "$chip" \
    analog_output=false polarity=normal testpulse=false threshold=1.8 \
    csa_res=32 pzc_res=32 shp_res=7.5 \
    csa_cap=250 pzc_cap=250 shp_cap=250
done
```

## Test pulse injection and analog output

Enable channel 1's injection path and analog output, then enable the FPGA's
10 kHz pulse generator for its ASIC. Both enables are required:

```bash
clavis boards set-channel stag-001 1 testpulse=true analog_output=true
clavis boards set-global stag-001 testpulse_mask=1
```

The generator mask uses one bit per ASIC: `1`, `2`, `4`, `8`; use `3` for the
first two ASICs or `15` for all four. Channel IDs are **1..64**, while ASIC group
IDs are **0..3** (channels 1–16 belong to group 0). Configure additional channels
with subsequent `set-channel` commands; previous edits remain in daemon memory.

Stop the FPGA generators:

```bash
clavis boards set-global stag-001 testpulse_mask=0
```

For an external input signal, enable analog output with injection disabled:

```bash
clavis boards set-channel stag-001 1 testpulse=false analog_output=true
```

The daemon remembers successful channel edits for the board instance's lifetime.
The first update to an ASIC
programs defaults for its other channels; later edits preserve remembered
settings. ASIC configuration has no readback. Test pulses continue after
disconnect; set `testpulse_mask=0` to disable the FPGA generator. ASIC power
is hardwired; software power and RBCP acquisition start/stop are unsupported.

To set all 64 channels to a threshold of 0.02 V without injection, use one
group operation per ASIC:

```bash
clavis boards set-global stag-001 testpulse_mask=0
for chip in 0 1 2 3; do
  clavis boards set-group stag-001 "$chip" threshold=0.02 testpulse=false
done
```

When finished, close the control connection:

```bash
clavis boards disconnect stag-001
```

Stopping FPGA pulses, disabling a channel's injection path, and disconnecting
are separate operations. Disconnect does not disable the configured hardware.
Daemon registrations and remembered settings need restoring after a daemon
restart. Recreate the board, connect, and initialize again after restarting.

## Configure channels using JSON files

### Channel-only files: update an existing board

For a reusable selection of channel edits, save this as `Test.json` in the
`clavis-slowcontrol` directory:

```json
{
  "1":  {"testpulse": true, "analog_output": true},
  "2":  {"testpulse": true},
  "17": {"testpulse": true, "analog_output": true}
}
```

Keys are channel IDs **1..64**. Each value contains only the parameters to
change. For example, add `"threshold": 0.02` to a channel's object to change its
threshold. JSON uses lowercase `true` and `false`.

First create, connect, and initialize `stag-001` using the commands above. Then,
with the virtual environment activated and `CLAVIS_UDS` set, run this adapter
from the directory containing `Test.json`. It translates each entry into a
shared Clavis CLI command:

```bash
python - <<'PY'
import json
import subprocess

with open("Test.json", encoding="utf-8") as source:
    channels = json.load(source)

for channel, parameters in channels.items():
    options = [
        f"{name}={value if isinstance(value, str) else json.dumps(value)}"
        for name, value in parameters.items()
    ]
    if options:
        subprocess.run(
            ["clavis", "boards", "set-channel", "stag-001", channel, *options],
            check=True,
        )
PY
```

This preserves parameters omitted from each edit and leaves other ASICs
untouched. It does not initialize all chips or automatically enable FPGA pulse
generators. For this example, channels 1 and 2 are on the first ASIC and channel
17 is on the second, so enable their generators with mask `3`:

```bash
clavis boards set-global stag-001 testpulse_mask=3
clavis boards status stag-001
```

For measurements without injection, use `"testpulse": false` in the relevant
channel entries and `testpulse_mask=0` to disable the FPGA generators. The
adapter stops at the first failed command; earlier successful edits remain
applied. Channel-only files cannot be passed directly to `clavis config load`.

### Full system files: create, connect, and initialize from JSON

Clavis's built-in JSON loader accepts a system configuration. Save the following
as `System.json`, replacing the example board IP as needed:

```json
{
  "boards": [
    {
      "config": {
        "board_id": "stag-001",
        "board_type": "stag",
        "name": "STAG",
        "host": "192.168.10.16"
      },
      "parameters": {
        "global": {"testpulse_mask": 0},
        "groups": {
          "0": {"threshold": 1.8},
          "1": {"threshold": 1.8},
          "2": {"threshold": 1.8},
          "3": {"threshold": 1.8}
        },
        "channels": {
          "1":  {"testpulse": true, "analog_output": true},
          "2":  {"testpulse": true},
          "17": {"testpulse": true, "analog_output": true}
        }
      }
    }
  ]
}
```

With the daemon running and the CLI socket configured, apply it directly:

```bash
clavis config load System.json
clavis boards set-global stag-001 testpulse_mask=3
clavis boards status stag-001
```

`config load` replaces the daemon's registered board list with the contents of
the file, creates fresh board instances, and connects them. It then applies
global settings, group settings, and channel settings in that order. Include
every board you want to keep registered. You do not need separate `create` or
`connect` commands for this workflow.

In this example, each threshold-only group edit transmits a complete ASIC image
using defaults from the fresh instance, so all four chips are initialized before
the selected channel edits. The FPGA generators remain off during loading and
are enabled explicitly afterward. Groups omitted from the file are not
automatically programmed; keep all four group entries when you need every chip
initialized. Loading a configuration is not atomic: a hardware failure can leave
earlier operations applied.

## Terminal output

Parameter setters print only the submitted changes for the selected target.
Recorded channels that match all parameter defaults are summarized as ranges:

```text
Board stag-001 / channel 1: testpulse=true, analog_output=true
Channels 2-64: default settings applied (remembered).
```

Setting a complete group configuration to defaults prints a short default
confirmation. Default-channel summaries include only channels with a recorded
successful configuration; they do not imply configuration readback. Stale
hardware status and programming errors remain visible. These confirmations
report the requested writes, even if a value was already set previously.

`boards status` shows nondefault channel parameters and groups default channels.
Use the global `--full` option before `boards` for complete parameter details:

```bash
clavis boards status stag-001
clavis --full boards status stag-001
clavis --full boards set-channel stag-001 1 testpulse=true
```

## Package and development

The package supplies the `stag` board plugin through the `clavis.boards` entry
point, matching SAMIDARE's integration. It provides no standalone executable or
`python -m clavis_stag` command. Channel and group updates preserve remembered
settings and touch only the affected ASIC; initialize all four as above when
you need a complete known starting configuration. FPGA pulse enables change
only when explicitly written with `set-global` or `set-group`.

After updating the source, reinstall the editable package to remove any old
standalone executable, then restart `clavisd` and refresh your shell's command
cache (`rehash` in zsh). For offline payload inspection, Python callers can use
`prepare_updates` from `clavis_stag.controls`.

From the `clavis-slowcontrol` directory with the environment activated, run the
automated tests without a board:

```bash
python -m pytest clavis-stag/tests -q
```

See [configuration](docs/configuration.md) and [hardware](docs/hardware.md) for
parameter units, SPI behavior, firmware references, and recovery after a failure.
