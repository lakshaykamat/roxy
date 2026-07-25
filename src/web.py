from fastapi import FastAPI
from fastapi.responses import JSONResponse

from src.utils.history import database_connection
from src.utils.errors import try_catch
import sqlite3

app = FastAPI()


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


def database_is_available() -> bool:
    def check_database() -> bool:
        with database_connection() as connection:
            connection.execute("SELECT 1")
        return True
    return try_catch(check_database, handle_error=lambda _: False, exception_types=sqlite3.Error)


@app.get("/ready")
async def ready() -> JSONResponse:
    database_ready = database_is_available()
    status_code = 200 if database_ready else 503
    return JSONResponse({"status": "ready" if status_code == 200 else "not_ready"}, status_code=status_code)
