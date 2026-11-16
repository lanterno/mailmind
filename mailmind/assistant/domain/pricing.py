"""What a call cost, in micro-dollars (US dollars × 1,000,000): integers, so a day's total never
drifts. Prices are each provider's list prices in US dollars per million tokens, which is the same as
micro-dollars per token, in one table: model names don't collide across providers. Where a price is set
to rise soon, the table holds the higher one. A cache write costs 1.25 times the input price: Anthropic's
5-minute cache, the only one MailMind would ask for. The other providers report no cache writes.

Anthropic's are its list prices. OpenAI's are developers.openai.com/api/docs/pricing, read 2026-11-16,
the standard tier for prompts up to 272K tokens, with a cached input priced as input where the page
gives none. Gemini's are aistudio.google.com/docs/pricing, read the same day, the paid standard tier.

A model is looked up by its name after the last `/`, so OpenRouter's `openai/gpt-6.1-sol` and Gemini's
`models/gemini-4-hydrogen` are found. One the table still doesn't know costs nothing when it runs on
this Mac (`local`: a model on this Mac, or any service on loopback), and otherwise the dearest price of its family
(claude-, gpt- or o<digit>, gemini-), or of the whole table for a name of none, so a spending limit errs
toward stopping early, never toward overspending.
"""
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Price:
    input: float              # US dollars per million input tokens
    output: float
    cache_read: float

    def nanos(self) -> tuple[int, int, int, int]:
        """Nano-dollars per token, exactly: input, output, cache write, cache read."""
        return (round(self.input * 1000), round(self.output * 1000),
                round(self.input * 1250), round(self.cache_read * 1000))


PRICES = {
    "claude-fable-5-1": Price(10, 50, 0.25),
    "claude-mythos-5-1": Price(10, 50, 0.25),
    "claude-fable-5": Price(10, 50, 1.00),
    "claude-mythos-5": Price(10, 50, 1.00),
    "claude-opus-5-5": Price(4, 20, 0.20),
    "claude-opus-5": Price(5, 25, 0.50),
    "claude-opus-4-8": Price(5, 25, 0.50),
    "claude-opus-4-7": Price(5, 25, 0.50),
    "claude-opus-4-6": Price(5, 25, 0.50),
    "claude-sonnet-5-5": Price(2, 10, 0.20),
    "claude-sonnet-5": Price(2, 10, 0.20),
    "claude-sonnet-4-6": Price(3, 15, 0.30),
    "claude-sonnet-4-5": Price(3, 15, 0.30),
    "claude-haiku-5-5": Price(0.10, 0.50, 0.01),
    "claude-haiku-4-5": Price(1, 5, 0.10),
    # OpenAI
    "gpt-6-astra": Price(10, 50, 1.00),
    "gpt-6.1-sol": Price(2, 10, 0.10),
    "gpt-6.1-terra": Price(1, 5, 0.05),
    "gpt-6-luna": Price(0.10, 0.50, 0.01),
    "gpt-6-sol": Price(2, 10, 0.20),
    "gpt-5.6-sol": Price(4, 20, 0.40),
    "gpt-5.6-terra": Price(2, 12, 0.20),
    "gpt-5.6-luna": Price(0.20, 1.20, 0.02),
    "gpt-5.5": Price(5, 30, 0.50),
    "gpt-5.5-pro": Price(30, 180, 30),
    "gpt-5.4": Price(2.50, 15, 0.25),
    "gpt-5.4-pro": Price(30, 180, 30),
    "gpt-5.4-mini": Price(0.75, 4.50, 0.075),
    "gpt-5.4-nano": Price(0.20, 1.25, 0.02),
    "gpt-5.2": Price(1.75, 14, 0.175),
    "gpt-5.2-pro": Price(21, 168, 21),
    "gpt-5.1": Price(1.25, 10, 0.125),
    "gpt-5": Price(1.25, 10, 0.125),
    "gpt-5-mini": Price(0.25, 2, 0.025),
    "gpt-5-nano": Price(0.05, 0.40, 0.005),
    "gpt-5-pro": Price(15, 120, 15),
    "gpt-4.1": Price(2, 8, 0.50),
    "gpt-4.1-mini": Price(0.40, 1.60, 0.10),
    "gpt-4o": Price(2.50, 10, 1.25),
    "gpt-4o-2024-05-13": Price(5, 15, 5),       # a dearer snapshot than gpt-4o's, which its name would be priced as
    "gpt-4o-mini": Price(0.15, 0.60, 0.075),
    "o3-pro": Price(20, 80, 20),
    "o3": Price(2, 8, 0.50),
    # Gemini
    "gemini-4-carbon": Price(4, 20, 0.20),
    "gemini-4-argon": Price(4, 20, 0.20),
    "gemini-4-hydrogen": Price(1.50, 7.50, 0.075),
    "gemini-3.8-flash": Price(1.50, 7.50, 0.15),
    "gemini-3.6-flash": Price(1.50, 7.50, 0.15),
    "gemini-3.5-flash-lite": Price(0.30, 2.50, 0.03),
    "gemini-3.1-pro-preview": Price(2, 12, 0.20),
    "gemini-3.1-flash-lite": Price(0.25, 1.50, 0.025),
    "gemini-3-flash-preview": Price(0.50, 3, 0.05),
    "gemini-2.5-pro": Price(1.25, 10, 0.125),
    "gemini-2.5-flash": Price(0.30, 2.50, 0.03),
    "gemini-2.5-flash-lite": Price(0.10, 0.40, 0.01),
}
# Some cost more once a prompt passes so many tokens.
LONG_PROMPTS = {"claude-haiku-5-5": (100_000, Price(0.50, 2.50, 0.05)),
                "gemini-3.1-pro-preview": (200_000, Price(4, 18, 0.40)),
                "gemini-2.5-pro": (200_000, Price(2.50, 15, 0.25))}
FREE = Price(0, 0, 0)
MOST = 2**63 - 1               # the largest count of tokens or cost the ledger holds: a signed 64-bit integer

_FAMILIES = {"claude": re.compile(r"claude-"), "openai": re.compile(r"gpt-|o[0-9]"), "gemini": re.compile(r"gemini-")}


def _dearest(names: list[str]) -> Price:
    """The dearest of these: the highest output price, then input; the first in the table of those alike."""
    return PRICES[max(names, key=lambda n: (PRICES[n].output, PRICES[n].input, -list(PRICES).index(n)))]


def _family(name: str) -> str | None:
    return next((f for f, starts in _FAMILIES.items() if starts.match(name)), None)


DEAREST_OF = {f: _dearest([n for n in PRICES if _family(n) == f]) for f in _FAMILIES}
DEAREST = _dearest(list(PRICES))


def price(model: str, prompt_tokens: int = 0, local: bool = False) -> Price:
    """The price of `model`, a bare name or one with a date after it, after any `vendor/`, for a prompt of `prompt_tokens`
    (input, cache reads and cache writes together). One the table doesn't know is FREE when `local`, and else the dearest
    of its family."""
    bare = model.rsplit("/", 1)[-1]
    name = max((k for k in PRICES if bare == k or bare.startswith(k + "-")), key=len, default=None)
    if name is None:
        if local:
            return FREE
        family = _family(bare)
        return DEAREST_OF[family] if family else DEAREST
    long = LONG_PROMPTS.get(name)
    return long[1] if long is not None and prompt_tokens > long[0] else PRICES[name]


def cost_micros(asked: str, served: str, input_tokens: int = 0, output_tokens: int = 0,
                cache_write_tokens: int = 0, cache_read_tokens: int = 0, local: bool = False) -> int:
    """What the tokens cost, in micro-dollars, rounded up, and at most MOST. Priced at the dearer of
    the model asked and the model that served the reply, so a server-side fallback never reads as
    cheaper than it may have been."""
    prompt = input_tokens + cache_write_tokens + cache_read_tokens

    def at(p: Price) -> int:
        i, o, w, r = p.nanos()
        return input_tokens * i + output_tokens * o + cache_write_tokens * w + cache_read_tokens * r
    nanos = max(at(price(asked, prompt, local)), at(price(served or asked, prompt, local)))
    return min(-(-nanos // 1000), MOST)


def batch_micros(micros: int) -> int:
    """What a call costs through the Message Batches API: half, rounded up."""
    return -(-micros // 2)


CHARS_PER_TOKEN = 3            # on the low side: mail runs near 4, and an estimate should err high
OUTPUT_TOKENS_GUESS = 400      # a triage answer is rarely half this


def batch_estimate(model: str, chars: int) -> int:
    """About the most one triage request of `chars` characters should cost through a batch. A batch's cost
    is only known when its answers are in, so this is what it books against the daily limit until then."""
    return batch_micros(cost_micros(model, model, chars // CHARS_PER_TOKEN + 1, OUTPUT_TOKENS_GUESS))


def usd(micros: int) -> str:
    """`micros` in dollars and cents, rounded up to the cent, so a little spending never reads as
    none: 15_200 is "$0.02"."""
    cents = -(-int(micros) // 10_000)
    return f"${cents // 100:,}.{cents % 100:02d}"
