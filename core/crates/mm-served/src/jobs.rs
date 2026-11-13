//! `work <job> <JSON>`: one pass of a background job, as a worker would run it, given the arguments in the JSON list
//! (`serve_python.py` says what each does). A job the Rust server doesn't run yet says so.

use mm_server::App;
use serde_json::Value;

/// The protocol's answer to `work <argument>`: `ok`, or `failed <why>`, naming only a class, never a value.
pub fn work(app: &App, argument: &str) -> String {
    let (job, given) = argument.split_once(' ').unwrap_or((argument, "[]"));
    let Ok(Value::Array(args)) = serde_json::from_str::<Value>(given) else {
        return format!("failed {job}: the arguments aren't a JSON list");
    };
    let text = |i: usize| args.get(i).and_then(Value::as_str).unwrap_or_default();
    let done = match job {
        "sync" => app.ingest.sync_folder(text(0), text(1), false, None).map(|_| ()).map_err(|e| e.class()),
        _ => return format!("failed {job}: not on the Rust server yet"),
    };
    match done {
        Ok(()) => "ok".to_owned(),
        Err(class) => format!("failed {job}: {class}"),
    }
}
