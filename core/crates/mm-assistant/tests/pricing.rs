//! One price table across the AI providers (#49): OpenAI's and Google's models beside Anthropic's, a name with a
//! vendor's prefix, a dearer long prompt, and what a model the table doesn't know costs on this machine and off it.

use mm_assistant::pricing::{cost_micros, cost_micros_on, price, price_on};

/// What a million of each kind of token costs, in micro-dollars: input, output, cache read.
fn per_million(model: &str, local: bool) -> (i64, i64, i64) {
    let one = |tokens: (i64, i64, i64, i64)| cost_micros_on(local, model, "", tokens);
    (one((1_000_000, 0, 0, 0)), one((0, 1_000_000, 0, 0)), one((0, 0, 0, 1_000_000)))
}

#[test]
fn openai_and_gemini_models_cost_what_their_pages_say() {
    for (model, input, output, cached) in [
        ("gpt-6.1-sol", 2.0, 10.0, 0.10),
        ("gpt-6-astra", 10.0, 50.0, 1.0),
        ("gpt-6-luna", 0.10, 0.50, 0.01),
        ("gpt-5.5-pro", 30.0, 180.0, 30.0),
        ("gpt-5.4-mini", 0.75, 4.50, 0.075),
        ("gpt-5-nano", 0.05, 0.40, 0.005),
        ("gpt-5", 1.25, 10.0, 0.125),
        ("gpt-4o", 2.50, 10.0, 1.25),
        ("gpt-4o-mini", 0.15, 0.60, 0.075),
        ("o3", 2.0, 8.0, 0.50),
        ("o3-pro", 20.0, 80.0, 20.0),
        ("gemini-4-hydrogen", 1.50, 7.50, 0.075),
        ("gemini-4-carbon", 4.0, 20.0, 0.20),
        ("gemini-3.5-flash-lite", 0.30, 2.50, 0.03),
        ("gemini-2.5-flash-lite", 0.10, 0.40, 0.01),
        ("claude-opus-5-5", 4.0, 20.0, 0.20),
    ] {
        let micros = |dollars: f64| (dollars * 1_000_000.0).round() as i64;
        assert_eq!(per_million(model, false), (micros(input), micros(output), micros(cached)), "{model}");
        assert_eq!(
            per_million(model, true),
            per_million(model, false),
            "{model}: known, so priced on this machine too"
        );
    }
}

/// The price of `model` for a short prompt.
fn price_of(model: &str) -> impl PartialEq + std::fmt::Debug {
    price(model, 0)
}

#[test]
fn the_longest_name_that_fits_is_the_price_and_a_date_or_a_version_after_it_keeps_it() {
    assert_eq!(price_of("gpt-5-mini"), price_of("gpt-5-mini-2026-08-07"));
    assert_ne!(price_of("gpt-5-mini"), price_of("gpt-5"));
    assert_eq!(price_of("gpt-4o-mini-2024-07-18"), price_of("gpt-4o-mini"));
    assert_eq!(price_of("gemini-2.5-flash-lite"), price_of("gemini-2.5-flash-lite-preview-09-2025"));
    assert_ne!(price_of("gemini-2.5-flash-lite"), price_of("gemini-2.5-flash"));
    assert_eq!(price_of("o3-pro"), price_of("o3-pro-2025-06-10"));
}

#[test]
fn a_vendors_prefix_and_geminis_models_prefix_are_read_past() {
    assert_eq!(price_of("openai/gpt-6.1-sol"), price_of("gpt-6.1-sol"));
    assert_eq!(price_of("models/gemini-4-hydrogen"), price_of("gemini-4-hydrogen"));
    assert_eq!(price_of("google/gemini-2.5-pro"), price_of("gemini-2.5-pro"));
    assert_eq!(cost_micros("models/gemini-4-hydrogen", "", 1_000_000, 0, 0, 0), 1_500_000);
}

#[test]
fn geminis_pro_models_cost_more_past_two_hundred_thousand_prompt_tokens() {
    assert_eq!(cost_micros("gemini-3.1-pro-preview", "", 200_000, 0, 0, 0), 400_000);
    assert_eq!(cost_micros("gemini-3.1-pro-preview", "", 200_001, 0, 0, 0), 800_004);
    // Cached tokens count toward the prompt's length.
    assert_eq!(cost_micros_on(false, "gemini-2.5-pro", "", (100_000, 0, 0, 100_001)), 250_000 + 25_001);
}

#[test]
fn an_unknown_model_costs_the_dearest_of_its_family_off_this_machine() {
    // gpt-5.5-pro and gpt-5.4-pro are the dearest of OpenAI's.
    assert_eq!(per_million("gpt-7-future", false), (30_000_000, 180_000_000, 30_000_000));
    assert_eq!(per_million("o9", false), (30_000_000, 180_000_000, 30_000_000));
    assert_eq!(per_million("openai/gpt-7-future", false), (30_000_000, 180_000_000, 30_000_000));
    // gemini-4-carbon and -argon for input and output, the 3.1 Pro's long prompts for a cache read.
    assert_eq!(per_million("gemini-9-pro", false), (4_000_000, 20_000_000, 400_000));
    assert_eq!(per_million("models/gemini-9-pro", false), (4_000_000, 20_000_000, 400_000));
    // Fable and Mythos 5.
    assert_eq!(per_million("claude-unknown", false), (10_000_000, 50_000_000, 1_000_000));
}

#[test]
fn a_name_of_no_known_family_costs_the_dearest_of_the_whole_table() {
    for model in ["llama3.2:3b", "mistral-large", "", "parse-error", "opus"] {
        assert_eq!(per_million(model, false), (30_000_000, 180_000_000, 30_000_000), "{model:?}");
    }
}

#[test]
fn an_unknown_model_on_this_machine_costs_nothing() {
    for model in ["llama3.2:3b", "qwen3:8b", "gpt-oss:20b", "claude-unknown"] {
        assert_eq!(per_million(model, true), (0, 0, 0), "{model}");
    }
    assert_eq!(price_on(true, "llama3.2", 0), price_on(true, "phi4", 1_000_000));
    // Served by a model the table knows, it costs what that one does.
    assert_eq!(cost_micros_on(true, "llama3.2", "gpt-6.1-sol", (1_000_000, 0, 0, 0)), 2_000_000);
}

#[test]
fn a_cache_write_costs_a_quarter_more_than_input_and_only_anthropic_reports_one() {
    assert_eq!(cost_micros("claude-opus-5-5", "", 0, 0, 1_000_000, 0), 5_000_000);
    // A price no cache write is reported for rounds a quarter more up, and never fails.
    assert_eq!(cost_micros("gpt-5-nano", "", 0, 0, 1_000_000, 0), 63_000);
}
