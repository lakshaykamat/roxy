import html
import logging
import math
import secrets
import sqlite3
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

from src import config
from src.core.errors import try_catch
from src.conversations.history import database_connection
from src.dashboard import service as dashboard

logger = logging.getLogger(__name__)
TEMPLATE_DIRECTORY = Path(__file__).with_name("templates")
app = FastAPI()
app.mount("/assets", StaticFiles(directory=Path(__file__).with_name("static")), name="assets")
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


def _shared_tags(first: dict[str, object], second: dict[str, object]) -> bool:
    return bool(set(first.get("tags", [])) & set(second.get("tags", [])))


def _brain_links(items: list[dict[str, object]]) -> list[tuple[int, int]]:
    links: list[tuple[int, int]] = []
    link_counts = [0] * len(items)
    for first_index, first_item in enumerate(items):
        for second_index in range(first_index + 1, len(items)):
            second_item = items[second_index]
            related = _shared_tags(first_item, second_item) or first_item.get("item_type") == second_item.get("item_type")
            if related and link_counts[first_index] < 3 and link_counts[second_index] < 3:
                links.append((first_index, second_index))
                link_counts[first_index] += 1
                link_counts[second_index] += 1
    return links


def _brain_node_position(index: int, total: int) -> tuple[int, int]:
    if total == 1:
        return (50, 50)
    angle = (index / total) * 6.283185307
    radius = 28 + (index % 3) * 8
    return (round(50 + radius * math.cos(angle)), round(50 + radius * math.sin(angle)))


def _brain_graph(items: list[dict[str, object]]) -> str:
    if not items:
        return '<p class="empty-state">Save something in Telegram to start growing your graph.</p>'
    positions = [_brain_node_position(index, len(items)) for index in range(len(items))]
    links = "".join(
        f'<line class="brain-link" x1="{positions[first][0]}" y1="{positions[first][1]}" x2="{positions[second][0]}" y2="{positions[second][1]}" />'
        for first, second in _brain_links(items)
    )
    nodes = "".join(
        f'<g class="brain-node brain-node--{_text(item.get("item_type", "note"))}" '
        f'data-select-item="{_text(item["id"])}" tabindex="0" role="button" '
        f'aria-label="Open {_text(item["title"])}"><title>{_text(item["title"])}</title>'
        f'<circle cx="{positions[index][0]}" cy="{positions[index][1]}" r="5" />'
        f'<text x="{positions[index][0]}" y="{positions[index][1] + 10}">{_text(str(item["title"])[:18])}</text></g>'
        for index, item in enumerate(items)
    )
    return f'<svg class="brain-graph" viewBox="0 0 100 100" role="img" aria-label="Brain network with {len(items)} saved items">{links}{nodes}</svg>'


def render_dashboard(snapshot: dict[str, object]) -> str:
    messages = snapshot["messages"]
    memories = snapshot["memories"]
    tasks = snapshot["tasks"]
    reminders = snapshot["reminders"]
    configuration = snapshot["configuration"]

    def rows(values: dict[str, object], fields: tuple[tuple[str, str], ...]) -> str:
        return "".join(
            f'<div class="data-row"><dt>{label}</dt><dd>{_text(values.get(key, 0))}</dd></div>'
            for label, key in fields
        )

    activity_rows = "".join((
        f'<div class="data-row"><dt>Messages</dt><dd>{_text(messages["total"])}</dd></div>',
        f'<div class="data-row"><dt>Saved items</dt><dd>{_text(memories["total"])}</dd></div>',
        f'<div class="data-row"><dt>Latest message</dt><dd>{_timestamp(messages["latest_at"])}</dd></div>',
        rows(messages["by_role"], (("messages_user", "user"), ("messages_assistant", "assistant"))),
        rows(memories["by_kind"], tuple((f"memory_{kind}", kind) for kind in memories["by_kind"])),
    ))
    queue_rows = "".join((
        rows(tasks["by_status"], (("tasks_active", "active"), ("tasks_completed", "completed"), ("tasks_cancelled", "cancelled"))),
        rows(reminders["by_status"], (("reminders_pending", "pending"), ("reminders_leased", "leased"), ("reminders_delivered", "delivered"), ("reminders_failed", "failed"))),
        f'<div class="data-row"><dt>Overdue reminders</dt><dd>{_text(reminders["overdue_pending"])}</dd></div>',
    ))
    configuration_rows = "".join(
        f'<div class="data-row"><dt>{_text(key.replace("_", " "))}</dt><dd>{_text(value)}</dd></div>'
        for key, value in configuration.items()
    )
    upcoming = "".join(
        f'<li><strong>{_text(item["title"])}</strong><span>{_timestamp(item["scheduled_at"])}</span></li>'
        for item in reminders["upcoming"]
    ) or '<li class="empty-state">No upcoming reminders.</li>'
    failures = "".join(
        f'<li><strong>{_text(item["title"])}</strong><span>{_text(item["error"])}</span></li>'
        for item in reminders["recent_failures"]
    ) or '<li class="empty-state">No failed deliveries.</li>'
    return render_template("dashboard.html", {
        "status": _text(snapshot["status"]), "service_rows": "", "activity_rows": activity_rows,
        "queue_rows": queue_rows, "upcoming": upcoming, "failures": failures,
        "configuration": configuration_rows, "notices": "", "generated_at": _timestamp(snapshot["generated_at"]),
    })


def render_brain_explorer(snapshot: dict[str, object], query: str = "") -> str:
    items = snapshot["items"]
    item_markup = "".join(
        '<li class="brain-list-item" data-brain-item '
        f'data-search="{_text(item["title"])} {_text(item["summary"])}">'
        f'<button data-select-item="{_text(item["id"])}" type="button"><span>{_text(item["title"])}</span><small>{_text(item.get("item_type", "note"))}</small></button></li>'
        for item in items
    ) or '<li class="empty-state">No saved items found.</li>'
    details = "".join(
        f'<article class="brain-detail" data-item-detail data-item-id="{_text(item["id"])}" {"hidden" if index else ""}>'
        f'<p class="eyebrow">{_text(item.get("item_type", "note"))}</p><h3>{_text(item["title"])}</h3><p>{_text(item["summary"])}</p>'
        f'<p class="detail-meta">Saved {_timestamp(item["captured_at"])}</p>'
        + (f'<a class="detail-source" href="{_text(item["source_url"])}" rel="noreferrer" target="_blank">Open source</a>' if item["source_url"] else "")
        + f'<div class="detail-actions"><button class="button button--quiet" data-archive-item="{_text(item["id"])}" type="button">Archive</button>'
        + f'<button class="button button--danger" data-delete-item="{_text(item["id"])}" data-delete-title="{_text(item["title"])}" type="button">Delete</button></div></article>'
        for index, item in enumerate(items)
    ) or '<p class="empty-state">No saved items.</p>'
    return render_template(
        "brain.html",
        {"items": item_markup, "details": details, "graph": _brain_graph(items), "query": _text(query)},
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
