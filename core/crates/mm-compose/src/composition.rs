//! What a request to send or save comes to before it is a message (`compose.domain.composition`): its text and
//! HTML, how much of that a person typed and how much is the quote, and its threading. Reading the original is the
//! use case's, which hands over what it found.

use std::collections::BTreeSet;

use crate::builder::{reference_chain, thread_ids};
use crate::markdown;
use crate::quote::{own_html, quote_tail, quoted_text_html};
use crate::replies::{Original, forward_bodies, reply_bodies};
use crate::resume::lf;
use crate::spec::{Attachment, MessageSpec};

/// A body made (`composition.Body`).
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Body {
    /// What a person typed, before any quote is added.
    pub own: String,
    /// That and the quote.
    pub text: String,
    pub html: Option<String>,
    pub tail_text: Option<String>,
    pub tail_html: Option<String>,
    pub in_reply_to: Option<String>,
    pub references: Vec<String>,
}

/// A message to build, and what a save records of it (`builder.Composed`): what a person typed, the quote, and the
/// files a forward took along.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Composed {
    pub spec: MessageSpec,
    pub own: String,
    pub tail_text: Option<String>,
    pub tail_html: Option<String>,
    pub forwarded: Vec<Attachment>,
}

/// The HTML beside the text (`_html_with`): what a person typed in Markdown is its own HTML, then the quote's, or the
/// quote's text where that has none. With no Markdown in it, a quote's HTML is what makes there be any at all.
fn html_with(
    own: &str,
    tail_text: Option<&str>,
    tail_html: Option<&str>,
    pictures: &BTreeSet<String>,
) -> Option<String> {
    match markdown::render(own, pictures) {
        None => tail_html.filter(|h| !h.is_empty()).map(|h| own_html(own) + h),
        Some(mine) => {
            let tail = match (tail_html.filter(|h| !h.is_empty()), tail_text.filter(|t| !t.is_empty())) {
                (Some(html), _) => html.to_owned(),
                (None, Some(text)) => quoted_text_html(text),
                (None, None) => String::new(),
            };
            Some(mine + &tail)
        }
    }
}

/// What the Preview shows of what a person typed, and whether it goes out as HTML at all.
pub fn preview(text: &str, pictures: &BTreeSet<String>) -> (String, bool) {
    match markdown::render(text, pictures) {
        None => (own_html(text), false),
        Some(mine) => (mine, true),
    }
}

pub fn as_typed(text: &str, pictures: &BTreeSet<String>) -> Body {
    Body { own: text.to_owned(), text: text.to_owned(), html: html_with(text, None, None, pictures), ..Body::default() }
}

/// The quote or forwarded section a reopened draft carries (`tail_of`).
pub fn tail_of(tail_text: Option<&str>) -> String {
    lf(tail_text.unwrap_or_default())
}

/// A reopened draft goes out as drafted (`as_drafted`): your text, the quote it already carried and its own threading
/// headers. `recorded` is what the draft's newest save holds of its quote, (text, html): its HTML goes only where that
/// vouches for this exact quote.
pub fn as_drafted(
    text: &str,
    tail: &str,
    recorded: Option<(Option<&str>, Option<&str>)>,
    in_reply_to: Option<&str>,
    references: Option<&str>,
    pictures: &BTreeSet<String>,
) -> Body {
    let own = lf(text);
    let tail_html = match recorded {
        Some((recorded_text, Some(html))) if !tail.is_empty() && !html.is_empty() => {
            (lf(recorded_text.unwrap_or_default()) == tail).then(|| mm_content::sanitize::html::without_fold(html))
        }
        _ => None,
    };
    let (in_reply_to, references) = thread_ids(in_reply_to, references);
    let tail_text = if tail.is_empty() { None } else { Some(tail.to_owned()) };
    Body {
        text: own.clone() + tail,
        html: html_with(&own, tail_text.as_deref(), tail_html.as_deref(), pictures),
        own,
        tail_text,
        tail_html,
        in_reply_to,
        references,
    }
}

/// The message a reply or forward answers, as its row and its parsed body give it (`_original`).
pub fn original(
    from_name: Option<String>,
    from_addr: Option<String>,
    subject: Option<String>,
    parsed: Option<&mm_content::mime::Parsed<'_>>,
) -> Original {
    Original {
        from_name,
        from_addr,
        subject,
        date: parsed.map(|p| p.date.clone()),
        to: parsed.map(|p| p.to.clone()),
        cc: parsed.map(|p| p.cc.clone()),
        text: parsed.and_then(|p| p.text.as_ref().map(|t| t.to_string())),
        html: parsed.and_then(|p| p.html.as_ref().map(|h| h.to_string())),
    }
}

/// A reply (`as_reply`): `message_id`, `refs` and `in_reply_to` are the original's.
pub fn as_reply(
    own: &str,
    original: &Original,
    message_id: Option<&str>,
    refs: Option<&str>,
    in_reply_to: Option<&str>,
    pictures: &BTreeSet<String>,
) -> Body {
    let (text, html) = reply_bodies(own, original);
    let (tail_text, tail_html) = quote_tail(own, Some(&text), html.as_deref());
    Body {
        own: own.to_owned(),
        html: html_with(own, tail_text.as_deref(), tail_html.as_deref(), pictures),
        text,
        tail_text,
        tail_html,
        in_reply_to: message_id.map(str::to_owned),
        references: reference_chain(message_id, refs, in_reply_to),
    }
}

/// A forward starts a thread of its own, so it names no original (`as_forward`).
pub fn as_forward(own: &str, original: &Original, pictures: &BTreeSet<String>) -> Body {
    let (text, html) = forward_bodies(own, original);
    let (tail_text, tail_html) = quote_tail(own, Some(&text), html.as_deref());
    Body {
        own: own.to_owned(),
        html: html_with(own, tail_text.as_deref(), tail_html.as_deref(), pictures),
        text,
        tail_text,
        tail_html,
        ..Body::default()
    }
}

/// The request's recipients and subject with the body made, from the sending account (`composed`).
#[allow(clippy::too_many_arguments)]
pub fn composed(
    account: &str,
    account_name: &str,
    to: Vec<String>,
    cc: Vec<String>,
    bcc: Vec<String>,
    subject: &str,
    made: Body,
    attachments: Vec<Attachment>,
    forwarded: Vec<Attachment>,
) -> Composed {
    Composed {
        spec: MessageSpec {
            account: account.to_owned(),
            account_name: account_name.to_owned(),
            to,
            cc,
            bcc,
            subject: subject.to_owned(),
            text: made.text,
            html: made.html,
            attachments,
            in_reply_to: made.in_reply_to,
            references: made.references,
        },
        own: made.own,
        tail_text: made.tail_text,
        tail_html: made.tail_html,
        forwarded,
    }
}
