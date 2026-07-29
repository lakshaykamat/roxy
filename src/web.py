import html
import logging
import secrets
import sqlite3
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

from src import config
from src.core.errors import try_catch
from src.conversations.history import database_connection
from src.dashboard import service as dashboard

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

    return try_catch(check_database, handle_error=lambda _: False, exception_types=sqlite3.Error)


@app.get("/ready")
async def ready() -> JSONResponse:
    status_code = 200 if database_is_available() else 503
    return JSONResponse({"status": "ready" if status_code == 200 else "not_ready"}, status_code=status_code)


async def submitted_password(request: Request) -> str:
    parsed = parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)
    return parsed.get("password", [""])[0]


def dashboard_authenticated(request: Request) -> bool:
    return request.session.get("dashboard_authenticated") is True


def render_template(template_name: str, values: dict[str, str]) -> str:
    template = (TEMPLATE_DIRECTORY / template_name).read_text()
    for name, value in values.items():
        template = template.replace(f"{{{{{name}}}}}", value)
    return template


def login_page(error: bool = False) -> HTMLResponse:
    message = "<p>Incorrect password.</p>" if error else ""
    return HTMLResponse(render_template("login.html", {"error_message": message}), status_code=401 if error else 200)


@app.get("/login")
async def login():
    return login_page()


@app.post("/login")
async def authenticate(request: Request):
    if not secrets.compare_digest(await submitted_password(request), config.DASHBOARD_PASSWORD):
        return login_page(error=True)
    request.session["dashboard_authenticated"] = True
    return RedirectResponse("/", status_code=303)


def dashboard_redirect(request: Request) -> RedirectResponse | None:
    if not dashboard_authenticated(request):
        return RedirectResponse("/login", status_code=303)
    return None


def _text(value: object) -> str:
    return html.escape(str(value if value is not None else "Not recorded"))


def _timestamp(value: object) -> str:
    if value is None:
        return '<span class="text-slate-400">Not recorded</span>'
    escaped_value = _text(value)
    return f'<time class="js-local-time" datetime="{escaped_value}">{escaped_value}</time>'


def render_dashboard(snapshot: dict[str, object]) -> str:
    messages = snapshot["messages"]
    memories = snapshot["memories"]
    tasks = snapshot["tasks"]
    reminders = snapshot["reminders"]
    configuration = snapshot["configuration"]

    def rows(values: dict[str, object], fields: tuple[tuple[str, str], ...]) -> str:
        return "".join(
            f'<div><dt>{label}</dt><dd class="font-bold tabular-nums">{_text(values.get(key, 0))}</dd></div>'
            for label, key in fields
        )

    activity_rows = "".join((
        f'<div><dt>MESSAGE_TOTAL</dt><dd>{_text(messages["total"])}</dd></div>',
        f'<div><dt>MEMORY_TOTAL</dt><dd>{_text(memories["total"])}</dd></div>',
        f'<div><dt>LATEST_MESSAGE_AT</dt><dd>{_timestamp(messages["latest_at"])}</dd></div>',
        rows(messages["by_role"], (("messages_user", "user"), ("messages_assistant", "assistant"))),
        rows(memories["by_kind"], tuple((f"memory_{kind}", kind) for kind in memories["by_kind"])),
    ))
    queue_rows = "".join((
        rows(tasks["by_status"], (("tasks_active", "active"), ("tasks_completed", "completed"), ("tasks_cancelled", "cancelled"))),
        rows(reminders["by_status"], (("reminders_pending", "pending"), ("reminders_leased", "leased"), ("reminders_delivered", "delivered"), ("reminders_failed", "failed"))),
        f'<div><dt>REMINDERS_OVERDUE</dt><dd>{_text(reminders["overdue_pending"])}</dd></div>',
    ))
    configuration_rows = "".join(
        f'<div><dt>{_text(key)}</dt><dd>{_text(value)}</dd></div>'
        for key, value in configuration.items()
    )
    upcoming = "".join(
        f'<li class="px-3 py-3 text-xs"><p class="font-bold">{_text(item["title"])}</p><p>{_timestamp(item["scheduled_at"])}</p></li>'
        for item in reminders["upcoming"]
    ) or '<li class="px-3 py-4 text-xs">(none recorded)</li>'
    failures = "".join(
        f'<li class="px-3 py-3 text-xs"><p class="font-bold">{_text(item["title"])}</p><p>{_text(item["error"])}</p></li>'
        for item in reminders["recent_failures"]
    ) or '<li class="px-3 py-4 text-xs">(none recorded)</li>'
    return render_template("dashboard.html", {
        "status": _text(snapshot["status"]), "service_rows": "", "activity_rows": activity_rows,
        "queue_rows": queue_rows, "upcoming": upcoming, "failures": failures,
        "configuration": configuration_rows, "notices": "", "generated_at": _timestamp(snapshot["generated_at"]),
    })


def render_brain_explorer(snapshot: dict[str, object], query: str = "") -> str:
    items = snapshot["items"]
    item_markup = "".join(
        '<li class="border-b border-line px-3 py-3" data-brain-item '
        f'data-search="{_text(item["title"])} {_text(item["summary"])}">'
        f'<button data-select-item="{_text(item["id"])}" type="button">{_text(item["title"])}</button></li>'
        for item in items
    ) or '<li class="px-3 py-4 text-xs">(none recorded)</li>'
    details = "".join(
        f'<article data-item-detail data-item-id="{_text(item["id"])}" {"hidden" if index else ""}>'
        f'<h3>{_text(item["title"])}</h3><p>{_text(item["summary"])}</p>'
        f'<p>SAVED: {_timestamp(item["captured_at"])}</p>'
        + (f'<p>SOURCE: {_text(item["source_url"])}</p>' if item["source_url"] else "")
        + f'<button data-archive-item="{_text(item["id"])}" type="button">ARCHIVE</button>'
        + f'<button data-delete-item="{_text(item["id"])}" data-delete-title="{_text(item["title"])}" type="button">DELETE</button></article>'
        for index, item in enumerate(items)
    ) or '<p class="text-xs">No saved items.</p>'
    return render_template(
        "brain.html",
        {"items": item_markup, "details": details, "query": _text(query)},
    )


def load_snapshot() -> dict[str, object] | None:
    return try_catch(dashboard.get_dashboard_snapshot, handle_error=lambda _: None, exception_types=sqlite3.Error)


def load_brain_snapshot(query: str | None = None) -> dict[str, object] | None:
    return try_catch(
        lambda: dashboard.get_brain_snapshot(query),
        handle_error=lambda _: None,
        exception_types=sqlite3.Error,
    )


@app.get("/")
async def dashboard_page(request: Request):
    if redirect := dashboard_redirect(request):
        return redirect
    snapshot = load_snapshot()
    return HTMLResponse(render_dashboard(snapshot)) if snapshot else HTMLResponse(render_template("unavailable.html", {}), status_code=503)


@app.get("/dashboard-data")
async def dashboard_data(request: Request):
    if redirect := dashboard_redirect(request):
        return redirect
    snapshot = load_snapshot()
    return JSONResponse(snapshot) if snapshot else JSONResponse({"status": "unavailable"}, status_code=503)


@app.get("/brain")
async def brain_page(request: Request, query: str | None = None):
    if redirect := dashboard_redirect(request):
        return redirect
    snapshot = load_brain_snapshot(query)
    return (
        HTMLResponse(render_brain_explorer(snapshot, query or ""))
        if snapshot
        else HTMLResponse(render_template("unavailable.html", {}), status_code=503)
    )


@app.get("/brain-data")
async def brain_data(request: Request, query: str | None = None):
    if redirect := dashboard_redirect(request):
        return redirect
    snapshot = load_brain_snapshot(query)
    return JSONResponse(snapshot) if snapshot else JSONResponse({"status": "unavailable"}, status_code=503)


class DeleteRequest(BaseModel):
    confirmed: bool = False
    title: str = ""


@app.post("/brain/items/{item_id}/archive")
async def archive_brain_item(item_id: int, request: Request):
    if redirect := dashboard_redirect(request):
        return redirect
    archived = try_catch(lambda: dashboard.archive_brain_item(item_id), handle_error=lambda _: None, exception_types=sqlite3.Error)
    if archived is None:
        return JSONResponse({"error": "Brain data is unavailable."}, status_code=503)
    if not archived:
        return JSONResponse({"error": "Brain item not found."}, status_code=404)
    return {"ok": True, "id": item_id, "action": "archived"}


@app.post("/brain/items/{item_id}/delete")
async def delete_brain_item(item_id: int, payload: DeleteRequest, request: Request):
    if redirect := dashboard_redirect(request):
        return redirect
    if not payload.confirmed or not payload.title.strip():
        return JSONResponse({"error": "Exact title confirmation is required."}, status_code=409)
    result = try_catch(lambda: dashboard.delete_brain_item(item_id, payload.title.strip()), handle_error=lambda _: None, exception_types=sqlite3.Error)
    if result is None:
        return JSONResponse({"error": "Brain data is unavailable."}, status_code=503)
    if result == "not_found":
        return JSONResponse({"error": "Brain item was not found."}, status_code=404)
    if result != "deleted":
        return JSONResponse({"error": "The exact active item title did not match."}, status_code=409)
    return {"ok": True, "id": item_id, "action": "deleted"}


@app.post("/logout")
async def logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
