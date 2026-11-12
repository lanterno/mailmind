//! A fictional mailbox, the same every time for the same seed. Every address is at example.test, and every name
//! and line of text is made up here, so a fixture can be shared, screenshotted and committed.
//!
//! The budgets are measured on the mailbox `build` makes with 100,000 messages: one inbox across three accounts,
//! the newest on 1 November 2026, the oldest about three years before.

use std::path::Path;

use mm_kernel::Refusal;
use mm_store::{DbKey, NewMessage, Store};

/// The key that opens a fixture. It guards only fictional mail; a real database's key comes from a key store.
pub const FIXTURE_KEY_HEX: &str = "f1c7f1c7f1c7f1c7f1c7f1c7f1c7f1c7f1c7f1c7f1c7f1c7f1c7f1c7f1c7f1c7";

/// The same key as bytes, as a shell's key store would hand it over.
pub const FIXTURE_KEY: [u8; 32] = {
    let mut key = [0xf1; 32];
    let mut i = 1;
    while i < 32 {
        key[i] = 0xc7;
        i += 2;
    }
    key
};

/// The fictional accounts the fixture's mail belongs to.
pub const ACCOUNTS: [&str; 3] = ["work@example.test", "home@example.test", "club@example.test"];

/// The folder every fixture message is in: one list holds them all, the hardest case for a view.
pub const FOLDER: &str = "INBOX";

/// The number of messages the budgets are measured on.
pub const BUDGET_MESSAGES: u32 = 100_000;

/// The seed the budgets are measured with.
pub const SEED: u64 = 7;

/// 2026-11-01 00:00 UTC: the newest message's date.
const NEWEST: i64 = 1_793_491_200;

/// Make the fixture database at `path`, which must not exist yet.
pub fn build(path: &Path, key: &DbKey, count: u32, seed: u64) -> Result<u32, Refusal> {
    if path.exists() {
        return Err(Refusal::invalid("a fixture is only made where nothing is yet"));
    }
    let mut store = Store::open(path, key)?;
    store.create_list_tables()?;
    store.add_messages(messages(count, seed))
}

/// The fixture's messages, newest first.
pub fn messages(count: u32, seed: u64) -> impl Iterator<Item = NewMessage> {
    let mut random = SplitMix(seed);
    let mut ts = NEWEST;
    let mut uids = [0i64; ACCOUNTS.len()];
    (0..count).map(move |n| {
        ts -= random.below(1_800) as i64 + 1;
        let account = random.below(ACCOUNTS.len() as u64) as usize;
        uids[account] += 1;
        let first = pick(&mut random, FIRST_NAMES);
        let last = pick(&mut random, LAST_NAMES);
        let place = pick(&mut random, PLACES);
        let subject = format!("{} {}", pick(&mut random, TOPICS), pick(&mut random, ENDINGS));
        let sentences = 2 + random.below(3);
        let snippet = (0..sentences).map(|_| pick(&mut random, SENTENCES)).collect::<Vec<_>>().join(" ");
        NewMessage {
            account: ACCOUNTS[account].to_owned(),
            folder: FOLDER.to_owned(),
            uid: uids[account],
            message_id: format!("<{n}.{seed}@mail.example.test>"),
            from_name: format!("{first} {last}"),
            from_addr: format!("{}.{}@{place}.example.test", first.to_lowercase(), last.to_lowercase()),
            subject,
            snippet,
            ts,
            seen: random.below(100) >= 15,
            flagged: random.below(100) < 3,
            has_attach: random.below(100) < 12,
        }
    })
}

/// SplitMix64: small, fast and the same on every platform.
struct SplitMix(u64);

impl SplitMix {
    fn next(&mut self) -> u64 {
        self.0 = self.0.wrapping_add(0x9e37_79b9_7f4a_7c15);
        let mut z = self.0;
        z = (z ^ (z >> 30)).wrapping_mul(0xbf58_476d_1ce4_e5b9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94d0_49bb_1331_11eb);
        z ^ (z >> 31)
    }

    fn below(&mut self, n: u64) -> u64 {
        self.next() % n
    }
}

fn pick<'a>(random: &mut SplitMix, from: &[&'a str]) -> &'a str {
    from[random.below(from.len() as u64) as usize]
}

const FIRST_NAMES: &[&str] = &[
    "Ada", "Bram", "Chidi", "Dana", "Elif", "Femi", "Greta", "Hiro", "Ines", "Jonas", "Kofi", "Lena", "Mateo", "Nell",
    "Oskar", "Priya", "Quinn", "Rosa", "Sami", "Tove", "Uma", "Viktor", "Wren", "Yara", "Zane", "Aiko", "Bea", "Cyril",
    "Dev", "Esme",
];

const LAST_NAMES: &[&str] = &[
    "Abara", "Berg", "Castell", "Dorsey", "Eklund", "Fane", "Gilroy", "Hale", "Ivers", "Juhl", "Kestrel", "Lund",
    "Marlow", "Nyberg", "Okafor", "Pell", "Quist", "Rowe", "Sato", "Thorne", "Ulric", "Vance", "Wilde", "Yoon",
];

const PLACES: &[&str] = &[
    "studio", "harbor", "orchard", "atlas", "meadow", "copper", "north", "summit", "river", "quarry", "field", "beacon",
];

const TOPICS: &[&str] = &[
    "Quarterly plan",
    "Invoice 4471",
    "Team lunch",
    "Design review",
    "Your order",
    "Trip to the coast",
    "Weekly notes",
    "Budget draft",
    "Book club",
    "Garden plot",
    "Flat viewing",
    "Release checklist",
    "Board minutes",
    "Workshop",
    "Concert tickets",
    "Training schedule",
    "Dentist",
    "Library loan",
    "Volunteer rota",
    "Recipe swap",
    "Bike repair",
    "School trip",
    "Photo album",
    "Server move",
];

const ENDINGS: &[&str] = &[
    "for Monday",
    "is ready",
    "needs a look",
    "moved to Thursday",
    "(updated)",
    "and next steps",
    "questions",
    "follow-up",
    "reminder",
    "draft 2",
    "confirmed",
    "this week",
];

const SENTENCES: &[&str] = &[
    "I have attached the latest version with the changes we talked about.",
    "Could you take a look before the end of the week?",
    "The room is booked from ten until noon.",
    "Thanks again for sorting this out so quickly.",
    "Let me know if the new time works for everyone.",
    "We are still waiting on two of the quotes.",
    "The numbers in the second table look off by a few percent.",
    "Bring a jacket, it gets cold by the water in the evening.",
    "I moved the meeting notes into the shared folder.",
    "Nothing urgent, just keeping you in the loop.",
    "The delivery should arrive between nine and one.",
    "Happy to help with the next round if you need it.",
];
