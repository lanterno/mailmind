//! SHA-1, which the Python app minted thread ids with (`hashlib.sha1`), so the ids already in every database are
//! minted here the same way. It names a thread and guards nothing, so SHA-1's broken collision resistance doesn't
//! matter: two roots that collide would share an id, as they did in Python.

const INITIAL: [u32; 5] = [0x6745_2301, 0xEFCD_AB89, 0x98BA_DCFE, 0x1032_5476, 0xC3D2_E1F0];

/// The SHA-1 digest of `data` (FIPS 180-4).
pub(super) fn digest(data: &[u8]) -> [u8; 20] {
    let mut state = INITIAL;
    let (blocks, rest) = data.as_chunks::<64>();
    for block in blocks {
        compress(&mut state, block);
    }
    // The rest, a one bit, zeros, and the length in bits: one block more, or two when the length doesn't fit.
    let mut last = [0u8; 128];
    last[..rest.len()].copy_from_slice(rest);
    last[rest.len()] = 0x80;
    let end = if rest.len() < 56 { 64 } else { 128 };
    let bits = (data.len() as u64).wrapping_mul(8);
    last[end - 8..end].copy_from_slice(&bits.to_be_bytes());
    for block in last[..end].as_chunks::<64>().0 {
        compress(&mut state, block);
    }
    let mut out = [0u8; 20];
    for (bytes, word) in out.as_chunks_mut::<4>().0.iter_mut().zip(state) {
        *bytes = word.to_be_bytes();
    }
    out
}

fn compress(state: &mut [u32; 5], block: &[u8; 64]) {
    let mut w = [0u32; 80];
    for (word, bytes) in w.iter_mut().zip(block.as_chunks::<4>().0) {
        *word = u32::from_be_bytes(*bytes);
    }
    for i in 16..80 {
        w[i] = (w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16]).rotate_left(1);
    }
    let [mut a, mut b, mut c, mut d, mut e] = *state;
    for (i, word) in w.into_iter().enumerate() {
        let (f, k) = match i {
            0..20 => ((b & c) | (!b & d), 0x5A82_7999),
            20..40 => (b ^ c ^ d, 0x6ED9_EBA1),
            40..60 => ((b & c) | (b & d) | (c & d), 0x8F1B_BCDC),
            _ => (b ^ c ^ d, 0xCA62_C1D6),
        };
        let next = a.rotate_left(5).wrapping_add(f).wrapping_add(e).wrapping_add(k).wrapping_add(word);
        e = d;
        d = c;
        c = b.rotate_left(30);
        b = a;
        a = next;
    }
    for (kept, add) in state.iter_mut().zip([a, b, c, d, e]) {
        *kept = kept.wrapping_add(add);
    }
}
