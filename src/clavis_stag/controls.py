"""AGASA v3 settings matching FEE-Control-Software's STAG v2 definitions."""

from dataclasses import asdict, dataclass, replace
from decimal import Decimal
import math
from typing import Any


RESISTANCES = tuple(range(32, 257, 32))
SHAPER_RESISTANCES = tuple(7.5 * i for i in range(1, 9))
CAPACITANCES = tuple(range(250, 4001, 250))


@dataclass(frozen=True)
class ChannelState:
    analog_output: bool = False
    polarity: str = "normal"
    testpulse: bool = False
    threshold: float = 1.8
    csa_res: float = 32
    pzc_res: float = 32
    shp_res: float = 7.5
    csa_cap: float = 250
    pzc_cap: float = 250
    shp_cap: float = 250

    def as_options(self) -> dict[str, Any]:
        return asdict(self)


def update_channel(state: ChannelState, options: dict[str, Any]) -> ChannelState:
    values = state.as_options()
    for key, value in options.items():
        if key not in values:
            raise ValueError(f"Unknown AGASA parameter: {key}")
        if key in {"analog_output", "testpulse"}:
            if type(value) is not bool:
                raise ValueError(f"{key} must be boolean")
        elif key == "polarity":
            if not isinstance(value, str) or value not in ("normal", "inverted"):
                raise ValueError("polarity must be normal or inverted")
        else:
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError(f"{key} must be a finite number")
            if key == "threshold":
                if not -1.8 <= value <= 1.8:
                    raise ValueError("threshold must be between -1.8 and 1.8 V")
            else:
                choices = (RESISTANCES if key in {"csa_res", "pzc_res"}
                           else SHAPER_RESISTANCES if key == "shp_res" else CAPACITANCES)
                if value not in choices:
                    raise ValueError(f"Invalid {key}: {value}")
        values[key] = value
    return replace(state, **values)


def channel_target(channel_id: str) -> tuple[int, int]:
    """Board channels 1..64 → ASIC 0..3 and local channel 0..15."""
    if not isinstance(channel_id, str) or channel_id not in {str(i) for i in range(1, 65)}:
        raise ValueError("Channel ID must be 1..64")
    return divmod(int(channel_id) - 1, 16)


def prepare_updates(
    channels: list[ChannelState], updates: dict[str, dict[str, Any]],
) -> tuple[list[ChannelState], dict[int, bytes]]:
    """Validate the whole edit before I/O, encoding each affected ASIC once."""
    if len(channels) != 64 or not isinstance(updates, dict):
        raise ValueError("Expected 64 channel states and a channel update object")
    candidate = channels.copy()
    affected = set()
    for channel_id, options in updates.items():
        asic, _ = channel_target(channel_id)
        if not isinstance(options, dict):
            raise ValueError(f"Channel {channel_id} options must be an object")
        index = int(channel_id) - 1
        candidate[index] = update_channel(candidate[index], options)
        if options:
            affected.add(asic)
    frames = {asic: encode_asic(candidate[asic * 16:(asic + 1) * 16])
              for asic in sorted(affected)}
    return candidate, frames


def encode_channel(state: ChannelState) -> bytes:
    state = update_channel(ChannelState(), state.as_options())
    # 0+R+C for CSA, SHP, PZC, then 000+TP+polarity+output+10-bit DAC.
    dac = int((Decimal(str(state.threshold)) + Decimal("1.8")) * 1023 / Decimal("3.6"))
    bits = ""
    for resistance, capacitance, choices in (
        (state.csa_res, state.csa_cap, RESISTANCES),
        (state.shp_res, state.shp_cap, SHAPER_RESISTANCES),
        (state.pzc_res, state.pzc_cap, RESISTANCES),
    ):
        bits += f"0{choices.index(resistance):03b}{CAPACITANCES.index(capacitance):04b}"
    bits += f"000{int(state.testpulse)}{int(state.polarity == 'normal')}{int(state.analog_output)}{dac:010b}"
    return int(bits, 2).to_bytes(5, "big")


def encode_asic(channels: list[ChannelState]) -> bytes:
    if len(channels) != 16:
        raise ValueError("An AGASA v3 image requires exactly 16 channels")
    return b"".join(encode_channel(channel) for channel in channels)
