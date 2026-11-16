//! Where the log goes as `mailmind-server` and the Mac app's host run (`mailmind.platform.logging`): every line on
//! standard error, for someone at a terminal, and once a data folder is named (`keep_in`), as one JSON object a line
//! to `mailmind.log` in it, which a Mac app opened from the Finder keeps when standard error goes nowhere.
//!
//! A line holds only what `mm_kernel::log` lets it: a literal message, the values of its allowed fields and the class
//! of what went wrong. A value holding `@` or `%40` is withheld whole, a control character in one is a space, and one
//! is cut at 300 characters. The file is readable by its owner only; at 1 MiB it moves to `mailmind.log.1`, the older
//! ones to `.2` and `.3`, and the oldest goes, as the Python app's `_OwnFile` keeps it. A line that can't be written
//! is lost, and that stops nothing.
//!
//! `MAILMIND_LOG_LEVEL=debug` (`Environment::log_debug`) shows debug lines at the terminal, and a panic's own words
//! there (`log_panics`); the file never holds either.
//!
//! A panic is logged by the thread it happened on and where in the code, never by its message, which can quote mail.

use std::fs::{File, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Mutex, MutexGuard, Once, PoisonError};
use std::time::{SystemTime, UNIX_EPOCH};

use mm_kernel::log::{self, Field, Level, Line, Sink};
use serde_json::{Map, Value};

/// The log file's name in the data folder.
pub const FILE: &str = "mailmind.log";
/// How large the file grows before it moves aside (`logging.MAX_BYTES`).
pub const MAX_BYTES: u64 = 1 << 20;
/// How many older files are kept: `.1`, the newest, to `.3` (`logging.BACKUPS`).
pub const BACKUPS: u32 = 3;
/// What shows in place of a value that may be a person's.
pub const WITHHELD: &str = "<withheld>";
/// The most of a value a line shows, in characters (`logging._MAX_TEXT`).
const MAX_TEXT: usize = 300;

/// What a line may say of `value`: withheld whole when it holds an address (`@`, or `%40` as a URL writes one), else
/// on one line and at most 300 characters.
pub fn shown(value: &str) -> String {
    if value.contains('@') || value.contains("%40") {
        return WITHHELD.to_owned();
    }
    value.chars().take(MAX_TEXT).map(|c| if c.is_control() { ' ' } else { c }).collect()
}

/// The line's message, each field it names put in where it says `%(name)s`.
pub fn message(line: &Line<'_>) -> String {
    let mut message = line.message.to_owned();
    for (field, value) in line.fields {
        message = message.replace(&format!("%({})s", field.name()), &shown(value));
    }
    message
}

/// `LEVEL [tag] message: Class`, as standard error shows a line.
pub fn terminal(line: &Line<'_>) -> String {
    let error = line.error.map(|class| format!(": {class}")).unwrap_or_default();
    format!("{} [{}] {}{error}", line.level.name(), line.tag, message(line))
}

/// `at` as `datetime.isoformat(timespec="milliseconds")` writes an aware UTC time: `2026-11-16T09:30:00.250+00:00`.
pub fn timestamp(at: SystemTime) -> String {
    let since = at.duration_since(UNIX_EPOCH).unwrap_or_default();
    let seconds = i64::try_from(since.as_secs()).unwrap_or(i64::MAX);
    let (year, month, day) = mm_kernel::when::civil(seconds.div_euclid(86_400));
    let of_day = seconds.rem_euclid(86_400);
    format!(
        "{year:04}-{month:02}-{day:02}T{:02}:{:02}:{:02}.{:03}+00:00",
        of_day / 3600,
        of_day % 3600 / 60,
        of_day % 60,
        since.subsec_millis()
    )
}

/// The line as the file holds it (`JsonFormatter`): when, how grave, whose, the message, the class of what went wrong,
/// then each field by its name.
pub fn json(line: &Line<'_>, at: SystemTime) -> String {
    let mut entry = Map::new();
    entry.insert("ts".into(), Value::from(timestamp(at)));
    entry.insert("level".into(), Value::from(line.level.name()));
    entry.insert("logger".into(), Value::from(line.tag));
    entry.insert("msg".into(), Value::from(message(line)));
    if let Some(class) = line.error {
        entry.insert("error".into(), Value::from(class));
    }
    for (field, value) in line.fields {
        entry.entry(field.name()).or_insert_with(|| Value::from(shown(value)));
    }
    Value::Object(entry).to_string()
}

/// `mailmind.log` and the older files beside it, readable by their owner only.
#[derive(Debug)]
pub struct LogFile {
    path: PathBuf,
    open: Option<(File, u64)>,
}

impl LogFile {
    /// The file at `path`, opened as the first line comes.
    pub fn at(path: PathBuf) -> LogFile {
        LogFile { path, open: None }
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    fn older(&self, n: u32) -> PathBuf {
        let mut name = self.path.clone().into_os_string();
        name.push(format!(".{n}"));
        PathBuf::from(name)
    }

    /// Open it to add to it, made readable by its owner only, a file that was there already brought into line.
    fn opened(&self) -> std::io::Result<(File, u64)> {
        let mut options = OpenOptions::new();
        options.append(true).create(true);
        #[cfg(unix)]
        std::os::unix::fs::OpenOptionsExt::mode(&mut options, 0o600);
        let file = options.open(&self.path)?;
        #[cfg(unix)]
        file.set_permissions(std::os::unix::fs::PermissionsExt::from_mode(0o600))?;
        let size = file.metadata()?.len();
        Ok((file, size))
    }

    /// Move the file aside as `.1`, each older one up a number, and the oldest out.
    fn rotate(&mut self) {
        self.open = None;
        let _ = std::fs::remove_file(self.older(BACKUPS));
        for n in (1..BACKUPS).rev() {
            let _ = std::fs::rename(self.older(n), self.older(n + 1));
        }
        let _ = std::fs::rename(&self.path, self.older(1));
    }

    /// Add `text` as a line, moving the file aside first when the line would take it to `MAX_BYTES`.
    pub fn write(&mut self, text: &str) {
        let bytes = text.len() as u64 + 1;
        if self.open.is_none() {
            self.open = self.opened().ok();
        }
        if self.open.as_ref().is_some_and(|(_, size)| *size > 0 && size + bytes >= MAX_BYTES) {
            self.rotate();
            self.open = self.opened().ok();
        }
        let Some((file, size)) = &mut self.open else { return };
        let mut whole = String::with_capacity(text.len() + 1);
        whole.push_str(text);
        whole.push('\n');
        match file.write_all(whole.as_bytes()) {
            Ok(()) => *size += bytes,
            // Opened again with the next line: the folder may be back by then.
            Err(_) => self.open = None,
        }
    }
}

/// Where this process's lines go: standard error, and the file once a data folder is named.
#[derive(Debug)]
struct Logging {
    debug: AtomicBool,
    file: Mutex<Option<LogFile>>,
}

static LOGGING: Logging = Logging { debug: AtomicBool::new(false), file: Mutex::new(None) };

impl Logging {
    fn file(&self) -> MutexGuard<'_, Option<LogFile>> {
        // A line is written whole or not at all, so a lock a panicking writer let go is still sound.
        self.file.lock().unwrap_or_else(PoisonError::into_inner)
    }
}

/// The sink the process installs: the one `Logging`.
#[derive(Debug)]
struct Installed;

impl Sink for Installed {
    fn write(&self, line: &Line<'_>) {
        if line.level > Level::Debug || LOGGING.debug.load(Ordering::Relaxed) {
            let _ = writeln!(std::io::stderr().lock(), "{}", terminal(line));
        }
        if line.level > Level::Debug
            && let Some(file) = LOGGING.file().as_mut()
        {
            file.write(&json(line, SystemTime::now()));
        }
    }
}

/// Send every line to standard error from now on, debug lines too when `debug`.
pub fn to_terminal(debug: bool) {
    LOGGING.debug.store(debug, Ordering::Relaxed);
    log::install(Box::new(Installed));
}

/// `to_terminal`, and every line but debug's to `mailmind.log` in `folder` too: what `mailmind-server` and the Mac
/// app's host do once they know their data folder. Named again, the file moves to the folder named last.
pub fn keep_in(folder: &Path, debug: bool) {
    to_terminal(debug);
    let path = folder.join(FILE);
    let mut file = LOGGING.file();
    if file.as_ref().is_none_or(|kept| kept.path() != path) {
        *file = Some(LogFile::at(path));
    }
}
/// Where a panic happened, as its line names it: a path in the core as it is, and one in a dependency from its
/// crate's folder on, so no line names the folders of the machine that built the app.
pub fn place(file: &str, line: u32) -> String {
    let short = match file.find("/registry/src/") {
        Some(at) => {
            let rest = &file[at + "/registry/src/".len()..];
            rest.split_once('/').map_or(rest, |(_, crate_on)| crate_on)
        }
        None if file.starts_with('/') && !file.starts_with("/rustc/") => file.rsplit('/').next().unwrap_or(file),
        None => file,
    };
    format!("{short}:{line}")
}

static HOOKED: Once = Once::new();

/// From now on a panic is logged by its thread's name and where it happened, as an error of the class `Panic`,
/// never by its message. Under `MAILMIND_LOG_LEVEL=debug` the panic's own words follow at the terminal as Rust
/// writes them. Installed once a process; asked again it changes nothing.
pub fn log_panics() {
    HOOKED.call_once(|| {
        let earlier = std::panic::take_hook();
        std::panic::set_hook(Box::new(move |info| {
            let current = std::thread::current();
            let thread = current.name().unwrap_or("unnamed");
            let at = info.location().map(|l| place(l.file(), l.line())).unwrap_or_default();
            log::error(
                "panic",
                "a thread panicked: %(what)s at %(path)s",
                &[(Field::What, thread), (Field::Path, &at)],
                Some("Panic"),
            );
            if LOGGING.debug.load(Ordering::Relaxed) {
                earlier(info);
            }
        }));
    });
}
