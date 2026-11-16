//! What a call cost, in micro-dollars (US dollars × 1,000,000): integers, so a day's total never drifts
//! (`mailmind.assistant.domain.pricing`). Prices are the providers' list prices in US dollars per million tokens,
//! which is micro-dollars per token, in one table across Anthropic, OpenAI and Google (#49): their models' names
//! don't collide. Where a price is set to rise soon, the table holds the higher one. A cache write costs 1.25 times
//! the input price; only Anthropic reports any.
//!
//! A model the table doesn't know is looked up by what follows the last `/` of its name (OpenRouter's
//! `openai/gpt-6.1-sol`, Gemini's `models/gemini-…`). Still unknown, it costs nothing on this machine (a model on
//! this Mac, or any provider at a loopback address); anywhere else it is priced as the dearest of its family
//! (`claude-`, `gpt-` or `o<digit>`, `gemini-`), or of the whole table for a name of no known family, so a spending
//! limit errs toward stopping early. Every price is the Python app's, to the nano-dollar.

/// Nano-dollars per token (thousandths of a dollar per million tokens): input, output and cache read.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Price {
    input: i64,
    output: i64,
    cache_read: i64,
}

impl Price {
    /// Nano-dollars per token: input, output, cache write (1.25 × input, a half rounded to even as Python's `round`
    /// does, which only prices no provider reports cache writes for need) and cache read.
    fn nanos(self) -> (i64, i64, i64, i64) {
        let (whole, quarters) = ((self.input * 5).div_euclid(4), (self.input * 5).rem_euclid(4));
        let write = match quarters {
            0 | 1 => whole,
            2 if whole % 2 == 0 => whole,
            _ => whole + 1,
        };
        (self.input, self.output, write, self.cache_read)
    }
}

const fn nanos(input: i64, output: i64, cache_read: i64) -> Price {
    Price { input, output, cache_read }
}

/// What a model that costs nothing costs: one on this machine.
const FREE: Price = nanos(0, 0, 0);

/// Each model's price: Anthropic's; OpenAI's (developers.openai.com/api/docs/pricing, read 2026-11-16, the standard
/// tier, prompts up to 272K, a cached input the page gives no price for priced as input); and Google's
/// (aistudio.google.com/docs/pricing, read 2026-11-16, the paid standard tier). A dated snapshot the page prices apart
/// from its name's (`gpt-4o-2024-05-13`) has a row of its own, or it would be priced as the name a date follows.
const PRICES: [(&str, Price); 55] = [
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
    ("gpt-6-astra", nanos(10_000, 50_000, 1_000)),
    ("gpt-6.1-sol", nanos(2_000, 10_000, 100)),
    ("gpt-6.1-terra", nanos(1_000, 5_000, 50)),
    ("gpt-6-luna", nanos(100, 500, 10)),
    ("gpt-6-sol", nanos(2_000, 10_000, 200)),
    ("gpt-5.6-sol", nanos(4_000, 20_000, 400)),
    ("gpt-5.6-terra", nanos(2_000, 12_000, 200)),
    ("gpt-5.6-luna", nanos(200, 1_200, 20)),
    ("gpt-5.5", nanos(5_000, 30_000, 500)),
    ("gpt-5.5-pro", nanos(30_000, 180_000, 30_000)),
    ("gpt-5.4", nanos(2_500, 15_000, 250)),
    ("gpt-5.4-pro", nanos(30_000, 180_000, 30_000)),
    ("gpt-5.4-mini", nanos(750, 4_500, 75)),
    ("gpt-5.4-nano", nanos(200, 1_250, 20)),
    ("gpt-5.2", nanos(1_750, 14_000, 175)),
    ("gpt-5.2-pro", nanos(21_000, 168_000, 21_000)),
    ("gpt-5.1", nanos(1_250, 10_000, 125)),
    ("gpt-5", nanos(1_250, 10_000, 125)),
    ("gpt-5-mini", nanos(250, 2_000, 25)),
    ("gpt-5-nano", nanos(50, 400, 5)),
    ("gpt-5-pro", nanos(15_000, 120_000, 15_000)),
    ("gpt-4.1", nanos(2_000, 8_000, 500)),
    ("gpt-4.1-mini", nanos(400, 1_600, 100)),
    ("gpt-4o", nanos(2_500, 10_000, 1_250)),
    ("gpt-4o-2024-05-13", nanos(5_000, 15_000, 5_000)),
    ("gpt-4o-mini", nanos(150, 600, 75)),
    ("o3-pro", nanos(20_000, 80_000, 20_000)),
    ("o3", nanos(2_000, 8_000, 500)),
    ("gemini-4-carbon", nanos(4_000, 20_000, 200)),
    ("gemini-4-argon", nanos(4_000, 20_000, 200)),
    ("gemini-4-hydrogen", nanos(1_500, 7_500, 75)),
    ("gemini-3.8-flash", nanos(1_500, 7_500, 150)),
    ("gemini-3.6-flash", nanos(1_500, 7_500, 150)),
    ("gemini-3.5-flash-lite", nanos(300, 2_500, 30)),
    ("gemini-3.1-pro-preview", nanos(2_000, 12_000, 200)),
    ("gemini-3.1-flash-lite", nanos(250, 1_500, 25)),
    ("gemini-3-flash-preview", nanos(500, 3_000, 50)),
    ("gemini-2.5-pro", nanos(1_250, 10_000, 125)),
    ("gemini-2.5-flash", nanos(300, 2_500, 30)),
    ("gemini-2.5-flash-lite", nanos(100, 400, 10)),
];
/// Models that cost more once a prompt passes so many tokens: Haiku 5.5 past 100K, Gemini's Pro models past 200K.
const LONG_PROMPTS: [(&str, i64, Price); 3] = [
    ("claude-haiku-5-5", 100_000, nanos(500, 2_500, 50)),
    ("gemini-3.1-pro-preview", 200_000, nanos(4_000, 18_000, 400)),
    ("gemini-2.5-pro", 200_000, nanos(2_500, 15_000, 250)),
];

/// The families a model's name may say it is of, by how it starts.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Family {
    Claude,
    Gpt,
    Gemini,
}

/// The family a bare name is of: `claude-`, `gpt-` or `o` and a digit, `gemini-`; None for a name of none of them.
fn family(name: &str) -> Option<Family> {
    let mut chars = name.chars();
    if name.starts_with("claude-") {
        Some(Family::Claude)
    } else if name.starts_with("gpt-") || chars.next() == Some('o') && chars.next().is_some_and(|c| c.is_ascii_digit())
    {
        Some(Family::Gpt)
    } else if name.starts_with("gemini-") {
        Some(Family::Gemini)
    } else {
        None
    }
}

/// The dearest price of the models of `of` (every model, for None), as the Python app picks it: the highest output
/// price, then input, and the first in the table of those alike.
fn dearest(of: Option<Family>) -> Price {
    let ours = PRICES.iter().filter(|(name, _)| of.is_none() || family(name) == of).map(|(_, p)| *p);
    ours.fold(None, |dearest: Option<Price>, p| match dearest {
        Some(d) if (d.output, d.input) >= (p.output, p.input) => Some(d),
        _ => Some(p),
    })
    .unwrap_or(FREE)
}

/// The price the table lists for `model`, a bare name or one with a date after it, for a prompt of `prompt_tokens`;
/// None for a model it doesn't know.
fn listed(model: &str, prompt_tokens: i64) -> Option<Price> {
    let named = PRICES
        .iter()
        .filter(|(name, _)| model == *name || model.strip_prefix(name).is_some_and(|rest| rest.starts_with('-')))
        .max_by_key(|(name, _)| name.len());
    let (name, found) = named?;
    match LONG_PROMPTS.iter().find(|(long, _, _)| long == name) {
        Some((_, over, dearer)) if prompt_tokens > *over => Some(*dearer),
        _ => Some(*found),
    }
}

/// The name a model is priced by: what follows the last `/`, so a vendor's prefix and Gemini's `models/` go.
fn bare(model: &str) -> &str {
    model.rsplit('/').next().unwrap_or(model)
}

/// The price of `model` for a prompt of `prompt_tokens` (input, cache reads and cache writes together), as a call off
/// this machine pays it: one the table doesn't know at the dearest of its family.
pub fn price(model: &str, prompt_tokens: i64) -> Price {
    price_on(false, model, prompt_tokens)
}

/// The price of `model` for a prompt of `prompt_tokens`, where a model on this machine (`local`) that the table
/// doesn't know costs nothing.
pub fn price_on(local: bool, model: &str, prompt_tokens: i64) -> Price {
    match listed(bare(model), prompt_tokens) {
        Some(price) => price,
        None if local => FREE,
        None => dearest(family(bare(model))),
    }
}

/// What the tokens cost, in micro-dollars, rounded up: at the dearer of the model asked and the model that served the
/// reply, so a server-side fallback never reads as cheaper than it may have been. Off this machine.
pub fn cost_micros(asked: &str, served: &str, input: i64, output: i64, cache_write: i64, cache_read: i64) -> i64 {
    cost_micros_on(false, asked, served, (input, output, cache_write, cache_read))
}

/// What the tokens (input, output, cache write, cache read) cost, in micro-dollars, rounded up, at the dearer of the
/// model asked and the one that served the reply; on this machine (`local`) a model the table doesn't know is free.
/// Worked out whole, as Python's integers are, and held at the most an `i64` holds: an absurd count of tokens a
/// service reports never wraps round to a cost that is small, or less than nothing.
pub fn cost_micros_on(local: bool, asked: &str, served: &str, tokens: (i64, i64, i64, i64)) -> i64 {
    let (input, output, cache_write, cache_read) = tokens;
    let prompt = input.saturating_add(cache_write).saturating_add(cache_read);
    let at = |p: Price| {
        let (i, o, w, r) = p.nanos();
        let each = [(input, i), (output, o), (cache_write, w), (cache_read, r)];
        each.iter().map(|(tokens, nanos)| i128::from(*tokens) * i128::from(*nanos)).sum::<i128>()
    };
    let served = if served.is_empty() { asked } else { served };
    let nanos = at(price_on(local, asked, prompt)).max(at(price_on(local, served, prompt)));
    let micros = -(-nanos).div_euclid(1000);
    i64::try_from(micros).unwrap_or(if micros < 0 { i64::MIN } else { i64::MAX })
}

/// What a call costs through the Message Batches API: half, rounded up, for any cost `cost_micros` gives.
pub fn batch_micros(micros: i64) -> i64 {
    micros.div_euclid(2) + micros.rem_euclid(2)
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
