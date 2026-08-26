"""Builds the WhatsApp message body in the exact requested format."""

from __future__ import annotations

import datetime as dt
from typing import Any, Optional

from .config import APP_VERSION, MIN_GMP_PCT_MAINBOARD, MIN_GMP_PCT_SME

SEPARATOR = "---------------------------------"


def _money(value: Optional[float]) -> str:
    if value is None:
        return "N/A"
    return f"₹{value:.0f}" if float(value).is_integer() else f"₹{value:.2f}"


def _pct(value: Optional[float]) -> str:
    return "N/A" if value is None else f"{value:.2f}%"


def _line(label: str, gmp: Optional[float], pct: Optional[float]) -> str:
    if gmp is None and pct is None:
        return f"- {label}: Not listed"
    return f"- {label}: {_money(gmp)} ({_pct(pct)})"


def build_message(rows: list[dict[str, Any]], run_date: dt.date) -> str:
    """Render the daily alert. `rows` are SQLite records enriched by Step 2."""
    header = (
        f"📈 IPOs Closing TODAY ({run_date.strftime('%d-%b-%Y')})\n"
        f"Filter: SME above {MIN_GMP_PCT_SME:.0f}% | Mainboard above "
        f"{MIN_GMP_PCT_MAINBOARD:.0f}%"
    )

    if not rows:
        return (
            f"{header}\n\n"
            "No IPO closing today crosses its GMP threshold. "
            "Nothing to apply for.\n"
            f"{SEPARATOR}\n"
            f"IPO Tracker v{APP_VERSION}"
        )

    blocks: list[str] = []
    for row in rows:
        blocks.append(
            "\n".join(
                [
                    f"Company Name: {row['name']} [{row.get('exchange') or '?'}]",
                    _line("InvestorGain GMP", row.get("ig_gmp"), row.get("ig_gmp_pct")),
                    _line("IPO Watch GMP", row.get("iw_gmp"), row.get("iw_gmp_pct")),
                    SEPARATOR,
                ]
            )
        )

    footer = (
        "⚠️ Apply before the cut-off (usually 5 PM). GMP is unofficial data.\n"
        f"IPO Tracker v{APP_VERSION}"
    )
    return f"{header}\n\n" + "\n\n".join(blocks) + f"\n\n{footer}"
