//! A zip file as Python's `zipfile` writes one with `ZIP_DEFLATED`: each entry deflated as it comes, its header
//! written first and filled in once its size and CRC-32 are known, the central directory after the last, and the
//! ZIP64 records where a size, an offset or the count of entries is past what the older fields hold.
//!
//! Nothing here holds a whole entry: what an entry is given goes straight through the deflater into the file.

use std::io::{self, Seek, SeekFrom, Write};

use flate2::write::DeflateEncoder;
use flate2::{Compression, Crc};
use mm_kernel::seams::Moment;
use mm_kernel::when::civil;

/// Past what a zip takes ZIP64's fields and records.
#[derive(Clone, Copy, Debug)]
pub(super) struct Limits {
    /// A size or an offset past this goes in a ZIP64 extra field.
    pub size: u64,
    /// More entries than this need the ZIP64 end records.
    pub count: usize,
}

/// `ZIP64_LIMIT` and `ZIP_FILECOUNT_LIMIT`.
pub(super) const PYTHONS: Limits = Limits { size: (1 << 31) - 1, count: (1 << 16) - 1 };
/// What the 32-bit fields say when the ZIP64 extra field holds the value.
const IN_ZIP64: u32 = 0xFFFF_FFFF;
/// `ZIP_DEFLATED`.
const DEFLATED: u16 = 8;
/// `DEFLATED_VERSION` and `ZIP64_VERSION`: the version needed to read an entry.
const VERSION: u8 = 20;
const ZIP64_VERSION: u8 = 45;
/// Made on Unix.
const UNIX: u8 = 3;
/// `_MASK_UTF_FILENAME`: the name is UTF-8.
const UTF8_NAME: u16 = 0x800;
/// A regular file its owner alone may read and write, as the high half of the external attributes.
const OWNER_ONLY: u32 = 0o100_600 << 16;
/// The local header's length before its name: where the name, and then the extra field, start.
const LOCAL_HEADER: u64 = 30;

/// An entry once written, as the central directory names it.
struct Entry {
    name: Vec<u8>,
    flags: u16,
    crc: u32,
    compressed: u64,
    size: u64,
    offset: u64,
}

/// A zip being written to `out`, from its start.
pub(super) struct Zip<W: Write + Seek> {
    out: W,
    /// How many bytes are written.
    at: u64,
    entries: Vec<Entry>,
    /// The entries' modification time and date, in MS-DOS's form.
    time: u16,
    date: u16,
    /// Raw deflate at zlib's default level, as `ZIP_DEFLATED` writes it.
    deflater: DeflateEncoder<Vec<u8>>,
    limits: Limits,
}

/// `at` as MS-DOS keeps a time and a date: two-second steps, and years from 1980.
fn dos(at: Moment) -> (u16, u16) {
    let seconds = at.whole_seconds();
    let (year, month, day) = civil(seconds.div_euclid(86_400));
    let of_day = seconds.rem_euclid(86_400);
    let time = ((of_day / 3600) << 11) | ((of_day % 3600 / 60) << 5) | ((of_day % 60) / 2);
    let date = ((year.clamp(1980, 2107) - 1980) << 9) | (i64::from(month) << 5) | i64::from(day);
    (u16::try_from(time).unwrap_or(0), u16::try_from(date).unwrap_or(0))
}

/// An entry's bytes on their way: summed into its CRC-32 and counted, deflated, and what the deflater has made of
/// them so far written to the zip.
struct Through<'a, W: Write> {
    deflater: &'a mut DeflateEncoder<Vec<u8>>,
    out: &'a mut W,
    crc: Crc,
    size: u64,
}

impl<W: Write> Through<'_, W> {
    /// What the deflater has made, out to the zip.
    fn drain(&mut self) -> io::Result<()> {
        let made = self.deflater.get_mut();
        self.out.write_all(made)?;
        made.clear();
        Ok(())
    }
}

impl<W: Write> Write for Through<'_, W> {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        let n = self.deflater.write(buf)?;
        self.crc.update(&buf[..n]);
        self.size += n as u64;
        self.drain()?;
        Ok(n)
    }

    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

/// What `fill` writes, deflated into `out` by `deflater`, which is ready for the next entry after: its CRC-32 and
/// how many bytes it was.
fn deflated<W: Write, E: From<io::Error>>(
    deflater: &mut DeflateEncoder<Vec<u8>>,
    out: &mut W,
    fill: impl FnOnce(&mut dyn Write) -> Result<(), E>,
) -> Result<(u32, u64), E> {
    let mut through = Through { deflater, out, crc: Crc::new(), size: 0 };
    fill(&mut through)?;
    through.deflater.try_finish()?;
    through.drain()?;
    // A deflater made once and set going again for each entry, rather than one made for each: an archive of a
    // .eml file a message has as many entries as there are messages.
    through.deflater.reset(Vec::new())?;
    Ok((through.crc.sum(), through.size))
}

impl<W: Write + Seek> Zip<W> {
    /// A zip whose entries say they were made at `at`, taking ZIP64's fields past `limits`.
    pub(super) fn new(out: W, at: Moment, limits: Limits) -> Zip<W> {
        let (time, date) = dos(at);
        let deflater = DeflateEncoder::new(Vec::new(), Compression::default());
        Zip { out, at: 0, entries: Vec::new(), time, date, deflater, limits }
    }

    /// Whether a size or an offset is past what the 32-bit fields hold here.
    fn past(&self, value: u64) -> bool {
        value > self.limits.size
    }

    /// A 32-bit field's value: the value itself, or the mark that says the ZIP64 extra field holds it.
    fn field(&self, value: u64) -> u32 {
        if self.past(value) { IN_ZIP64 } else { u32::try_from(value).unwrap_or(IN_ZIP64) }
    }

    fn put(&mut self, bytes: &[u8]) -> io::Result<()> {
        self.out.write_all(bytes)?;
        self.at += bytes.len() as u64;
        Ok(())
    }

    /// An entry named `name`, whatever `fill` writes, deflated. `size` is how much that is, when it is known before
    /// it is written: an entry whose size isn't, or is near 2 GiB, keeps room in its header for sizes past that, as
    /// `zipfile` does (`force_zip64`, and `file_size * 1.05 > ZIP64_LIMIT`). What `fill` fails with ends the zip's
    /// writing.
    pub(super) fn entry<E: From<io::Error>>(
        &mut self,
        name: &str,
        size: Option<u64>,
        fill: impl FnOnce(&mut dyn Write) -> Result<(), E>,
    ) -> Result<(), E> {
        let large = size.is_none_or(|size| self.past(size.saturating_mul(105) / 100));
        let (name, flags) = if name.is_ascii() { (name.as_bytes(), 0) } else { (name.as_bytes(), UTF8_NAME) };
        let name = name.to_vec();
        let offset = self.at;
        let extra: u16 = if large { 20 } else { 0 };
        let mut header = Vec::with_capacity(LOCAL_HEADER as usize + name.len() + usize::from(extra));
        header.extend_from_slice(b"PK\x03\x04");
        header.extend_from_slice(&[VERSION, 0]);
        header.extend_from_slice(&flags.to_le_bytes());
        header.extend_from_slice(&DEFLATED.to_le_bytes());
        header.extend_from_slice(&self.time.to_le_bytes());
        header.extend_from_slice(&self.date.to_le_bytes());
        header.extend_from_slice(&[0; 12]); // the CRC-32 and both sizes, filled in once known
        header.extend_from_slice(&u16::try_from(name.len()).unwrap_or(u16::MAX).to_le_bytes());
        header.extend_from_slice(&extra.to_le_bytes());
        header.extend_from_slice(&name);
        if large {
            header.extend_from_slice(&1u16.to_le_bytes());
            header.extend_from_slice(&16u16.to_le_bytes());
            header.extend_from_slice(&[0; 16]);
        }
        self.put(&header)?;

        let (crc, size) = deflated(&mut self.deflater, &mut self.out, fill)?;
        let end = self.out.stream_position()?;
        let compressed = end - self.at;
        self.at = end;

        let wide = self.past(size) || self.past(compressed);
        if wide && !large {
            return Err(io::Error::other("an entry past 2 GiB that kept no room for its size").into());
        }
        // The header, filled in: the version needed, the CRC-32, both sizes, and the ZIP64 field's.
        let version = if wide { ZIP64_VERSION } else { VERSION };
        self.out.seek(SeekFrom::Start(offset + 4))?;
        self.out.write_all(&[version])?;
        self.out.seek(SeekFrom::Start(offset + 14))?;
        let mut sizes = Vec::with_capacity(12);
        sizes.extend_from_slice(&crc.to_le_bytes());
        let (field_compressed, field_size) =
            if wide { (IN_ZIP64, IN_ZIP64) } else { (self.field(compressed), self.field(size)) };
        sizes.extend_from_slice(&field_compressed.to_le_bytes());
        sizes.extend_from_slice(&field_size.to_le_bytes());
        self.out.write_all(&sizes)?;
        if large {
            self.out.seek(SeekFrom::Start(offset + LOCAL_HEADER + name.len() as u64 + 4))?;
            let mut wide = Vec::with_capacity(16);
            wide.extend_from_slice(&size.to_le_bytes());
            wide.extend_from_slice(&compressed.to_le_bytes());
            self.out.write_all(&wide)?;
        }
        self.out.seek(SeekFrom::Start(end))?;
        self.entries.push(Entry { name, flags, crc, compressed, size, offset });
        Ok(())
    }

    /// The central directory and the end records after the last entry: the zip, whole, and what it was written to.
    pub(super) fn finish(mut self) -> io::Result<W> {
        let start = self.at;
        let entries = std::mem::take(&mut self.entries);
        for entry in &entries {
            let mut wide: Vec<u64> = Vec::new();
            let sizes_wide = self.past(entry.size) || self.past(entry.compressed);
            if sizes_wide {
                wide.extend([entry.size, entry.compressed]);
            }
            if self.past(entry.offset) {
                wide.push(entry.offset);
            }
            let version = if wide.is_empty() { VERSION } else { ZIP64_VERSION };
            let mut extra = Vec::new();
            if !wide.is_empty() {
                extra.extend_from_slice(&1u16.to_le_bytes());
                extra.extend_from_slice(&u16::try_from(8 * wide.len()).unwrap_or(24).to_le_bytes());
                for value in &wide {
                    extra.extend_from_slice(&value.to_le_bytes());
                }
            }
            let (compressed, size) =
                if sizes_wide { (IN_ZIP64, IN_ZIP64) } else { (self.field(entry.compressed), self.field(entry.size)) };
            let mut record = Vec::with_capacity(46 + entry.name.len() + extra.len());
            record.extend_from_slice(b"PK\x01\x02");
            record.extend_from_slice(&[version, UNIX, version, 0]);
            record.extend_from_slice(&entry.flags.to_le_bytes());
            record.extend_from_slice(&DEFLATED.to_le_bytes());
            record.extend_from_slice(&self.time.to_le_bytes());
            record.extend_from_slice(&self.date.to_le_bytes());
            record.extend_from_slice(&entry.crc.to_le_bytes());
            record.extend_from_slice(&compressed.to_le_bytes());
            record.extend_from_slice(&size.to_le_bytes());
            record.extend_from_slice(&u16::try_from(entry.name.len()).unwrap_or(u16::MAX).to_le_bytes());
            record.extend_from_slice(&u16::try_from(extra.len()).unwrap_or(0).to_le_bytes());
            record.extend_from_slice(&[0; 6]); // no comment, disk 0, no internal attributes
            record.extend_from_slice(&OWNER_ONLY.to_le_bytes());
            record.extend_from_slice(&self.field(entry.offset).to_le_bytes());
            record.extend_from_slice(&entry.name);
            record.extend_from_slice(&extra);
            self.put(&record)?;
        }
        let (count, size, offset) = (entries.len(), self.at - start, start);
        if count > self.limits.count || self.past(offset) || self.past(size) {
            let at = self.at;
            let mut record = Vec::with_capacity(76);
            record.extend_from_slice(b"PK\x06\x06");
            record.extend_from_slice(&44u64.to_le_bytes());
            record.extend_from_slice(&u16::from(ZIP64_VERSION).to_le_bytes());
            record.extend_from_slice(&u16::from(ZIP64_VERSION).to_le_bytes());
            record.extend_from_slice(&[0; 8]); // this disk, and the directory's
            record.extend_from_slice(&(count as u64).to_le_bytes());
            record.extend_from_slice(&(count as u64).to_le_bytes());
            record.extend_from_slice(&size.to_le_bytes());
            record.extend_from_slice(&offset.to_le_bytes());
            record.extend_from_slice(b"PK\x06\x07");
            record.extend_from_slice(&0u32.to_le_bytes());
            record.extend_from_slice(&at.to_le_bytes());
            record.extend_from_slice(&1u32.to_le_bytes());
            self.put(&record)?;
        }
        // Each as itself, or as much as its field holds, as `zipfile` writes them: a reader takes the ZIP64 record's.
        let narrow = |value: u64| u32::try_from(value).unwrap_or(IN_ZIP64);
        let count = u16::try_from(count).unwrap_or(u16::MAX);
        let mut end = Vec::with_capacity(22);
        end.extend_from_slice(b"PK\x05\x06");
        end.extend_from_slice(&[0; 4]); // this disk, and the directory's
        end.extend_from_slice(&count.to_le_bytes());
        end.extend_from_slice(&count.to_le_bytes());
        end.extend_from_slice(&narrow(size).to_le_bytes());
        end.extend_from_slice(&narrow(offset).to_le_bytes());
        end.extend_from_slice(&0u16.to_le_bytes());
        self.put(&end)?;
        self.out.flush()?;
        Ok(self.out)
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)]

    use std::io::{Cursor, Read};

    use flate2::read::DeflateDecoder;

    use super::*;

    fn u16_at(b: &[u8], at: usize) -> u16 {
        u16::from_le_bytes([b[at], b[at + 1]])
    }

    fn u32_at(b: &[u8], at: usize) -> u32 {
        u32::from_le_bytes(b[at..at + 4].try_into().unwrap())
    }

    fn u64_at(b: &[u8], at: usize) -> u64 {
        u64::from_le_bytes(b[at..at + 8].try_into().unwrap())
    }

    /// An entry as a reader finds it: its name, its bytes, and whether its sizes and its offset came from ZIP64's.
    #[derive(Debug, PartialEq)]
    struct Read_ {
        name: String,
        bytes: Vec<u8>,
        wide: bool,
    }

    /// Each entry of a zip as a reader finds it, from the end records (ZIP64's, when its locator is there) through
    /// the central directory to its local header, its bytes inflated once its CRC-32 and sizes agree with them.
    fn read_back(zip: &[u8]) -> (Vec<Read_>, bool) {
        let end = zip.len() - 22;
        assert_eq!(&zip[end..end + 4], b"PK\x05\x06");
        let (mut count, mut start) = (u64::from(u16_at(zip, end + 10)), u64::from(u32_at(zip, end + 16)));
        let records64 = end >= 20 && &zip[end - 20..end - 16] == b"PK\x06\x07";
        if records64 {
            let record = usize::try_from(u64_at(zip, end - 20 + 8)).unwrap();
            assert_eq!(&zip[record..record + 4], b"PK\x06\x06");
            (count, start) = (u64_at(zip, record + 32), u64_at(zip, record + 48));
        }
        let mut at = usize::try_from(start).unwrap();
        let mut entries = Vec::new();
        for _ in 0..count {
            assert_eq!(&zip[at..at + 4], b"PK\x01\x02");
            let crc = u32_at(zip, at + 16);
            let (mut compressed, mut size) = (u64::from(u32_at(zip, at + 20)), u64::from(u32_at(zip, at + 24)));
            let (name_len, extra_len) = (usize::from(u16_at(zip, at + 28)), usize::from(u16_at(zip, at + 30)));
            let mut offset = u64::from(u32_at(zip, at + 42));
            let name = String::from_utf8(zip[at + 46..at + 46 + name_len].to_vec()).unwrap();
            let extra = &zip[at + 46 + name_len..at + 46 + name_len + extra_len];
            let mut wide = (4..extra.len()).step_by(8).map(|i| u64_at(extra, i));
            let mut from_wide = false;
            if size == u64::from(IN_ZIP64) {
                (size, compressed) = (wide.next().unwrap(), wide.next().unwrap());
                from_wide = true;
            }
            if offset == u64::from(IN_ZIP64) {
                offset = wide.next().unwrap();
                from_wide = true;
            }
            assert_eq!(zip[at + 6], if extra_len > 0 { ZIP64_VERSION } else { VERSION }, "{name}");
            let local = usize::try_from(offset).unwrap();
            assert_eq!(&zip[local..local + 4], b"PK\x03\x04");
            assert_eq!(u32_at(zip, local + 14), crc);
            assert_eq!(&zip[local + 30..local + 30 + name_len], name.as_bytes());
            let data = local + 30 + name_len + usize::from(u16_at(zip, local + 28));
            let deflated = &zip[data..data + usize::try_from(compressed).unwrap()];
            let mut bytes = Vec::new();
            DeflateDecoder::new(deflated).read_to_end(&mut bytes).unwrap();
            assert_eq!(bytes.len() as u64, size, "{name}");
            let mut sum = Crc::new();
            sum.update(&bytes);
            assert_eq!(sum.sum(), crc, "{name}");
            entries.push(Read_ { name, bytes, wide: from_wide });
            at += 46 + name_len + extra_len + usize::from(u16_at(zip, at + 32));
        }
        (entries, records64)
    }

    /// Where each entry's local header starts, in order.
    fn local_headers(zip: &[u8]) -> Vec<usize> {
        let mut at = 0;
        let mut found = Vec::new();
        while &zip[at..at + 4] == b"PK\x03\x04" {
            found.push(at);
            let mut compressed = usize::try_from(u32_at(zip, at + 18)).unwrap();
            let (name_len, extra_len) = (usize::from(u16_at(zip, at + 26)), usize::from(u16_at(zip, at + 28)));
            if compressed == IN_ZIP64 as usize {
                compressed = usize::try_from(u64_at(zip, at + 30 + name_len + 12)).unwrap();
            }
            at += 30 + name_len + extra_len + compressed;
        }
        found
    }

    const NOW: Moment = Moment::from_micros(1_793_620_800_000_000);

    fn read(name: &str, bytes: &[u8]) -> Read_ {
        Read_ { name: name.to_owned(), bytes: bytes.to_vec(), wide: false }
    }

    #[test]
    fn each_entry_reads_back_as_it_was_given() {
        let long = "mail ".repeat(20_000);
        let mut zip = Zip::new(Cursor::new(Vec::new()), NOW, PYTHONS);
        zip.entry("ada@example.test/INBOX/one-1.eml", Some(5), |out| out.write_all(b"hello")).unwrap();
        zip.entry("ada@example.test/INBOX.mbox", None, |out| {
            for piece in long.as_bytes().chunks(777) {
                out.write_all(piece)?;
            }
            Ok::<(), io::Error>(())
        })
        .unwrap();
        zip.entry("ada@example.test/Entw\u{fc}rfe/empty-2.eml", Some(0), |_| Ok::<(), io::Error>(())).unwrap();
        let zip = zip.finish().unwrap().into_inner();
        let (entries, records64) = read_back(&zip);
        assert!(!records64);
        assert_eq!(
            entries,
            [
                read("ada@example.test/INBOX/one-1.eml", b"hello"),
                read("ada@example.test/INBOX.mbox", long.as_bytes()),
                read("ada@example.test/Entw\u{fc}rfe/empty-2.eml", b""),
            ]
        );
        let headers = local_headers(&zip);
        assert_eq!(headers.len(), 3);
        // One whose size wasn't known kept room for a ZIP64 one; a name that isn't ASCII says it is UTF-8.
        assert_eq!(u16_at(&zip, headers[0] + 28), 0);
        assert_eq!(u16_at(&zip, headers[1] + 28), 20);
        assert_eq!(u16_at(&zip, headers[1] + 30 + "ada@example.test/INBOX.mbox".len()), 1);
        assert_eq!(u16_at(&zip, headers[2] + 6) & UTF8_NAME, UTF8_NAME);
        assert_eq!(u16_at(&zip, headers[0] + 6) & UTF8_NAME, 0);
        // Each made on Unix, its owner's alone.
        let directory = zip.windows(4).position(|w| w == b"PK\x01\x02").unwrap();
        assert_eq!(zip[directory + 5], UNIX);
        assert_eq!(u32_at(&zip, directory + 38), 0o100_600 << 16);
    }

    #[test]
    fn what_fill_fails_with_ends_the_writing() {
        let mut zip = Zip::new(Cursor::new(Vec::new()), NOW, PYTHONS);
        let failed = zip.entry("x.eml", None, |_| Err::<(), io::Error>(io::Error::other("refused")));
        assert!(failed.is_err());
    }

    /// Limits small enough that a few bytes are past them, so ZIP64's fields and records are written for a test.
    const SMALL: Limits = Limits { size: 100, count: 3 };

    #[test]
    fn sizes_and_offsets_past_the_old_fields_go_in_zip64s() {
        // Bytes that don't deflate to less, so the next entry starts past the limit too.
        let big: Vec<u8> = (0u32..150).map(|n| (n.wrapping_mul(2_654_435_761) >> 13) as u8).collect();
        let mut zip = Zip::new(Cursor::new(Vec::new()), NOW, SMALL);
        zip.entry("big.mbox", None, |out| out.write_all(&big)).unwrap();
        zip.entry("late.eml", Some(1), |out| out.write_all(b"y")).unwrap();
        let zip = zip.finish().unwrap().into_inner();
        let (entries, records64) = read_back(&zip);
        // The first is past in size, which its header says too; the second is past in where it starts.
        assert_eq!(
            entries,
            [
                Read_ { name: "big.mbox".to_owned(), bytes: big, wide: true },
                Read_ { name: "late.eml".to_owned(), bytes: b"y".to_vec(), wide: true },
            ]
        );
        assert_eq!(zip[4], ZIP64_VERSION);
        assert_eq!(u32_at(&zip, 22), IN_ZIP64);
        assert_eq!(u64_at(&zip, 30 + "big.mbox".len() + 4), 150);
        // The directory starts past the limit, so the end records are ZIP64's too.
        assert!(records64);
    }

    #[test]
    fn an_entry_past_the_limit_that_kept_no_room_is_refused() {
        let mut zip = Zip::new(Cursor::new(Vec::new()), NOW, SMALL);
        let refused = zip.entry("said-small.eml", Some(10), |out| out.write_all(&[b'z'; 150]));
        assert!(refused.is_err());
    }

    #[test]
    fn more_entries_than_the_old_fields_count_take_the_zip64_end_records() {
        let mut zip = Zip::new(Cursor::new(Vec::new()), NOW, Limits { size: PYTHONS.size, count: 3 });
        for n in 0..4 {
            zip.entry(&format!("{n}.eml"), Some(1), |out| out.write_all(b"x")).unwrap();
        }
        let zip = zip.finish().unwrap().into_inner();
        let (entries, records64) = read_back(&zip);
        assert!(records64);
        assert_eq!(entries.iter().map(|e| e.name.as_str()).collect::<Vec<_>>(), ["0.eml", "1.eml", "2.eml", "3.eml"]);
        assert!(entries.iter().all(|e| e.bytes == b"x" && !e.wide));
    }

    #[test]
    fn the_time_is_written_as_ms_dos_keeps_one() {
        // 2026-11-02 12:00:00
        assert_eq!(dos(NOW), (12 << 11, (46 << 9) | (11 << 5) | 2));
    }
}
