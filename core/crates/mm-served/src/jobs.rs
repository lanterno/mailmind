//! `work <job> <JSON>`: one pass of a background job, as a worker would run it, given the arguments in the JSON list
//! (`serve_python.py` says what each does). Those this server has yet: a folder's sync, waking what is snoozed, the
//! minute's round of follow-ups, a pass of the change queue, an account's reconcile, a pass of the outbox and a round
//! of the drafts' pushes; and the assistant's triage and a run of Tidy. A job the Rust server doesn't run yet says so.

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
        "sync" => app.ingest.sync_folder(text(0), text(1), false, None).map(|_| ()).map_err(|e| e.class().to_owned()),
        "snoozes" => app.attention.snoozing.wake_due().map(|_| ()).map_err(|failure| failure.to_string()),
        "follow-ups" => app.attention.follow_ups.tick().map(|_| ()).map_err(|failure| failure.to_string()),
        "commands" => app.commands.drain().map_err(|fault| fault.class.to_owned()),
        "reconcile" => app.reconciler.auto_reconcile_one(text(0)).map_err(|fault| fault.class.to_owned()),
        // Compose's: a pass of the outbox, and a round of the drafts' pushes.
        "outbox" => app.compose.delivery.deliver().map_err(|fault| fault.class.to_owned()),
        "drafts" => app.compose.drafting.push_round().map(drop).map_err(|fault| fault.class.to_owned()),
        // The forget round's Mailbox part: keys with no copy left and finished changes, a month on.
        "forget" => app.forgetting.round().map(drop).map_err(|fault| fault.class.to_owned()),
        // The assistant's triage of what it hasn't summarized.
        // The assistant's triage of what it hasn't summarized, and a run of Tidy for someone who turned it on.
        "assess" => app.background.assess_round().map(drop).map_err(str::to_owned),
        "tidy" => app.background.tidy_round().map(drop).map_err(str::to_owned),
        _ => return format!("failed {job}: not on the Rust server yet"),
    };
    match done {
        Ok(()) => "ok".to_owned(),
        Err(why) => format!("failed {job}: {why}"),
    }
}
