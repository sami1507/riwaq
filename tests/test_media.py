import asyncio
import importlib.util
import io
import json
import wave

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend import media


def png(width=12, height=10):
    pytest.importorskip("PIL")
    from PIL import Image
    output = io.BytesIO()
    Image.new("RGB", (width, height), "red").save(output, format="PNG")
    return output.getvalue()


def wav(seconds=.5):
    output = io.BytesIO()
    with wave.open(output, "wb") as recording:
        recording.setnchannels(1)
        recording.setsampwidth(2)
        recording.setframerate(16000)
        recording.writeframes(b"\x00\x00" * int(16000 * seconds))
    return output.getvalue()


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(media.router)
    with TestClient(app) as session:
        yield session


def test_image_validation_dimensions_and_format():
    # Test the native implementation executed only inside the fixed worker.
    from backend.image_normalize import normalize,ImageRejected
    data, dimensions = normalize(png())
    assert data.startswith(b"\x89PNG")
    assert dimensions == {"width": 12, "height": 10}
    with pytest.raises(ImageRejected,match='image_pixel_limit'):normalize(png(2500,2000))
    with pytest.raises(ImageRejected,match='image_invalid'):normalize(b'not an image')


def test_image_upload_bound_before_parser():
    with pytest.raises(HTTPException) as caught:
        media.validate_image(b"a" * (media.MAX_IMAGE_BYTES + 1))
    assert caught.value.status_code == 413


def test_image_metadata_is_removed():
    pytest.importorskip("PIL")
    from PIL import Image, PngImagePlugin
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("private-location", "never forward this metadata")
    output = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(output, format="PNG", pnginfo=metadata)
    from backend.image_normalize import normalize
    normalized, _ = normalize(output.getvalue())
    with Image.open(io.BytesIO(normalized)) as result:
        assert "private-location" not in result.info


def test_audio_magic_and_upload_limit():
    assert media.audio_kind(wav()) == "wav"
    assert media.audio_kind(b"\x1aE\xdf\xa3fixture") == "webm"
    with pytest.raises(HTTPException) as caught:
        media.audio_kind(b"playlist http://example.invalid/audio")
    assert caught.value.status_code == 415
    with pytest.raises(HTTPException) as caught:
        media.audio_kind(b"x" * (media.MAX_AUDIO_BYTES + 1))
    assert caught.value.status_code == 413


def test_audio_decode_has_actual_duration_limit(monkeypatch):
    pytest.importorskip("av")
    # Trusted synthetic fixtures exercise the native implementation that now
    # runs inside the isolated worker in production, not the host adapter.
    from backend import audio_decode,isolated_audio
    raw,source,_=audio_decode.decode(wav(.5))
    values=isolated_audio.diagnostics(raw,source)[0]
    assert len(values) == 8000
    monkeypatch.setattr(isolated_audio, "MAX_SECONDS", .1)
    with pytest.raises(ValueError, match="audio_duration"):
        audio_decode.decode(wav(.5))


def test_whisper_never_uses_model_alias_or_automatic_download(monkeypatch):
    monkeypatch.delenv("RIWAQ_WHISPER_MODEL", raising=False)
    assert not media.whisper_config()["configured"]
    monkeypatch.setenv("RIWAQ_WHISPER_MODEL", "small")
    assert not media.whisper_config()["configured"]
    monkeypatch.setenv("RIWAQ_WHISPER_MODEL", "https://example.invalid/model")
    assert not media.whisper_config()["configured"]


def test_canonical_whisper_files_without_preprocessor_are_configured(tmp_path, monkeypatch):
    pytest.importorskip("faster_whisper")
    pytest.importorskip("av")
    for name in ("model.bin", "config.json", "tokenizer.json"):
        (tmp_path / name).write_bytes(b"fixture")
    monkeypatch.setenv("RIWAQ_WHISPER_MODEL", str(tmp_path))
    from backend import audio_isolation
    monkeypatch.setattr(audio_isolation,'status',lambda:{'available':True,'image_id':'sha256:synthetic-decoder','protocol':'isolated-audio-decode-v1'})
    configuration = media.whisper_config()
    assert configuration["configured"]
    before = configuration["fingerprint"]
    (tmp_path / "vocabulary.txt").write_text("optional vocabulary", encoding="utf-8")
    assert media.whisper_config()["fingerprint"] != before
    (tmp_path / "tokenizer.json").unlink()
    assert not media.whisper_config()["configured"]


def test_environment_flag_cannot_certify_packaged_transcription(tmp_path,monkeypatch):
    for name in ('model.bin','config.json','tokenizer.json'):(tmp_path/name).write_bytes(b'fixture')
    monkeypatch.setenv('RIWAQ_WHISPER_MODEL',str(tmp_path))
    monkeypatch.setenv('RIWAQ_PACKAGED_MEDIA_WORKER','1')
    monkeypatch.setattr(media.sys,'frozen',True,raising=False)
    monkeypatch.setattr(media.importlib.util,'find_spec',lambda name:object())
    assert media.whisper_config()['configured'] is False
    assert 'no verified transcription worker' in media.whisper_config()['reason']


def test_missing_transcription_engine_fails_explicitly(client, monkeypatch):
    monkeypatch.delenv("RIWAQ_WHISPER_MODEL", raising=False)
    response = client.post("/api/media/transcribe", files={"file": ("voice.wav", wav(), "audio/wav")})
    assert response.status_code == 503
    assert "RIWAQ_WHISPER_MODEL" in response.json()["detail"]


def test_no_parallel_media_jobs():
    first = media.begin_media("vision")
    try:
        with pytest.raises(HTTPException) as caught:
            media.begin_media("stt")
        assert caught.value.status_code == 409
    finally:
        media.finish_media(first)
    assert media.ACTIVE_MEDIA is None


@pytest.mark.asyncio
async def test_status_requires_matching_real_test_evidence(tmp_path, monkeypatch):
    async def inspection():
        return {"model": "actual-vision", "digest": "digest-1", "capabilities": ["vision"],"normalization_image_id":"sha256:synthetic","normalization_protocol":"isolated-image-normalize-v1"}
    monkeypatch.setattr(media, "inspect_vision", inspection)
    monkeypatch.setattr(media, "whisper_config", lambda: {"configured": False, "reason": "No model"})
    report = tmp_path / "media.json"
    monkeypatch.setenv("RIWAQ_MEDIA_VALIDATION_REPORT", str(report))
    before = await media.media_status()
    assert before["vision"]["configured"] and not before["vision"]["available"]
    report.write_text(json.dumps({"vision": {"passed": True, "model": "actual-vision", "digest": "different"}}))
    assert not (await media.media_status())["vision"]["available"]
    report.write_text(json.dumps({"vision": {"passed": True, "model": "actual-vision", "digest": "digest-1"}}))
    assert not (await media.media_status())["vision"]["available"]
    report.write_text(json.dumps({"vision": {"passed": True, "model": "actual-vision", "digest": "digest-1","normalization_image_id":"sha256:synthetic","normalization_protocol":"isolated-image-normalize-v1"}}))
    result = await media.media_status()
    assert result["vision"]["available"] and result["vision"]["tested"]
    assert not result["tts"]["available"] and not result["image_generation"]["available"]


@pytest.mark.asyncio
@pytest.mark.parametrize("info", [{"capabilities": ["completion"]}, {"capabilities": ["vision"], "remote_host": "https://external.invalid"}])
async def test_vision_rejects_unsupported_or_remote_models(monkeypatch, info):
    pytest.importorskip("PIL")
    original = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=info))
    monkeypatch.setattr(media.httpx, "AsyncClient", lambda **kwargs: original(transport=transport))
    with pytest.raises(HTTPException) as caught:
        await media.inspect_vision()
    assert caught.value.status_code == 503


@pytest.mark.asyncio
async def test_stop_cancels_inflight_work():
    closed = []
    async def slow():
        try:
            await asyncio.sleep(100)
        finally:
            closed.append(True)
    class ConnectedRequest:
        async def is_disconnected(self):
            return False
    job = media.begin_media("vision")
    try:
        task = asyncio.create_task(slow())
        await asyncio.sleep(0)
        job["stop"].set()
        with pytest.raises(HTTPException) as caught:
            await media.await_work(task, job, ConnectedRequest(), timeout=1)
        assert caught.value.status_code == 499
        assert task.cancelled() and closed == [True]
    finally:
        media.finish_media(job)


@pytest.mark.asyncio
async def test_timeout_cancels_inflight_work():
    class ConnectedRequest:
        async def is_disconnected(self):
            return False
    job = media.begin_media("stt")
    try:
        task = asyncio.create_task(asyncio.sleep(100))
        with pytest.raises(HTTPException) as caught:
            await media.await_work(task, job, ConnectedRequest(), timeout=.01)
        assert caught.value.status_code == 504 and task.cancelled()
    finally:
        media.finish_media(job)
