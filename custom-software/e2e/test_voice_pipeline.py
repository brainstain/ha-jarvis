"""Voice-pipeline services on the inference node: STT, TTS, wake word, and
speaker ID. No audio is sent — `describe` is a genuine Wyoming protocol
round trip (the service must have a model loaded to answer it), which is
enough to prove each one is alive without shipping audio fixtures around.
"""

from __future__ import annotations

import httpx

from conftest import require
from wyoming import describe


def test_whisper_describes_itself(endpoints, budgets, perf, reachability):
    require(reachability, "wyoming_whisper")
    info, _ = perf.timed(
        "voice/whisper_describe",
        lambda: describe(endpoints.wyoming_whisper_host, endpoints.wyoming_whisper_port),
        budget=budgets.wyoming_describe,
    )
    assert info["type"] == "info"
    assert info["data"]["asr"], "no ASR model reported — whisper has nothing loaded"


def test_piper_describes_itself(endpoints, budgets, perf, reachability):
    require(reachability, "wyoming_piper")
    info, _ = perf.timed(
        "voice/piper_describe",
        lambda: describe(endpoints.wyoming_piper_host, endpoints.wyoming_piper_port),
        budget=budgets.wyoming_describe,
    )
    assert info["type"] == "info"
    assert info["data"]["tts"], "no TTS voice reported — piper has nothing loaded"


def test_openwakeword_describes_itself(endpoints, budgets, perf, reachability):
    require(reachability, "wyoming_wakeword")
    info, _ = perf.timed(
        "voice/wakeword_describe",
        lambda: describe(endpoints.wyoming_wakeword_host, endpoints.wyoming_wakeword_port),
        budget=budgets.wyoming_describe,
    )
    assert info["type"] == "info"
    assert info["data"]["wake"], "no wake model reported — openWakeWord has nothing loaded"


def test_speechbrain_health(endpoints, budgets, perf, reachability):
    require(reachability, "speechbrain")
    resp, _ = perf.timed(
        "voice/speechbrain_health",
        lambda: httpx.get(f"{endpoints.speechbrain_url}/health", timeout=5.0),
        budget=budgets.health,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_speechbrain_speakers_list(endpoints, budgets, perf, reachability):
    require(reachability, "speechbrain")
    resp, _ = perf.timed(
        "voice/speechbrain_speakers",
        lambda: httpx.get(f"{endpoints.speechbrain_url}/speakers", timeout=5.0),
        budget=budgets.health,
    )
    assert resp.status_code == 200
