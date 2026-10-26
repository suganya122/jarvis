"""Choosing the microphone (skip inputs that are only static or silence) and the learned
background level that decides what "quiet" means for each microphone."""

import pytest

from contracts.ears import Microphone
from core.hands_free_types import QUIET_LEVEL, NoiseFloor
from core.speech_text import match_wake
from workers.ears import devices

BUILT_IN = Microphone(id="alsa_input.pci.analog", name="Built-in Audio")
WEBCAM = Microphone(id="alsa_input.usb.webcam", name="HD Webcam C910")


def test_static_and_silence_are_told_apart_from_a_room() -> None:
    assert (
        devices.health([0.73, 0.75, 0.72, 0.74, 0.73, 0.76, 0.74]) == "static"
    )  # this machine's built-in input
    assert devices.health([0.38, 0.29, 0.25, 0.31, 0.27, 0.6, 0.33]) == "ok"  # its webcam: a room
    assert devices.health([0.34, 0.33, 0.35, 0.34, 0.33]) == "ok"  # a steady but quiet room
    assert devices.health([0.0, 0.0, 0.01]) == "silent"
    assert devices.health([]) == "silent"
    assert devices.health([0.65, 0.9, 0.55, 0.8, 0.7]) == "ok"  # loud but changing: someone talking


def _probe(results: dict[str, devices.Health]) -> object:
    return lambda mic_id, _level_of, seconds=0.8: results[mic_id]


def test_a_static_default_is_skipped_for_a_working_microphone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(devices, "probe", _probe({BUILT_IN.id: "static", WEBCAM.id: "ok"}))
    mic, why = devices.choose("", lambda _b: 0.0, ([BUILT_IN, WEBCAM], BUILT_IN.id))
    assert mic == WEBCAM
    assert why == "chosen automatically (Built-in Audio is only static)"


def test_a_working_default_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(devices, "probe", _probe({BUILT_IN.id: "ok", WEBCAM.id: "ok"}))
    assert devices.choose("", lambda _b: 0.0, ([WEBCAM, BUILT_IN], BUILT_IN.id)) == (
        BUILT_IN,
        "the system default",
    )


def test_a_microphone_chosen_in_settings_wins_without_probing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(devices, "probe", lambda *_a, **_k: pytest.fail("no probing for a chosen microphone"))
    assert devices.choose(BUILT_IN.id, lambda _b: 0.0, ([BUILT_IN, WEBCAM], WEBCAM.id)) == (
        BUILT_IN,
        "chosen in Settings",
    )
    mic, why = devices.choose("unplugged", lambda _b: 0.0, ([BUILT_IN], BUILT_IN.id))
    assert mic is None and "isn't connected" in why


def test_when_nothing_sounds_right_it_falls_back_to_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(devices, "probe", _probe({BUILT_IN.id: "static", WEBCAM.id: "silent"}))
    mic, why = devices.choose("", lambda _b: 0.0, ([WEBCAM, BUILT_IN], BUILT_IN.id))
    assert mic == BUILT_IN and "no input sounded like a room" in why
    assert devices.choose("", lambda _b: 0.0, ([], "")) == (None, "no microphones found")


def test_quiet_is_relative_to_the_room() -> None:
    headset, webcam = NoiseFloor(), NoiseFloor()
    for _ in range(50):
        headset.update(0.05)
        webcam.update(0.3)
    assert headset.sound() == QUIET_LEVEL  # never below the fixed minimum
    assert webcam.sound() == pytest.approx(0.42, abs=0.03)  # a webcam room's own hum isn't "speech"
    for i in range(100):  # ten seconds of talking, with the usual short pauses
        webcam.update(0.3 if i % 4 == 0 else 0.65)
    assert webcam.sound() < 0.5  # the talking didn't become "background"
    quiet = NoiseFloor()
    for _ in range(100):
        quiet.update(0.06)
    assert quiet.value == pytest.approx(0.06)


@pytest.mark.parametrize("heard", ["a jarvis open firefox", "uh jarvis what time is it", "um jarvis"])
def test_fillers_before_the_name_are_fine(heard: str) -> None:
    assert match_wake(heard, "jarvis")[0]


def test_speech_heard_first_is_not_taken_for_the_room() -> None:
    floor = NoiseFloor()
    for _ in range(10):  # the mic opens while someone is talking
        floor.update(0.8)
    assert floor.sound() < 0.8
