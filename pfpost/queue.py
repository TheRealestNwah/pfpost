"""Local scheduling queue.

Pixelfed has no server-side scheduling - there is no `scheduled_at` on
POST /api/v1/statuses - so "scheduled" posts are a local queue plus a clock.
"""

from __future__ import annotations

import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import api, store

MAX_ATTEMPTS = 3
# Wait this long after each failed attempt. Without a backoff, a runner that
# checks every minute burns all three attempts inside three minutes, so one
# brief outage or a single 429 permanently fails a post that would have gone
# out fine ten minutes later.
RETRY_BACKOFF_SECONDS = (120, 600, 1800)


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
    """Queue a post, copying its images somewhere they cannot move or change.

    The originals stay where they are; `original` is kept only so the UI can
    show a familiar name.
    """
    item_id = uuid.uuid4().hex[:8]
    staged = []
    for index, (path, alt) in enumerate(images):
        source = Path(path)
        if not source.exists():
            store.discard_staged(item_id)
            raise QueueError("File not found: %s" % source)
        target = store.staged_dir(item_id) / ("%02d_%s" % (index, source.name))
        try:
            shutil.copy2(source, target)
        except OSError as exc:
            store.discard_staged(item_id)
            raise QueueError("Could not stage %s: %s" % (source.name, exc))
        staged.append({"path": str(target), "alt": alt, "original": str(source)})

    item = {
        "id": item_id,
        "created": datetime.now(timezone.utc).isoformat(),
        "post_at": when.astimezone(timezone.utc).isoformat(),
        "caption": caption,
        "visibility": visibility,
        "images": staged,
        "status": "pending",
        "attempts": 0,
    }
    queue = store.load_queue()
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
    store.discard_staged(item_id)


def remove_many(item_ids) -> int:
    """Drop several items at once. Returns how many were removed."""
    wanted = set(item_ids)
    if not wanted:
        return 0
    queue = store.load_queue()
    remaining = [i for i in queue["items"] if i["id"] not in wanted]
    removed = len(queue["items"]) - len(remaining)
    queue["items"] = remaining
    store.save_queue(queue)
    for item_id in wanted:
        store.discard_staged(item_id)
    return removed


def clear(statuses=("posted",)) -> int:
    """Drop every item in the given states. Returns how many went.

    Pending items are never touched by this - cancelling a scheduled post is a
    deliberate act, whereas tidying away finished ones is housekeeping.
    """
    wanted = set(statuses)
    if "pending" in wanted:
        raise QueueError("clear() will not drop pending posts; use remove().")
    queue = store.load_queue()
    dropped = [i["id"] for i in queue["items"] if i["status"] in wanted]
    queue["items"] = [i for i in queue["items"] if i["status"] not in wanted]
    store.save_queue(queue)
    for item_id in dropped:
        store.discard_staged(item_id)
    return len(dropped)


def prune_staged() -> int:
    """Delete staged copies with no queue item left. Returns how many folders.

    Staged files are the only thing here that grows without bound, so an
    interrupted removal must not leak a copy of every photo forever.
    """
    known = {i["id"] for i in store.load_queue()["items"]}
    removed = 0
    base = store.staged_dir()
    for folder in base.iterdir() if base.exists() else []:
        if folder.is_dir() and folder.name not in known:
            store.discard_staged(folder.name)
            removed += 1
    return removed


def counts() -> dict:
    tally = {"pending": 0, "posted": 0, "failed": 0}
    for item in store.load_queue()["items"]:
        tally[item["status"]] = tally.get(item["status"], 0) + 1
    return tally


def retry_at(item: dict) -> datetime | None:
    raw = item.get("next_attempt_at")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def due(now: datetime | None = None) -> list[dict]:
    """Pending items whose time has come and whose retry backoff has elapsed."""
    now = now or datetime.now(timezone.utc)
    ready = []
    for item in items():
        if datetime.fromisoformat(item["post_at"]) > now:
            continue
        wait_until = retry_at(item)
        if wait_until and wait_until > now:
            continue
        ready.append(item)
    return ready


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
            # The copies existed to survive until publication; that is done.
            store.discard_staged(live["id"])
            emit("posted", live, live.get("result_url") or "")
        except (api.ApiError, api.ValidationError) as exc:
            live["error"] = str(exc)[:400]
            # A rejected payload will be rejected identically next time, so
            # retrying it only delays telling the user.
            fatal = isinstance(exc, api.ValidationError)
            if fatal or live["attempts"] >= MAX_ATTEMPTS:
                live["status"] = "failed"
                live.pop("next_attempt_at", None)
                emit("failed", live, live["error"])
            else:
                wait = RETRY_BACKOFF_SECONDS[
                    min(live["attempts"] - 1, len(RETRY_BACKOFF_SECONDS) - 1)]
                live["next_attempt_at"] = (
                    datetime.now(timezone.utc) + timedelta(seconds=wait)).isoformat()
                emit("retry", live, "%s (next attempt in %d minutes)"
                     % (live["error"], round(wait / 60)))
        store.save_queue(queue)
        processed.append(live)

    return processed
