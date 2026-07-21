import asyncio
import logging
from pathlib import Path
from tempfile import TemporaryDirectory

from telegram import File

from src.config import OPENAI_TRANSCRIPTION_MODEL
from src.utils.llm import client

logger = logging.getLogger(__name__)


async def transcribe_voice(file: File) -> str:
    with TemporaryDirectory() as temporary_directory:
        voice_path = Path(temporary_directory) / "voice.ogg"
        logger.info("Downloading voice message for transcription")
        await file.download_to_drive(voice_path)
        with voice_path.open("rb") as audio:
            logger.info(
                "Submitting voice message for transcription with model %s",
                OPENAI_TRANSCRIPTION_MODEL,
            )
            response = await asyncio.to_thread(
                client.audio.transcriptions.create,
                model=OPENAI_TRANSCRIPTION_MODEL,
                file=audio,
            )
    logger.info("Voice message transcription completed")
    return response.text
