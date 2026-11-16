//! TCP keepalive on a connection that waits (`Stream::keep_alive`): off until asked, then the system probes it once it
//! is quiet, as often and as many times as asked. Loopback only.

#![allow(clippy::unwrap_used)]

use std::net::{TcpListener, TcpStream};
use std::time::Duration;

use mm_net::Stream;

#[test]
fn a_connection_asked_to_is_probed_once_quiet_as_often_and_as_many_times_as_asked() {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let tcp = TcpStream::connect(listener.local_addr().unwrap()).unwrap();
    let _far_end = listener.accept().unwrap();
    let stream = Stream::Plain(tcp.try_clone().unwrap());
    assert_eq!(stream.keep_alive_after().unwrap(), None);
    stream.keep_alive(Duration::from_secs(60), Duration::from_secs(15), 4).unwrap();
    assert_eq!(stream.keep_alive_after().unwrap(), Some(Duration::from_secs(60)));
    let socket = socket2::SockRef::from(&tcp);
    assert_eq!(socket.tcp_keepalive_interval().unwrap(), Duration::from_secs(15));
    assert_eq!(socket.tcp_keepalive_retries().unwrap(), 4);
}
