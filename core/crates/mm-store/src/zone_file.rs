//! The person's zone, as the C library finds it for Python's `time.localtime`: the file `TZ` names, or the rule it
//! writes out; with `TZ` unset, the system's own (`/etc/localtime`). A zone that can't be read is UTC's.

use std::path::{Path, PathBuf};
use std::sync::Arc;

use mm_kernel::seams::Outside;
use mm_kernel::zone::{Rule, Tzif, Utc, Zone};

/// Where the system keeps its zone files.
const ZONEINFO: &str = "/usr/share/zoneinfo";

fn read(path: &Path, outside: &dyn Outside) -> Option<Tzif> {
    outside.open(path).ok()?;
    Tzif::parse(&std::fs::read(path).ok()?)
}

/// The zone `tz` names, the value of `TZ`, None when it is unset.
pub fn person_zone(tz: Option<&str>, outside: &dyn Outside) -> Arc<dyn Zone> {
    let Some(tz) = tz else {
        return read(Path::new("/etc/localtime"), outside)
            .map_or_else(|| Arc::new(Utc) as Arc<dyn Zone>, |z| Arc::new(z));
    };
    let name = tz.strip_prefix(':').unwrap_or(tz);
    if name.is_empty() {
        return Arc::new(Utc);
    }
    // A name that climbs out of the zone files names none of them.
    let path = if name.starts_with('/') { PathBuf::from(name) } else { Path::new(ZONEINFO).join(name) };
    if !name.split('/').any(|part| part == "..")
        && let Some(zone) = read(&path, outside)
    {
        return Arc::new(zone);
    }
    match Rule::parse(name) {
        Some(rule) => Arc::new(rule),
        None => Arc::new(Utc),
    }
}
