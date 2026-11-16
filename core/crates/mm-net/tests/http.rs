//! One HTTP exchange's limits against peers on loopback that send what no well-behaved server would
//! (`tests/contract/test_chat_limits_contract.py`): an attempt's time limit is a deadline for all of it, however the
//! peer trickles its answer in, and an answer runs to 32 MiB at most.

use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};

use mm_net::Lost;
use mm_net::http::{self, Endpoint};

const HEAD: &str = "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n";

/// Seconds a peer trickles at most, so a deadline missed fails its test rather than hanging it.
const GIVES_UP: Duration = Duration::from_secs(10);

/// What a peer does with a connection once it has read the request.
type Answering = Arc<dyn Fn(&mut TcpStream, &AtomicBool) + Send + Sync>;

/// A loopback server that reads each request and answers it with `answering`, then closes the connection; it stops
/// when dropped.
struct Peer {
    url: String,
    stop: Arc<AtomicBool>,
}

impl Peer {
    fn new(answering: Answering) -> Peer {
        let listener = TcpListener::bind("127.0.0.1:0").expect("a loopback port");
        let url = format!("http://127.0.0.1:{}/v1", listener.local_addr().expect("its address").port());
        let stop = Arc::new(AtomicBool::new(false));
        let stopping = stop.clone();
        std::thread::spawn(move || {
            for conn in listener.incoming() {
                let Ok(mut conn) = conn else { return };
                let (answering, stop) = (answering.clone(), stopping.clone());
                std::thread::spawn(move || {
                    let mut asked = Vec::new();
                    let mut chunk = [0u8; 4096];
                    while !asked.windows(4).any(|w| w == b"\r\n\r\n") {
                        match conn.read(&mut chunk) {
                            Ok(0) | Err(_) => return,
                            Ok(n) => asked.extend_from_slice(&chunk[..n]),
                        }
                    }
                    answering(&mut conn, &stop);
                });
            }
        });
        Peer { url, stop }
    }
}

impl Drop for Peer {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::SeqCst);
    }
}

/// Send `head`, then `piece` every fifth of a second until stopped: never idle a second, never done in time.
fn trickling(head: &str, piece: &'static [u8]) -> Answering {
    let head = head.to_owned();
    Arc::new(move |conn: &mut TcpStream, stop: &AtomicBool| {
        if conn.write_all(head.as_bytes()).is_err() {
            return;
        }
        let ends = Instant::now() + GIVES_UP;
        while !stop.load(Ordering::SeqCst) && Instant::now() < ends {
            if conn.write_all(piece).is_err() {
                return;
            }
            std::thread::sleep(Duration::from_millis(200));
        }
    })
}

/// Send each of `parts` in turn.
fn sending(parts: Vec<Vec<u8>>) -> Answering {
    Arc::new(move |conn: &mut TcpStream, _: &AtomicBool| {
        for part in &parts {
            let _ = conn.write_all(part);
            std::thread::sleep(Duration::from_millis(20));
        }
    })
}

/// `GET /v1/models` of `peer`, held to `limit`: what came of it, and how long it took.
fn asked(peer: &Peer, limit: Duration) -> (Result<http::Response, Lost>, Duration) {
    let endpoint = Endpoint::parse(&peer.url).expect("a loopback URL");
    let request = http::request("GET", "/v1/models", &endpoint.host_header(), &[("Accept", "application/json")], None);
    let began = Instant::now();
    let answered = endpoint.send(&request, limit);
    (answered, began.elapsed())
}

#[test]
fn an_answer_trickled_in_is_let_go_at_the_deadline() {
    // An answer that ends its connection (HTTP/1.0, `Connection: close`, or no length) reads to the end: the
    // deadline still ends it, and what came by then is no answer.
    let ending = [
        "HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n\r\n".to_owned(),
        format!("{HEAD}Connection: close\r\n\r\n"),
        format!("{HEAD}\r\n"),
    ];
    let mut peers = vec![
        ("body", trickling(&format!("{HEAD}Content-Length: 100000\r\n\r\n"), b" ")),
        ("head", trickling(HEAD, b"X-Slow: 1\r\n")),
    ];
    peers.extend(ending.iter().map(|head| ("ending", trickling(head, b"x"))));
    for (what, answering) in peers {
        let peer = Peer::new(answering);
        let (answered, took) = asked(&peer, Duration::from_secs(1));
        assert_eq!(answered, Err(Lost("Timeout")), "{what}");
        assert!(took > Duration::from_millis(900) && took < Duration::from_millis(2500), "{what}: {took:?}");
    }
}

#[test]
fn an_answer_whole_within_the_deadline_is_read() {
    let body = br#"{"data": [{"id": "llama3.3"}]}"#;
    let head = format!("{HEAD}Content-Length: {}\r\n\r\n", body.len());
    let peer = Peer::new(sending(vec![head.into_bytes(), body[..10].to_vec(), body[10..].to_vec()]));
    let (answered, _) = asked(&peer, Duration::from_secs(1));
    let answered = answered.expect("an answer");
    assert_eq!((answered.status, answered.body.as_slice()), (200, &body[..]));
}

#[test]
fn an_answer_that_says_it_is_longer_than_the_most_is_read_no_further() {
    let peer = Peer::new(sending(vec![format!("{HEAD}Content-Length: {}\r\n\r\n", (32 << 20) + 1).into_bytes()]));
    let (answered, took) = asked(&peer, Duration::from_secs(1));
    assert_eq!(answered, Err(Lost("ResponseTooLarge")));
    assert!(took < Duration::from_millis(900), "{took:?}");
}

#[test]
fn nothing_listening_is_a_refused_connection_at_once() {
    let port = TcpListener::bind("127.0.0.1:0").expect("a port").local_addr().expect("its address").port();
    let endpoint = Endpoint::parse(&format!("http://127.0.0.1:{port}")).expect("a loopback URL");
    let request = http::request("GET", "/", &endpoint.host_header(), &[], None);
    assert_eq!(endpoint.send(&request, Duration::from_secs(1)), Err(Lost("ConnectionRefused")));
}
