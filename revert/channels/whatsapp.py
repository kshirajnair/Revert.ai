"""WhatsApp Cloud API (Meta Graph API), over plain HTTPS with httpx.

Sending: text, approved templates, and PDF documents (uploaded to Meta's media store first, so
Revert needs no public URL to send files). Receiving: Meta calls our webhook
(`/webhooks/whatsapp` in the dashboard app); `verify_signature` and `parse_webhook` live here.

Needs a Meta developer app with the WhatsApp product. Setup steps are in the README.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass, field

import httpx

from revert.channels.base import Channel, ChannelError, OutsideWindow, Template
from revert.core.phones import is_demo_number, normalize_phone

log = logging.getLogger(__name__)

GRAPH = "https://graph.facebook.com"
TEXT_LIMIT = 4096
# Meta error codes we treat specially
RE_ENGAGEMENT = 131047  # more than 24 hours since the user's last message
LANGUAGE_CODES = {"en": "en", "hi": "hi", "kn": "kn"}


class WhatsAppChannel(Channel):
    name = "whatsapp"
    session_window_hours = 24

    def __init__(self, token: str, phone_number_id: str, api_version: str = "v23.0",
                 template_names: dict[str, str] | None = None, timeout_s: float = 15,
                 transport: httpx.BaseTransport | None = None):
        if not token or not phone_number_id:
            raise ValueError("CHANNEL=whatsapp needs WHATSAPP_TOKEN and WHATSAPP_PHONE_NUMBER_ID in .env")
        self.token, self.phone_number_id, self.api_version = token, phone_number_id, api_version
        self.template_names = template_names or {}
        self._client = httpx.Client(timeout=timeout_s, transport=transport,
                                    headers={"Authorization": f"Bearer {token}"})

    def for_phone(self, phone: str) -> Channel:
        if is_demo_number(phone):
            from revert.channels.web_simulator import WebSimulatorChannel

            return WebSimulatorChannel()
        return self

    @property
    def _base(self) -> str:
        return f"{GRAPH}/{self.api_version}"

    # --- sending ------------------------------------------------------------------------------

    def send_text(self, phone: str, body: str) -> str:
        return self._message(phone, {"type": "text", "text": {"preview_url": False, "body": body[:TEXT_LIMIT]}})

    def send_template(self, phone: str, template: Template) -> str:
        name = self.template_names.get(template.key, template.key)
        return self._message(phone, {"type": "template", "template": {
            "name": name,
            "language": {"code": LANGUAGE_CODES.get(template.language, "en")},
            "components": [{"type": "body", "parameters": [{"type": "text", "text": p} for p in template.params]}],
        }})

    def send_document(self, phone: str, data: bytes, filename: str, caption: str = "") -> str:
        r = self._request("POST", f"{self._base}/{self.phone_number_id}/media",
                          data={"messaging_product": "whatsapp", "type": "application/pdf"},
                          files={"file": (filename, data, "application/pdf")})
        media_id = r.json()["id"]
        doc = {"id": media_id, "filename": filename}
        if caption:
            doc["caption"] = caption[:1024]
        return self._message(phone, {"type": "document", "document": doc})

    def _message(self, phone: str, content: dict) -> str:
        payload = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": wa_id(phone), **content}
        r = self._request("POST", f"{self._base}/{self.phone_number_id}/messages", json=payload)
        try:
            return r.json()["messages"][0]["id"]
        except (KeyError, IndexError, ValueError) as e:
            raise ChannelError(f"WhatsApp returned no message id: {r.text[:200]}") from e

    # --- receiving media and checks -------------------------------------------------------------

    def download_media(self, media_id: str) -> tuple[bytes, str]:
        meta = self._request("GET", f"{self._base}/{media_id}").json()
        r = self._request("GET", meta["url"])
        return r.content, meta.get("mime_type", r.headers.get("content-type", "application/octet-stream"))

    def check(self) -> dict:
        """The business number's details; proves the token and phone number id work."""
        return self._request("GET", f"{self._base}/{self.phone_number_id}",
                             params={"fields": "display_phone_number,verified_name,quality_rating"}).json()

    def _request(self, method: str, url: str, **kw) -> httpx.Response:
        try:
            r = self._client.request(method, url, **kw)
        except httpx.HTTPError as e:
            raise ChannelError(f"WhatsApp unreachable: {e}") from e
        if r.status_code >= 400:
            try:
                err = r.json().get("error", {})
            except ValueError:
                err = {}
            code = err.get("code")
            msg = f"WhatsApp error {code or r.status_code}: {err.get('message') or r.text[:200]}"
            if code == RE_ENGAGEMENT:
                raise OutsideWindow(msg)
            raise ChannelError(msg)
        return r


def wa_id(phone: str) -> str:
    """WhatsApp ids are E.164 without the plus."""
    return phone.lstrip("+")


# --- webhook --------------------------------------------------------------------------------

def verify_signature(app_secret: str, raw_body: bytes, header: str | None) -> bool:
    """Meta signs every webhook POST with the app secret: X-Hub-Signature-256: sha256=<hex>."""
    if not app_secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


@dataclass
class InboundMessage:
    provider_id: str
    phone: str  # E.164
    kind: str  # text | image | document | audio | video | sticker | location | contacts | ...
    text: str = ""  # body, button/list title, or media caption
    media_id: str = ""
    mime_type: str = ""
    filename: str = ""
    timestamp: str = ""


@dataclass
class StatusUpdate:
    provider_id: str
    status: str  # sent | delivered | read | failed
    phone: str = ""
    errors: list[dict] = field(default_factory=list)


def parse_webhook(payload: dict) -> tuple[list[InboundMessage], list[StatusUpdate]]:
    messages, statuses = [], []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for m in value.get("messages", []):
                kind = m.get("type", "unknown")
                msg = InboundMessage(provider_id=m.get("id", ""), phone=normalize_phone(m.get("from")) or "",
                                     kind=kind, timestamp=m.get("timestamp", ""))
                if kind == "text":
                    msg.text = m.get("text", {}).get("body", "")
                elif kind == "button":
                    msg.kind, msg.text = "text", m.get("button", {}).get("text", "")
                elif kind == "interactive":
                    i = m.get("interactive", {})
                    reply = i.get("button_reply") or i.get("list_reply") or {}
                    msg.kind, msg.text = "text", reply.get("title", "")
                elif kind in ("image", "document", "audio", "video", "sticker"):
                    media = m.get(kind, {})
                    msg.media_id, msg.mime_type = media.get("id", ""), media.get("mime_type", "")
                    msg.text, msg.filename = media.get("caption", ""), media.get("filename", "")
                messages.append(msg)
            for s in value.get("statuses", []):
                statuses.append(StatusUpdate(provider_id=s.get("id", ""), status=s.get("status", ""),
                                             phone=normalize_phone(s.get("recipient_id")) or "",
                                             errors=s.get("errors", [])))
    return messages, statuses
