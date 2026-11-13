//! The live event stream's hub: what a tab's stream sends, how one that reconnects resumes, and when it is told to
//! fetch afresh instead.

#![allow(clippy::unwrap_used)]

use std::pin::Pin;
use std::sync::Arc;
use std::task::{Context, Poll, Waker};

use futures_core::Stream;
use mm_kernel::events::{Events, Topic};
use mm_server::events::{Hub, MAX_LISTENERS, RING, TabStream};
use serde_json::json;

/// What the stream has ready now, without waiting: None when it would wait.
fn ready(stream: &mut TabStream) -> Option<String> {
    match Pin::new(stream).poll_next(&mut Context::from_waker(Waker::noop())) {
        Poll::Ready(Some(Ok(frames))) => Some(frames),
        Poll::Ready(None) => panic!("the stream ended"),
        Poll::Pending => None,
    }
}

/// The run's part of an id, from a stream's opening.
fn run_of(opening: &str) -> String {
    let id = opening.lines().find_map(|l| l.strip_prefix("id: ")).unwrap();
    id.rsplit_once('-').unwrap().0.to_owned()
}

fn open(hub: &Arc<Hub>, last_event_id: Option<&str>) -> TabStream {
    TabStream::new(hub.listen(last_event_id).unwrap(), "b1")
}

fn runtime() -> tokio::runtime::Runtime {
    tokio::runtime::Builder::new_current_thread().enable_time().build().unwrap()
}

#[test]
fn a_stream_opens_with_its_position_and_the_build_then_waits() {
    let runtime = runtime();
    let _entered = runtime.enter();
    let hub = Arc::new(Hub::new().unwrap());
    let mut tab = open(&hub, None);
    let first = ready(&mut tab).unwrap();
    let run = run_of(&first);
    assert_eq!(
        first,
        format!("retry: 3000\nid: {run}-0\n: connected\n\nevent: hello\ndata: {{\"build\": \"b1\"}}\n\n")
    );
    assert_eq!(ready(&mut tab), None);
}

#[test]
fn every_listener_hears_an_event_numbered_from_one() {
    let runtime = runtime();
    let _entered = runtime.enter();
    let hub = Arc::new(Hub::new().unwrap());
    let (mut one, mut two) = (open(&hub, None), open(&hub, None));
    let run = run_of(&ready(&mut one).unwrap());
    ready(&mut two).unwrap();
    hub.publish(Topic::Mute, json!({"account": "ada@example.test"}));
    hub.publish(Topic::Outbox, json!(["not an object"]));
    let said = format!(
        "id: {run}-1\nevent: mute\ndata: {{\"account\": \"ada@example.test\"}}\n\nid: {run}-2\nevent: outbox\ndata: {{}}\n\n"
    );
    assert_eq!(ready(&mut one).unwrap(), said);
    assert_eq!(ready(&mut two).unwrap(), said);
    assert_eq!(ready(&mut one), None);
}

#[test]
fn a_tab_that_reconnects_resumes_right_after_the_last_event_it_saw() {
    let runtime = runtime();
    let _entered = runtime.enter();
    let hub = Arc::new(Hub::new().unwrap());
    hub.publish(Topic::SyncDone, json!({}));
    let mut tab = open(&hub, None);
    let run = run_of(&ready(&mut tab).unwrap());
    drop(tab);
    hub.publish(Topic::NewMail, json!({}));
    hub.publish(Topic::Mute, json!({}));
    let mut back = open(&hub, Some(&format!(" {run}-1 ")));
    ready(&mut back).unwrap();
    let frames = ready(&mut back).unwrap();
    assert_eq!(frames.matches("event: ").collect::<Vec<_>>().len(), 2);
    assert!(frames.starts_with(&format!("id: {run}-2\nevent: new-mail\n")));
}

#[test]
fn a_tab_from_before_a_restart_or_too_far_behind_is_told_to_fetch_afresh() {
    let runtime = runtime();
    let _entered = runtime.enter();
    let hub = Arc::new(Hub::new().unwrap());
    let mut earlier = open(&hub, Some("0badf00d-3"));
    let run = run_of(&ready(&mut earlier).unwrap());
    assert_eq!(ready(&mut earlier).unwrap(), format!("id: {run}-0\nevent: resync\ndata: {{}}\n\n"));
    assert_eq!(ready(&mut earlier), None);

    let mut behind = open(&hub, None);
    ready(&mut behind).unwrap();
    for _ in 0..RING + 2 {
        hub.publish(Topic::FlagsChanged, json!({}));
    }
    let last = RING + 2;
    assert_eq!(ready(&mut behind).unwrap(), format!("id: {run}-{last}\nevent: resync\ndata: {{}}\n\n"));
    for gone in ["", "x", &format!("{run}-"), &format!("{run}-1"), &format!("{run}-999999")] {
        let mut tab = open(&hub, Some(gone));
        ready(&mut tab).unwrap();
        let next = ready(&mut tab);
        assert!(gone.is_empty() == next.is_none(), "{gone:?}: {next:?}");
    }
}

#[test]
fn no_more_than_eight_streams_are_open_at_once() {
    let hub = Arc::new(Hub::new().unwrap());
    let mut open: Vec<_> = (0..MAX_LISTENERS).map(|_| hub.listen(None).unwrap()).collect();
    assert!(hub.listen(None).is_none());
    assert_eq!(hub.listening(), MAX_LISTENERS);
    open.pop();
    assert!(hub.listen(None).is_some());
}
