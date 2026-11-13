//! Building a message, byte for byte as the Python app's `builder.build_message` builds it: the cases in
//! `fixtures/builder_oracle.json`, each with what Python answered for it (its bytes, Message-ID, envelope and who it is
//! to, or the words it refused in), and the threading ids a reply and a reopened draft name.
#![allow(clippy::unwrap_used)]

use std::collections::VecDeque;
use std::sync::Mutex;

use mm_compose::builder::{build_message, reference_chain, thread_ids};
use mm_compose::spec::{Attachment, Draws, MessageSpec, Minting};
use mm_kernel::seams::Moment;
use serde_json::Value;

/// Random numbers replayed in order, as the oracle replayed them to Python's `random`.
#[derive(Debug)]
struct Replay(Mutex<VecDeque<u64>>);

impl Draws for Replay {
    fn number(&self) -> u64 {
        self.0.lock().unwrap().pop_front().unwrap()
    }
}

fn strings(v: &Value) -> Vec<String> {
    v.as_array().map(|a| a.iter().map(|s| s.as_str().unwrap().to_owned()).collect()).unwrap_or_default()
}

/// The oracle's text for bytes: each character one byte.
fn latin1(s: &str) -> Vec<u8> {
    s.chars().map(|c| u8::try_from(u32::from(c)).unwrap()).collect()
}

fn spec_of(s: &Value) -> MessageSpec {
    MessageSpec {
        account: s["account"].as_str().unwrap().to_owned(),
        account_name: s["account_name"].as_str().unwrap().to_owned(),
        to: strings(&s["to"]),
        cc: strings(&s["cc"]),
        bcc: strings(&s["bcc"]),
        subject: s["subject"].as_str().unwrap().to_owned(),
        text: s["text"].as_str().unwrap().to_owned(),
        html: s["html"].as_str().map(str::to_owned),
        attachments: s["attachments"]
            .as_array()
            .unwrap()
            .iter()
            .map(|a| Attachment {
                filename: a["filename"].as_str().unwrap().to_owned(),
                content_type: a["content_type"].as_str().unwrap().to_owned(),
                data: latin1(a["data"].as_str().unwrap()),
                params: a["params"]
                    .as_array()
                    .map(|ps| {
                        ps.iter()
                            .map(|p| (p[0].as_str().unwrap().to_owned(), p[1].as_str().unwrap().to_owned()))
                            .collect()
                    })
                    .unwrap_or_default(),
                cid: a["cid"].as_str().map(str::to_owned),
            })
            .collect(),
        in_reply_to: s["in_reply_to"].as_str().map(str::to_owned),
        references: strings(&s["references"]),
    }
}

/// What differs between this builder's answer to a case and Python's, one line each.
fn differences(case: &Value) -> Vec<String> {
    let name = case["name"].as_str().unwrap();
    let draws = Replay(Mutex::new(case["draws"].as_array().unwrap().iter().map(|d| d.as_u64().unwrap()).collect()));
    let minting = Minting {
        now: Moment::from_micros(case["now"].as_i64().unwrap()),
        pid: u32::try_from(case["pid"].as_u64().unwrap()).unwrap(),
        draws: &draws,
    };
    let got = build_message(&spec_of(&case["spec"]), case["draft"].as_bool().unwrap(), minting);
    let want = &case["expected"];
    let refused = want["error"].as_str().or_else(|| want["words"].as_str());
    let built = match (got, refused) {
        (Err(got), Some(said)) if got == said => return Vec::new(),
        (Err(got), said) => return vec![format!("{name}: refused {got:?}, Python {said:?}")],
        (Ok(_), Some(said)) => return vec![format!("{name}: built, where Python refused {said:?}")],
        (Ok(built), None) => built,
    };
    let mut out = Vec::new();
    let raw: String = built.raw.iter().map(|b| char::from(*b)).collect();
    let (lines, python): (Vec<&str>, Vec<&str>) =
        (raw.split("\r\n").collect(), want["raw"].as_str().unwrap().split("\r\n").collect());
    for at in 0..lines.len().max(python.len()) {
        let (mine, theirs) = (lines.get(at).copied(), python.get(at).copied());
        if mine != theirs {
            out.push(format!("{name}: line {at} is {mine:?}, Python's {theirs:?}"));
        }
    }
    let left = draws.0.lock().unwrap().len();
    for (what, mine, theirs) in [
        ("message_id", Value::from(built.message_id), &want["message_id"]),
        ("envelope", Value::from(built.envelope), &want["envelope"]),
        ("to_disp", Value::from(built.to_disp), &want["to_disp"]),
        ("cc_disp", Value::from(built.cc_disp), &want["cc_disp"]),
        ("bcc_disp", Value::from(built.bcc_disp), &want["bcc_disp"]),
        ("draws left", Value::from(left), &want["left"]),
    ] {
        if &mine != theirs {
            out.push(format!("{name}: {what} is {mine}, Python's {theirs}"));
        }
    }
    out
}

#[test]
fn each_case_is_built_as_python_built_it() {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/tests/fixtures/builder_oracle.json");
    let cases: Vec<Value> = serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
    assert!(cases.len() > 60, "the oracle holds its cases");
    let differ: Vec<String> = cases.iter().flat_map(differences).collect();
    assert!(differ.is_empty(), "{} differences:\n{}", differ.len(), differ.join("\n"));
}

#[test]
fn no_bcc_is_ever_written_into_the_bytes() {
    let draws = Replay(Mutex::new((1..=8).map(|n| n * 0x0101_0101_0101_0101).collect()));
    let spec = MessageSpec {
        account: "ada@example.test".to_owned(),
        account_name: "Ada".to_owned(),
        to: vec!["lin@example.test".to_owned()],
        bcc: vec!["Noor <noor@studio.example.test>".to_owned(), "LIN@example.test".to_owned()],
        subject: "Fabric".to_owned(),
        text: "Hi".to_owned(),
        ..MessageSpec::default()
    };
    let built = build_message(&spec, false, Minting { now: Moment::from_micros(0), pid: 1, draws: &draws }).unwrap();
    let raw = String::from_utf8(built.raw).unwrap();
    assert!(!raw.to_lowercase().contains("bcc"), "{raw}");
    assert!(!raw.contains("noor@studio.example.test"));
    // The envelope names each person once, by the first spelling.
    assert_eq!(built.envelope, ["lin@example.test", "noor@studio.example.test"]);
    assert_eq!(built.bcc_disp, ["noor@studio.example.test", "LIN@example.test"]);
}

#[test]
fn a_reply_chains_the_ids_it_answers_and_a_draft_keeps_the_well_formed_ones() {
    assert_eq!(
        reference_chain(
            Some(" <c@example.test> "),
            Some("<a@example.test> junk <b@example.test>"),
            Some("<b@example.test>")
        ),
        ["<a@example.test>", "<b@example.test>", "<c@example.test>"]
    );
    assert_eq!(reference_chain(None, Some("<not an id>"), None), Vec::<String>::new());
    let refs: String = (0..60).map(|n| format!("<r{n}@example.test> ")).collect();
    let (irt, kept) = thread_ids(Some("Bo <a@example.test> <b@example.test>"), Some(&refs));
    assert_eq!(irt.as_deref(), Some("<a@example.test>"));
    assert_eq!(kept.len(), 50);
    assert_eq!((kept[0].as_str(), kept[1].as_str()), ("<r0@example.test>", "<r11@example.test>"));
    assert_eq!(thread_ids(Some("garbage"), None), (None, Vec::new()));
}
