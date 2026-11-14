//! The one-click unsubscribe POST (`OneClick::post`), the one request MailMind makes to a host a message names, held
//! to the guards of tests/unit/curation/test_unsub_post.py: anything but https refused, a host refused if any address
//! it resolves to is private, loopback, link-local or otherwise internal, the connection pinned to the address vetted,
//! at most one redirect, vetted too, and each wait and the whole request limited.
//!
//! Names resolve through a table here, never DNS, and connections go through the test's network: the list's server
//! has a public address in the table, and only a connection to that address reaches the local HTTPS server standing
//! in for it, whose certificate a root the test makes signed. The vetting is the real one, and no connection anywhere
//! else is made: one to any other address is noted and refused.

#![allow(clippy::unwrap_used)]

use std::collections::HashMap;
use std::io::{self, BufRead, BufReader, Read, Write};
use std::net::{IpAddr, Ipv4Addr, Ipv6Addr, SocketAddr, TcpListener, TcpStream};
use std::path::Path;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use mm_kernel::seams::{Outside, Tripped};
use mm_oneclick::{Network, OneClick, Posted};
use rcgen::{BasicConstraints, CertificateParams, ExtendedKeyUsagePurpose, IsCa, Issuer, KeyPair, KeyUsagePurpose};
use rustls::pki_types::{CertificateDer, PrivateKeyDer, PrivatePkcs8KeyDer};
use rustls::{ServerConfig, ServerConnection, StreamOwned};

const HOST: &str = "unsub.example.test";
/// The list's server's address, as the table gives it: a public one, which only the test's network reaches.
const DOOR: &str = "100.128.0.1";

fn posted(ok: bool, status: u16, reason: &'static str) -> Posted {
    Posted { ok, status, reason }
}

/// The names the table resolves (`NAMES`): the list's own, and hosts with an internal address among theirs.
fn names() -> HashMap<String, Vec<String>> {
    [
        (HOST, vec![DOOR]),
        ("private.example.test", vec!["10.0.0.5"]),
        ("metadata.example.test", vec!["169.254.169.254"]),
        ("mixed.example.test", vec!["8.8.8.8", "192.168.1.20"]),
        ("v6-loopback.example.test", vec!["::1"]),
        ("mapped.example.test", vec!["::ffff:10.0.0.5"]),
        ("cgnat.example.test", vec!["100.64.0.9"]),
        ("mixed-first.example.test", vec!["192.168.1.20", "8.8.8.8"]),
        ("mapped-metadata.example.test", vec!["::ffff:169.254.169.254"]),
        ("mapped-cgnat.example.test", vec!["::ffff:100.64.0.9"]),
        ("link-local.example.test", vec!["fe80::1"]),
        ("any.example.test", vec!["0.0.0.0"]),
    ]
    .into_iter()
    .map(|(name, ips)| (name.to_owned(), ips.into_iter().map(str::to_owned).collect()))
    .collect()
}

/// The test's network: names from a table (a name answering differently each time it is asked, where one does), and
/// a connection to the door goes to the local server. Every name asked and connection made is noted.
#[derive(Debug, Default)]
struct Table {
    names: Mutex<HashMap<String, Vec<String>>>,
    /// A name whose answer changes: each time it is asked, the next, staying on the last.
    rebinding: Mutex<Option<(String, Vec<String>)>>,
    server: u16,
    asked: Mutex<Vec<String>>,
    made: Mutex<Vec<SocketAddr>>,
}

impl Table {
    fn asked(&self) -> Vec<String> {
        self.asked.lock().unwrap().clone()
    }

    fn made(&self) -> Vec<SocketAddr> {
        self.made.lock().unwrap().clone()
    }

    fn name(&self, name: &str, ips: &[&str]) {
        self.names.lock().unwrap().insert(name.to_owned(), ips.iter().map(|ip| (*ip).to_owned()).collect());
    }

    fn rebind(&self, name: &str, answers: &[&str]) {
        *self.rebinding.lock().unwrap() = Some((name.to_owned(), answers.iter().map(|a| (*a).to_owned()).collect()));
    }
}

impl Network for Table {
    fn resolve(&self, host: &str, port: u16) -> io::Result<Vec<SocketAddr>> {
        self.asked.lock().unwrap().push(host.to_owned());
        let ips = match &*self.rebinding.lock().unwrap() {
            Some((name, answers)) if name == host => {
                let asked = self.asked().iter().filter(|h| *h == host).count();
                vec![answers[asked.min(answers.len()) - 1].clone()]
            }
            _ => self.names.lock().unwrap().get(host).cloned().ok_or(io::ErrorKind::NotFound)?,
        };
        Ok(ips.iter().map(|ip| SocketAddr::new(ip.parse().unwrap(), port)).collect())
    }

    fn connect(&self, address: SocketAddr, timeout: Duration) -> io::Result<TcpStream> {
        self.made.lock().unwrap().push(address);
        if address.ip() != DOOR.parse::<IpAddr>().unwrap() {
            return Err(io::ErrorKind::ConnectionRefused.into());
        }
        TcpStream::connect_timeout(&SocketAddr::from(([127, 0, 0, 1], self.server)), timeout)
    }
}

/// The tripwire, as `Outside` asks it of each host; one host it refuses, where a test names one.
#[derive(Debug, Default)]
struct Wire {
    refused: Option<String>,
    reached: Mutex<Vec<String>>,
}

impl Outside for Wire {
    fn open(&self, _path: &Path) -> Result<(), Tripped> {
        Ok(())
    }

    fn reach(&self, host: &str) -> Result<(), Tripped> {
        self.reached.lock().unwrap().push(host.to_owned());
        if self.refused.as_deref() == Some(host) {
            return Err(Tripped(format!("DNS lookup of {host:?}")));
        }
        Ok(())
    }

    fn run(&self, _program: &str) -> Result<(), Tripped> {
        Ok(())
    }
}

/// A request as the list's server saw it.
#[derive(Clone, Debug, PartialEq, Eq)]
struct Seen {
    path: String,
    /// The head's lines after the request line, as sent.
    headers: Vec<String>,
    body: Vec<u8>,
}

impl Seen {
    fn header(&self, name: &str) -> Option<String> {
        self.headers
            .iter()
            .filter_map(|h| h.split_once(": "))
            .find(|(n, _)| n.eq_ignore_ascii_case(name))
            .map(|(_, v)| v.to_owned())
    }
}

/// How the list's server answers a path.
#[derive(Clone, Debug)]
enum Reply {
    /// A status, and where it sends the request on (`{port}` is the server's).
    Status(u16, Option<&'static str>),
    /// These bytes, as they are.
    Raw(Vec<u8>),
    /// Nothing, until the client leaves.
    Silent,
    /// A status line, then a header line a byte at a time, for ever.
    Drip,
}

/// The list's server: HTTPS on loopback for `HOST`, its certificate signed by `root`.
struct Lists {
    port: u16,
    routes: Arc<Mutex<HashMap<String, Reply>>>,
    seen: Arc<Mutex<Vec<Seen>>>,
}

impl Lists {
    fn route(&self, path: &str, reply: Reply) {
        self.routes.lock().unwrap().insert(path.to_owned(), reply);
    }

    fn seen(&self) -> Vec<Seen> {
        self.seen.lock().unwrap().clone()
    }

    fn paths(&self) -> Vec<String> {
        self.seen().into_iter().map(|s| s.path).collect()
    }

    fn url(&self, path: &str) -> String {
        format!("https://{HOST}:{}{path}", self.port)
    }
}

/// A root the test trusts, and the list's certificate it signed.
struct Certs {
    root: CertificateDer<'static>,
    chain: Vec<CertificateDer<'static>>,
    key: PrivateKeyDer<'static>,
}

fn certs() -> Certs {
    let root_key = KeyPair::generate().unwrap();
    let mut root_params = CertificateParams::new(Vec::<String>::new()).unwrap();
    root_params.is_ca = IsCa::Ca(BasicConstraints::Unconstrained);
    root_params.key_usages = vec![KeyUsagePurpose::KeyCertSign, KeyUsagePurpose::CrlSign];
    let root = root_params.self_signed(&root_key).unwrap();
    let key = KeyPair::generate().unwrap();
    let mut params = CertificateParams::new(vec![HOST.to_owned()]).unwrap();
    params.extended_key_usages = vec![ExtendedKeyUsagePurpose::ServerAuth];
    let cert = params.signed_by(&key, &Issuer::from_params(&root_params, &root_key)).unwrap();
    Certs {
        root: root.der().clone(),
        chain: vec![cert.der().clone()],
        key: PrivateKeyDer::Pkcs8(PrivatePkcs8KeyDer::from(key.serialize_der())),
    }
}

/// A request off `tls`: its head, then a body of the length it declares.
fn read_request(tls: &mut impl Read) -> io::Result<Seen> {
    let mut reader = BufReader::new(tls);
    let mut first = String::new();
    reader.read_line(&mut first)?;
    let path = first.split(' ').nth(1).unwrap_or_default().to_owned();
    let mut headers = Vec::new();
    loop {
        let mut line = String::new();
        if reader.read_line(&mut line)? == 0 || line == "\r\n" {
            break;
        }
        headers.push(line.trim_end_matches("\r\n").to_owned());
    }
    let length =
        headers.iter().find_map(|h| h.strip_prefix("Content-Length: ")).and_then(|l| l.parse().ok()).unwrap_or(0);
    let mut body = vec![0; length];
    reader.read_exact(&mut body)?;
    Ok(Seen { path, headers, body })
}

fn serve(
    tcp: TcpStream,
    config: Arc<ServerConfig>,
    port: u16,
    routes: &Mutex<HashMap<String, Reply>>,
    seen: &Mutex<Vec<Seen>>,
) {
    tcp.set_read_timeout(Some(Duration::from_secs(5))).unwrap();
    let mut tls = StreamOwned::new(ServerConnection::new(config).unwrap(), tcp);
    // A client that refused the certificate leaves here, having said nothing.
    let Ok(request) = read_request(&mut tls) else { return };
    let path = request.path.split('?').next().unwrap_or_default().to_owned();
    seen.lock().unwrap().push(request);
    let reply = routes.lock().unwrap().get(&path).cloned().unwrap_or(Reply::Status(404, None));
    match reply {
        Reply::Status(status, location) => {
            let mut head = format!("HTTP/1.1 {status} Said\r\n");
            if let Some(location) = location {
                head.push_str(&format!("Location: {}\r\n", location.replace("{port}", &port.to_string())));
            }
            head.push_str("Content-Length: 0\r\n\r\n");
            let _ = tls.write_all(head.as_bytes());
        }
        Reply::Raw(bytes) => {
            let _ = tls.write_all(&bytes);
        }
        Reply::Silent => {
            let mut rest = [0u8; 1];
            let _ = tls.read(&mut rest);
        }
        Reply::Drip => {
            let _ = tls.write_all(b"HTTP/1.1 200 OK\r\nX-Drip: ");
            while tls.write_all(b"a").and_then(|()| tls.flush()).is_ok() {
                std::thread::sleep(Duration::from_millis(50));
            }
        }
    }
    let _ = tls.flush();
}

fn lists(certs: &Certs) -> Lists {
    let provider = Arc::new(rustls::crypto::ring::default_provider());
    let config = ServerConfig::builder_with_provider(provider)
        .with_safe_default_protocol_versions()
        .unwrap()
        .with_no_client_auth()
        .with_single_cert(certs.chain.clone(), certs.key.clone_key())
        .unwrap();
    let config = Arc::new(config);
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    let routes: Arc<Mutex<HashMap<String, Reply>>> = Arc::default();
    let seen: Arc<Mutex<Vec<Seen>>> = Arc::default();
    let (r, s) = (routes.clone(), seen.clone());
    std::thread::spawn(move || {
        for tcp in listener.incoming().flatten() {
            let (config, r, s) = (config.clone(), r.clone(), s.clone());
            std::thread::spawn(move || serve(tcp, config, port, &r, &s));
        }
    });
    Lists { port, routes, seen }
}

/// A world: the list's server, the network that reaches it, the tripwire, and the POST over them.
struct World {
    lists: Lists,
    table: Arc<Table>,
    wire: Arc<Wire>,
    /// TLS that trusts the root the list's certificate was signed by.
    tls: Arc<rustls::ClientConfig>,
    post: OneClick,
}

impl World {
    /// The same POST, with other time limits.
    fn waiting(&self, wait: Duration, whole: Duration) -> OneClick {
        OneClick::over(self.table.clone(), self.tls.clone(), self.wire.clone()).waiting(wait, whole)
    }
}

fn world_with(wire: Wire) -> World {
    let certs = certs();
    let lists = lists(&certs);
    let table = Arc::new(Table { names: Mutex::new(names()), server: lists.port, ..Table::default() });
    let wire = Arc::new(wire);
    let tls = mm_net::tls::verified(vec![certs.root.clone()]).unwrap();
    let post = OneClick::over(table.clone(), tls.clone(), wire.clone());
    World { lists, table, wire, tls, post }
}

fn world() -> World {
    world_with(Wire::default())
}

#[test]
fn a_one_click_post_says_only_what_rfc_8058_asks() {
    let w = world();
    w.lists.route("/u/ada-218", Reply::Status(200, None));
    assert_eq!(w.post.post(&w.lists.url("/u/ada-218?list=weekly")), posted(true, 200, "ok"));
    let [seen] = w.lists.seen().try_into().unwrap();
    assert_eq!(seen.path, "/u/ada-218?list=weekly");
    assert_eq!(
        seen.headers,
        [
            "Accept-Encoding: identity",
            "Host: unsub.example.test",
            "Content-Type: application/x-www-form-urlencoded",
            "Content-Length: 26",
            "User-Agent: MailMind-Unsub/1.0 (+list-unsubscribe one-click)",
            "Connection: close",
        ]
    );
    assert_eq!(seen.body, b"List-Unsubscribe=One-Click");
    assert_eq!((seen.header("Cookie"), seen.header("Authorization")), (None, None));
}

#[test]
fn a_path_goes_as_written_its_params_and_all() {
    let w = world();
    w.lists.route("/u;list=weekly", Reply::Status(200, None));
    assert_eq!(w.post.post(&w.lists.url("/u;list=weekly?ada=218#top")), posted(true, 200, "ok"));
    assert_eq!(w.lists.paths(), ["/u;list=weekly?ada=218"]);
    w.lists.route("/", Reply::Status(204, None));
    assert_eq!(w.post.post(&w.lists.url("")), posted(true, 204, "ok"));
}

#[test]
fn a_status_that_isnt_success_fails() {
    let w = world();
    w.lists.route("/gone", Reply::Status(410, None));
    assert_eq!(w.post.post(&w.lists.url("/gone")), posted(false, 410, "bad_status"));
}

#[test]
fn one_redirect_is_followed_and_a_second_is_not() {
    let w = world();
    w.lists.route("/hop", Reply::Status(302, Some("https://unsub.example.test:{port}/done")));
    w.lists.route("/done", Reply::Status(200, None));
    w.lists.route("/hop-hop", Reply::Status(307, Some("/hop")));
    assert_eq!(w.post.post(&w.lists.url("/hop")), posted(true, 200, "ok"));
    assert_eq!(w.lists.paths(), ["/hop", "/done"]);
    w.lists.seen.lock().unwrap().clear();
    assert_eq!(w.post.post(&w.lists.url("/hop-hop")), posted(false, 302, "bad_status"));
    assert_eq!(w.lists.paths(), ["/hop-hop", "/hop"]);
}

#[test]
fn a_redirect_that_says_nowhere_is_the_status_it_answered() {
    let w = world();
    w.lists.route("/moved", Reply::Status(301, None));
    assert_eq!(w.post.post(&w.lists.url("/moved")), posted(false, 301, "bad_status"));
}

#[test]
fn a_redirect_to_an_internal_host_is_refused() {
    let w = world();
    w.lists.route("/sneaky", Reply::Status(302, Some("https://private.example.test/admin")));
    assert_eq!(w.post.post(&w.lists.url("/sneaky")), posted(false, 0, "ssrf_blocked"));
    assert_eq!(w.lists.paths(), ["/sneaky"]);
}

#[test]
fn a_redirect_to_plain_http_is_refused() {
    let w = world();
    w.lists.route("/downgrade", Reply::Status(301, Some("http://unsub.example.test/u")));
    assert_eq!(w.post.post(&w.lists.url("/downgrade")), posted(false, 0, "bad_scheme"));
}

#[test]
fn a_redirect_to_an_internal_address_is_refused() {
    for (location, host) in [
        ("https://169.254.169.254/latest/meta-data/", "169.254.169.254"),
        ("https://10.0.0.1/", "10.0.0.1"),
        ("https://[::1]/", "::1"),
        ("https://127.0.0.2/", "127.0.0.2"),
        ("https://[::ffff:10.0.0.1]/", "::ffff:10.0.0.1"),
    ] {
        let w = world();
        w.table.name(host, &[host]);
        w.lists.route("/sneaky", Reply::Status(302, Some(location)));
        assert_eq!(w.post.post(&w.lists.url("/sneaky")), posted(false, 0, "ssrf_blocked"), "{location}");
        assert_eq!(w.lists.paths(), ["/sneaky"], "{location}");
        assert_eq!(w.table.made().len(), 1, "{location}: only the list's server was reached");
    }
}

#[test]
fn a_host_with_any_internal_address_is_refused_before_connecting() {
    let w = world();
    for host in [
        "private.example.test",
        "metadata.example.test",
        "mixed.example.test",
        "v6-loopback.example.test",
        "mapped.example.test",
        "cgnat.example.test",
        "mixed-first.example.test",
        "mapped-metadata.example.test",
        "mapped-cgnat.example.test",
        "link-local.example.test",
        "any.example.test",
    ] {
        assert_eq!(w.post.post(&format!("https://{host}/u")), posted(false, 0, "ssrf_blocked"), "{host}");
    }
    assert_eq!(w.table.made(), []);
    assert_eq!(w.lists.seen(), []);
}

#[test]
fn anything_but_https_is_refused() {
    let w = world();
    for url in
        ["http://unsub.example.test/u", "mailto:u@example.test", "", "https:///nohost", "ftp://unsub.example.test/u"]
    {
        assert_eq!(w.post.post(url), posted(false, 0, "bad_scheme"), "{url:?}");
    }
    assert_eq!((w.table.asked(), w.table.made()), (vec![], vec![]));
}

#[test]
fn what_isnt_a_url_goes_nowhere() {
    let w = world();
    for url in [
        "https://[::1/u",
        "https://[1.2.3.4]/u",
        "https://unsub.example.test:x/u",
        "https://unsub.example.test:70000/u",
    ] {
        assert_eq!(w.post.post(url), posted(false, 0, "bad_url"), "{url:?}");
    }
    assert_eq!((w.table.asked(), w.table.made()), (vec![], vec![]));
}

#[test]
fn a_name_that_doesnt_resolve() {
    let w = world();
    assert_eq!(w.post.post("https://nowhere.example.test/u"), posted(false, 0, "dns"));
}

#[test]
fn a_host_the_tripwire_refuses_is_never_looked_up() {
    let w = world_with(Wire { refused: Some(HOST.to_owned()), ..Wire::default() });
    assert_eq!(w.post.post(&w.lists.url("/u")), posted(false, 0, "dns"));
    assert_eq!(*w.wire.reached.lock().unwrap(), [HOST]);
    assert_eq!((w.table.asked(), w.table.made()), (vec![], vec![]));
}

#[test]
fn a_certificate_for_another_name_fails() {
    let w = world();
    // The server's certificate names HOST only.
    w.table.name("other.example.test", &[DOOR]);
    w.lists.route("/u", Reply::Status(200, None));
    assert_eq!(w.post.post(&format!("https://other.example.test:{}/u", w.lists.port)), posted(false, 0, "timeout"));
    assert_eq!(w.lists.seen(), []);
}

#[test]
fn a_certificate_no_trusted_root_signed_fails() {
    let w = world();
    let stranger = certs();
    let post = OneClick::over(w.table.clone(), mm_net::tls::verified(vec![stranger.root]).unwrap(), w.wire.clone());
    w.lists.route("/u", Reply::Status(200, None));
    assert_eq!(post.post(&w.lists.url("/u")), posted(false, 0, "timeout"));
    assert_eq!(w.lists.seen(), []);
}

#[test]
fn a_name_is_asked_once_and_the_connection_goes_where_the_answer_vetted_said() {
    let w = world();
    w.table.rebind(HOST, &[DOOR, "10.0.0.5"]);
    w.lists.route("/u", Reply::Status(200, None));
    assert_eq!(w.post.post(&w.lists.url("/u")), posted(true, 200, "ok"));
    assert_eq!(w.table.asked(), [HOST]);
    assert_eq!(w.table.made(), [SocketAddr::new(DOOR.parse().unwrap(), w.lists.port)]);
    assert_eq!(w.lists.seen().into_iter().map(|s| s.header("Host")).collect::<Vec<_>>(), [Some(HOST.to_owned())]);
}

#[test]
fn a_redirect_back_to_a_name_that_now_answers_internally_is_refused() {
    let w = world();
    w.table.rebind(HOST, &[DOOR, "10.0.0.5"]);
    w.lists.route("/hop", Reply::Status(302, Some("https://unsub.example.test:{port}/done")));
    w.lists.route("/done", Reply::Status(200, None));
    assert_eq!(w.post.post(&w.lists.url("/hop")), posted(false, 0, "ssrf_blocked"));
    assert_eq!(w.table.asked().len(), 2);
    assert_eq!(w.lists.paths(), ["/hop"]);
}

#[test]
fn a_name_that_answers_internally_first_gets_no_second_question() {
    let w = world();
    w.table.rebind(HOST, &["10.0.0.5", DOOR]);
    assert_eq!(w.post.post(&w.lists.url("/u")), posted(false, 0, "ssrf_blocked"));
    assert_eq!(w.table.asked().len(), 1);
    assert_eq!(w.lists.seen(), []);
}

#[test]
fn a_login_in_a_url_goes_nowhere() {
    let w = world();
    w.lists.route("/u", Reply::Status(200, None));
    for login in ["user:secret", "127.0.0.1", "unsub.example.test"] {
        assert_eq!(w.post.post(&format!("https://{login}@{HOST}:{}/u", w.lists.port)), posted(true, 200, "ok"));
    }
    let seen: Vec<(Option<String>, Option<String>, String)> =
        w.lists.seen().into_iter().map(|s| (s.header("Host"), s.header("Authorization"), s.path)).collect();
    assert_eq!(seen, vec![(Some(HOST.to_owned()), None, "/u".to_owned()); 3]);
}

#[test]
fn a_url_cant_write_a_line_of_its_own_request() {
    let w = world();
    for path in ["/u\r\nX-Sneaked: 1", "/u\nX-Sneaked: 1", "/u X-Sneaked: 1", "/u\x00", "/u\u{e9}"] {
        assert!(!w.post.post(&w.lists.url(path)).ok, "{path:?}");
    }
    assert_eq!(w.table.made(), [], "nothing connected");
    assert_eq!(w.lists.seen(), []);
}

#[test]
fn a_continue_is_passed_over_for_the_answer_after_it() {
    let w = world();
    w.lists.route(
        "/u",
        Reply::Raw(b"HTTP/1.1 100 Continue\r\n\r\nHTTP/1.1 202 Accepted\r\nContent-Length: 0\r\n\r\n".to_vec()),
    );
    assert_eq!(w.post.post(&w.lists.url("/u")), posted(true, 202, "ok"));
}

#[test]
fn of_the_answer_only_its_status_is_read() {
    let w = world();
    let mut big = b"HTTP/1.1 200 OK\r\nContent-Length: 4194304\r\n\r\n".to_vec();
    big.extend(std::iter::repeat_n(b'x', 4 << 20));
    w.lists.route("/u", Reply::Raw(big));
    let started = Instant::now();
    assert_eq!(w.post.post(&w.lists.url("/u")), posted(true, 200, "ok"));
    assert!(started.elapsed() < Duration::from_secs(5));
}

#[test]
fn an_answer_that_isnt_http_fails() {
    for raw in [
        &b"SMTP 220 ready\r\n\r\n"[..],
        b"HTTP/2.0 200 OK\r\n\r\n",
        b"HTTP/1.1 OK\r\n\r\n",
        b"HTTP/1.1 99 Low\r\n\r\n",
        b"",
    ] {
        let w = world();
        w.lists.route("/u", Reply::Raw(raw.to_vec()));
        assert_eq!(w.post.post(&w.lists.url("/u")), posted(false, 0, "timeout"), "{:?}", String::from_utf8_lossy(raw));
    }
}

#[test]
fn a_head_past_its_limits_fails() {
    let mut long = b"HTTP/1.1 200 OK\r\nX-Long: ".to_vec();
    long.extend(std::iter::repeat_n(b'a', mm_oneclick::HEAD_LINE));
    long.extend(b"\r\n\r\n");
    let mut many = b"HTTP/1.1 200 OK\r\n".to_vec();
    for n in 0..=mm_oneclick::HEADERS {
        many.extend(format!("X-{n}: a\r\n").as_bytes());
    }
    many.extend(b"\r\n");
    for raw in [long, many] {
        let w = world();
        w.lists.route("/u", Reply::Raw(raw));
        assert_eq!(w.post.post(&w.lists.url("/u")), posted(false, 0, "timeout"));
    }
}

#[test]
fn a_server_that_says_nothing_is_waited_on_no_longer_than_a_wait() {
    let w = world();
    w.lists.route("/u", Reply::Silent);
    let started = Instant::now();
    let waiting = w.waiting(Duration::from_millis(300), Duration::from_secs(20));
    assert_eq!(waiting.post(&w.lists.url("/u")), posted(false, 0, "timeout"));
    assert!(started.elapsed() < Duration::from_secs(3), "{:?}", started.elapsed());
}

#[test]
fn a_server_that_answers_a_byte_at_a_time_is_waited_on_no_longer_than_the_whole() {
    let w = world();
    w.lists.route("/u", Reply::Drip);
    let started = Instant::now();
    let waiting = w.waiting(Duration::from_secs(5), Duration::from_millis(600));
    assert_eq!(waiting.post(&w.lists.url("/u")), posted(false, 0, "timeout"));
    assert!(started.elapsed() < Duration::from_secs(4), "{:?}", started.elapsed());
}

// ── an address in the URL itself ─────────────────────────────────────────────────────────────────

/// A number in any of the spellings `inet_aton` reads (one to four parts, each decimal, octal or hex), as a resolver
/// on glibc reads one.
fn inet_aton(text: &str) -> Option<Ipv4Addr> {
    let number = |part: &str| -> Option<u32> {
        match part.strip_prefix("0x").or_else(|| part.strip_prefix("0X")) {
            Some(hex) => u32::from_str_radix(hex, 16).ok(),
            None if part.len() > 1 && part.starts_with('0') => u32::from_str_radix(&part[1..], 8).ok(),
            None => part.parse().ok(),
        }
    };
    let parts: Vec<u32> = text.split('.').map(number).collect::<Option<_>>()?;
    let byte = |n: &u32| *n <= 0xff;
    let n = match parts.as_slice() {
        [a] => *a,
        [a, b] if byte(a) && *b <= 0xff_ffff => (a << 24) | b,
        [a, b, c] if byte(a) && byte(b) && *c <= 0xffff => (a << 24) | (b << 16) | c,
        [a, b, c, d] if [a, b, c, d].into_iter().all(byte) => (a << 24) | (b << 16) | (c << 8) | d,
        _ => return None,
    };
    Some(Ipv4Addr::from(n))
}

/// Names resolved as the machine's own resolver reads a number, in each of its spellings, with no DNS (`nowhere`); and
/// a connection noted and refused: what it lists is where the POST would have gone.
#[derive(Debug, Default)]
struct Nowhere {
    made: Mutex<Vec<SocketAddr>>,
}

impl Network for Nowhere {
    fn resolve(&self, host: &str, port: u16) -> io::Result<Vec<SocketAddr>> {
        let ips: Vec<IpAddr> = if host == "localhost" {
            vec![Ipv4Addr::LOCALHOST.into(), Ipv6Addr::LOCALHOST.into()]
        } else if let Ok(v6) = host.parse::<Ipv6Addr>() {
            vec![v6.into()]
        } else {
            vec![inet_aton(host).ok_or(io::ErrorKind::NotFound)?.into()]
        };
        Ok(ips.into_iter().map(|ip| SocketAddr::new(ip, port)).collect())
    }

    fn connect(&self, address: SocketAddr, _timeout: Duration) -> io::Result<TcpStream> {
        self.made.lock().unwrap().push(address);
        Err(io::Error::other("no further, in a test"))
    }
}

fn nowhere() -> (Arc<Nowhere>, OneClick) {
    let network = Arc::new(Nowhere::default());
    let tls = mm_net::tls::verified(vec![certs().root]).unwrap();
    (network.clone(), OneClick::over(network, tls, Arc::new(Wire::default())))
}

#[test]
fn an_internal_address_in_a_url_is_refused_however_it_is_written() {
    let (network, post) = nowhere();
    for url in [
        "https://127.0.0.1/u",
        "https://127.1/u",
        "https://2130706433/u",
        "https://0x7f.0.0.1/u",
        "https://0177.0.0.1/u",
        "https://0/u",
        "https://localhost/u",
        "https://LOCALHOST/u",
        "https://[::1]/u",
        "https://[::ffff:127.0.0.1]/u",
        "https://[::ffff:7f00:1]/u",
        "https://169.254.169.254/latest/meta-data/",
        "https://0xa9fea9fe/latest/meta-data/",
        "https://10.0.0.5:8443/u",
        "https://100.64.0.1/u",
        "https://[fe80::1]/u",
        "https://[fc00::1]/u",
        "https://unsub.example.test@127.0.0.1/u",
        "https://user:secret@10.0.0.5/u",
        "https://8.8.8.8@169.254.169.254/u",
    ] {
        assert_eq!(post.post(url), posted(false, 0, "ssrf_blocked"), "{url}");
    }
    assert_eq!(*network.made.lock().unwrap(), []);
}

#[test]
fn a_public_address_gets_as_far_as_its_own_door() {
    for (url, there) in [
        ("https://8.8.8.8/u", "8.8.8.8:443"),
        ("https://134744072/u", "8.8.8.8:443"),
        ("https://127.0.0.1@8.8.8.8:8443/u", "8.8.8.8:8443"),
    ] {
        let (network, post) = nowhere();
        assert_eq!(post.post(url), posted(false, 0, "timeout"), "{url}");
        assert_eq!(*network.made.lock().unwrap(), [there.parse::<SocketAddr>().unwrap()], "{url}");
    }
}
