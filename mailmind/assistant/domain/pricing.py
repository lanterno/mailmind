"""What a call cost, in micro-dollars (US dollars × 1,000,000): integers, so a day's total never
drifts. Prices are Anthropic's list prices in US dollars per million tokens, which is the same as
micro-dollars per token. A cache write costs 1.25 times the input price: the 5-minute cache, the
only one MailMind would ask for.

A model the table doesn't know is priced as the dearest one, so a spending limit errs toward
stopping early, never toward overspending.
"""
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
}
# Haiku 5.5 costs more once a prompt passes 100K tokens.
LONG_PROMPTS = {"claude-haiku-5-5": (100_000, Price(0.50, 2.50, 0.05))}
DEAREST = PRICES["claude-fable-5-1"]


def price(model: str, prompt_tokens: int = 0) -> Price:
    """The price of `model`, a bare name or one with a date after it, for a prompt of
    `prompt_tokens` (input, cache reads and cache writes together)."""
    name = max((k for k in PRICES if model == k or model.startswith(k + "-")), key=len, default=None)
    if name is None:
        return DEAREST
    long = LONG_PROMPTS.get(name)
    return long[1] if long is not None and prompt_tokens > long[0] else PRICES[name]


def cost_micros(asked: str, served: str, input_tokens: int = 0, output_tokens: int = 0,
                cache_write_tokens: int = 0, cache_read_tokens: int = 0) -> int:
    """What the tokens cost, in micro-dollars, rounded up. Priced at the dearer of the model asked
    and the model that served the reply, so a server-side fallback never reads as cheaper than it
    may have been."""
    prompt = input_tokens + cache_write_tokens + cache_read_tokens

    def at(p: Price) -> int:
        i, o, w, r = p.nanos()
        return input_tokens * i + output_tokens * o + cache_write_tokens * w + cache_read_tokens * r
    nanos = max(at(price(asked, prompt)), at(price(served or asked, prompt)))
    return -(-nanos // 1000)


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
