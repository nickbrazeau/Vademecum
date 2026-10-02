"""The HTML this server renders: one frame, one set of credential forms.

Two pages use it -- the OAuth consent page and the desk's sign-in -- and they
have to look and behave the same, because they are the same act: a learner
proving who they are before something is done in their name. No script, no
external asset, no cookie set by the page itself.
"""

from __future__ import annotations

import html

from starlette.responses import HTMLResponse, Response

def _csp(form_action: str) -> str:
    return (
        f"default-src 'none'; style-src 'unsafe-inline'; form-action {form_action}; "
        "base-uri 'none'; frame-ancestors 'none'"
    )


# Where a form on a page may end up. Browsers apply `form-action` to the
# redirect that follows a submission as well as to the submission itself, so
# the consent page -- whose approval is a redirect back to the client -- must
# name the places a registered client may redirect to: HTTPS anywhere, or HTTP
# on this machine, exactly the rule `register_client` enforces. The desk's
# sign-in only ever redirects to itself.
FORM_ACTION_SELF = "'self'"
FORM_ACTION_CLIENTS = "'self' https: http://127.0.0.1:* http://localhost:*"

SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "Content-Security-Policy": _csp(FORM_ACTION_SELF),
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    # `same-origin`, not `no-referrer`: Chrome serialises the Origin header of
    # a form submission as `null` under `no-referrer`, and the gateway's
    # same-origin check then refuses the learner's own sign-in. With
    # `same-origin` nothing is sent to another site (the consent redirect
    # included) and a same-site submission carries its real origin.
    "Referrer-Policy": "same-origin",
}
CONSENT_HEADERS = {**SECURITY_HEADERS, "Content-Security-Policy": _csp(FORM_ACTION_CLIENTS)}

BOUNDARY = (
    "Vademecum is educational; nothing it shows is a substitute for clinical judgment, "
    "current guidance or a specialist. Never enter patient identifiers anywhere in it."
)


def hidden(name: str, value: str) -> str:
    return f'<input type="hidden" name="{html.escape(name)}" value="{html.escape(value)}">'


def learner_forms(action_path: str, hidden_fields: str, *, approve_label: str, register_label: str, deny: bool) -> str:
    """Sign in, or register with an invite. Multi tenancy."""
    decline = (
        '<button type="submit" name="action" value="deny" class="secondary" formnovalidate>Decline</button>'
        if deny
        else ""
    )
    return f"""
<form method="post" action="{action_path}" autocomplete="off">
  {hidden_fields}
  <h2>Sign in</h2>
  <label for="handle">Handle</label>
  <input id="handle" name="handle" type="text" autocomplete="username" autocapitalize="none" spellcheck="false">
  <label for="passphrase">Passphrase</label>
  <input id="passphrase" name="passphrase" type="password" autocomplete="current-password">
  <div class="actions">
    <button type="submit" name="action" value="approve">{html.escape(approve_label)}</button>
    {decline}
  </div>
</form>
<form method="post" action="{action_path}" autocomplete="off">
  {hidden_fields}
  <h2>First time here?</h2>
  <p class="muted">You need an invite code from the person who runs this Vademecum.</p>
  <label for="invite">Invite code</label>
  <input id="invite" name="invite" type="text" autocapitalize="none" spellcheck="false">
  <label for="new_handle">Choose a handle</label>
  <input id="new_handle" name="new_handle" type="text" autocomplete="username" autocapitalize="none" spellcheck="false">
  <label for="new_passphrase">Choose a passphrase (at least 12 characters)</label>
  <input id="new_passphrase" name="new_passphrase" type="password" autocomplete="new-password">
  <label for="new_passphrase_again">The same passphrase again</label>
  <input id="new_passphrase_again" name="new_passphrase_again" type="password" autocomplete="new-password">
  <div class="actions">
    <button type="submit" name="action" value="register">{html.escape(register_label)}</button>
  </div>
</form>
"""


def owner_form(action_path: str, hidden_fields: str) -> str:
    """The owner's passphrase. Single tenancy."""
    return f"""
<form method="post" action="{action_path}" autocomplete="off">
  {hidden_fields}
  <label for="passphrase">Vademecum passphrase</label>
  <input id="passphrase" name="passphrase" type="password" required autofocus
         autocomplete="current-password" minlength="1">
  <div class="actions">
    <button type="submit" name="action" value="approve">Approve</button>
    <button type="submit" name="action" value="deny" class="secondary" formnovalidate>Decline</button>
  </div>
</form>
"""


def notice(error: str) -> str:
    return f'<p class="error" role="alert">{html.escape(error)}</p>' if error else ""


def page(content: str, *, status: int = 200, raw: bool = False, headers: dict[str, str] | None = None) -> Response:
    inner = content if raw else f"<h1>Vademecum</h1><p>{html.escape(content)}</p>"
    document = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Vademecum</title>
<style>
:root {{ color-scheme: light dark; --fg: #1d1d1f; --bg: #fbfbfa; --muted: #5c5c60; --accent: #2b5d8c;
  --line: #d9d9de; --error: #a52a2a; }}
@media (prefers-color-scheme: dark) {{ :root {{ --fg: #ececec; --bg: #17181b; --muted: #a9a9b0;
  --accent: #8fb8e8; --line: #34353a; --error: #ff8a80; }} }}
body {{ margin: 0; padding: 24px 16px 48px; background: var(--bg); color: var(--fg);
  font: 16px/1.5 -apple-system, BlinkMacSystemFont, "Helvetica Neue", Helvetica, Arial, sans-serif; }}
main {{ max-width: 34rem; margin: 0 auto; }}
h1 {{ font-size: 1.4rem; line-height: 1.25; margin: 0 0 1rem; }}
h2 {{ font-size: 1.1rem; margin: 2rem 0 .25rem; }}
p {{ margin: 0 0 1rem; }}
.muted {{ color: var(--muted); }}
form + form {{ border-top: 1px solid var(--line); margin-top: 1.5rem; }}
label {{ display: block; font-weight: 600; margin: 1.25rem 0 .4rem; }}
input[type=password], input[type=text] {{ width: 100%; box-sizing: border-box; font: inherit;
  padding: .7rem .8rem; border: 1px solid var(--line); border-radius: 8px; background: transparent;
  color: inherit; }}
.actions {{ display: flex; gap: .75rem; margin-top: 1.25rem; flex-wrap: wrap; }}
button {{ font: inherit; font-weight: 600; padding: .75rem 1.1rem; min-height: 44px; border-radius: 8px;
  border: 1px solid var(--accent); background: var(--accent); color: #fff; cursor: pointer; }}
button.secondary {{ background: transparent; color: var(--accent); }}
.error {{ color: var(--error); font-weight: 600; }}
</style>
</head>
<body><main>{inner}</main></body>
</html>"""
    return HTMLResponse(document, status_code=status, headers={**SECURITY_HEADERS, **(headers or {})})
