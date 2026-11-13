//! The search box read into words, as Python's `shlex.split` read it: "quoted phrases" stay one word.

/// The words `shlex.split(text)` gives, or None where it raised ValueError: a quote left open, or a backslash with
/// nothing after it.
///
/// POSIX rules with no comments: words part at a space, a tab, CR or LF and nothing else, so a vertical tab or a
/// no-break space stays inside a word. A backslash outside quotes keeps the next character as it is. Inside double
/// quotes it does that only for a double quote or another backslash, and stays itself before anything else. Single
/// quotes keep everything. `""` is a word of its own, an empty one.
pub(crate) fn split(text: &str) -> Option<Vec<String>> {
    let mut words = Vec::new();
    let mut word = String::new();
    let mut state = State::Between;
    for c in text.chars() {
        state = match state {
            State::Between if parts_words(c) => State::Between,
            State::Between | State::Word if c == '\\' => State::Escaped(After::Word),
            State::Between | State::Word if c == '"' || c == '\'' => State::Quoted(c),
            State::Between => {
                word.push(c);
                State::Word
            }
            // A word is never empty here unless it was quoted, and Python gives a quoted empty word too.
            State::Word if parts_words(c) => {
                words.push(std::mem::take(&mut word));
                State::Between
            }
            State::Word => {
                word.push(c);
                State::Word
            }
            State::Quoted(quote) if c == quote => State::Word,
            State::Quoted('"') if c == '\\' => State::Escaped(After::Quoted('"')),
            State::Quoted(quote) => {
                word.push(c);
                State::Quoted(quote)
            }
            State::Escaped(after) => {
                if let After::Quoted(quote) = after
                    && c != '\\'
                    && c != quote
                {
                    word.push('\\');
                }
                word.push(c);
                match after {
                    After::Word => State::Word,
                    After::Quoted(quote) => State::Quoted(quote),
                }
            }
        };
    }
    match state {
        State::Between => {}
        State::Word => words.push(word),
        State::Quoted(_) | State::Escaped(_) => return None,
    }
    Some(words)
}

#[derive(Clone, Copy)]
enum State {
    Between,
    Word,
    Quoted(char),
    Escaped(After),
}

/// Where a backslash's character goes back to.
#[derive(Clone, Copy)]
enum After {
    Word,
    Quoted(char),
}

/// shlex's whitespace, which is narrower than Python's.
fn parts_words(c: char) -> bool {
    matches!(c, ' ' | '\t' | '\r' | '\n')
}
