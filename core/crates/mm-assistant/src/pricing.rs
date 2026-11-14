//! What a call cost, in micro-dollars (US dollars × 1,000,000): integers, so a day's total never drifts
//! (`mailmind.assistant.domain.pricing`). Prices are Anthropic's list prices in US dollars per million tokens, which
//! is micro-dollars per token. A cache write costs 1.25 times the input price.
//!
//! A model the table doesn't know is priced as the dearest one, so a spending limit errs toward stopping early.

/// Nano-dollars per token (thousandths of a dollar per million tokens): input, output and cache read.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Price {
    input: i64,
    output: i64,
    cache_read: i64,
}

impl Price {
    /// Nano-dollars per token, exactly: input, output, cache write (`round(input × 1250)`), cache read.
    fn nanos(self) -> (i64, i64, i64, i64) {
        // Every input price here is a whole number of nano-dollars divisible by 4, so this is exact.
        (self.input, self.output, self.input * 5 / 4, self.cache_read)
    }
}

const fn nanos(input: i64, output: i64, cache_read: i64) -> Price {
    Price { input, output, cache_read }
}

/// Each model's price.
const PRICES: [(&str, Price); 15] = [
    ("claude-fable-5-1", nanos(10_000, 50_000, 250)),
    ("claude-mythos-5-1", nanos(10_000, 50_000, 250)),
    ("claude-fable-5", nanos(10_000, 50_000, 1_000)),
    ("claude-mythos-5", nanos(10_000, 50_000, 1_000)),
    ("claude-opus-5-5", nanos(4_000, 20_000, 200)),
    ("claude-opus-5", nanos(5_000, 25_000, 500)),
    ("claude-opus-4-8", nanos(5_000, 25_000, 500)),
    ("claude-opus-4-7", nanos(5_000, 25_000, 500)),
    ("claude-opus-4-6", nanos(5_000, 25_000, 500)),
    ("claude-sonnet-5-5", nanos(2_000, 10_000, 200)),
    ("claude-sonnet-5", nanos(2_000, 10_000, 200)),
    ("claude-sonnet-4-6", nanos(3_000, 15_000, 300)),
    ("claude-sonnet-4-5", nanos(3_000, 15_000, 300)),
    ("claude-haiku-5-5", nanos(100, 500, 10)),
    ("claude-haiku-4-5", nanos(1_000, 5_000, 100)),
];
/// Haiku 5.5 costs more once a prompt passes 100K tokens.
const LONG_PROMPTS: [(&str, i64, Price); 1] = [("claude-haiku-5-5", 100_000, nanos(500, 2_500, 50))];
const DEAREST: Price = nanos(10_000, 50_000, 250);

/// The price of `model`, a bare name or one with a date after it, for a prompt of `prompt_tokens` (input, cache reads
/// and cache writes together).
pub fn price(model: &str, prompt_tokens: i64) -> Price {
    let named = PRICES
        .iter()
        .filter(|(name, _)| model == *name || model.strip_prefix(name).is_some_and(|rest| rest.starts_with('-')))
        .max_by_key(|(name, _)| name.len());
    let Some((name, found)) = named else { return DEAREST };
    match LONG_PROMPTS.iter().find(|(long, _, _)| long == name) {
        Some((_, over, dearer)) if prompt_tokens > *over => *dearer,
        _ => *found,
    }
}

/// What the tokens cost, in micro-dollars, rounded up: at the dearer of the model asked and the model that served the
/// reply, so a server-side fallback never reads as cheaper than it may have been.
pub fn cost_micros(asked: &str, served: &str, input: i64, output: i64, cache_write: i64, cache_read: i64) -> i64 {
    let prompt = input + cache_write + cache_read;
    let at = |p: Price| {
        let (i, o, w, r) = p.nanos();
        input * i + output * o + cache_write * w + cache_read * r
    };
    let served = if served.is_empty() { asked } else { served };
    let nanos = at(price(asked, prompt)).max(at(price(served, prompt)));
    -(-nanos).div_euclid(1000)
}

/// What a call costs through the Message Batches API: half, rounded up.
pub fn batch_micros(micros: i64) -> i64 {
    -(-micros).div_euclid(2)
}

/// On the low side: mail runs near 4, and an estimate should err high.
pub const CHARS_PER_TOKEN: i64 = 3;
/// A triage answer is rarely half this.
pub const OUTPUT_TOKENS_GUESS: i64 = 400;

/// About the most one triage request of `chars` characters should cost through a batch. A batch's cost is only known
/// when its answers are in, so this is what it books against the daily limit until then.
pub fn batch_estimate(model: &str, chars: i64) -> i64 {
    batch_micros(cost_micros(model, model, chars.div_euclid(CHARS_PER_TOKEN) + 1, OUTPUT_TOKENS_GUESS, 0, 0))
}
