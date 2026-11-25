//! Nothing hostile reaches IMAP (test-strategy.md section 6): folder names and values, as a sender or a server could
//! make them, through the only ways onto a command line, `mailbox_arg`, `imap_quote` and `gmail_labels`. Each is fed
//! a few hundred thousand strings drawn from what ends a string or a line (quotes, backslashes, CR, LF, NUL, braces
//! that announce a literal, IMAP's wildcards and specials, and text outside ASCII), and every one it gives back is one
//! token that reads back as what was asked, on one line.
//!
//! The strings come from a fixed seed, so a failure is the same on every run and names its string.

use mm_imap::{gmail_labels, imap_quote, mailbox_arg};

/// What ends a quoted string, a line or a command, or bends one, and a little of everything else.
const RISKY: &[&str] = &[
    "\"", "\\", "\r", "\n", "\r\n", "\0", "{", "}", "{5}", "%", "*", "(", ")", "[", "]", " ", "a", "Z", "é", "日",
    "\u{2028}", "~",
];

/// xorshift64*: enough to spread strings over `RISKY`, the same on every run.
struct Draw(u64);

impl Draw {
    fn next(&mut self) -> u64 {
        self.0 ^= self.0 >> 12;
        self.0 ^= self.0 << 25;
        self.0 ^= self.0 >> 27;
        self.0.wrapping_mul(0x2545_F491_4F6C_DD1D)
    }

    fn below(&mut self, n: usize) -> usize {
        (self.next() % n as u64) as usize
    }

    fn string(&mut self) -> String {
        (0..self.below(9)).map(|_| RISKY[self.below(RISKY.len())]).collect()
    }
}

/// Every string of up to three pieces of `RISKY`, then 200,000 drawn ones of up to eight.
fn strings() -> impl Iterator<Item = String> {
    let n = RISKY.len();
    let short = (0..n * n * n + n * n + n + 1).map(move |mut i| {
        let mut out = String::new();
        while i > 0 {
            i -= 1;
            out.push_str(RISKY[i % n]);
            i /= n;
        }
        out
    });
    let mut draw = Draw(0x6d61_696c_6d69_6e64);
    short.chain((0..200_000).map(move |_| draw.string()))
}

/// The body of a quoted string at the start of `token`, unescaped, and what follows its closing quote; None when it
/// doesn't start with a quote or never closes.
fn read_quoted(token: &str) -> Option<(String, &str)> {
    let mut chars = token.strip_prefix('"')?.char_indices();
    let mut body = String::new();
    while let Some((at, c)) = chars.next() {
        match c {
            '"' => return Some((body, &token[at + 2..])),
            '\\' => body.push(chars.next()?.1),
            c => body.push(c),
        }
    }
    None
}

fn on_one_line(token: &str) -> bool {
    !token.contains(['\r', '\n', '\0'])
}

#[test]
fn a_value_goes_as_one_quoted_string_that_reads_back_as_itself_or_not_at_all() {
    let mut refused = 0;
    for value in strings() {
        match imap_quote(&value) {
            None => {
                assert!(value.contains(['\r', '\n', '\0']), "{value:?} was refused, but a quoted string can carry it");
                refused += 1;
            }
            Some(token) => {
                assert!(on_one_line(&token), "{value:?} went as {token:?}");
                assert_eq!(read_quoted(&token), Some((value.clone(), "")), "{value:?} went as {token:?}");
            }
        }
    }
    assert!(refused > 1000, "the strings reach the refusals");
}

#[test]
fn a_folder_goes_wrapped_as_list_gave_it_and_ends_only_at_its_own_closing_quote() {
    let (mut sent, mut refused) = (0, 0);
    for name in strings() {
        let Some(token) = mailbox_arg(&name) else {
            refused += 1;
            continue;
        };
        sent += 1;
        assert!(on_one_line(&token), "{name:?} went as {token:?}");
        assert_eq!(token, format!("\"{name}\""), "a folder name is only wrapped");
        let (_, rest) = read_quoted(&token).unwrap_or_else(|| panic!("{name:?} went as {token:?}, which never closes"));
        assert_eq!(rest, "", "{name:?} went as {token:?}, whose string ends early");
    }
    assert!(sent > 1000 && refused > 1000, "the strings reach both: {sent} sent, {refused} refused");
}

#[test]
fn gmail_labels_go_as_one_list_of_tokens_each_reading_back_as_its_label() {
    let mut draw = Draw(0x6c61_6265_6c73_2121);
    let mut sent = 0;
    for _ in 0..50_000 {
        let labels: Vec<String> = (0..1 + draw.below(4)).map(|_| draw.string()).collect();
        let asked: Vec<&str> = labels.iter().map(String::as_str).collect();
        let Some(list) = gmail_labels(&asked) else { continue };
        sent += 1;
        assert!(on_one_line(&list), "{asked:?} went as {list:?}");
        let mut rest = list.strip_prefix('(').and_then(|l| l.strip_suffix(')')).expect("a parenthesized list");
        for label in &asked {
            rest = rest.trim_start_matches(' ');
            if let Some((body, after)) = read_quoted(rest) {
                assert_eq!(&body, label, "{asked:?} went as {list:?}");
                rest = after;
            } else {
                let (atom, after) = rest.split_once(' ').unwrap_or((rest, ""));
                assert_eq!(&atom, label, "{asked:?} went as {list:?}: a bare label must be the label itself");
                assert!(atom.starts_with('\\'), "only a system label goes bare: {list:?}");
                rest = after;
            }
        }
        assert_eq!(rest.trim(), "", "{asked:?} went as {list:?}, with more than its labels");
    }
    assert!(sent > 1000, "the labels reach a list: {sent}");
}
