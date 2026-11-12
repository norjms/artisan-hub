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

# The shared ArtisanHub look (artisanhub-ui glass-dark values), written out because this page is served on its own,
# without the hub's front end. Fonts are the hub's copies under /theme/fonts (SIL Open Font License).
_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<title>Choose your password - ArtisanHub</title>
<style>
@font-face {{ font-family: Manrope; font-weight: 400; src: url(/theme/fonts/manrope-latin-400-normal.woff2) format("woff2"); }}
@font-face {{ font-family: Manrope; font-weight: 600; src: url(/theme/fonts/manrope-latin-600-normal.woff2) format("woff2"); }}
@font-face {{ font-family: Sora; font-weight: 500; src: url(/theme/fonts/sora-latin-500-normal.woff2) format("woff2"); }}
:root {{ --text: #f1f6fa; --text-2: rgba(236,243,248,0.85); --accent: #8fd3ff; --on-accent: #1d2a34;
  --glass: rgba(255,255,255,0.08); --glass-line: rgba(255,255,255,0.16); --field: rgba(20,30,38,0.5);
  --field-line: rgba(255,255,255,0.2); --bad: #ffd0d0; }}
@media (prefers-reduced-transparency: reduce) {{ :root {{ --glass: #26343f; }} }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; min-height: 100vh; display: grid; place-items: center; padding: 24px; color: var(--text);
  background: radial-gradient(80% 75% at 55% 35%, #3f566a 0%, #273642 50%, #172028 100%) fixed #172028;
  font: 14px/1.5 Manrope, system-ui, sans-serif; -webkit-font-smoothing: antialiased; }}
.wrap {{ width: min(520px, 100%); }}
.brand {{ display: flex; align-items: center; justify-content: center; gap: 10px; margin-bottom: 18px;
  font: 600 20px Sora, Manrope, sans-serif; letter-spacing: -0.02em; }}
.tile {{ width: 36px; height: 36px; border-radius: 12px; background: var(--accent); display: grid; place-items: center; }}
main {{ display: flex; flex-direction: column; gap: 16px; padding: 28px; background: var(--glass);
  border: 1px solid var(--glass-line); border-radius: 22px; backdrop-filter: blur(24px); -webkit-backdrop-filter: blur(24px); }}
h1 {{ font: 500 30px/1.1 Sora, Manrope, sans-serif; letter-spacing: -0.03em; margin: 0; }}
p {{ color: var(--text-2); margin: 0; }}
form {{ display: flex; flex-direction: column; gap: 16px; }}
label {{ display: flex; flex-direction: column; gap: 6px; font-size: 12px; color: var(--text-2); }}
input {{ height: 38px; padding: 0 12px; font: 14px Manrope, system-ui, sans-serif; color: var(--text); background: var(--field);
  border: 1px solid var(--field-line); border-radius: 10px; }}
input:focus {{ outline: none; box-shadow: 0 0 0 2px #172028, 0 0 0 4px var(--accent); }}
button {{ height: 38px; font: 600 14px Manrope, system-ui, sans-serif; color: var(--on-accent); background: var(--accent);
  border: 0; border-radius: 12px; cursor: pointer; }}
.error {{ color: var(--bad); }}
</style></head>
<body><div class="wrap"><div class="brand"><span class="tile"><svg width="18" height="18" viewBox="0 0 24 24" fill="none"
 stroke="#1d2a34" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 2 3 7v10l9 5 9-5V7l-9-5z"/><path
 d="M3 7l9 5 9-5M12 12v10"/></svg></span>ArtisanHub</div><main>{body}</main></div></body></html>"""


def _page(body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(_PAGE.format(body=body), status_code=status,
                        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


def _form(uid: str, error: str = "") -> HTMLResponse:
    err = f'<p class="error" role="alert">{html.escape(error)}</p>' if error else ""
    return _page(
        "<h1>Choose your password</h1>"
        f"<p>For your ArtisanHub login, <strong>{html.escape(uid)}</strong>. At least {MIN_LENGTH} characters."
        " This link works once.</p>"
        '<form method="post" autocomplete="on">'
        f'<input type="text" name="username" value="{html.escape(uid)}" autocomplete="username" hidden>'
        '<label for="pw">New password'
        f'<input id="pw" name="password" type="password" minlength="{MIN_LENGTH}" autocomplete="new-password" required></label>'
        '<label for="pw2">The same again'
        '<input id="pw2" name="again" type="password" autocomplete="new-password" required></label>'
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
