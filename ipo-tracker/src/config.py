"""Central configuration. Everything sensitive comes from environment variables
(GitHub Repository Secrets in production, a local .env file for testing)."""

from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo

# --------------------------------------------------------------------------- #
# Time
# --------------------------------------------------------------------------- #
IST = ZoneInfo("Asia/Kolkata")

# --------------------------------------------------------------------------- #
# Data sources
# --------------------------------------------------------------------------- #
INVESTORGAIN_URL = os.getenv(
    "INVESTORGAIN_URL", "https://www.investorgain.com/report/ipo-gmp-live/331/"
)
IPOWATCH_URL = os.getenv(
    "IPOWATCH_URL", "https://ipowatch.in/ipo-grey-market-premium-latest-ipo-gmp/"
)

# --------------------------------------------------------------------------- #
# Business rules
# --------------------------------------------------------------------------- #
# Keep only IPOs whose (GMP / Issue Price) * 100 is strictly greater than this.
MIN_GMP_PCT = float(os.getenv("MIN_GMP_PCT", "15"))

# rapidfuzz / difflib score (0-100) below which we treat a name as "no match".
FUZZY_THRESHOLD = int(os.getenv("FUZZY_THRESHOLD", "80"))

# Send a "nothing today" heartbeat message so you know the pipeline is alive.
SEND_WHEN_EMPTY = os.getenv("SEND_WHEN_EMPTY", "true").lower() in {"1", "true", "yes"}

# --------------------------------------------------------------------------- #
# Storage
# --------------------------------------------------------------------------- #
DB_PATH = Path(os.getenv("DB_PATH", "ipo_tracker.db"))
DEBUG_DIR = Path(os.getenv("DEBUG_DIR", "debug"))

# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
HTTP_TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "30"))
HTTP_RETRIES = int(os.getenv("HTTP_RETRIES", "3"))
USER_AGENT = os.getenv(
    "USER_AGENT",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
)

# Set USE_PLAYWRIGHT=true only if a site starts rendering its table via JS.
# InvestorGain needs this; IPO Watch does not (and hangs a headless browser
# because its ad scripts never go idle), so it has its own opt-in flag.
USE_PLAYWRIGHT = os.getenv("USE_PLAYWRIGHT", "false").lower() in {"1", "true", "yes"}
IPOWATCH_USE_PLAYWRIGHT = os.getenv("IPOWATCH_USE_PLAYWRIGHT", "false").lower() in {
    "1", "true", "yes"
}

# --------------------------------------------------------------------------- #
# WhatsApp delivery
# --------------------------------------------------------------------------- #
# "telegram" (most reliable & free), "callmebot" (free WhatsApp, capacity-limited),
# or "twilio" (WhatsApp sandbox, needs re-joining every 72h)
WHATSAPP_PROVIDER = os.getenv("WHATSAPP_PROVIDER", "telegram").lower()

# Telegram Bot API
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# CallMeBot
CALLMEBOT_PHONE = os.getenv("CALLMEBOT_PHONE", "")   # e.g. +919876543210
CALLMEBOT_APIKEY = os.getenv("CALLMEBOT_APIKEY", "")

# Twilio WhatsApp Sandbox
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM = os.getenv("TWILIO_FROM", "whatsapp:+14155238886")  # sandbox number
TWILIO_TO = os.getenv("TWILIO_TO", "")                           # whatsapp:+91...

# CallMeBot silently truncates very long URLs; split above this many characters.
MAX_MESSAGE_CHARS = int(os.getenv("MAX_MESSAGE_CHARS", "900"))
