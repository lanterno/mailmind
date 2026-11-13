//! The person's zone: offsets read from a zone file and its rule, and a wall-clock time placed as Python placed it.

use mm_kernel::seams::Moment;
use mm_kernel::zone::{Rule, Tzif, Utc, Zone, date, place};
use mm_kernel::{relative, zone};

/// A version 2 zone file: an empty version 1 part, then `transitions` (moment, index into `offsets`) and `footer`.
fn tzif(offsets: &[i32], transitions: &[(i64, u8)], footer: &str) -> Vec<u8> {
    fn header(out: &mut Vec<u8>, timecnt: u32, typecnt: u32, charcnt: u32) {
        out.extend_from_slice(b"TZif2");
        out.extend_from_slice(&[0; 15]);
        for count in [0, 0, 0, timecnt, typecnt, charcnt] {
            out.extend_from_slice(&count.to_be_bytes());
        }
    }
    let mut out = Vec::new();
    header(&mut out, 0, 1, 1);
    out.extend_from_slice(&[0, 0, 0, 0, 0, 0, 0]);
    header(&mut out, transitions.len() as u32, offsets.len() as u32, 4);
    for (at, _) in transitions {
        out.extend_from_slice(&at.to_be_bytes());
    }
    for (_, index) in transitions {
        out.push(*index);
    }
    for offset in offsets {
        out.extend_from_slice(&offset.to_be_bytes());
        out.extend_from_slice(&[0, 0]);
    }
    out.extend_from_slice(b"ABC\0");
    out.push(b'\n');
    out.extend_from_slice(footer.as_bytes());
    out.push(b'\n');
    out
}

#[test]
fn a_rule_gives_central_europes_offsets_either_side_of_its_changes() {
    let rule = Rule::parse("CET-1CEST,M3.5.0,M10.5.0/3").expect("a rule");
    // What Python's zoneinfo gives Europe/Berlin at each.
    for (at, offset) in [
        (1_700_000_000, 3600),
        (1_711_846_799, 3600),
        (1_711_846_800, 7200),
        (1_729_990_799, 7200),
        (1_729_990_800, 3600),
        (2_000_000_000, 7200),
        (4_102_444_800, 3600),
    ] {
        assert_eq!(rule.offset_at(at), offset, "at {at}");
    }
}

#[test]
fn a_rule_reads_new_yorks_and_a_fixed_offset_and_refuses_what_isnt_one() {
    let rule = Rule::parse("EST5EDT,M3.2.0,M11.1.0").expect("a rule");
    assert_eq!(rule.offset_at(1_700_000_000), -18_000);
    assert_eq!(rule.offset_at(4_118_000_000), -14_400);
    assert_eq!(Rule::parse("<+0530>-5:30").expect("a rule").offset_at(0), 19_800);
    for bad in ["", "AB", "CET-1CEST,M13.1.0,M10.5.0", "CET"] {
        assert!(Rule::parse(bad).is_none(), "{bad:?}");
    }
}

#[test]
fn a_zone_file_gives_its_transitions_then_its_footer() {
    // 1990: an offset of an hour; from 2000, two; after, the rule's.
    let bytes = tzif(&[3600, 7200], &[(631_152_000, 0), (946_684_800, 1)], "CET-1CEST,M3.5.0,M10.5.0/3");
    let zone = Tzif::parse(&bytes).expect("a zone file");
    assert_eq!(zone.offset_at(0), 3600, "before the first transition: the first type's");
    assert_eq!(zone.offset_at(700_000_000), 3600);
    assert_eq!(zone.offset_at(1_700_000_000), 3600, "past the last, the footer's winter");
    assert_eq!(zone.offset_at(2_000_000_000), 7200, "and its summer");
    assert!(Tzif::parse(b"not a zone file").is_none());
    assert!(Tzif::parse(&bytes[..60]).is_none());
}

#[test]
fn a_wall_clock_time_is_placed_as_python_placed_it() {
    let berlin = Tzif::parse(&tzif(&[3600], &[], "CET-1CEST,M3.5.0,M10.5.0/3")).expect("a zone file");
    let wall = |y, m, d, h: i64, mi: i64| mm_kernel::when::days_from_civil(y, m, d) * 86_400 + h * 3600 + mi * 60;
    // An ordinary winter's noon.
    assert_eq!(place(&berlin, wall(2026, 11, 2, 12, 0)), 1_793_617_200);
    // 02:30 on 2024-03-31 never was: Python gives the later reading, 03:30 summer time.
    assert_eq!(place(&berlin, wall(2024, 3, 31, 2, 30)), 1_711_848_600);
    // 02:30 on 2024-10-27 was twice: fold 0 is the first, in summer time.
    assert_eq!(place(&berlin, wall(2024, 10, 27, 2, 30)), 1_729_989_000);
    assert_eq!(place(&Utc, wall(2026, 11, 2, 12, 0)), 1_793_620_800);
    assert_eq!(date(&berlin, 1_793_660_400), (2026, 11, 3), "midnight in Berlin is the next day");
    assert_eq!(zone::local(&berlin, 0), 3600);
}

#[test]
fn rel_time_says_how_long_ago_as_a_list_does() {
    let now = Moment::from_micros(1_793_620_800_000_000); // 2026-11-02T12:00:00Z
    let said = |iso: &str| relative::rel_time(Some(iso), now);
    assert_eq!(said("2026-11-02T11:30:00+00:00"), "just now");
    assert_eq!(said("2026-11-02T07:00:00+00:00"), "5h ago");
    assert_eq!(said("2026-11-02T13:00:00+01:00"), "just now", "an offset is counted");
    assert_eq!(said("2026-11-02T07:00:00"), "5h ago", "no zone is UTC's");
    assert_eq!(said("2026-11-01T11:00:00+00:00"), "yesterday");
    assert_eq!(said("2026-10-29T12:00:00+00:00"), "4d ago");
    assert_eq!(said("2026-10-19T12:00:00+00:00"), "2w ago");
    assert_eq!(said("2026-09-05T12:00:00+00:00"), "Sep 05");
    assert_eq!(said("2026-11-03T12:00:00+00:00"), "-1d ago", "a day ahead is days of -1, as Python said it");
    assert_eq!(said("not a time at all"), "not a time");
    assert_eq!(relative::rel_time(Some(""), now), "");
    assert_eq!(relative::rel_time(None, now), "");
}

#[test]
fn ago_is_a_cards_shortest() {
    let now = Moment::from_micros(1_000_000_000_000_000);
    assert_eq!(relative::ago(None, now), "a while");
    assert_eq!(relative::ago(Some(0), now), "a while");
    assert_eq!(relative::ago(Some(1_000_000_000 - 720), now), "12m");
    assert_eq!(relative::ago(Some(1_000_000_000 - 5 * 3600), now), "5h");
    assert_eq!(relative::ago(Some(1_000_000_000 - 3 * 86_400), now), "3d");
    assert_eq!(relative::ago(Some(1_000_000_100), now), "0m");
}
