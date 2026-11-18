//! The wire against a server that sends one line of several megabytes, as a SEARCH of a large mailbox or a header a
//! sender padded can: the line reads in time that grows with its length, not with its square.

#![allow(clippy::unwrap_used)]

use std::io::Write;
use std::net::{TcpListener, TcpStream};
use std::time::{Duration, Instant};

use super::{Piece, Stream, Wire};

/// What a server on loopback sends, in `parts` a moment apart, read by the wire as one response and then the next:
/// the first, and how long it took to read.
fn read_as_sent(parts: Vec<Vec<u8>>) -> (Vec<Piece>, Duration) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    let server = std::thread::spawn(move || {
        let (mut tcp, _) = listener.accept().unwrap();
        for part in parts {
            tcp.write_all(&part).unwrap();
            tcp.flush().unwrap();
            std::thread::sleep(Duration::from_millis(50));
        }
        tcp.write_all(b"* OK the next line\r\n").unwrap();
    });
    let tcp = TcpStream::connect(("127.0.0.1", port)).unwrap();
    tcp.set_read_timeout(Some(Duration::from_secs(60))).unwrap();
    let mut wire = Wire::new(Stream::Plain(tcp));
    let began = Instant::now();
    let read = wire.response().unwrap();
    let took = began.elapsed();
    assert_eq!(wire.response().unwrap(), vec![Piece::Text("* OK the next line".to_owned())], "the next line follows");
    server.join().unwrap();
    (read, took)
}

/// A line of `length` bytes, its CRLF after.
fn line_of(length: usize) -> Vec<u8> {
    let mut line = b"* ".to_vec();
    line.resize(length, b'7');
    line.extend_from_slice(b"\r\n");
    line
}

#[test]
fn a_line_of_megabytes_reads_in_time_that_grows_with_its_length() {
    // 8 MiB, read 8 KiB at a time: a search for the line's end that started again from the front at every read would
    // look at about four billion bytes, many seconds; looking at each once takes a few tens of milliseconds.
    let length = 8 << 20;
    let (read, took) = read_as_sent(vec![line_of(length)]);
    let [Piece::Text(line)] = read.as_slice() else { panic!("one line: {} pieces", read.len()) };
    assert_eq!(line.len(), length);
    assert!(line.starts_with("* 777") && line.ends_with("777"));
    assert!(took < Duration::from_secs(2), "an 8 MiB line took {took:?}");
}

#[test]
fn a_line_whose_cr_and_lf_come_in_two_reads_still_ends_there() {
    // The CR is the last byte of what has come, and its LF the first of what comes next.
    let line = line_of(20_000);
    let (before, after) = line.split_at(line.len() - 1);
    let (read, _) = read_as_sent(vec![before.to_vec(), after.to_vec()]);
    let [Piece::Text(line)] = read.as_slice() else { panic!("one line: {} pieces", read.len()) };
    assert_eq!(line.len(), 20_000);
}
