"""Check GitHub for a newer release of pfpost.

Only ever run when the user asks - never on launch. It is the one request
pfpost makes to anyone other than your Pixelfed instance, and it carries
nothing about you: an anonymous GET with pfpost's User-Agent.
"""

from __future__ import annotations

import json
import re

from . import __version__, api

REPO = "TheRealestNwah/pfpost"
LATEST_API = "https://api.github.com/repos/%s/releases/latest" % REPO
RELEASES_PAGE = "https://github.com/%s/releases" % REPO


class UpdateError(Exception):
    pass


def parse_version(text: str) -> tuple[int, ...]:
    """'v1.0.4' -> (1, 0, 4). Anything after the numbers (a -dev suffix) is ignored."""
    match = re.match(r"\s*v?(\d+(?:\.\d+)*)", text or "")
    if not match:
        raise UpdateError("not a version: %r" % text)
    parts = [int(p) for p in match.group(1).split(".")]
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def is_newer(latest: str, current: str = __version__) -> bool:
    return parse_version(latest) > parse_version(current)


def check(current: str = __version__) -> dict:
    """{'current', 'latest', 'newer', 'url'} for the newest published release."""
    status, body = api.http("GET", LATEST_API, timeout=15)
    if status == 404:
        raise UpdateError("No releases are published yet.")
    if status == 403:
        raise UpdateError("GitHub is rate-limiting update checks from this network; "
                          "try again in an hour.")
    if status != 200:
        raise UpdateError("GitHub answered HTTP %d." % status)
    try:
        data = json.loads(body.decode("utf-8"))
        tag = data["tag_name"]
    except (ValueError, KeyError, TypeError):
        raise UpdateError("GitHub's reply was not a release.")
    # The link goes to the desktop's browser: accept only this repository's
    # own release pages, whatever the reply claims.
    url = data.get("html_url") or ""
    if not url.startswith("https://github.com/%s/releases/" % REPO):
        url = RELEASES_PAGE
    return {"current": current, "latest": tag.lstrip("v"),
            "newer": is_newer(tag, current),
            # A local build can be ahead of anything released; say so rather
            # than calling it "the latest".
            "ahead": parse_version(current) > parse_version(tag),
            "url": url}


def describe(info: dict) -> str:
    if info["newer"]:
        return "pfpost %s is available (you have %s)." % (info["latest"], info["current"])
    if info.get("ahead"):
        return ("pfpost %s is newer than the latest release (%s)."
                % (info["current"], info["latest"]))
    return "pfpost %s is the latest version." % info["current"]
