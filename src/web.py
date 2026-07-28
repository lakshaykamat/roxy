import html
import logging
import math
import secrets
import sqlite3
from pathlib import Path
from urllib.parse import parse_qs
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel
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


def render_brain_explorer(snapshot: dict[str, object]) -> str:
    timeline = snapshot["timeline"]
    items = snapshot["items"]

    def text(value: object) -> str:
        return html.escape(str(value if value is not None else "Not recorded"))

    def timestamp(value: object) -> str:
        if value is None:
            return '<span class="text-slate-500">Not recorded</span>'
        escaped_value = text(value)
        return f'<time class="js-local-time" datetime="{escaped_value}">{escaped_value}</time>'

    def source_url_markup(value: object) -> str:
        url = str(value)
        parsed_url = urlparse(url)
        if parsed_url.scheme in {"http", "https"} and parsed_url.netloc:
            return f'<a class="break-all text-accent underline" href="{text(url)}" rel="noreferrer">{text(url)}</a>'
        return f'<p class="break-all">{text(url)}</p>'

    timeline_markup = "".join(
        '<li class="border-b border-line px-3 py-3 last:border-0">'
        f'<p class="font-bold">{text(capture["request"])}</p>'
        f'<p class="mt-1 text-xs">{timestamp(capture["captured_at"])}</p>'
        f'<p class="mt-2 text-xs leading-5">{text(capture["analysis"])}</p>'
        '</li>'
        for capture in timeline
    ) or '<li class="px-3 py-4 text-xs">(none recorded)</li>'
    def tag_markup(tags: object, limit: int = 3) -> str:
        values = tags if isinstance(tags, list) else []
        return "".join(
            f'<span class="brain-tag">{text(tag)}</span>' for tag in values[:limit]
        )

    item_markup = "".join(
        '<li class="brain-item border-b border-line last:border-0" data-brain-item '
        f'data-item-id="{text(item["id"])}" data-search="{text(item["title"])} {text(item["summary"])} {text(" ".join(item["tags"]))}">'
        f'<button class="min-h-14 w-full px-3 py-3 text-left focus:outline-none focus:ring-2 focus:ring-inset focus:ring-accent" data-select-item="{text(item["id"])}" type="button">'
        f'<span class="block font-bold">{text(item["title"])}</span><span class="mt-1 block text-xs">{text(item["item_type"])} · {len(item["relations"])} direct links</span>{tag_markup(item["tags"])}'
        '</button></li>'
        for item in items
    ) or '<li class="px-3 py-4 text-xs">(none recorded)</li>'
    relation_rows: list[tuple[object, object, object, object, object, object, object]] = []
    relation_keys: set[tuple[str, str, str, str]] = set()
    item_titles = {str(item["id"]): str(item["title"]) for item in items}
    for item in items:
        for relation in item["relations"]:
            source_id = str(item["id"])
            target_id = str(relation["related_item_id"])
            key = tuple(sorted((source_id, target_id))) + (
                str(relation["relation_type"]), str(relation.get("origin", ""))
            )
            if key not in relation_keys:
                relation_keys.add(key)
                relation_rows.append((source_id, target_id, item_titles.get(source_id, item["title"]), relation["related_item_title"], relation["relation_type"], relation["explanation"], relation.get("origin", "Not recorded")))
    relation_markup = "".join(
        '<li><button class="brain-relation focus:ring-2 focus:ring-inset focus:ring-accent" '
        f'data-relation-source="{text(source_id)}" data-relation-target="{text(target_id)}" data-select-item="{text(source_id)}" type="button">'
        f'<span class="font-bold">{text(source_title)} <span aria-hidden="true">↔</span> {text(target_title)}</span>'
        f'<span class="relation-kind">{text(relation_type)} · {text(origin)}</span>'
        f'<span class="relation-explanation">{text(explanation)}</span></button></li>'
        for source_id, target_id, source_title, target_title, relation_type, explanation, origin in relation_rows
    ) or '<li class="px-3 py-4 text-xs">No stored relationships yet.</li>'

    positions: dict[str, tuple[float, float]] = {}
    node_count = len(items)
    for index, item in enumerate(items):
        angle = (2 * math.pi * index / max(node_count, 1)) - (math.pi / 2)
        positions[str(item["id"])] = (450 + 300 * math.cos(angle), 245 + 165 * math.sin(angle))

    graph_edges = "".join(
        f'<line class="brain-edge" data-edge-source="{text(source_id)}" data-edge-target="{text(target_id)}" '
        f'x1="{positions[str(source_id)][0]:.1f}" y1="{positions[str(source_id)][1]:.1f}" '
        f'x2="{positions[str(target_id)][0]:.1f}" y2="{positions[str(target_id)][1]:.1f}"><title>{text(source_title)} {text(relation_type)} {text(target_title)}</title></line>'
        for source_id, target_id, source_title, target_title, relation_type, _, _ in relation_rows
        if str(source_id) in positions and str(target_id) in positions
    )

    def graph_label(value: object, limit: int = 19) -> str:
        label = str(value)
        return text(label if len(label) <= limit else f"{label[:limit - 1]}…")

    graph_nodes = "".join(
        '<g class="brain-node" role="button" tabindex="0" data-map-node '
        f'data-item-id="{text(item["id"])}" data-item-title="{text(item["title"])}" data-select-item="{text(item["id"])}" '
        f'transform="translate({positions[str(item["id"])][0]:.1f} {positions[str(item["id"])][1]:.1f})">'
        f'<title>{text(item["title"])} — {text(item["item_type"])}; {len(item["relations"])} direct links</title>'
        '<circle r="36"></circle>'
        f'<text class="brain-node-title" y="-3">{graph_label(item["title"])}</text>'
        f'<text class="brain-node-meta" y="13">{text(item["item_type"])}</text></g>'
        for item in items
    )
    connection_markup = (
        '<svg class="brain-network" viewBox="0 0 900 490" role="img" aria-label="Network graph of stored brain records and their direct relationships">'
        '<desc>Lines show stored direct relationships. Select a record to focus its connections.</desc>'
        f'{graph_edges}{graph_nodes}</svg>'
        if items else '<p class="p-3 text-xs">No active records to explore.</p>'
    )

    tag_groups: dict[str, list[dict[str, object]]] = {}
    for item in items:
        for tag in item["tags"]:
            tag_groups.setdefault(str(tag), []).append(item)
    cluster_markup = "".join(
        '<div class="brain-cluster"><button data-select-item="'
        f'{text(group_items[0]["id"])}" type="button"><span class="font-bold">{text(tag)}</span> <span class="text-xs">({len(group_items)})</span></button>'
        f'<small>{text(" · ".join(str(item["title"]) for item in group_items[:3]))}</small></div>'
        for tag, group_items in sorted(tag_groups.items(), key=lambda group: (-len(group[1]), group[0]))[:6]
    ) or '<p class="mt-3 text-xs">No tagged clusters yet.</p>'
    details_markup = "".join(
        '<article class="brain-detail" data-item-detail '
        f'data-item-id="{text(item["id"])}" {"hidden" if index else ""}>'
        f'<h3 class="text-lg font-bold">{text(item["title"])}</h3><p class="mt-2 text-xs leading-5">{text(item["summary"])}</p>'
        f'<p class="mt-3 text-xs">SAVED: {timestamp(item["captured_at"])} · STATE: {text(item["source_state"])}</p>'
        + (f'<p class="mt-2 text-xs">SOURCE: {source_url_markup(item["source_url"])}</p>' if item["source_url"] else "")
        + '<div class="mt-4"><p class="font-bold text-xs">DIRECT_CONNECTIONS</p><ul class="mt-2 space-y-2 text-xs">'
        + "".join(
            f'<li class="brain-detail-connection"><span class="font-bold">{text(relation["related_item_title"])}</span><br><span>{text(relation["relation_type"])} · {text(relation.get("origin", "Not recorded"))} · {text(f"{float(relation.get('confidence', 0)):.0%}")}</span><br>{text(relation["explanation"])}</li>'
            for relation in item["relations"]
        )
        + ('</ul></div>' if item["relations"] else '<li class="mt-2 text-xs">No direct stored relationships yet.</li></ul></div>')
        + '<div class="mt-5 flex gap-2"><button class="min-h-11 border-2 border-ink px-3 py-1 text-xs font-bold hover:bg-ink hover:text-panel focus:outline-none focus:ring-2 focus:ring-accent" data-archive-item="'
        + text(item["id"])
        + '" type="button">ARCHIVE</button><button class="min-h-11 border-2 border-alert px-3 py-1 text-xs font-bold text-alert hover:bg-alert hover:text-panel focus:outline-none focus:ring-2 focus:ring-alert" data-delete-item="'
        + text(item["id"])
        + '" data-delete-title="'
        + text(item["title"])
        + '" type="button">DELETE</button></div></article>'
        for index, item in enumerate(items)
    ) or '<p class="text-xs">Select a saved item to inspect it.</p>'
    return render_template(
        "brain.html",
        {"timeline": timeline_markup, "items": item_markup, "connections": connection_markup, "clusters": cluster_markup, "relations": relation_markup, "details": details_markup, "relationship_count": str(len(relation_rows))},
    )


def load_snapshot() -> dict[str, object] | None:
    def unavailable(_: BaseException) -> None:
        logger.exception("Unable to load dashboard snapshot")
        return None

    return try_catch(
        dashboard.get_dashboard_snapshot,
        handle_error=unavailable,
        exception_types=sqlite3.Error,
    )


def load_brain_snapshot() -> dict[str, object] | None:
    def unavailable(_: BaseException) -> None:
        logger.exception("Unable to load Brain explorer snapshot")
        return None

    return try_catch(
        dashboard.get_brain_snapshot,
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


@app.get("/brain")
async def brain_page(request: Request):
    if redirect := dashboard_redirect(request):
        return redirect
    snapshot = load_brain_snapshot()
    if snapshot is None:
        return HTMLResponse(render_template("unavailable.html", {}), status_code=503)
    return HTMLResponse(render_brain_explorer(snapshot))


@app.get("/brain-data")
async def brain_data(request: Request):
    if redirect := dashboard_redirect(request):
        return redirect
    snapshot = load_brain_snapshot()
    if snapshot is None:
        return JSONResponse({"status": "unavailable"}, status_code=503)
    return JSONResponse(snapshot)


class DeleteRequest(BaseModel):
    confirmed: bool = False
    title: str = ""


def run_brain_action(operation: object) -> bool | None:
    return try_catch(
        operation,
        handle_error=lambda _: None,
        exception_types=sqlite3.Error,
    )


@app.post("/brain/items/{item_id}/archive")
async def archive_brain_item(item_id: int, request: Request):
    if redirect := dashboard_redirect(request):
        return redirect
    archived = run_brain_action(lambda: dashboard.archive_brain_item(item_id))
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
    delete_result = run_brain_action(
        lambda: dashboard.delete_brain_item(item_id, payload.title.strip())
    )
    if delete_result is None:
        return JSONResponse({"error": "Brain data is unavailable."}, status_code=503)
    if delete_result == "not_found":
        return JSONResponse({"error": "Brain item was not found."}, status_code=404)
    if delete_result != "deleted":
        return JSONResponse({"error": "The exact active item title did not match."}, status_code=409)
    return {"ok": True, "id": item_id, "action": "deleted"}


@app.post("/logout")
async def logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse("/login", status_code=303)

