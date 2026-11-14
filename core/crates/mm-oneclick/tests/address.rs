//! Which addresses a one-click POST may never reach (`address::forbidden`), as the Python app's
//! `safe_http.ip_is_forbidden` says on Python 3.14: the addresses of tests/unit/curation/test_unsub_post.py's
//! `test_which_addresses_count_as_internal`, and each edge of every block, each verdict Python's.

use std::net::IpAddr;

use mm_oneclick::address::forbidden;

const INSIDE: [&str; 61] = [
    "10.0.0.1",
    "172.16.5.4",
    "192.168.0.1",
    "127.0.0.1",
    "169.254.169.254",
    "100.64.0.1",
    "0.0.0.0",
    "224.0.0.1",
    "240.0.0.1",
    "::1",
    "fe80::1",
    "fc00::1",
    "::",
    "::ffff:10.0.0.1",
    "::ffff:127.0.0.1",
    "100.127.255.255",
    "198.18.0.1",
    "192.0.2.1",
    "255.255.255.255",
    "::127.0.0.1",
    "64:ff9b::7f00:1",
    "::ffff:100.64.0.1",
    "::ffff:169.254.169.254",
    "fd00::1",
    "ff02::1",
    "2001:db8::1",
    "2002:7f00:1::",
    "2002:a00:5::",
    "2002:a9fe:a9fe::",
    "2002:6440:9::",
    "192.0.0.8",
    "192.0.0.170",
    "192.0.0.171",
    "192.0.0.172",
    "198.51.100.7",
    "203.0.113.9",
    "239.255.255.255",
    "0.255.255.255",
    "2001:1::3",
    "2001:4:113::1",
    "2001::1",
    "2001:1ff::1",
    "3fff::1",
    "64:ff9b:1::1",
    "64:ff9b::808:808",
    "100::1",
    "100:0:0:1::1",
    "fe00::1",
    "ff00::1",
    "4000::1",
    "febf::1",
    "fe7f::1",
    "::ffff:0.0.0.0",
    "::ffff:224.0.0.1",
    "::0.0.0.1",
    "::2",
    "e000::1",
    "f000::1",
    "fbff::1",
    "fc00::",
    "fdff:ffff::1",
];

const OUTSIDE: [&str; 28] = [
    "8.8.8.8",
    "1.1.1.1",
    "2606:4700:4700::1111",
    "100.63.255.255",
    "100.128.0.1",
    "::ffff:8.8.8.8",
    "192.0.0.9",
    "192.0.0.10",
    "172.15.255.255",
    "172.32.0.0",
    "198.17.255.255",
    "198.20.0.0",
    "223.255.255.255",
    "1.0.0.0",
    "11.0.0.0",
    "2001:1::1",
    "2001:1::2",
    "2001:3::1",
    "2001:4:112::1",
    "2001:20::1",
    "2001:2f::1",
    "2001:30::1",
    "2001:200::1",
    "3fff:1000::1",
    "fec0::1",
    "2c0f:fb50::1",
    "3ffe::1",
    "::ffff:192.0.0.9",
];

fn address(text: &str) -> IpAddr {
    text.parse().unwrap_or_else(|_| panic!("{text} is an address"))
}

#[test]
fn private_loopback_link_local_multicast_reserved_and_shared_addresses_are_forbidden() {
    let let_through: Vec<&str> = INSIDE.into_iter().filter(|a| !forbidden(address(a))).collect();
    assert_eq!(let_through, Vec::<&str>::new());
}

#[test]
fn a_globally_reachable_address_is_not() {
    let refused: Vec<&str> = OUTSIDE.into_iter().filter(|a| forbidden(address(a))).collect();
    assert_eq!(refused, Vec::<&str>::new());
}
