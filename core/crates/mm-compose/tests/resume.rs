//! A saved draft read back into the composer as the Python app's `resume.read` and `resume_files` read it: the drafts
//! in `fixtures/resume_oracle.json` (the corpus's, the core's hand-made MIME cases, a battery of Subject, To, Bcc and
//! threading headers, drafts the builder writes and hard ones), each with what Python answered for it.
//!
//! Left out of the oracle, where the two part on purpose or at the edges: an attached email, which comes back as it
//! was sent where Python wrote it out again; base64 cut short or padded mid-line; and a part's header nested past
//! what Python's stack held, or a multipart whose boundary is empty.
#![allow(clippy::unwrap_used)]

use mm_compose::builder::thread_ids;
use mm_compose::resume;
use serde_json::{Value, json};

/// The oracle's text for bytes: each character one byte.
fn latin1(s: &str) -> Vec<u8> {
    s.chars().map(|c| u8::try_from(u32::from(c)).unwrap()).collect()
}

/// What this crate reads of a draft, in the oracle's shape.
fn answer(raw: &[u8]) -> Value {
    let draft = match resume::read(raw) {
        Ok(draft) => draft,
        Err(refused) => return json!({"result": {"refused": refused.said}}),
    };
    let result = match resume::resume_files(raw) {
        Ok((files, inline)) => json!({
            "files": files.iter().map(|f| {
                let mut file = json!({"filename": f.filename, "content_type": f.content_type, "size": f.size, "b64": f.b64});
                if let Some(cid) = &f.cid {
                    file["cid"] = json!(cid);
                }
                file
            }).collect::<Vec<_>>(),
            "inline": inline,
        }),
        Err(refused) => json!({"refused": refused.said}),
    };
    let (irt, refs) = thread_ids(draft.in_reply_to.as_deref(), draft.references.as_deref());
    json!({
        "subject": draft.subject, "to": draft.to, "cc": draft.cc, "bcc": draft.bcc, "irt": irt, "refs": refs,
        "text": draft.text, "html": draft.html, "result": result,
    })
}

#[test]
fn each_draft_reopens_as_python_reopened_it() {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/tests/fixtures/resume_oracle.json");
    let cases: Vec<Value> = serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
    assert!(cases.len() > 400, "the oracle holds its cases");
    let mut differ = Vec::new();
    for case in &cases {
        let mine = answer(&latin1(case["raw"].as_str().unwrap()));
        for (field, theirs) in case["expected"].as_object().unwrap() {
            if mine.get(field) != Some(theirs) {
                let shown = |v: Option<&Value>| {
                    v.map(Value::to_string).unwrap_or_default().chars().take(300).collect::<String>()
                };
                differ.push(format!(
                    "{}: {field} is {}, Python's {}",
                    case["name"].as_str().unwrap(),
                    shown(mine.get(field)),
                    shown(Some(theirs))
                ));
            }
        }
    }
    assert!(differ.is_empty(), "{} differences:\n{}", differ.len(), differ.join("\n"));
}

#[test]
fn an_attached_email_comes_back_as_it_was_sent() {
    let inner = "From: Bo <bo@example.test>\r\nSubject: Inner\r\n\r\nThe forwarded text.\r\n";
    let raw = format!(
        "From: ada@example.test\r\nSubject: Fwd\r\nContent-Type: multipart/mixed; boundary=b\r\n\r\n--b\r\n\
         Content-Type: text/plain\r\n\r\nHave a look.\r\n--b\r\nContent-Type: message/rfc822\r\n\
         Content-Disposition: attachment; filename=Inner.eml\r\n\r\n{inner}--b--\r\n"
    );
    let (files, inline) = resume::resume_files(raw.as_bytes()).unwrap();
    assert_eq!(inline, 0);
    assert_eq!(files.len(), 1);
    assert_eq!((files[0].filename.as_str(), files[0].content_type.as_str()), ("Inner.eml", "message/rfc822"));
    // Its bytes as written, but for the line end before the delimiter, which is the delimiter's (RFC 2046).
    assert_eq!(files[0].size, inner.len() - 2);
}
