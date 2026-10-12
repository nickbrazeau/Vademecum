"""Passkeys: signing in with Face ID (ADR 0036).

The owner adds a passkey once, from a session already signed in with the passphrase;
from then on the phone signs in with Face ID. The passkey is made by the phone and never
leaves it; this copy keeps only its public key and how many times it has signed. Every
ceremony takes a challenge made here, usable once, for five minutes, for one purpose,
and is checked against this copy's own address (relying party and origin), so a passkey
cannot be used from another site or replayed.

Verification is Duo's ``webauthn`` library. Nothing here logs a credential.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from urllib.parse import urlsplit

from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.exceptions import InvalidAuthenticationResponse, InvalidRegistrationResponse
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    AuthenticatorTransport,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from .store import OWNER_SUBJECT, AccessStore

logger = logging.getLogger("vademecum_mcp.passkeys")

REGISTER = "register"
SIGN_IN = "sign_in"
RP_NAME = "Vademecum"


@dataclass(frozen=True)
class Party:
    """Who the passkeys belong to: this copy's own address."""

    rp_id: str
    origin: str

    @classmethod
    def of(cls, public_url: str) -> "Party":
        parts = urlsplit(public_url)
        return cls(rp_id=(parts.hostname or "").lower(), origin=f"{parts.scheme}://{parts.netloc}".lower())


def registration_options(store: AccessStore, party: Party, *, learner_id: str = OWNER_SUBJECT) -> dict:
    challenge_id, challenge = store.new_challenge(REGISTER)
    existing = [
        PublicKeyCredentialDescriptor(id=base64url_to_bytes(row["credential_id"])) for row in store.passkeys(learner_id)
    ]
    options = generate_registration_options(
        rp_id=party.rp_id,
        rp_name=RP_NAME,
        user_id=learner_id.encode("utf-8"),
        user_name="Vademecum",
        user_display_name="Vademecum",
        challenge=challenge,
        exclude_credentials=existing,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
    )
    return {"challenge_id": challenge_id, "options": json.loads(options_to_json(options))}


def register(store: AccessStore, party: Party, challenge_id: str, credential: dict, *, label: str, learner_id: str = OWNER_SUBJECT) -> bool:
    challenge = store.take_challenge(challenge_id, REGISTER)
    if challenge is None:
        return False
    try:
        verified = verify_registration_response(
            credential=credential,
            expected_challenge=challenge,
            expected_origin=party.origin,
            expected_rp_id=party.rp_id,
            require_user_verification=True,
        )
    except (InvalidRegistrationResponse, ValueError, KeyError, TypeError):
        logger.info("passkey_registration_refused")
        return False
    credential_id = bytes_to_base64url(verified.credential_id)
    if store.passkey(credential_id) is not None:
        return False
    transports = [str(t) for t in ((credential.get("response") or {}).get("transports") or []) if str(t) in {x.value for x in AuthenticatorTransport}]
    store.add_passkey(
        credential_id=credential_id,
        public_key=verified.credential_public_key,
        sign_count=verified.sign_count,
        transports=transports,
        label=label,
        learner_id=learner_id,
    )
    logger.info("passkey_added")
    return True


def sign_in_options(store: AccessStore, party: Party) -> dict:
    """Discoverable: the phone offers the passkeys it holds for this address."""
    challenge_id, challenge = store.new_challenge(SIGN_IN)
    options = generate_authentication_options(
        rp_id=party.rp_id,
        challenge=challenge,
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    return {"challenge_id": challenge_id, "options": json.loads(options_to_json(options))}


def sign_in(store: AccessStore, party: Party, challenge_id: str, credential: dict) -> str | None:
    """The learner the passkey belongs to, or None. A failure counts towards the lockout."""
    challenge = store.take_challenge(challenge_id, SIGN_IN)
    raw_id = str(credential.get("rawId") or credential.get("id") or "")
    row = store.passkey(raw_id) if raw_id else None
    if challenge is None or row is None:
        store.record_failure(OWNER_SUBJECT)
        logger.info("passkey_sign_in_refused")
        return None
    try:
        verified = verify_authentication_response(
            credential=credential,
            expected_challenge=challenge,
            expected_rp_id=party.rp_id,
            expected_origin=party.origin,
            credential_public_key=bytes(row["public_key"]),
            credential_current_sign_count=int(row["sign_count"]),
            require_user_verification=True,
        )
    except (InvalidAuthenticationResponse, ValueError, KeyError, TypeError):
        store.record_failure(OWNER_SUBJECT)
        logger.info("passkey_sign_in_refused")
        return None
    store.used_passkey(raw_id, verified.new_sign_count)
    logger.info("passkey_signed_in")
    return str(row["learner_id"])


# The page script: asks the phone for a passkey and posts what it gives back. Served from
# this address (the pages' CSP allows scripts from 'self' only on the pages that need it).
SCRIPT = r"""
(() => {
  const b64 = (buffer) => btoa(String.fromCharCode(...new Uint8Array(buffer))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  const bytes = (text) => Uint8Array.from(atob(text.replace(/-/g, '+').replace(/_/g, '/') + '==='.slice((text.length + 3) % 4)), (c) => c.charCodeAt(0));
  const post = (path, body) => fetch(path, { method: 'POST', credentials: 'same-origin', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
  const say = (text) => { const out = document.getElementById('passkey-status'); if (out) out.textContent = text; };
  const asJson = (credential) => ({
    id: credential.id, rawId: b64(credential.rawId), type: credential.type,
    response: Object.fromEntries(Object.entries({
      clientDataJSON: credential.response.clientDataJSON, attestationObject: credential.response.attestationObject,
      authenticatorData: credential.response.authenticatorData, signature: credential.response.signature, userHandle: credential.response.userHandle,
    }).filter(([, v]) => v).map(([k, v]) => [k, b64(v)]).concat(credential.response.getTransports ? [['transports', credential.response.getTransports()]] : [])),
    clientExtensionResults: credential.getClientExtensionResults ? credential.getClientExtensionResults() : {},
  });
  async function signIn(button) {
    const begin = await (await post(button.dataset.options, {})).json();
    const o = begin.options;
    const credential = await navigator.credentials.get({ publicKey: { ...o, challenge: bytes(o.challenge),
      allowCredentials: (o.allowCredentials || []).map((c) => ({ ...c, id: bytes(c.id) })) } });
    if (button.dataset.form) {
      const form = document.getElementById(button.dataset.form);
      form.querySelector('[name=challenge_id]').value = begin.challenge_id;
      form.querySelector('[name=credential]').value = JSON.stringify(asJson(credential));
      form.submit();
      return;
    }
    const done = await post(button.dataset.verify, { challenge_id: begin.challenge_id, credential: asJson(credential) });
    const reply = await done.json().catch(() => ({}));
    if (done.ok) { window.location.assign(reply.redirect || '/'); return; }
    say(reply.message || 'That passkey was not accepted.');
  }
  async function add(button) {
    const begin = await (await post(button.dataset.options, {})).json();
    const o = begin.options;
    const credential = await navigator.credentials.create({ publicKey: { ...o, challenge: bytes(o.challenge),
      user: { ...o.user, id: bytes(o.user.id) }, excludeCredentials: (o.excludeCredentials || []).map((c) => ({ ...c, id: bytes(c.id) })) } });
    const done = await post(button.dataset.verify, { challenge_id: begin.challenge_id, credential: asJson(credential), label: navigator.platform || '' });
    const reply = await done.json().catch(() => ({}));
    if (done.ok) { window.location.reload(); return; }
    say(reply.message || 'The passkey was not added.');
  }
  for (const button of document.querySelectorAll('[data-passkey]')) {
    if (!window.PublicKeyCredential) { button.hidden = true; continue; }
    button.addEventListener('click', () => {
      say('');
      (button.dataset.passkey === 'add' ? add(button) : signIn(button)).catch(() => say('Face ID was cancelled or is not available here.'));
    });
  }
})();
"""
