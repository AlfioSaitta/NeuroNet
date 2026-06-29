"""Audio transcription, translation, and speech endpoints."""
import os
import tempfile

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from config import logger
from .models import SpeechRequestOpenAI

router = APIRouter()

_whisper_model = None


async def _transcribe_audio(
    audio_bytes: bytes,
    filename: str,
    language: str | None = None,
    prompt_text: str | None = None,
    response_format: str = "json",
) -> str | dict:
    """Shared whisper transcription logic."""
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        _whisper_model = WhisperModel("base", device="cpu", compute_type="int8")

    suffix = os.path.splitext(filename)[1] or ".webm"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name

    try:
        segments, info = _whisper_model.transcribe(
            tmp_path,
            language=language,
            initial_prompt=prompt_text or None,
            beam_size=5,
        )
        segments_list = list(segments)
        full_text = " ".join(seg.text for seg in segments_list)
    except Exception as e:
        logger.error("Whisper transcription error: %s", e)
        raise
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass

    if response_format == "text":
        return full_text

    return {"text": full_text}


@router.post("/v1/audio/transcriptions")
async def openai_audio_transcriptions(request: Request):
    """Transcribe audio to text in the original language."""
    form = await request.form()
    audio_file = form.get("file")
    if not audio_file:
        return JSONResponse(status_code=400, content={"error": "Missing 'file' field"})

    language = form.get("language", None)
    response_format = form.get("response_format", "json")
    prompt_text = form.get("prompt", None)

    audio_bytes = await audio_file.read()
    try:
        result = await _transcribe_audio(
            audio_bytes,
            str(audio_file.filename or "audio"),
            language=language or None,
            prompt_text=prompt_text,
            response_format=response_format,
        )
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

    if response_format == "text":
        return Response(content=result, media_type="text/plain")

    return result


@router.post("/v1/audio/translations")
async def openai_audio_translations(request: Request):
    """Translate audio to English text.

    OpenAI-compatible — forces language='en' per spec.
    """
    form = await request.form()
    audio_file = form.get("file")
    if not audio_file:
        return JSONResponse(status_code=400, content={"error": "Missing 'file' field"})

    response_format = form.get("response_format", "json")
    prompt_text = form.get("prompt", None)

    audio_bytes = await audio_file.read()
    try:
        result = await _transcribe_audio(
            audio_bytes,
            str(audio_file.filename or "audio"),
            language="en",  # forced translation target
            prompt_text=prompt_text,
            response_format=response_format,
        )
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

    if response_format == "text":
        return Response(content=result, media_type="text/plain")

    return result


@router.post("/v1/audio/speech")
async def openai_audio_speech(payload: SpeechRequestOpenAI, request: Request):
    """Text-to-speech via gTTS in OpenAI format."""
    from gtts import gTTS
    import io as _io

    tts = gTTS(text=payload.input, lang="it", slow=False)
    audio_buf = _io.BytesIO()
    tts.write_to_fp(audio_buf)
    audio_buf.seek(0)

    return Response(content=audio_buf.read(), media_type="audio/mpeg")
