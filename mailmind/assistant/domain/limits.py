"""How much MailMind may spend on the Claude API in a local day."""
import math

DEFAULT_DAILY_LIMIT_USD = 15.0          # decision D7


def daily_limit_usd(value) -> float:
    """`value`, the configuration's "ai_daily_limit_usd", in US dollars: a positive, finite number,
    or DEFAULT_DAILY_LIMIT_USD when it is anything else or missing (None)."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return DEFAULT_DAILY_LIMIT_USD
    return v if v > 0 and math.isfinite(v) else DEFAULT_DAILY_LIMIT_USD
