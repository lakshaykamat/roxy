import html
import logging
import secrets
import sqlite3
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

from src import config
from src.dashboard import service as dashboard
from src.core.errors import try_catch
from src.conversations.history import database_connection

logger = logging.getLogger(__name__)
TEMPLATE_DIRECTORY = Path(__file__).with_name("templates")
app = FastAPI()
app.add_middleware(
    SessionMiddleware,
    secret_key=config.DASHBOARD_SESSION_SECRET,
    session_cookie="roxy_dashboard_session",
    max_age=config.DASHBOARD_SESSION_MAX_AGE_SECONDS,
    same_site="lax",
    https_only=config.DASHBOARD_SECURE_COOKIES,
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


def database_is_available() -> bool:
    def check_database() -> bool:
        with database_connection() as connection:
            connection.execute("SELECT 1")
        return True

    return try_catch(
        check_database, handle_error=lambda _: False, exception_types=sqlite3.Error
    )


@app.get("/ready")
async def ready() -> JSONResponse:
    database_ready = database_is_available()
    status_code = 200 if database_ready else 503
    return JSONResponse(
        {"status": "ready" if status_code == 200 else "not_ready"},
        status_code=status_code,
    )


async def submitted_password(request: Request) -> str:
    parsed = parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)
    return parsed.get("password", [""])[0]


def dashboard_authenticated(request: Request) -> bool:
    return request.session.get("dashboard_authenticated") is True


def login_page(error: bool = False) -> HTMLResponse:
    message = "<p>Incorrect password.</p>" if error else ""
    return HTMLResponse(
        render_template("login.html", {"error_message": message}),
        status_code=401 if error else 200,
    )


@app.get("/login")
async def login():
    return login_page()


@app.post("/login")
async def authenticate(request: Request):
    password = await submitted_password(request)
    if not secrets.compare_digest(password, config.DASHBOARD_PASSWORD):
        return login_page(error=True)
    request.session["dashboard_authenticated"] = True
    return RedirectResponse("/", status_code=303)


def dashboard_redirect(request: Request) -> RedirectResponse | None:
    if not dashboard_authenticated(request):
        return RedirectResponse("/login", status_code=303)
    return None


def render_dashboard(snapshot: dict[str, object]) -> str:
    def text(value: object) -> str:
        return html.escape(str(value if value is not None else "Not recorded"))

    def timestamp(value: object) -> str:
        if value is None:
            return '<span class="text-slate-400">Not recorded</span>'
        escaped_value = text(value)
        return (
            f'<time class="js-local-time" datetime="{escaped_value}">'
            f"{escaped_value}</time>"
        )

    def status_badge(status: object) -> str:
        value = str(status).lower()
        classes = {
            "healthy": "text-accent",
            "active": "text-accent",
            "delivered": "text-accent",
            "completed": "text-accent",
            "degraded": "text-amber-800",
            "pending": "text-amber-800",
            "leased": "text-sky-800",
            "unhealthy": "text-alert",
            "failed": "text-alert",
            "cancelled": "text-slate-600",
        }.get(value, "text-slate-600")
        return (
            f'<span class="font-bold uppercase {classes}">[{text(value)}]</span>'
        )

    def count_rows(counts: object, fields: tuple[tuple[str, str], ...]) -> str:
        values = counts if isinstance(counts, dict) else {}
        return "".join(
            '<div>'
            f'<dt>{text(label)}</dt>'
            f'<dd class="font-bold tabular-nums">{text(values.get(key, 0))}</dd>'
            "</div>"
            for label, key in fields
        )

    def configuration_rows(values: object) -> str:
        configuration_values = values if isinstance(values, dict) else {}
        labels = (
            ("OpenAI model", "openai_model"),
            ("Voice transcription", "transcription_model"),
            ("Task timezone", "task_timezone"),
            ("Message retention", "history_retention_days"),
            ("Memory retention", "memory_retention_days"),
            ("Expense tracking", "expense_tracker_enabled"),
        )
        return "".join(
            '<div>'
            f'<dt>{label}</dt>'
            f'<dd class="font-bold">'
            f'{text("Enabled" if configuration_values.get(key) is True else "Disabled" if configuration_values.get(key) is False else configuration_values.get(key))}'
            "</dd></div>"
            for label, key in labels
        )

    services = snapshot["services"]
    messages = snapshot["messages"]
    memories = snapshot["memories"]
    tasks = snapshot["tasks"]
    reminders = snapshot["reminders"]
    configuration = snapshot["configuration"]
    service_rows = "".join(
        '<li class="flex items-start justify-between gap-4 px-3 py-3 text-xs">'
        '<div class="min-w-0"><p class="font-bold uppercase">'
        f"{text(name)}</p><p class=\"mt-1\">LAST_HEARTBEAT: {timestamp(details['updated_at'])}</p>"
        f"</div>{status_badge(details['status'])}</li>"
        for name, details in services.items()
    )
    upcoming = "".join(
        '<li class="px-3 py-3 text-xs">'
        f'<p class="break-words font-bold">{text(item["title"])}</p>'
        f'<p class="mt-1">SCHEDULED_AT: {timestamp(item["scheduled_at"])} · '
        f'{text("Repeats " + item["recurrence"] if item["recurrence"] else "One-time")}</p></li>'
        for item in reminders["upcoming"]
    ) or '<li class="px-3 py-4 text-xs">(none recorded)</li>'
    failures = "".join(
        '<li class="px-3 py-3 text-xs">'
        f'<div class="flex items-start justify-between gap-3"><p class="min-w-0 break-words font-bold">{text(item["title"])}</p>'
        f'<span class="shrink-0 text-alert">ATTEMPT:{text(item["attempt_count"])}</span></div>'
        f'<p class="mt-1 break-words">ERROR: {text(item["error"])}</p>'
        f'<p class="mt-1">RECORDED_AT: {timestamp(item["updated_at"])}</p></li>'
        for item in reminders["recent_failures"]
    ) or '<li class="px-3 py-4 text-xs">(none recorded)</li>'
    notices = "".join(f'<li class="px-3 py-3 text-xs">{text(notice)}</li>' for notice in snapshot["notices"])
    notices = notices or '<li class="px-3 py-3 text-xs">(none recorded)</li>'
    activity_rows = "".join((
        '<div><dt>MESSAGE_TOTAL</dt><dd class="font-bold tabular-nums">' + text(messages["total"]) + "</dd></div>",
        '<div><dt>MESSAGES_LAST_24_HOURS</dt><dd class="font-bold tabular-nums">' + text(messages["last_24_hours"]) + "</dd></div>",
        '<div><dt>LATEST_MESSAGE_AT</dt><dd>' + timestamp(messages["latest_at"]) + "</dd></div>",
        '<div><dt>MEMORY_TOTAL</dt><dd class="font-bold tabular-nums">' + text(memories["total"]) + "</dd></div>",
        '<div><dt>MEMORIES_EXPIRING_7_DAYS</dt><dd class="font-bold tabular-nums">' + text(memories["expiring_within_7_days"]) + "</dd></div>",
        count_rows(messages["by_role"], (("messages_user", "user"), ("messages_assistant", "assistant"))),
        count_rows(memories["by_kind"], (("memory_fact", "fact"), ("memory_person", "person"), ("memory_preference", "preference"), ("memory_project", "project"), ("memory_routine", "routine"))),
    ))
    queue_rows = "".join((
        count_rows(tasks["by_status"], (("tasks_active", "active"), ("tasks_completed", "completed"), ("tasks_cancelled", "cancelled"))),
        count_rows(reminders["by_status"], (("reminders_pending", "pending"), ("reminders_leased", "leased"), ("reminders_delivered", "delivered"), ("reminders_failed", "failed"))),
        '<div><dt>REMINDERS_OVERDUE</dt><dd class="font-bold tabular-nums">' + text(reminders["overdue_pending"]) + "</dd></div>",
    ))
    return render_template(
        "dashboard.html",
        {
            "status": text(snapshot["status"]),
            "status_badge": status_badge(snapshot["status"]),
            "service_rows": service_rows,
            "activity_rows": activity_rows,
            "queue_rows": queue_rows,
            "upcoming": upcoming,
            "failures": failures,
            "configuration": configuration_rows(configuration),
            "notices": notices,
            "generated_at": timestamp(snapshot["generated_at"]),
        },
    )


def render_template(template_name: str, values: dict[str, str]) -> str:
    template = (TEMPLATE_DIRECTORY / template_name).read_text()
    for name, value in values.items():
        template = template.replace(f"{{{{{name}}}}}", value)
    return template


def load_snapshot() -> dict[str, object] | None:
    def unavailable(_: BaseException) -> None:
        logger.exception("Unable to load dashboard snapshot")
        return None

    return try_catch(
        dashboard.get_dashboard_snapshot,
        handle_error=unavailable,
        exception_types=sqlite3.Error,
    )


@app.get("/")
async def dashboard_page(request: Request):
    if redirect := dashboard_redirect(request):
        return redirect
    snapshot = load_snapshot()
    if snapshot is None:
        return HTMLResponse(render_template("unavailable.html", {}), status_code=503)
    return HTMLResponse(render_dashboard(snapshot))


@app.get("/dashboard-data")
async def dashboard_data(request: Request):
    if redirect := dashboard_redirect(request):
        return redirect
    snapshot = load_snapshot()
    if snapshot is None:
        return JSONResponse({"status": "unavailable"}, status_code=503)
    return JSONResponse(snapshot)


@app.post("/logout")
async def logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
