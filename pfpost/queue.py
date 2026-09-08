"""Local scheduling queue.

Pixelfed has no server-side scheduling - there is no `scheduled_at` on
POST /api/v1/statuses - so "scheduled" posts are a local queue plus a clock.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import api, store

MAX_ATTEMPTS = 3


class QueueError(Exception):
    pass


def parse_when(text: str) -> datetime:
    """ISO-8601, 'YYYY-MM-DD HH:MM' in local time, or relative '+90m/+2h/+3d'."""
    text = (text or "").strip()
    if not text:
        raise QueueError("No time given.")
    if text.startswith("+"):
        unit = text[-1].lower()
        seconds = {"m": 60, "h": 3600, "d": 86400}.get(unit)
        if not seconds:
            raise QueueError("Unknown unit in %r. Use m, h or d." % text)
        try:
            amount = float(text[1:-1])
        except ValueError:
            raise QueueError("Could not parse %r. Try forms like +90m, +2h, +3d." % text)
        return datetime.now(timezone.utc) + timedelta(seconds=amount * seconds)
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).astimezone()
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.astimezone()
    except ValueError:
        raise QueueError("Could not parse time %r. Try '2026-09-10 17:00' or '+2h'." % text)


def local_str(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%Y-%m-%d %H:%M")
    except Exception:
        return iso


def add(images, caption: str, visibility: str, when: datetime) -> dict:
    queue = store.load_queue()
    item = {
        "id": uuid.uuid4().hex[:8],
        "created": datetime.now(timezone.utc).isoformat(),
        "post_at": when.astimezone(timezone.utc).isoformat(),
        "caption": caption,
        "visibility": visibility,
        "images": [{"path": str(p), "alt": a} for p, a in images],
        "status": "pending",
        "attempts": 0,
    }
    queue["items"].append(item)
    store.save_queue(queue)
    return item


def items(include_done: bool = False) -> list[dict]:
    rows = store.load_queue()["items"]
    if not include_done:
        rows = [i for i in rows if i["status"] == "pending"]
    return sorted(rows, key=lambda i: i["post_at"])


def remove(item_id: str) -> None:
    queue = store.load_queue()
    remaining = [i for i in queue["items"] if i["id"] != item_id]
    if len(remaining) == len(queue["items"]):
        raise QueueError("No queue item with id %s" % item_id)
    queue["items"] = remaining
    store.save_queue(queue)


def due(now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    return [i for i in items()
            if datetime.fromisoformat(i["post_at"]) <= now]


def run(session, limit: int = 0, on_event=None) -> list[dict]:
    """Publish everything due. on_event(kind, item, detail) for progress.

    Each item is saved as it completes, so an interrupted run never loses work.
    """
    def emit(kind, item, detail=""):
        if on_event:
            on_event(kind, item, detail)

    pending = due()
    if limit:
        pending = pending[:limit]
    if not pending:
        return []

    client = session.client()
    limits = client.limits()
    processed = []

    for item in pending:
        queue = store.load_queue()
        live = next((i for i in queue["items"] if i["id"] == item["id"]), None)
        if live is None or live["status"] != "pending":
            continue

        emit("start", live)
        images = [(Path(i["path"]), i["alt"]) for i in live["images"]]
        missing = [str(p) for p, _ in images if not p.exists()]
        if missing:
            live["status"] = "failed"
            live["error"] = "missing files: %s" % ", ".join(missing)
            store.save_queue(queue)
            emit("failed", live, live["error"])
            processed.append(live)
            continue

        live["attempts"] = live.get("attempts", 0) + 1
        try:
            api.validate(images, live["caption"], limits)
            result = client.publish(images, live["caption"], live["visibility"])
            live["status"] = "posted"
            live["result_url"] = result.get("url")
            live["posted_at"] = datetime.now(timezone.utc).isoformat()
            emit("posted", live, live.get("result_url") or "")
        except (api.ApiError, api.ValidationError) as exc:
            live["error"] = str(exc)[:400]
            if live["attempts"] >= MAX_ATTEMPTS or isinstance(exc, api.ValidationError):
                live["status"] = "failed"
                emit("failed", live, live["error"])
            else:
                emit("retry", live, live["error"])
        store.save_queue(queue)
        processed.append(live)

    return processed
