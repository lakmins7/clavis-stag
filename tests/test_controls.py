from dataclasses import FrozenInstanceError

import pytest

from clavis_stag.controls import (
    ChannelState, channel_target, encode_asic, encode_channel, prepare_updates, update_channel,
)


def test_known_agasa_vectors():
    assert encode_channel(ChannelState()) == bytes.fromhex("0000000bff")
    assert encode_channel(update_channel(ChannelState(), {"threshold": -1.8})) == bytes.fromhex("0000000800")
    assert encode_channel(update_channel(ChannelState(), {"threshold": 0})) == bytes.fromhex("00000009ff")
    maximum = update_channel(ChannelState(), {
        "csa_res": 256, "shp_res": 60, "pzc_res": 256,
        "csa_cap": 4000, "shp_cap": 4000, "pzc_cap": 4000,
        "testpulse": True, "analog_output": True, "polarity": "inverted",
    })
    assert encode_channel(maximum) == bytes.fromhex("7f7f7f17ff")
    frame = encode_asic([ChannelState()] * 15 + [maximum])
    assert len(frame) == 80
    assert frame == bytes.fromhex("0000000bff") * 15 + bytes.fromhex("7f7f7f17ff")


@pytest.mark.parametrize("channel, target", [("1", (0, 0)), ("16", (0, 15)), ("17", (1, 0)),
                                          ("32", (1, 15)), ("33", (2, 0)), ("64", (3, 15))])
def test_mapping(channel, target):
    assert channel_target(channel) == target


@pytest.mark.parametrize("channel", ["0", "65", "01", "-1", "asic1", 1, None])
def test_invalid_mapping(channel):
    with pytest.raises(ValueError):
        channel_target(channel)


@pytest.mark.parametrize("options", [
    {"threshold": 1.81}, {"threshold": -1.81}, {"threshold": float("nan")},
    {"threshold": float("inf")}, {"threshold": True}, {"threshold": "0.5"},
    {"csa_res": 33}, {"shp_res": 32}, {"pzc_cap": 0}, {"csa_cap": 251},
    {"analog_output": 1}, {"testpulse": "false"}, {"polarity": "positive"}, {"output": True},
])
def test_invalid_settings(options):
    with pytest.raises(ValueError):
        update_channel(ChannelState(), options)


def test_partial_updates_preserve_peers_and_encode_each_asic_once():
    initial = [ChannelState()] * 64
    initial[1] = update_channel(initial[1], {"testpulse": True})
    candidate, frames = prepare_updates(initial, {"1": {"threshold": 0.5}, "2": {"csa_res": 64},
                                                   "64": {"analog_output": True}})
    assert list(frames) == [0, 3]
    assert candidate[1].testpulse is True
    assert candidate[1].csa_res == 64
    assert initial[0].threshold == 1.8
    assert frames[0][5:10] == encode_channel(candidate[1])
    with pytest.raises(FrozenInstanceError):
        initial[0].threshold = 0


def test_invalid_bulk_shapes_and_frame_size():
    for updates in ([], {"1": []}, {"65": {}}):
        with pytest.raises(ValueError):
            prepare_updates([ChannelState()] * 64, updates)
    with pytest.raises(ValueError):
        encode_asic([ChannelState()] * 15)
