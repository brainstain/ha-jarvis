"""wyoming-identify-proxy: WAV wrapping, /identify handling, TTL expiry."""

from __future__ import annotations

import wave
from io import BytesIO
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp.test_utils import make_mocked_request

import app


@pytest.fixture(autouse=True)
def _reset_last_speaker():
    app._last_speaker = None
    yield
    app._last_speaker = None


class _FakeResponse:
    def __init__(self, json_data: dict, status: int = 200):
        self._json_data = json_data
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")

    def json(self):
        return self._json_data


class _FakeHttpxClient:
    def __init__(self, response: _FakeResponse):
        self._response = response
        self.last_post_kwargs = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, **kwargs):
        self.last_post_kwargs = kwargs
        return self._response


@pytest.mark.asyncio
async def test_identify_match_updates_last_speaker():
    response = _FakeResponse({"user_id": "michael", "confidence": 0.91})
    fake_client = _FakeHttpxClient(response)

    with patch("app.httpx.AsyncClient", return_value=fake_client):
        await app._run_identify(b"\x00\x01" * 8000, rate=16000, width=2, channels=1)

    assert app._last_speaker is not None
    assert app._last_speaker["user_id"] == "michael"
    assert app._last_speaker["confidence"] == 0.91

    # Confirm a real, valid WAV was actually sent, not just raw PCM bytes.
    sent_file = fake_client.last_post_kwargs["files"]["audio"]
    wav_bytes = sent_file[1]
    with wave.open(BytesIO(wav_bytes), "rb") as wf:
        assert wf.getframerate() == 16000
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2


@pytest.mark.asyncio
async def test_identify_unknown_does_not_update_last_speaker():
    response = _FakeResponse({"user_id": "unknown", "confidence": 0.2})
    fake_client = _FakeHttpxClient(response)

    with patch("app.httpx.AsyncClient", return_value=fake_client):
        await app._run_identify(b"\x00\x01" * 8000, rate=16000, width=2, channels=1)

    assert app._last_speaker is None


@pytest.mark.asyncio
async def test_identify_skips_empty_audio():
    with patch("app.httpx.AsyncClient") as mock_client:
        await app._run_identify(b"", rate=16000, width=2, channels=1)
    mock_client.assert_not_called()


@pytest.mark.asyncio
async def test_identify_http_failure_leaves_last_speaker_unset():
    class _RaisingClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, **kwargs):
            import httpx

            raise httpx.ConnectError("refused")

    with patch("app.httpx.AsyncClient", return_value=_RaisingClient()):
        await app._run_identify(b"\x00\x01" * 8000, rate=16000, width=2, channels=1)

    assert app._last_speaker is None


@pytest.mark.asyncio
async def test_last_speaker_endpoint_returns_none_when_empty():
    request = make_mocked_request("GET", "/last-speaker")
    resp = await app._http_last_speaker(request)
    assert resp.status == 200
    assert resp.text == '{"user_id": null}'


@pytest.mark.asyncio
async def test_last_speaker_endpoint_returns_fresh_match():
    import time

    app._last_speaker = {"user_id": "michael", "confidence": 0.9, "ts": time.monotonic()}
    request = make_mocked_request("GET", "/last-speaker")
    resp = await app._http_last_speaker(request)
    assert resp.status == 200
    assert '"user_id": "michael"' in resp.text


@pytest.mark.asyncio
async def test_last_speaker_endpoint_expires_after_ttl():
    import time

    app._last_speaker = {
        "user_id": "michael",
        "confidence": 0.9,
        "ts": time.monotonic() - (app.LAST_SPEAKER_TTL + 1),
    }
    request = make_mocked_request("GET", "/last-speaker")
    resp = await app._http_last_speaker(request)
    assert resp.text == '{"user_id": null}'
