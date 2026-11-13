//! Threading, as the Python app decided it: the expected values here are what `mailmind.mailbox.domain.threading`
//! returned for the same copies before the core decided, but where a test says the core fixed a bug since. The
//! Python suite holds the two to the same answers over many more (tests/unit/mailbox/test_threading_in_rust.py).

use mm_mailbox::threading::{Conversation, Placement, REFERENCES_MAX, StoredCopy, Threader};

const A: &str = "ada@example.test";

fn copy(pk: i64, message_id: &str) -> StoredCopy {
    StoredCopy { pk, message_id: Some(message_id.to_owned()), ..StoredCopy::default() }
}

fn replying(mut m: StoredCopy, in_reply_to: &str) -> StoredCopy {
    m.in_reply_to = Some(in_reply_to.to_owned());
    m
}

fn referring(mut m: StoredCopy, refs: &str) -> StoredCopy {
    m.refs = Some(refs.to_owned());
    m
}

fn in_gmail(mut m: StoredCopy, thrid: &str) -> StoredCopy {
    m.gmail_thread = Some(thrid.to_owned());
    m
}

fn holding(mut m: StoredCopy, thread_id: &str) -> StoredCopy {
    m.thread_id = Some(thread_id.to_owned());
    m
}

fn at(mut m: StoredCopy, folder: &str, ts: Option<i64>) -> StoredCopy {
    m.folder = folder.to_owned();
    m.ts = ts;
    m
}

fn touching(pairs: &[(&str, &[&str])]) -> Vec<(String, Vec<String>)> {
    pairs.iter().map(|(name, ids)| (name.to_string(), ids.iter().map(|id| id.to_string()).collect())).collect()
}

fn placement(given: &[(i64, &str)], merged: &[(&str, &str)]) -> Placement {
    Placement {
        given: given.iter().map(|(pk, tid)| (*pk, tid.to_string())).collect(),
        merged: merged.iter().map(|(gone, kept)| (gone.to_string(), kept.to_string())).collect(),
    }
}

fn place(arrivals: &[StoredCopy], touched: &[(&str, &[&str])], members: &[StoredCopy]) -> Placement {
    let threader = Threader::new(A);
    let touching = touching(touched);
    threader.place(&threader.conversations(arrivals, &touching), members)
}

fn gm(thrid: &str) -> String {
    format!("gm:{A}:{thrid}")
}

// -- what a copy names --------------------------------------------------------------------------------------------

#[test]
fn a_copy_names_its_own_id_what_it_follows_and_its_gmail_thread() {
    let m = StoredCopy {
        pk: 1,
        message_id: Some(" <a@example.test>\u{1c}".into()),
        gmail_thread: Some(" 17 \u{3000}".into()),
        in_reply_to: Some("<p@example.test>,<q@example.test><no-at>".into()),
        refs: Some("<two words@x> <r@example.test>".into()),
        ..StoredCopy::default()
    };
    let names: Vec<_> = m.names().into_iter().collect();
    assert_eq!(
        names,
        ["<a@example.test>", "<no-at>", "<p@example.test>", "<q@example.test>", "<r@example.test>", "gm:17"]
    );
    assert_eq!(m.thrid(), "17");
}

#[test]
fn only_a_usable_message_id_is_a_copys_own() {
    assert_eq!(copy(1, "<a@example.test>\n").key(), "<a@example.test>");
    assert_eq!(copy(-3, "<a@b@c>").key(), "<a@b@c>");
    assert_eq!(copy(-3, "<a b@c>").key(), "pk:-3");
    assert_eq!(copy(-3, "<\0@c>").key(), "<\0@c>");
    assert_eq!(copy(4, "not-an-id").key(), "pk:4");
    assert!(copy(1, "not-an-id").names().is_empty());
    assert_eq!(referring(copy(1, "<no-at-sign>"), "<m@example.test>").names().len(), 1);
}

#[test]
fn every_character_python_splits_at_ends_an_id() {
    let every = "\t\n\u{b}\u{c}\r\u{1c}\u{1d}\u{1e}\u{1f} \u{85}\u{a0}\u{1680}\u{2000}\u{200a}\u{2028}\u{2029}\u{202f}\u{205f}\u{3000}";
    for c in every.chars() {
        let m = replying(copy(1, &format!("<a{c}b@example.test>")), &format!("<p{c}@example.test><q@example.test>"));
        assert_eq!(m.key(), "pk:1", "{c:?}");
        assert_eq!(m.references(), ["<q@example.test>"], "{c:?}");
        assert_eq!(in_gmail(copy(1, ""), &format!("{c}1781{c}")).thrid(), "1781", "{c:?}");
    }
    // A zero-width space is not whitespace to either.
    assert_eq!(copy(1, "<a\u{200b}b@example.test>").key(), "<a\u{200b}b@example.test>");
}

#[test]
fn a_forged_references_header_counts_for_its_nearest_ids() {
    let refs: Vec<String> = (0..REFERENCES_MAX + 5).map(|n| format!("<r{n:04}@example.test>")).collect();
    let m = replying(referring(copy(1, "<a@example.test>"), &refs.join(" ")), "<p@example.test>");
    let references = m.references();
    assert_eq!(references.len(), REFERENCES_MAX + 1);
    assert_eq!(references[0], "<p@example.test>");
    assert_eq!(references[1], "<r0005@example.test>");
    assert_eq!(m.names().len(), REFERENCES_MAX + 2);
}

// -- placing what a sync stored -----------------------------------------------------------------------------------

#[test]
fn new_mail_that_touches_nothing_mints_from_the_smallest_id_it_names() {
    let placed = place(&[replying(copy(1, "<r@example.test>"), "<m@example.test>")], &[], &[]);
    assert_eq!(placed, placement(&[(1, "rf:ada@example.test:850a48d02e8a257f")], &[]));
}

#[test]
fn a_copy_with_no_usable_id_mints_from_its_pk() {
    assert_eq!(place(&[copy(7, "")], &[], &[]), placement(&[(7, "rf:ada@example.test:00fb3bcf6d7f724d")], &[]));
}

#[test]
fn a_root_is_hashed_as_utf_8_however_long() {
    assert_eq!(
        place(&[copy(1, "<café@exämple.test>")], &[], &[]),
        placement(&[(1, "rf:ada@example.test:d2ddaee8e94b34d1")], &[])
    );
    let long = format!("<{}@example.test>", "y".repeat(200));
    assert_eq!(place(&[copy(1, &long)], &[], &[]), placement(&[(1, "rf:ada@example.test:8a3da0efbd5e084e")], &[]));
}

#[test]
fn a_reply_takes_the_thread_it_touches_and_merges_nothing() {
    let reply = replying(copy(1, "<r@example.test>"), "<m@example.test>");
    assert_eq!(place(&[reply], &[("<m@example.test>", &["rf:x"])], &[]), placement(&[(1, "rf:x")], &[]));
}

#[test]
fn touching_two_threads_merges_them_into_the_smaller_id() {
    let joined = referring(copy(1, "<j@example.test>"), "<p@example.test> <q@example.test>");
    let placed = place(&[joined], &[("<p@example.test>", &["rf:b"]), ("<q@example.test>", &["rf:a"])], &[]);
    assert_eq!(placed, placement(&[(1, "rf:a")], &[("rf:b", "rf:a")]));
}

#[test]
fn conversations_that_meet_one_thread_are_one_merged_into_the_smallest_id() {
    // Python placed these apart, merging c into b and b into a: a chain whose outcome hung on the order a store
    // applied it in, and which gave the new mail b, which was gone. Fixed here, so Python never answered this.
    let arrivals = [copy(1, "<n@example.test>"), holding(copy(2, "<o@example.test>"), "rf:b")];
    let placed = place(&arrivals, &[("<n@example.test>", &["rf:b", "rf:c"]), ("<o@example.test>", &["rf:a"])], &[]);
    assert_eq!(placed, placement(&[(1, "rf:a"), (2, "rf:a")], &[("rf:b", "rf:a"), ("rf:c", "rf:a")]));
}

#[test]
fn an_arrival_meets_the_conversation_holding_its_gmail_thread() {
    // Python placed these apart too: the first arrival took gm:1781, which the second's conversation absorbed into
    // gm:1799. Fixed here, so Python never answered this.
    let threader = Threader::new(A);
    let arrivals = [
        at(replying(in_gmail(copy(1, "<n@example.test>"), "1781"), "<p@example.test>"), "", Some(300)),
        at(holding(replying(copy(2, ""), "<q@example.test>"), &gm("1781")), "", Some(250)),
    ];
    let touching = touching(&[("<p@example.test>", &["rf:b"]), ("<q@example.test>", &[&gm("1799")])]);
    let conversations = threader.conversations(&arrivals, &touching);
    assert_eq!(conversations.len(), 1);
    assert_eq!(threader.contested(&conversations).into_iter().collect::<Vec<_>>(), [gm("1781"), gm("1799")]);
    let members = [
        at(holding(in_gmail(copy(3, "<g1@example.test>"), "1781"), &gm("1781")), "", Some(100)),
        at(holding(in_gmail(copy(4, "<g2@example.test>"), "1799"), &gm("1799")), "", Some(200)),
    ];
    assert_eq!(
        threader.place(&conversations, &members),
        placement(&[(1, &gm("1799")), (2, &gm("1799"))], &[(&gm("1781"), &gm("1799")), ("rf:b", &gm("1799"))])
    );
}

#[test]
fn new_mail_that_mints_an_id_another_conversation_absorbs_goes_where_it_goes() {
    // The first mints from <m@example.test>, as the thread the second still holds once did. Python gave the first
    // the absorbed id. Fixed here, so Python never answered this.
    let minted = "rf:ada@example.test:850a48d02e8a257f";
    let arrivals =
        [replying(copy(1, "<r@example.test>"), "<m@example.test>"), holding(copy(2, "<o@example.test>"), minted)];
    let placed = place(&arrivals, &[("<o@example.test>", &["rf:a"])], &[]);
    assert_eq!(placed, placement(&[(1, "rf:a"), (2, "rf:a")], &[(minted, "rf:a")]));
}

#[test]
fn a_thread_that_gains_a_gmail_id_takes_it() {
    let arrival = replying(in_gmail(copy(1, "<q@example.test>"), "1781"), "<p@example.test>");
    let placed = place(&[arrival], &[("<p@example.test>", &["rf:a"])], &[]);
    assert_eq!(placed, placement(&[(1, &gm("1781"))], &[("rf:a", &gm("1781"))]));
}

#[test]
fn of_two_gmail_threads_the_one_whose_first_copy_comes_later_survives() {
    // In store order the threads come 1781, 1799, 1781: 1799 is the last to appear, though a 1781 copy is last.
    let threader = Threader::new(A);
    let arrivals = [at(referring(copy(9, "<j@example.test>"), "<p@example.test> <q@example.test>"), "", Some(300))];
    let touching = touching(&[("<p@example.test>", &[&gm("1781")]), ("<q@example.test>", &[&gm("1799")])]);
    let conversations = threader.conversations(&arrivals, &touching);
    assert_eq!(threader.contested(&conversations).into_iter().collect::<Vec<_>>(), [gm("1781"), gm("1799")]);
    let members = [
        at(holding(in_gmail(copy(1, "<p@example.test>"), "1781"), &gm("1781")), "", Some(100)),
        at(holding(in_gmail(copy(2, "<q@example.test>"), "1799"), &gm("1799")), "", Some(200)),
        at(holding(in_gmail(copy(3, "<p2@example.test>"), "1781"), &gm("1781")), "", Some(300)),
    ];
    let placed = threader.place(&conversations, &members);
    assert_eq!(placed, placement(&[(9, &gm("1799"))], &[(&gm("1781"), &gm("1799"))]));
}

#[test]
fn a_copy_with_no_date_comes_before_one_dated_0() {
    let arrivals = [referring(copy(9, "<j@example.test>"), "<p@example.test> <q@example.test>")];
    let members = [
        at(holding(in_gmail(copy(1, "<p@example.test>"), "1781"), &gm("1781")), "INBOX", Some(0)),
        at(holding(in_gmail(copy(2, "<q@example.test>"), "1799"), &gm("1799")), "INBOX", None),
    ];
    let placed =
        place(&arrivals, &[("<p@example.test>", &[&gm("1781")]), ("<q@example.test>", &[&gm("1799")])], &members);
    assert_eq!(placed, placement(&[(9, &gm("1781"))], &[(&gm("1799"), &gm("1781"))]));
}

#[test]
fn one_gmail_id_is_not_contested() {
    let threader = Threader::new(A);
    let arrivals = [replying(in_gmail(copy(9, "<j@example.test>"), "1781"), "<p@example.test>")];
    let touching = touching(&[("<p@example.test>", &[&gm("1781"), "rf:a"])]);
    assert!(threader.contested(&threader.conversations(&arrivals, &touching)).is_empty());
}

#[test]
fn a_pk_placed_twice_keeps_its_first_place_and_its_last_id() {
    let arrivals = [
        holding(copy(5, "<a@example.test>"), "rf:z"),
        copy(6, "<b@example.test>"),
        holding(copy(5, "<b@example.test>"), "rf:y"),
    ];
    assert_eq!(place(&arrivals, &[], &[]), placement(&[(5, "rf:y"), (6, "rf:y")], &[]));
}

#[test]
fn conversations_come_in_the_order_their_first_copies_arrived() {
    let threader = Threader::new(A);
    let arrivals = [
        holding(copy(1, "<b@example.test>"), "rf:q"),
        copy(2, "<z@example.test>"),
        holding(referring(copy(3, "<a@example.test>"), "<b@example.test>"), "rf:p"),
    ];
    let touching = touching(&[("<z@example.test>", &["rf:t"]), ("<a@example.test>", &["rf:p", "rf:r"])]);
    let found = threader.conversations(&arrivals, &touching);
    let expected = [
        Conversation {
            root: "\0rf:p".into(),
            arrivals: vec![&arrivals[0], &arrivals[2]],
            held: vec!["rf:q".into(), "rf:p".into(), "rf:r".into()],
        },
        Conversation { root: "\0rf:t".into(), arrivals: vec![&arrivals[1]], held: vec!["rf:t".into()] },
    ];
    assert_eq!(found, expected);
}

// -- the repair ---------------------------------------------------------------------------------------------------

#[test]
fn the_repair_trusts_a_gmail_id_only_while_a_gmail_copy_backs_it() {
    let apart = holding(referring(copy(2, ""), "<elsewhere@example.test>"), &gm("1781"));
    let elsewhere = "rf:ada@example.test:0fa338eee0eb4406";
    let placed =
        Threader::new(A).regroup(&[holding(in_gmail(copy(1, "<g@example.test>"), "1781"), &gm("1781")), apart.clone()]);
    assert_eq!(placed, placement(&[(1, &gm("1781")), (2, elsewhere)], &[]));
    assert_eq!(Threader::new(A).regroup(&[apart]), placement(&[(2, elsewhere)], &[(&gm("1781"), elsewhere)]));
}

#[test]
fn the_repair_goes_in_store_order_and_merges_only_what_no_copy_holds() {
    let rows = [
        at(holding(copy(4, "<b@example.test>"), "rf:old"), "INBOX", Some(5)),
        at(holding(copy(3, "<a@example.test>"), "rf:old"), "Archive", Some(9)),
        at(holding(replying(copy(2, "<c@example.test>"), "<a@example.test>"), "rf:other"), "INBOX", Some(1)),
        at(holding(copy(1, ""), "gm:x"), "INBOX", None),
    ];
    let minted = "rf:ada@example.test:8d2ac0a7364a6f3c";
    assert_eq!(
        Threader::new(A).regroup(&rows),
        placement(
            &[(3, "rf:old"), (2, "rf:old"), (1, minted), (4, "rf:old")],
            &[("gm:x", minted), ("rf:other", "rf:old")]
        )
    );
}
