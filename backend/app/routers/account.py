"""The one-time page where a person chooses the password of their LLDAP login, /account/password/<token>.

Someone invited who had no login gets a link here; so does the hub's owner, from the installer. The password
goes from this page to LLDAP and nowhere else; the link works once. Plain server-rendered HTML, so it works
before sign-in and without the app's own screens.
"""
import html
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Path
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from ..clients import lldap_api
from ..clients.base import ApiError
from ..db import get_db
from ..services.teams import find_password_link
from ..tenant import unscoped

router = APIRouter()

TOKEN = r"^[A-Za-z0-9_-]{20,80}$"
MIN_LENGTH = 10

_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Choose your password - Artisan Hub</title>
<style>
:root {{ --bg: #f6f5f2; --panel: #fff; --text: #1d1d1b; --muted: #5f5e5a; --line: #dcdad4; --accent: #b4532a;
  --bad: #a3271f; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg: #161615; --panel: #212120; --text: #ecebe7;
  --muted: #a6a49e; --line: #3a3936; --accent: #e07a4b; --bad: #f08a80; }} }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; min-height: 100vh; display: grid; place-items: center; background: var(--bg);
  color: var(--text); font: 16px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; padding: 16px; }}
main {{ width: 100%; max-width: 420px; background: var(--panel); border: 1px solid var(--line);
  border-radius: 12px; padding: 28px; }}
h1 {{ font-size: 1.35rem; margin: 0 0 6px; }}
p {{ color: var(--muted); margin: 0 0 18px; }}
label {{ display: block; font-weight: 600; margin: 14px 0 6px; }}
input {{ width: 100%; padding: 10px 12px; font: inherit; color: var(--text); background: var(--bg);
  border: 1px solid var(--line); border-radius: 8px; }}
button {{ margin-top: 20px; width: 100%; padding: 11px; font: inherit; font-weight: 600; color: #fff;
  background: var(--accent); border: 0; border-radius: 8px; cursor: pointer; }}
.error {{ color: var(--bad); margin-top: 14px; }}
</style></head>
<body><main>{body}</main></body></html>"""


def _page(body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(_PAGE.format(body=body), status_code=status,
                        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


def _form(uid: str, error: str = "") -> HTMLResponse:
    err = f'<p class="error" role="alert">{html.escape(error)}</p>' if error else ""
    return _page(
        "<h1>Choose your password</h1>"
        f"<p>For your Artisan Hub login, <strong>{html.escape(uid)}</strong>. At least {MIN_LENGTH} characters."
        " This link works once.</p>"
        '<form method="post" autocomplete="on">'
        f'<input type="text" name="username" value="{html.escape(uid)}" autocomplete="username" hidden>'
        '<label for="pw">New password</label>'
        f'<input id="pw" name="password" type="password" minlength="{MIN_LENGTH}" autocomplete="new-password" required>'
        '<label for="pw2">The same again</label>'
        '<input id="pw2" name="again" type="password" autocomplete="new-password" required>'
        f"{err}<button type=\"submit\">Save and sign in</button></form>",
        400 if error else 200)


def _gone() -> HTMLResponse:
    return _page("<h1>This link does not work</h1><p>It was used already or it expired. Ask whoever sent it for a"
                 " new one.</p>", 410)


@router.get("/account/password/{token}", include_in_schema=False)
def password_page(token: str = Path(pattern=TOKEN), db: Session = Depends(get_db)):
    with unscoped():
        link = find_password_link(db, token)
    return _form(link.uid) if link else _gone()


@router.post("/account/password/{token}", include_in_schema=False)
def password_set(token: str = Path(pattern=TOKEN), password: str = Form(""), again: str = Form(""),
                 db: Session = Depends(get_db)):
    with unscoped():
        link = find_password_link(db, token)
        if not link:
            return _gone()
        if len(password) < MIN_LENGTH:
            return _form(link.uid, f"Use at least {MIN_LENGTH} characters.")
        if password != again:
            return _form(link.uid, "The two passwords are not the same.")
        ld = lldap_api.client()
        if ld is None:
            return _page("<h1>Passwords are not set here</h1><p>This hub does not manage logins.</p>", 409)
        try:
            ld.set_password(link.uid, password)
        except ApiError:
            return _form(link.uid, "The password could not be saved. Try again in a moment.")
        link.used_at = datetime.now(timezone.utc)
        db.commit()
    return RedirectResponse("/auth/login", status_code=303)
