"""Central configuration. Everything sensitive comes from environment variables
(GitHub Repository Secrets in production, a local .env file for testing)."""

from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo

# Local runs: pick up a .env file automatically (CI has none, so this is a no-op
# there). Real environment variables always win over .env values.
try:
    from dotenv import load_dotenv

    load_dotenv(override=False)
except ImportError:  # python-dotenv is optional
    pass

# --------------------------------------------------------------------------- #
# Time
# --------------------------------------------------------------------------- #
IST = ZoneInfo("Asia/Kolkata")

# Bumped by hand when behaviour changes, and shown in the daily message so you
# can always tell which version of the pipeline produced an alert.
#   v1.0 - single 15% GMP threshold for every IPO
#   v2.0 - per-type thresholds (SME 40% / Mainboard 15%), cleaned company names
#   v3.0 - exact-time trigger, WhatsApp-first provider, Meta Cloud API, no silent failures
APP_VERSION = "3.0"

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
# Keep only IPOs whose (GMP / Issue Price) * 100 is strictly greater than the
# threshold for their listing type. SME issues have tiny floats and a thin grey
# market, so their GMPs run far higher and need a much stricter bar to be
# meaningful. MIN_GMP_PCT is still honoured as the mainboard default so existing
# setups keep working.
MIN_GMP_PCT = float(os.getenv("MIN_GMP_PCT", "15"))
MIN_GMP_PCT_MAINBOARD = float(os.getenv("MIN_GMP_PCT_MAINBOARD", str(MIN_GMP_PCT)))
MIN_GMP_PCT_SME = float(os.getenv("MIN_GMP_PCT_SME", "40"))

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
# Delivery
# --------------------------------------------------------------------------- #
def _env(name: str, default: str = "") -> str:
    """Env var with surrounding whitespace removed. GitHub passes an unset
    secret as an empty string, and pasted secrets often carry a trailing
    newline — both used to cause confusing auth failures."""
    return (os.getenv(name) or default).strip()


# "auto" (default) picks the first provider whose credentials are present, in
# this order: meta -> callmebot -> twilio -> telegram. Set it explicitly to
# force one. Only meta / callmebot / twilio deliver to WhatsApp.
WHATSAPP_PROVIDER = _env("WHATSAPP_PROVIDER", "auto").lower() or "auto"

# Optional second channel, used if the primary provider fails after retries
# (and for failure alerts). Typically "telegram". Empty = no fallback.
FALLBACK_PROVIDER = _env("FALLBACK_PROVIDER").lower()

# Meta WhatsApp Cloud API (official). Business-initiated messages need an
# APPROVED template; plain text only works within 24h of you messaging the
# business number.
META_WA_TOKEN = _env("META_WA_TOKEN")                      # permanent system-user token
META_WA_PHONE_NUMBER_ID = _env("META_WA_PHONE_NUMBER_ID")  # sender's phone-number ID
META_WA_TO = _env("META_WA_TO")                            # recipient, e.g. 919876543210
META_WA_TEMPLATE = _env("META_WA_TEMPLATE")                # approved template name
META_WA_TEMPLATE_LANG = _env("META_WA_TEMPLATE_LANG", "en")
META_GRAPH_VERSION = _env("META_GRAPH_VERSION", "v23.0")

# Telegram Bot API
TELEGRAM_BOT_TOKEN = _env("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = _env("TELEGRAM_CHAT_ID")

# CallMeBot
CALLMEBOT_PHONE = _env("CALLMEBOT_PHONE")   # e.g. +919876543210
CALLMEBOT_APIKEY = _env("CALLMEBOT_APIKEY")

# Twilio WhatsApp
TWILIO_ACCOUNT_SID = _env("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = _env("TWILIO_AUTH_TOKEN")
TWILIO_FROM = _env("TWILIO_FROM", "whatsapp:+14155238886")  # sandbox number
TWILIO_TO = _env("TWILIO_TO")                               # whatsapp:+91...

# CallMeBot silently truncates very long URLs and Meta template parameters are
# capped at 1024 chars; split above this many characters.
MAX_MESSAGE_CHARS = int(_env("MAX_MESSAGE_CHARS", "900"))
SEND_RETRIES = int(_env("SEND_RETRIES", "4"))

# --------------------------------------------------------------------------- #
# Timing (all IST)
# --------------------------------------------------------------------------- #
# The alert must be on your phone by this time. A message sent later than this
# is still delivered (silence is worse) but carries a "delayed" banner.
SEND_DEADLINE = _env("SEND_DEADLINE", "14:00")
# Applications for most IPOs close around 17:00 IST; after this the banner
# warns that the subscription has probably already closed.
SUBSCRIPTION_CUTOFF = _env("SUBSCRIPTION_CUTOFF", "17:00")
