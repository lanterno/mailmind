//! The AI features, by the kind of call each makes, which decides how the cost governor treats it
//! (`mailmind.assistant.domain.features`).
//!
//! - Interactive: someone asked for it, and is waiting. Everything not named below.
//! - Background: triage, which keeps summaries up with new mail, triage-batch, the same for a backlog, and delta, the
//!   line on what came in since you last looked, which the page asks for on its own.
//! - Speculative: a reply drafted ahead, made only in case it is wanted.
//! - Scheduled: what a person turned on in Settings and doesn't wait for: the morning's brief, the week's review and
//!   Tidy's daily run.

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Kind {
    Interactive,
    Background,
    Speculative,
    Scheduled,
}

pub fn kind(feature: &str) -> Kind {
    match feature {
        "triage" | "triage-batch" | "delta" => Kind::Background,
        "draft-ahead" => Kind::Speculative,
        "brief-ahead" | "review-ahead" | "auto-tidy" => Kind::Scheduled,
        _ => Kind::Interactive,
    }
}
