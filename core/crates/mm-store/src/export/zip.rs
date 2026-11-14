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
