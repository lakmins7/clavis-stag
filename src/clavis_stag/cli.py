"""Direct STAG slow-control CLI; no daemon is required."""

import argparse
import json
from pathlib import Path
import sys

from clavis.core.errors import ClavisError
from clavis_stag.config import StagConfig
from clavis_stag.controls import ChannelState, prepare_updates
from clavis_stag.plugin import create_board


def parse_options(items: list[str]) -> dict:
    options = {}
    for item in items:
        key, sep, value = item.partition("=")
        if not sep or not key or key in options:
            raise ValueError(f"Expected unique KEY=VALUE options, got {item!r}")
        try:
            options[key] = json.loads(value)
        except json.JSONDecodeError:
            options[key] = value
    return options


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", help="STAG IPv4 address or hostname")
    parser.add_argument("--port", type=int, default=4660)
    parser.add_argument("--timeout", type=float, default=0.1)
    parser.add_argument("--spi-timeout", type=float, default=1.0)
    parser.add_argument("--firmware-id", type=lambda v: int(v, 0), default=0x5354)
    parser.add_argument("--firmware-version", default="1.0")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="Verify firmware and read SPI busy status")
    channel = commands.add_parser("set-channel", help="Program all four ASICs, defaulting unspecified channels")
    channel.add_argument("channel", help="Board channel 1..64")
    channel.add_argument("options", nargs="+", metavar="KEY=VALUE")
    apply = commands.add_parser("apply", help="Program all four ASICs from a JSON channel map plus defaults")
    apply.add_argument("file", type=Path)
    for command in (channel, apply):
        command.add_argument("--dry-run", action="store_true", help="Print ASIC images without network access")
        command.add_argument("--testpulse-mask", type=lambda v: int(v, 0), choices=range(16),
                             help="Set FPGA pulse enables (bits 0..3 = ASICs); omitted leaves them unchanged")
    pulse = commands.add_parser("set-testpulse", help="Set FPGA 10 kHz pulse enables")
    pulse.add_argument("mask", type=lambda v: int(v, 0), choices=range(16))
    args = parser.parse_args(argv)
    board = None
    result_code = 0
    try:
        updates = {}
        if args.command in {"apply", "set-channel"}:
            updates = (json.loads(args.file.read_text(encoding="utf-8")) if args.command == "apply"
                       else {args.channel: parse_options(args.options)})
            channels, _ = prepare_updates([ChannelState() for _ in range(64)], updates)
            # Explicitly program defaults even on chips with no requested edit.
            updates = {str(i + 1): state.as_options() for i, state in enumerate(channels)}
            _, frames = prepare_updates(channels, updates)
            if args.dry_run:
                print(json.dumps({"dry_run": True, "unspecified_channels": "AGASA defaults",
                                  "asic_payloads": {str(i): data.hex() for i, data in frames.items()},
                                  "testpulse_mask": args.testpulse_mask}, indent=2))
                return 0
        if not args.host:
            raise ValueError("--host is required for hardware access")
        config = StagConfig(board_id="stag-cli", name="STAG", host=args.host, port=args.port,
                            timeout=args.timeout, spi_timeout=args.spi_timeout,
                            firmware_id=args.firmware_id, firmware_version=args.firmware_version)
        board = create_board(config)
        board.connect()
        if args.command in {"apply", "set-channel"}:
            board.set_channels(updates)
            if args.testpulse_mask is not None:
                board.set_global({"testpulse_mask": args.testpulse_mask})
        elif args.command == "set-testpulse":
            board.set_global({"testpulse_mask": args.mask})
        status = board.get_status()
        print(status.model_dump_json(indent=2))
        if not status.status_fresh:
            result_code = 1
    except (ClavisError, OSError, ValueError, RuntimeError) as exc:
        print(f"clavis-stag: {exc}", file=sys.stderr)
        result_code = 1
    except KeyboardInterrupt:
        print("clavis-stag: interrupted; an active SPI transfer may be incomplete", file=sys.stderr)
        result_code = 130
    finally:
        if board is not None and board.connected:
            try:
                board.disconnect()
            except Exception as exc:
                print(f"clavis-stag: disconnect failed: {exc}", file=sys.stderr)
                result_code = 1
    return result_code
