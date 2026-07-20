import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

from telegram import File

from src.config import OPENAI_TRANSCRIPTION_MODEL
from src.utils.llm import client


async def transcribe_voice(file: File) -> str:
    with TemporaryDirectory() as temporary_directory:
        voice_path = Path(temporary_directory) / "voice.oga"
        await file.download_to_drive(voice_path)
        with voice_path.open("rb") as audio:
            response = await asyncio.to_thread(
                client.audio.transcriptions.create,
                model=OPENAI_TRANSCRIPTION_MODEL,
                file=audio,
            )
    return response.text
