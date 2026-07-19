import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("ALLOWED_USER_ID", "1")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("OPENAI_API_KEY", "test-key")

from src import app


class AppTests(unittest.TestCase):
    @patch("src.app.ApplicationBuilder")
    def test_create_telegram_application_registers_existing_handlers(self, builder_class):
        application = builder_class.return_value.token.return_value.build.return_value

        result = app.create_telegram_application()

        self.assertIs(result, application)
        self.assertEqual(application.add_handler.call_count, 2)

    @patch("src.app.uvicorn.Server")
    @patch("src.app.create_telegram_application")
    def test_run_logs_and_reraises_lifecycle_failures(
        self, create_application, server_class
    ):
        telegram_app = MagicMock()
        telegram_app.initialize = AsyncMock()
        telegram_app.start = AsyncMock()
        telegram_app.stop = AsyncMock()
        telegram_app.shutdown = AsyncMock()
        telegram_app.updater.start_polling = AsyncMock()
        telegram_app.updater.stop = AsyncMock()
        create_application.return_value = telegram_app

        server = MagicMock()
        server.started = False
        server.serve = AsyncMock()
        server_class.return_value = server

        with self.assertLogs("src.app", level="INFO") as logs:
            with self.assertRaisesRegex(
                RuntimeError, "The HTTP server stopped before it started."
            ):
                import asyncio

                asyncio.run(app.run())

        telegram_app.updater.stop.assert_awaited_once()
        telegram_app.stop.assert_awaited_once()
        telegram_app.shutdown.assert_awaited_once()
        self.assertTrue(
            any(
                entry.startswith("ERROR:src.app:Application lifecycle failed")
                for entry in logs.output
            )
        )
