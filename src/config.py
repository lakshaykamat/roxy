import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY")
ALLOWED_USER_ID: int = int(os.getenv("ALLOWED_USER_ID"))
OPENAI_MODEL: str = "gpt-4o-mini"
