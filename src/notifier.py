"""STEP 3 — Dispatch.

Two free providers are supported:

  callmebot  (default, recommended)  Free forever, no card, no expiry. You send
             one WhatsApp message to their bot once to get a personal API key.
  twilio     Free trial credit + WhatsApp Sandbox. Note the sandbox connection
             expires every 72 hours unless you re-send the join code, which
             makes it a poor fit for unattended daily automation.

No browser automation (pywhatkit / selenium) is used — everything is a plain
HTTPS call, so it runs headless on GitHub's runners."""

from __future__ import annotations

import logging
import time
import urllib.parse
from typing import Optional

import requests

from .config import (
    CALLMEBOT_APIKEY,
    CALLMEBOT_PHONE,
    HTTP_TIMEOUT,
    MAX_MESSAGE_CHARS,
    TWILIO_ACCOUNT_SID,
    TWILIO_AUTH_TOKEN,
    TWILIO_FROM,
    TWILIO_TO,
    WHATSAPP_PROVIDER,
)

log = logging.getLogger(__name__)

CALLMEBOT_ENDPOINT = "https://api.callmebot.com/whatsapp.php"
TWILIO_ENDPOINT = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"


class NotifierError(RuntimeError):
    """Raised when a message could not be delivered."""


# --------------------------------------------------------------------------- #
# Chunking
# --------------------------------------------------------------------------- #


def chunk_message(message: str, limit: int = MAX_MESSAGE_CHARS) -> list[str]:
    """Split on IPO block boundaries so a long day never truncates mid-record.
    Most days this returns a single chunk — i.e. one WhatsApp message."""
    if len(message) <= limit:
        return [message]

    chunks: list[str] = []
    current = ""
    for block in message.split("\n\n"):
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) > limit and current:
            chunks.append(current)
            current = block
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


# --------------------------------------------------------------------------- #
# Providers
# --------------------------------------------------------------------------- #


def _send_callmebot(text: str) -> None:
    if not CALLMEBOT_PHONE or not CALLMEBOT_APIKEY:
        raise NotifierError("CALLMEBOT_PHONE and CALLMEBOT_APIKEY must both be set")

    params = {
        "phone": CALLMEBOT_PHONE,
        "text": text,
        "apikey": CALLMEBOT_APIKEY,
    }
    url = f"{CALLMEBOT_ENDPOINT}?{urllib.parse.urlencode(params)}"
    resp = requests.get(url, timeout=HTTP_TIMEOUT)

    body = (resp.text or "").lower()
    if resp.status_code != 200 or "error" in body or "invalid" in body:
        raise NotifierError(
            f"CallMeBot rejected the message (HTTP {resp.status_code}): {resp.text[:300]}"
        )
    log.info("CallMeBot accepted the message (HTTP %s)", resp.status_code)


def _send_twilio(text: str) -> None:
    if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_TO):
        raise NotifierError("TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN and TWILIO_TO required")

    resp = requests.post(
        TWILIO_ENDPOINT.format(sid=TWILIO_ACCOUNT_SID),
        auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
        data={"From": TWILIO_FROM, "To": TWILIO_TO, "Body": text},
        timeout=HTTP_TIMEOUT,
    )
    if resp.status_code >= 300:
        raise NotifierError(f"Twilio error (HTTP {resp.status_code}): {resp.text[:300]}")
    log.info("Twilio accepted the message (SID %s)", resp.json().get("sid"))


_PROVIDERS = {"callmebot": _send_callmebot, "twilio": _send_twilio}


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def send_whatsapp(
    message: str,
    *,
    provider: Optional[str] = None,
    dry_run: bool = False,
    retries: int = 3,
) -> bool:
    """Deliver `message` over WhatsApp. Returns True on success."""
    provider = (provider or WHATSAPP_PROVIDER).lower()
    if provider not in _PROVIDERS:
        raise NotifierError(f"Unknown provider '{provider}'. Use: {list(_PROVIDERS)}")

    if dry_run:
        print("\n----- DRY RUN: message not sent -----")
        print(message)
        print("-------------------------------------\n")
        return True

    send = _PROVIDERS[provider]
    chunks = chunk_message(message)
    log.info("Sending %s chunk(s) via %s", len(chunks), provider)

    for i, chunk in enumerate(chunks, start=1):
        last_error: Optional[Exception] = None
        for attempt in range(1, retries + 1):
            try:
                send(chunk)
                last_error = None
                break
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                log.warning("Send attempt %s/%s failed: %s", attempt, retries, exc)
                if attempt < retries:
                    time.sleep(3 * attempt)
        if last_error:
            raise NotifierError(f"Chunk {i}/{len(chunks)} failed: {last_error}")
        if i < len(chunks):
            time.sleep(4)  # CallMeBot rate-limits rapid consecutive sends

    return True


if __name__ == "__main__":  # python -m src.notifier  -> sends a test message
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    send_whatsapp("✅ IPO Tracker test message — your setup works!")
