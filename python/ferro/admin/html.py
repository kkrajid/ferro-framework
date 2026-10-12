"""Small HTML builder for the admin. Every value goes through :func:`escape`; markup is only
produced by the helpers here, so user data cannot inject tags (Feature 008 SC-003)."""

from __future__ import annotations

import base64
import hashlib
import html
from typing import Any

from ferro.response import Response

__all__ = ["Markup", "escape", "page", "tag"]


class Markup(str):
    """Text that is already HTML."""

    __slots__ = ()


def escape(value: Any) -> Markup:
    if isinstance(value, Markup):
        return value
    return Markup(html.escape("" if value is None else str(value), quote=True))


def tag(element: str, /, *children: Any, **attributes: Any) -> Markup:
    """``tag("a", "text", href="/x", class_="y")``; boolean attributes are written when true and
    omitted when false or None."""
    parts = [element]
    for key, value in attributes.items():
        key = key.rstrip("_").replace("_", "-")
        if value is None or value is False:
            continue
        if value is True:
            parts.append(key)
        else:
            parts.append(f'{key}="{escape(value)}"')
    opening = f"<{' '.join(parts)}>"
    if element in {"input", "meta", "br", "link"}:
        return Markup(opening)
    inner = "".join(escape(child) for child in children if child is not None)
    return Markup(f"{opening}{inner}</{element}>")


def join(*items: Any) -> Markup:
    return Markup("".join(escape(item) for item in items if item is not None))


CSS = """
:root{color-scheme:light dark;--fg:#1d2433;--muted:#5b6475;--line:#d9dde5;--bg:#f6f7f9;
--card:#fff;--accent:#1f5fbf;--danger:#b42318;--ok:#1a7f37}
@media (prefers-color-scheme:dark){:root{--fg:#e6e9ef;--muted:#9aa3b2;--line:#323a48;
--bg:#14181f;--card:#1b2029;--accent:#7aa7ff;--danger:#ff8a80;--ok:#6fdc8c}}
*{box-sizing:border-box}body{margin:0;font:15px/1.5 system-ui,sans-serif;color:var(--fg);
background:var(--bg)}header{display:flex;justify-content:space-between;align-items:center;
padding:12px 24px;background:var(--card);border-bottom:1px solid var(--line)}
header a{font-weight:600;color:var(--fg);text-decoration:none}main{padding:24px;max-width:1100px}
a{color:var(--accent)}table{border-collapse:collapse;width:100%;background:var(--card)}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line)}
.bar{display:flex;gap:16px;flex-wrap:wrap;align-items:center;margin-bottom:16px}
.filters{display:flex;gap:24px;flex-wrap:wrap;margin-bottom:16px;color:var(--muted)}
.filters a{margin-right:8px}.message{padding:10px 14px;border-left:4px solid var(--ok);
background:var(--card);margin-bottom:16px}.error,.errors{color:var(--danger)}
form.edit label{display:block;font-weight:600;margin-top:14px}
input,select,textarea{font:inherit;padding:6px 8px;border:1px solid var(--line);
border-radius:4px;background:var(--card);color:var(--fg);max-width:100%}
textarea{width:100%;min-height:6em}select[multiple]{min-height:8em}
button{font:inherit;padding:6px 14px;border-radius:4px;border:1px solid var(--accent);
background:var(--accent);color:#fff;cursor:pointer}button.link{background:none;
color:var(--accent);border:none;padding:0}.danger{background:var(--danger);
border-color:var(--danger)}.actions{margin-top:20px;display:flex;gap:16px;align-items:center}
.inline{display:inline}.readonly{padding:6px 0}
""".strip()

_CSS_HASH = base64.b64encode(hashlib.sha256(CSS.encode()).digest()).decode()

CONTENT_SECURITY_POLICY = (
    "default-src 'none'; "
    f"style-src 'sha256-{_CSS_HASH}'; "
    "img-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
)
HEADERS = {"content-security-policy": CONTENT_SECURITY_POLICY, "cache-control": "no-store"}


def page(
    title: str,
    body: Any,
    *,
    prefix: str,
    user: Any = None,
    message: str | None = None,
    status: int = 200,
) -> Response:
    account = None
    if user is not None and getattr(user, "is_authenticated", False):
        account = tag(
            "form",
            escape(user.username),
            " ",
            tag("button", "Log out", type="submit", class_="link"),
            method="post",
            action=f"{prefix}/logout",
            class_="inline",
        )
    document = join(
        Markup("<!doctype html>"),
        tag(
            "html",
            tag(
                "head",
                tag("meta", charset="utf-8"),
                tag("meta", name="viewport", content="width=device-width, initial-scale=1"),
                tag("title", f"{title} | Ferro admin"),
                tag("style", Markup(CSS)),
            ),
            tag(
                "body",
                tag("header", tag("a", "Ferro administration", href=prefix), account),
                tag(
                    "main",
                    tag("h1", title),
                    tag("div", message, class_="message") if message else None,
                    body,
                ),
            ),
            lang="en",
        ),
    )
    return Response(
        str(document), status=status, headers=HEADERS, media_type="text/html; charset=utf-8"
    )
