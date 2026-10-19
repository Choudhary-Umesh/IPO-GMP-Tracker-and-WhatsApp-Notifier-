"""STEP 3 — Dispatch.

Providers (all plain HTTPS, nothing browser-based, so it runs headless in CI):

  meta       Official WhatsApp Cloud API. Most reliable WhatsApp route. Sends an
             APPROVED template (required for business-initiated messages); falls
             back to a plain text message when no template is configured, which
             Meta only delivers within 24h of you last messaging the business.
  callmebot  Free WhatsApp to your own number. Capacity-limited signups.
  twilio     WhatsApp via Twilio. The sandbox expires every 72h unless re-joined.
  telegram   Not WhatsApp, but free and dependable — best used as FALLBACK_PROVIDER.

`WHATSAPP_PROVIDER=auto` (default) picks the first provider that has credentials,
in the order above, and logs loudly when that turns out not to be WhatsApp —
the old silent default of "telegram" is why alerts never reached WhatsApp.

Settings are read from `config` at call time (not import time) so tests and the
CLI can override them.
"""

from __future__ import annotations

import logging
import re
import time
import urllib.parse
from typing import Callable, Optional

import requests

from . import config as cfg

log = logging.getLogger(__name__)

CALLMEBOT_ENDPOINT = "https://api.callmebot.com/whatsapp.php"
TWILIO_ENDPOINT = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
META_ENDPOINT = "https://graph.facebook.com/{version}/{phone_id}/messages"
TELEGRAM_ENDPOINT = "https://api.telegram.org/bot{token}/sendMessage"

WHATSAPP_PROVIDERS = ("meta", "callmebot", "twilio")
AUTO_ORDER = ("meta", "callmebot", "twilio", "telegram")


class NotifierError(RuntimeError):
    """Raised when a message could not be delivered."""


class PermanentError(NotifierError):
    """A failure that retrying cannot fix (bad credentials, bad config)."""


class DeliveredViaFallback(NotifierError):
    """The message reached the user, but only through FALLBACK_PROVIDER.
    Raised (rather than returned) so the run is still marked as failed and the
    broken primary gets fixed instead of being silently papered over."""

    def __init__(self, provider: str, primary_error: str):
        super().__init__(f"Delivered only via fallback '{provider}'; primary failed: {primary_error}")
        self.provider = provider


# --------------------------------------------------------------------------- #
# Provider selection
# --------------------------------------------------------------------------- #


def is_configured(provider: str) -> bool:
    if provider == "meta":
        return bool(cfg.META_WA_TOKEN and cfg.META_WA_PHONE_NUMBER_ID and cfg.META_WA_TO)
    if provider == "callmebot":
        return bool(cfg.CALLMEBOT_PHONE and cfg.CALLMEBOT_APIKEY)
    if provider == "twilio":
        return bool(cfg.TWILIO_ACCOUNT_SID and cfg.TWILIO_AUTH_TOKEN and cfg.TWILIO_TO)
    if provider == "telegram":
        return bool(cfg.TELEGRAM_BOT_TOKEN and cfg.TELEGRAM_CHAT_ID)
    return False


def resolve_provider(requested: Optional[str] = None) -> str:
    """Turn 'auto' (or an explicit name) into a concrete, configured provider."""
    name = (requested or cfg.WHATSAPP_PROVIDER or "auto").lower()
    if name == "auto":
        for candidate in AUTO_ORDER:
            if is_configured(candidate):
                name = candidate
                break
        else:
            raise PermanentError(
                "No notification provider is configured. Set the secrets for one of: "
                "META_WA_* (WhatsApp Cloud API), CALLMEBOT_* (WhatsApp), "
                "TWILIO_* (WhatsApp) or TELEGRAM_* (Telegram). See README Part 1."
            )
    if name not in _PROVIDERS:
        raise PermanentError(f"Unknown provider '{name}'. Use one of: {list(_PROVIDERS)}")
    if not is_configured(name):
        raise PermanentError(f"Provider '{name}' selected but its secrets are missing/empty")
    if name not in WHATSAPP_PROVIDERS:
        log.warning("Delivering via %s — this is NOT WhatsApp. Configure META_WA_* or "
                    "CALLMEBOT_* secrets to receive the alert on WhatsApp.", name)
    return name


def describe_config() -> list[str]:
    """Human-readable provider status for logs. Never prints secret values."""
    lines = []
    for p in AUTO_ORDER:
        lines.append(f"{p:<10} {'configured' if is_configured(p) else 'missing'}"
                     f"{'  (WhatsApp)' if p in WHATSAPP_PROVIDERS else ''}")
    try:
        lines.append(f"selected   {resolve_provider()}  (WHATSAPP_PROVIDER={cfg.WHATSAPP_PROVIDER})")
    except NotifierError as exc:
        lines.append(f"selected   NONE — {exc}")
    if cfg.FALLBACK_PROVIDER:
        ok = is_configured(cfg.FALLBACK_PROVIDER)
        lines.append(f"fallback   {cfg.FALLBACK_PROVIDER} ({'configured' if ok else 'MISSING secrets'})")
    if is_configured("meta") and not cfg.META_WA_TEMPLATE:
        lines.append("warning    META_WA_TEMPLATE is empty: plain text is only delivered "
                     "within 24h of your last message to the business number")
    return lines


# --------------------------------------------------------------------------- #
# Chunking
# --------------------------------------------------------------------------- #


def chunk_message(message: str, limit: Optional[int] = None) -> list[str]:
    """Split on IPO block boundaries so a long day never truncates mid-record.
    Most days this returns a single chunk — i.e. one WhatsApp message. A single
    block longer than the limit is hard-split rather than sent oversized."""
    limit = limit or cfg.MAX_MESSAGE_CHARS
    if len(message) <= limit:
        return [message]

    chunks: list[str] = []
    current = ""
    for block in message.split("\n\n"):
        while len(block) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(block[:limit])
            block = block[limit:]
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) > limit:
            chunks.append(current)
            current = block
        else:
            current = candidate
    if current:
        chunks.append(current)
    if len(chunks) > 1:
        chunks = [f"({i}/{len(chunks)}) {c}" for i, c in enumerate(chunks, start=1)]
    return chunks


# --------------------------------------------------------------------------- #
# Providers
# --------------------------------------------------------------------------- #


def _raise_for_http(provider: str, resp: requests.Response) -> None:
    """4xx (except 408/429) means bad credentials/config: retrying won't help."""
    if resp.status_code < 300:
        return
    detail = (resp.text or "")[:300]
    if 400 <= resp.status_code < 500 and resp.status_code not in (408, 429):
        raise PermanentError(f"{provider} rejected the request (HTTP {resp.status_code}): {detail}")
    raise NotifierError(f"{provider} error (HTTP {resp.status_code}): {detail}")


def flatten_for_template(text: str) -> str:
    """Meta rejects template parameters containing newlines, tabs or more than
    four consecutive spaces (error 132018), so the message is flattened."""
    text = text.replace("\t", " ")
    text = re.sub(r"\n{2,}", " ‖ ", text)
    text = text.replace("\n", " | ")
    return re.sub(r" {2,}", " ", text).strip()[:1024]


def meta_payload(text: str) -> dict:
    to = re.sub(r"\D", "", cfg.META_WA_TO)
    if cfg.META_WA_TEMPLATE:
        return {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "template",
            "template": {
                "name": cfg.META_WA_TEMPLATE,
                "language": {"code": cfg.META_WA_TEMPLATE_LANG},
                "components": [{
                    "type": "body",
                    "parameters": [{"type": "text", "text": flatten_for_template(text)}],
                }],
            },
        }
    return {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text, "preview_url": False},
    }


def _send_meta(text: str) -> None:
    resp = requests.post(
        META_ENDPOINT.format(version=cfg.META_GRAPH_VERSION, phone_id=cfg.META_WA_PHONE_NUMBER_ID),
        headers={"Authorization": f"Bearer {cfg.META_WA_TOKEN}"},
        json=meta_payload(text),
        timeout=cfg.HTTP_TIMEOUT,
    )
    _raise_for_http("Meta WhatsApp", resp)
    data = resp.json()
    msgs = data.get("messages") or []
    if not msgs:
        raise NotifierError(f"Meta WhatsApp returned no message id: {str(data)[:300]}")
    log.info("Meta WhatsApp accepted the message (id %s, status %s)",
             msgs[0].get("id"), msgs[0].get("message_status", "accepted"))


def _send_callmebot(text: str) -> None:
    params = {"phone": cfg.CALLMEBOT_PHONE, "text": text, "apikey": cfg.CALLMEBOT_APIKEY}
    resp = requests.get(f"{CALLMEBOT_ENDPOINT}?{urllib.parse.urlencode(params)}",
                        timeout=cfg.HTTP_TIMEOUT)
    body = (resp.text or "").lower()
    # CallMeBot answers HTTP 200 even for most errors, so the body must be read.
    if any(bad in body for bad in ("apikey is invalid", "invalid", "you need to", "not activated")):
        raise PermanentError(f"CallMeBot rejected the message: {resp.text[:300]}")
    _raise_for_http("CallMeBot", resp)
    if "error" in body:
        raise NotifierError(f"CallMeBot error: {resp.text[:300]}")
    if not any(ok in body for ok in ("queued", "sent")):
        log.warning("CallMeBot gave an unrecognised reply (treated as sent): %s", resp.text[:200])
    log.info("CallMeBot accepted the message (HTTP %s)", resp.status_code)


def _send_twilio(text: str) -> None:
    resp = requests.post(
        TWILIO_ENDPOINT.format(sid=cfg.TWILIO_ACCOUNT_SID),
        auth=(cfg.TWILIO_ACCOUNT_SID, cfg.TWILIO_AUTH_TOKEN),
        data={"From": cfg.TWILIO_FROM, "To": cfg.TWILIO_TO, "Body": text},
        timeout=cfg.HTTP_TIMEOUT,
    )
    _raise_for_http("Twilio", resp)
    log.info("Twilio accepted the message (SID %s)", resp.json().get("sid"))


def _send_telegram(text: str) -> None:
    resp = requests.post(
        TELEGRAM_ENDPOINT.format(token=cfg.TELEGRAM_BOT_TOKEN),
        json={"chat_id": cfg.TELEGRAM_CHAT_ID, "text": text, "disable_web_page_preview": True},
        timeout=cfg.HTTP_TIMEOUT,
    )
    _raise_for_http("Telegram", resp)
    if not resp.json().get("ok"):
        raise NotifierError(f"Telegram rejected the message: {resp.text[:300]}")
    log.info("Telegram accepted the message")


_PROVIDERS: dict[str, Callable[[str], None]] = {
    "meta": _send_meta,
    "callmebot": _send_callmebot,
    "twilio": _send_twilio,
    "telegram": _send_telegram,
}


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def _deliver(provider: str, message: str, retries: int) -> None:
    send = _PROVIDERS[provider]
    chunks = chunk_message(message)
    log.info("Sending %s chunk(s) via %s", len(chunks), provider)
    for i, chunk in enumerate(chunks, start=1):
        for attempt in range(1, retries + 1):
            try:
                send(chunk)
                break
            except PermanentError:
                raise
            except Exception as exc:  # noqa: BLE001 - network errors, 5xx, 429
                log.warning("%s send attempt %s/%s failed: %s", provider, attempt, retries, exc)
                if attempt == retries:
                    raise NotifierError(f"Chunk {i}/{len(chunks)} via {provider} failed: {exc}") from exc
                time.sleep(min(5 * 2 ** (attempt - 1), 60))  # 5s, 10s, 20s ...
        if i < len(chunks):
            time.sleep(4)  # CallMeBot rate-limits rapid consecutive sends


def send_whatsapp(
    message: str,
    *,
    provider: Optional[str] = None,
    dry_run: bool = False,
    retries: Optional[int] = None,
    use_fallback: bool = True,
) -> str:
    """Deliver `message`. Returns the name of the provider that delivered it.
    Tries the primary provider (with retries), then FALLBACK_PROVIDER."""
    retries = retries or cfg.SEND_RETRIES

    if dry_run:
        print("\n----- DRY RUN: message not sent -----")
        print(message)
        print("-------------------------------------\n")
        return "dry-run"

    errors: list[str] = []
    primary = None
    try:
        primary = resolve_provider(provider)
        _deliver(primary, message, retries)
        return primary
    except NotifierError as exc:
        log.error("Primary delivery failed: %s", exc)
        errors.append(str(exc))

    fallback = cfg.FALLBACK_PROVIDER
    if use_fallback and fallback and fallback != primary and is_configured(fallback):
        log.warning("Trying fallback provider %s", fallback)
        try:
            _deliver(fallback, f"⚠️ Primary channel ({primary or 'none'}) failed — sent via {fallback}.\n\n{message}",
                     retries)
        except NotifierError as exc:
            errors.append(f"fallback {fallback}: {exc}")
        else:
            raise DeliveredViaFallback(fallback, errors[0])

    raise NotifierError(" | ".join(errors))


if __name__ == "__main__":  # python -m src.notifier  -> prints config, sends a test
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    print("\n".join(describe_config()))
    send_whatsapp("✅ IPO Tracker test message — your setup works!")
