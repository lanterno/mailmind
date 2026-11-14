//! A reply drafted by Claude (`application.reply_draft`): a thread or a single message gathered as summaries gather
//! them, and one schema-constrained call answering `{"body": ...}`. An editable draft, never sent. The mail goes to
//! the model as untrusted data.

use std::sync::Arc;

use mm_kernel::errors::Problem;
use serde_json::{Value, json};

use crate::calls::Calls;
use crate::context::Context;
use crate::prompts;
use crate::refusals::{AiFailure, answered, unreachable};
use crate::rewrite::text_of;
use crate::text::{cut, strip};

pub fn schema() -> Value {
    json!({"type": "object", "properties": {"body": {"type": "string"}}, "required": ["body"]})
}

/// What a reply is drafted to, and how.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Draft<'a> {
    /// Whose mail it is.
    pub account: &'a str,
    pub thread_id: Option<&'a str>,
    pub pk: Option<i64>,
    pub instruction: &'a str,
    pub tone: &'a str,
    /// Who replies: the From may name another of the person's addresses.
    pub me: &'a str,
    /// What names the call in the usage ledger: "draft", or "draft-ahead" when it is drafted ahead.
    pub feature: &'a str,
}

#[derive(Debug)]
pub struct ReplyDrafts {
    calls: Arc<Calls>,
    context: Context,
}

impl ReplyDrafts {
    pub fn new(calls: Arc<Calls>, context: Context) -> ReplyDrafts {
        ReplyDrafts { calls, context }
    }

    pub fn calls(&self) -> &Arc<Calls> {
        &self.calls
    }

    /// A reply to the thread, or else to the message.
    pub fn draft(&self, asked: &Draft<'_>) -> Result<Value, AiFailure> {
        let rows = match (asked.thread_id.filter(|t| !t.is_empty()), asked.pk) {
            (Some(thread_id), _) => self.context.rows(asked.account, thread_id)?,
            (None, Some(pk)) => self.context.mail().to_reply_to(pk)?,
            (None, None) => Vec::new(),
        };
        if rows.is_empty() {
            return Err(Problem::not_found("nothing to reply to").into());
        }
        let (blocks, _) = self.context.gather(&rows)?;
        let subject = rows[0].subject.as_deref().filter(|s| !s.is_empty()).unwrap_or("(no subject)");
        let tone_word = prompts::look_up(&prompts::TONES, asked.tone)
            .or_else(|| prompts::look_up(&prompts::TONES, prompts::DEFAULT_TONE))
            .unwrap_or_default();
        let instruction = strip(asked.instruction);
        let instr = if instruction.is_empty() {
            prompts::REPLY_UNINSTRUCTED.to_owned()
        } else {
            prompts::REPLY_INSTRUCTED.replace("{instruction}", cut(instruction, prompts::INSTRUCTION_MAX))
        };
        let ctx = if asked.thread_id.is_some_and(|t| !t.is_empty()) { "thread" } else { "message" };
        let me = if asked.me.is_empty() { "the user" } else { asked.me };
        let prompt = prompts::REPLY.fill(&[
            ("ctx", ctx),
            ("me", me),
            ("tone", tone_word),
            ("instr", &instr),
            ("subject", subject),
            ("blocks", &blocks.join("\n")),
        ]);
        let answer = answered(
            self.calls.call_claude_json(&prompt, &schema(), None, asked.feature),
            "draft failed",
            "draft unavailable",
        )?;
        let body = text_of(&answer, "body")?;
        let body = strip(&body);
        if body.is_empty() {
            return Err(unreachable("no draft produced").into());
        }
        Ok(json!({"ok": true, "body": cut(body, 6000), "tone": asked.tone, "model": self.calls.model_label()}))
    }
}
