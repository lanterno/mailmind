//! The event stream's text (`/api/events`, server-sent events) read back into events, as a browser's EventSource
//! reads it: a blank line ends an event, `id`, `event` and `data` lines say what it is, and a line that starts with a
//! colon is a comment (the stream's opening and its keepalives), which no one hears.

/// One event of the stream: its id (empty for `hello`, which has none), its name (a topic, `hello` or `resync`) and
/// its data, the JSON text it carries.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Event {
    pub id: String,
    pub name: String,
    pub data: String,
}

/// The stream's bytes as they come, cut into events.
#[derive(Debug, Default)]
pub struct Frames {
    /// What has come since the last blank line.
    pending: Vec<u8>,
}

impl Frames {
    /// The events `chunk` completes. A chunk may end mid-event, or mid-character: what is left waits for the next.
    pub fn take(&mut self, chunk: &[u8]) -> Vec<Event> {
        self.pending.extend_from_slice(chunk);
        let mut events = Vec::new();
        while let Some(end) = self.pending.windows(2).position(|pair| pair == b"\n\n") {
            let block: Vec<u8> = self.pending.drain(..end + 2).collect();
            events.extend(event(&String::from_utf8_lossy(&block)));
        }
        events
    }
}

/// The event one block says, or None for a block of comments, a `retry` or an `id` alone.
fn event(block: &str) -> Option<Event> {
    let (mut id, mut name, mut data, mut said) = (String::new(), String::new(), Vec::new(), false);
    for line in block.lines() {
        if line.is_empty() || line.starts_with(':') {
            continue;
        }
        let (field, value) = line.split_once(':').unwrap_or((line, ""));
        let value = value.strip_prefix(' ').unwrap_or(value);
        match field {
            "id" => id = value.to_owned(),
            "event" => {
                name = value.to_owned();
                said = true;
            }
            "data" => {
                data.push(value);
                said = true;
            }
            _ => {}
        }
    }
    let name = if name.is_empty() { "message".to_owned() } else { name };
    said.then(|| Event { id, name, data: data.join("\n") })
}
