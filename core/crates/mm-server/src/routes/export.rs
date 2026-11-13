//! What an export would hold, and as a download a snapshot of the database (`mailmind.http.routers.export`).
//!
//! A download goes in chunks, each read on a thread of the blocking pool once the one before it is taken, and no
//! thread held while a client is slow, so a large database never sits in memory; its folder goes once the last is
//! read or the client leaves, however that ends.

use std::io::Read;
use std::pin::Pin;
use std::sync::Arc;
use std::task::{Context, Poll};

use axum::Router;
use axum::body::Body;
use axum::extract::State;
use axum::http::{HeaderValue, Method, Response, StatusCode, Uri, header};
use axum::routing::get;
use mm_kernel::errors::{Failure, Fault};
use mm_store::export::Download;
use tokio::sync::mpsc;

use super::reading::run;
use crate::answer;
use crate::app::App;

/// How much of a download is read at a time.
pub const CHUNK: usize = 262_144;

pub(super) fn routes() -> Router<Arc<App>> {
    Router::new().route("/api/export/plan", get(plan)).route("/api/export", get(snapshot))
}

/// What an export would hold, by account and folder.
async fn plan(State(app): State<Arc<App>>, method: Method, uri: Uri) -> Response<Body> {
    run(&app, &method, &uri, |app| Ok(app.reading.exports.plan()?)).await
}

/// A snapshot of the database, encrypted with its key, as a file to save.
async fn snapshot(State(app): State<Arc<App>>, method: Method, uri: Uri) -> Response<Body> {
    let made = {
        let app = app.clone();
        tokio::task::spawn_blocking(move || app.reading.exports.snapshot()).await
    };
    match made.unwrap_or(Err(Fault::new("Panicked"))) {
        Ok(download) => sent(download),
        Err(fault) => answer::failure(&Failure::Fault(fault), method.as_str(), uri.path()),
    }
}

/// The chunks a download is read in, as they come.
struct Chunks(mpsc::Receiver<std::io::Result<Vec<u8>>>);

impl futures_core::Stream for Chunks {
    type Item = std::io::Result<Vec<u8>>;

    fn poll_next(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Option<Self::Item>> {
        self.0.poll_recv(cx)
    }
}

/// A download as the answer: its type, its length, and its name to save it under.
pub(super) fn sent(download: Download) -> Response<Body> {
    let (length, kind) = (download.size, download.content_type);
    let disposition = HeaderValue::from_str(&format!("attachment; filename={}", download.name));
    let (chunks, taken) = mpsc::channel(1);
    tokio::spawn(async move {
        let mut held = (download, None::<std::fs::File>);
        loop {
            let read = tokio::task::spawn_blocking(move || {
                let (download, file) = held;
                let read = match file {
                    Some(file) => Ok(file),
                    None => std::fs::File::open(&download.file),
                }
                .and_then(|mut file| {
                    let mut chunk = vec![0; CHUNK];
                    let n = fill(&mut file, &mut chunk)?;
                    chunk.truncate(n);
                    Ok((file, chunk))
                });
                (download, read)
            })
            .await;
            let Ok((download, read)) = read else { return };
            match read {
                Ok((_, chunk)) if chunk.is_empty() => return,
                Ok((file, chunk)) => {
                    if chunks.send(Ok(chunk)).await.is_err() {
                        return;
                    }
                    held = (download, Some(file));
                }
                Err(e) => {
                    let _ = chunks.send(Err(e)).await;
                    return;
                }
            }
        }
    });
    let mut answer = Response::new(Body::from_stream(Chunks(taken)));
    *answer.status_mut() = StatusCode::OK;
    let headers = answer.headers_mut();
    headers.insert(header::CONTENT_TYPE, HeaderValue::from_static(kind));
    headers.insert(header::CONTENT_LENGTH, HeaderValue::from(length));
    if let Ok(disposition) = disposition {
        headers.insert(header::CONTENT_DISPOSITION, disposition);
    }
    answer
}

/// As much of `chunk` as the file holds, read again until it is full or the file ends.
fn fill(file: &mut std::fs::File, chunk: &mut [u8]) -> std::io::Result<usize> {
    let mut read = 0;
    while read < chunk.len() {
        match file.read(&mut chunk[read..])? {
            0 => break,
            n => read += n,
        }
    }
    Ok(read)
}
