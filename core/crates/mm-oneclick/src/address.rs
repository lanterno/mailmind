//! Which addresses a one-click POST may never reach (`safe_http.ip_is_forbidden`): anything that could be the local
//! network, this machine or a cloud's metadata endpoint. It is Python's `ipaddress` as the Python app reads it (3.14):
//! an address that is private (not globally reachable, IANA's special registries), loopback, link-local, multicast,
//! reserved or unspecified, or in the carrier-grade NAT block, and an IPv4-mapped IPv6 address as the IPv4 address it
//! carries.

use std::net::{IpAddr, Ipv4Addr, Ipv6Addr};

/// A block of addresses: the first, and how many leading bits it fixes.
type Block<T> = (T, u32);

const V4_PRIVATE: [Block<[u8; 4]>; 14] = [
    ([0, 0, 0, 0], 8),
    ([10, 0, 0, 0], 8),
    ([127, 0, 0, 0], 8),
    ([169, 254, 0, 0], 16),
    ([172, 16, 0, 0], 12),
    ([192, 0, 0, 0], 24),
    ([192, 0, 0, 170], 31),
    ([192, 0, 2, 0], 24),
    ([192, 168, 0, 0], 16),
    ([198, 18, 0, 0], 15),
    ([198, 51, 100, 0], 24),
    ([203, 0, 113, 0], 24),
    ([240, 0, 0, 0], 4),
    ([255, 255, 255, 255], 32),
];
/// Globally reachable inside the blocks above.
const V4_PRIVATE_EXCEPT: [Block<[u8; 4]>; 2] = [([192, 0, 0, 9], 32), ([192, 0, 0, 10], 32)];
const V4_LOOPBACK: Block<[u8; 4]> = ([127, 0, 0, 0], 8);
const V4_LINK_LOCAL: Block<[u8; 4]> = ([169, 254, 0, 0], 16);
const V4_MULTICAST: Block<[u8; 4]> = ([224, 0, 0, 0], 4);
const V4_RESERVED: Block<[u8; 4]> = ([240, 0, 0, 0], 4);
/// Carrier-grade NAT, shared address space that Python's `is_private` leaves out.
const V4_SHARED: Block<[u8; 4]> = ([100, 64, 0, 0], 10);

const V6_PRIVATE: [Block<u128>; 11] = [
    (0x1, 128),
    (0x0, 128),
    (0xffff_0000_0000, 96),
    (0x0064_ff9b_0001 << 80, 48),
    (0x0100 << 112, 64),
    (0x2001 << 112, 23),
    (0x2001_0db8 << 96, 32),
    (0x2002 << 112, 16),
    (0x3fff << 112, 20),
    (0xfc00 << 112, 7),
    (0xfe80 << 112, 10),
];
/// Globally reachable inside the blocks above.
const V6_PRIVATE_EXCEPT: [Block<u128>; 6] = [
    ((0x2001_0001 << 96) | 1, 128),
    ((0x2001_0001 << 96) | 2, 128),
    (0x2001_0003 << 96, 32),
    (0x2001_0004_0112 << 80, 48),
    (0x2001_0020 << 96, 28),
    (0x2001_0030 << 96, 28),
];
const V6_LINK_LOCAL: Block<u128> = (0xfe80 << 112, 10);
const V6_MULTICAST: Block<u128> = (0xff00 << 112, 8);
const V6_RESERVED: [Block<u128>; 15] = [
    (0x0000 << 112, 8),
    (0x0100 << 112, 8),
    (0x0200 << 112, 7),
    (0x0400 << 112, 6),
    (0x0800 << 112, 5),
    (0x1000 << 112, 4),
    (0x4000 << 112, 3),
    (0x6000 << 112, 3),
    (0x8000 << 112, 3),
    (0xa000 << 112, 3),
    (0xc000 << 112, 3),
    (0xe000 << 112, 4),
    (0xf000 << 112, 5),
    (0xf800 << 112, 6),
    (0xfe00 << 112, 9),
];

fn in_v4(address: u32, (first, bits): Block<[u8; 4]>) -> bool {
    let mask = u32::MAX.checked_shl(32 - bits).unwrap_or(0);
    address & mask == u32::from_be_bytes(first) & mask
}

fn in_v6(address: u128, (first, bits): Block<u128>) -> bool {
    let mask = u128::MAX.checked_shl(128 - bits).unwrap_or(0);
    address & mask == first & mask
}

fn v4_forbidden(address: Ipv4Addr) -> bool {
    let n = u32::from(address);
    let private = V4_PRIVATE.iter().any(|b| in_v4(n, *b)) && !V4_PRIVATE_EXCEPT.iter().any(|b| in_v4(n, *b));
    private
        || in_v4(n, V4_LOOPBACK)
        || in_v4(n, V4_LINK_LOCAL)
        || in_v4(n, V4_MULTICAST)
        || in_v4(n, V4_RESERVED)
        || n == 0
        || in_v4(n, V4_SHARED)
}

fn v6_forbidden(address: Ipv6Addr) -> bool {
    // An IPv4-mapped address is what it carries, in each of Python's tests of it.
    if let Some(carried) = address.to_ipv4_mapped() {
        return v4_forbidden(carried);
    }
    let n = u128::from(address);
    let private = V6_PRIVATE.iter().any(|b| in_v6(n, *b)) && !V6_PRIVATE_EXCEPT.iter().any(|b| in_v6(n, *b));
    private
        || n == 1
        || in_v6(n, V6_LINK_LOCAL)
        || in_v6(n, V6_MULTICAST)
        || V6_RESERVED.iter().any(|b| in_v6(n, *b))
        || n == 0
}

/// Whether a one-click POST must never reach `address`: it could be the local network, this machine or a metadata
/// endpoint.
pub fn forbidden(address: IpAddr) -> bool {
    match address {
        IpAddr::V4(v4) => v4_forbidden(v4),
        IpAddr::V6(v6) => v6_forbidden(v6),
    }
}
