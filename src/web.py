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
from src.services import dashboard
from src.utils.errors import try_catch
from src.utils.history import database_connection

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
            "healthy": "border-emerald-200 bg-emerald-50 text-emerald-800",
            "active": "border-emerald-200 bg-emerald-50 text-emerald-800",
            "delivered": "border-emerald-200 bg-emerald-50 text-emerald-800",
            "completed": "border-emerald-200 bg-emerald-50 text-emerald-800",
            "degraded": "border-amber-200 bg-amber-50 text-amber-800",
            "pending": "border-amber-200 bg-amber-50 text-amber-800",
            "leased": "border-sky-200 bg-sky-50 text-sky-800",
            "unhealthy": "border-red-200 bg-red-50 text-red-800",
            "failed": "border-red-200 bg-red-50 text-red-800",
            "cancelled": "border-slate-200 bg-slate-100 text-slate-700",
        }.get(value, "border-slate-200 bg-slate-100 text-slate-700")
        return (
            f'<span class="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 '
            f'text-xs font-semibold capitalize {classes}"><span class="h-1.5 w-1.5 rounded-full '
            f'bg-current"></span>{text(value)}</span>'
        )

    def count_tiles(counts: object, labels: tuple[str, ...]) -> str:
        values = counts if isinstance(counts, dict) else {}
        return "".join(
            '<div class="rounded-lg border border-line bg-slate-50 px-3 py-2.5">'
            f'<dt class="text-xs font-medium capitalize text-slate-500">{text(label)}</dt>'
            f'<dd class="mt-1 text-lg font-semibold tabular-nums text-ink">{text(values.get(label, 0))}</dd>'
            "</div>"
            for label in labels
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
            '<div class="flex items-start justify-between gap-5 border-b border-line py-3 '
            'first:pt-0 last:border-0 last:pb-0">'
            f'<dt class="text-sm text-slate-500">{label}</dt>'
            f'<dd class="max-w-[58%] break-words text-right text-sm font-medium text-slate-800">'
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
    service_cards = "".join(
        '<li class="flex items-center justify-between gap-4 py-3 first:pt-0 last:pb-0">'
        '<div class="min-w-0"><p class="font-medium capitalize text-slate-800">'
        f"{text(name)}</p><p class=\"mt-0.5 text-xs text-slate-500\">Last heartbeat: {timestamp(details['updated_at'])}</p>"
        f"</div>{status_badge(details['status'])}</li>"
        for name, details in services.items()
    )
    upcoming = "".join(
        '<li class="rounded-lg border border-line bg-slate-50 px-3.5 py-3">'
        f'<p class="truncate font-medium text-slate-800">{text(item["title"])}</p>'
        f'<p class="mt-1 text-xs text-slate-500">{timestamp(item["scheduled_at"])} · '
        f'{text("Repeats " + item["recurrence"] if item["recurrence"] else "One-time")}</p></li>'
        for item in reminders["upcoming"]
    ) or '<li class="rounded-lg border border-dashed border-line bg-slate-50 px-3.5 py-5 text-center text-slate-500">No upcoming reminders.</li>'
    failures = "".join(
        '<li class="rounded-lg border border-red-100 bg-red-50/50 px-3.5 py-3">'
        f'<div class="flex items-start justify-between gap-3"><p class="min-w-0 truncate font-medium text-slate-800">{text(item["title"])}</p>'
        f'<span class="shrink-0 text-xs font-medium text-red-700">Attempt {text(item["attempt_count"])}</span></div>'
        f'<p class="mt-1.5 line-clamp-2 text-xs leading-5 text-slate-600">{text(item["error"])}</p>'
        f'<p class="mt-1.5 text-xs text-slate-500">{timestamp(item["updated_at"])}</p></li>'
        for item in reminders["recent_failures"]
    ) or '<li class="rounded-lg border border-dashed border-line bg-slate-50 px-3.5 py-5 text-center text-slate-500">No failed deliveries.</li>'
    notices = "".join(f"<li>{text(notice)}</li>" for notice in snapshot["notices"])
    notices = notices or "<li>No notices.</li>"
    return render_template(
        "dashboard.html",
        {
            "status": text(snapshot["status"]),
            "status_badge": status_badge(snapshot["status"]),
            "service_cards": service_cards,
            "message_total": text(messages["total"]),
            "message_last_24_hours": text(messages["last_24_hours"]),
            "message_latest_at": timestamp(messages["latest_at"]),
            "message_roles": count_tiles(messages["by_role"], ("user", "assistant")),
            "memory_total": text(memories["total"]),
            "expiring_memories": text(memories["expiring_within_7_days"]),
            "memory_kinds": count_tiles(memories["by_kind"], ("fact", "person", "preference", "project", "routine")),
            "task_statuses": count_tiles(tasks["by_status"], ("active", "completed", "cancelled")),
            "reminder_statuses": count_tiles(reminders["by_status"], ("pending", "leased", "delivered", "failed")),
            "overdue_pending": text(reminders["overdue_pending"]),
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
