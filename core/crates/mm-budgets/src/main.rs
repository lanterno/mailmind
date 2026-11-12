//! `mm-budgets`: build the 100,000-message fixture, measure the core on it, and hold each number to its limit in
//! the repository's `budgets.toml`. It exits non-zero when one is over. Run it in release: `just budgets`.
//!
//! Memory is counted by wrapping the allocator, so it is the core's own heap plus what SQLite holds, not the
//! process's: code pages and the platform's own libraries are measured by each shell.

#![allow(unsafe_code)] // the counting allocator
#![allow(clippy::print_stdout)]

use std::alloc::{GlobalAlloc, Layout, System};
use std::path::Path;
use std::process::ExitCode;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Instant;

use mm_app::{DbKey, ListQuery, PAGE, Refusal, Session, SessionConfig, ViewBatch, ViewObserver};
use mm_fixtures::{ACCOUNTS, BUDGET_MESSAGES, FIXTURE_KEY_HEX, FOLDER, SEED};
use mm_store::Store;

struct Counting;

static LIVE: AtomicUsize = AtomicUsize::new(0);

unsafe impl GlobalAlloc for Counting {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        // SAFETY: the layout is passed through unchanged to the system allocator.
        let p = unsafe { System.alloc(layout) };
        if !p.is_null() {
            LIVE.fetch_add(layout.size(), Ordering::Relaxed);
        }
        p
    }

    unsafe fn alloc_zeroed(&self, layout: Layout) -> *mut u8 {
        // SAFETY: as above.
        let p = unsafe { System.alloc_zeroed(layout) };
        if !p.is_null() {
            LIVE.fetch_add(layout.size(), Ordering::Relaxed);
        }
        p
    }

    unsafe fn dealloc(&self, ptr: *mut u8, layout: Layout) {
        // SAFETY: the caller hands back a pointer this allocator gave, with its layout.
        unsafe { System.dealloc(ptr, layout) };
        LIVE.fetch_sub(layout.size(), Ordering::Relaxed);
    }

    unsafe fn realloc(&self, ptr: *mut u8, layout: Layout, new_size: usize) -> *mut u8 {
        // SAFETY: as for dealloc; the system allocator keeps the old block when it fails.
        let p = unsafe { System.realloc(ptr, layout, new_size) };
        if !p.is_null() {
            LIVE.fetch_add(new_size, Ordering::Relaxed);
            LIVE.fetch_sub(layout.size(), Ordering::Relaxed);
        }
        p
    }
}

#[global_allocator]
static ALLOCATOR: Counting = Counting;

/// Rows a phone shows at once, roughly; a desktop shows about twice as many.
const VISIBLE: u32 = 20;

#[derive(Default)]
struct Seen {
    batches: Mutex<Vec<usize>>,
    refused: Mutex<Option<Refusal>>,
}

impl ViewObserver for Seen {
    fn on_batch(&self, batch: ViewBatch) {
        let rows = batch.ranges.iter().map(|r| r.rows.len()).sum();
        self.batches.lock().unwrap_or_else(|p| p.into_inner()).push(rows);
    }

    fn on_refusal(&self, refusal: Refusal) {
        *self.refused.lock().unwrap_or_else(|p| p.into_inner()) = Some(refusal);
    }
}

fn main() -> ExitCode {
    match run() {
        Ok(true) => ExitCode::SUCCESS,
        Ok(false) => ExitCode::FAILURE,
        Err(problem) => {
            eprintln!("mm-budgets: {problem}");
            ExitCode::FAILURE
        }
    }
}

fn run() -> Result<bool, String> {
    let limits = limits()?;
    let dir = tempfile::tempdir().map_err(|e| e.to_string())?;
    let path = dir.path().join("budgets.db");
    let started = Instant::now();
    mm_fixtures::build(&path, &key()?, BUDGET_MESSAGES, SEED).map_err(|r| r.to_string())?;
    println!("fixture: {BUDGET_MESSAGES} fictional messages in {:.1} s\n", started.elapsed().as_secs_f64());

    let mut opens = Vec::new();
    for _ in 0..7 {
        let started = Instant::now();
        let (session, list, seen) = open(&path)?;
        list.set_visible(0, VISIBLE);
        opens.push(ms(started));
        check(&seen, 1)?;
        drop((list, session));
    }

    let before = LIVE.load(Ordering::Relaxed);
    let (session, list, seen) = open(&path)?;
    list.set_visible(0, VISIBLE);
    let heap = LIVE.load(Ordering::Relaxed).saturating_sub(before);
    let idle_mb = (heap as f64 + Store::sqlite_memory_used() as f64) / (1024.0 * 1024.0);

    let mut scroll = Vec::new();
    for step in 1..=1000 {
        let started = Instant::now();
        list.set_visible(step * PAGE, VISIBLE);
        scroll.push(ms(started));
    }
    check(&seen, 1001)?;

    let mut random = 0x5eed_u64;
    let mut jumps = Vec::new();
    for _ in 0..1000 {
        random = random.wrapping_mul(6_364_136_223_846_793_005).wrapping_add(1_442_695_040_888_963_407);
        let first = u32::try_from((random >> 33) % u64::from(list.total())).unwrap_or(0);
        let started = Instant::now();
        list.set_visible(first, VISIBLE);
        jumps.push(ms(started));
    }
    drop((list, session));

    let measured = [
        ("open_to_first_rows_ms", median(&mut opens)),
        ("idle_memory_mb", idle_mb),
        ("scroll_page_p95_ms", p95(&mut scroll)),
        ("jump_p95_ms", p95(&mut jumps)),
    ];
    println!("{:<26} {:>8} {:>10}", "core budget", "limit", "measured");
    let mut within = true;
    for (name, value) in measured {
        let limit = limits.get(name).and_then(|b| b.get("limit")).and_then(toml::Value::as_float);
        let verdict = match limit {
            Some(limit) if value <= limit => format!("{limit:>8} {value:>10.2}"),
            Some(limit) => {
                within = false;
                format!("{limit:>8} {value:>10.2}  OVER")
            }
            None => {
                within = false;
                format!("{:>8} {value:>10.2}  no limit in budgets.toml", "-")
            }
        };
        println!("{name:<26} {verdict}");
    }
    Ok(within)
}

fn open(path: &Path) -> Result<(Session, Arc<mm_app::ListView>, Arc<Seen>), String> {
    let session = Session::open(SessionConfig { database: path.to_owned(), key: key()?, workers: 0 })
        .map_err(|r| r.to_string())?;
    let seen = Arc::new(Seen::default());
    let query = ListQuery { accounts: ACCOUNTS.iter().map(|a| (*a).to_owned()).collect(), folder: FOLDER.to_owned() };
    let list = session.open_list(query, seen.clone()).map_err(|r| r.to_string())?;
    Ok((session, list, seen))
}

fn check(seen: &Seen, batches: usize) -> Result<(), String> {
    if let Some(refusal) = seen.refused.lock().unwrap_or_else(|p| p.into_inner()).take() {
        return Err(refusal.to_string());
    }
    let got = seen.batches.lock().unwrap_or_else(|p| p.into_inner()).len();
    if got != batches {
        return Err(format!("expected {batches} batches, the view sent {got}"));
    }
    Ok(())
}

fn key() -> Result<DbKey, String> {
    DbKey::from_hex(FIXTURE_KEY_HEX).map_err(|r| r.to_string())
}

fn limits() -> Result<toml::Table, String> {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/../../../budgets.toml");
    let text = std::fs::read_to_string(path).map_err(|e| format!("budgets.toml: {e}"))?;
    let all: toml::Table = text.parse().map_err(|e| format!("budgets.toml: {e}"))?;
    match all.get("core") {
        Some(toml::Value::Table(core)) => Ok(core.clone()),
        _ => Err("budgets.toml has no [core] budgets".into()),
    }
}

fn ms(started: Instant) -> f64 {
    started.elapsed().as_secs_f64() * 1000.0
}

fn median(values: &mut [f64]) -> f64 {
    values.sort_by(f64::total_cmp);
    values[values.len() / 2]
}

fn p95(values: &mut [f64]) -> f64 {
    values.sort_by(f64::total_cmp);
    let at = (values.len() * 95).div_ceil(100).saturating_sub(1);
    values[at]
}
