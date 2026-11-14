//! Where an element the sanitizer drops with all it holds ends.
//!
//! Python's sanitizer skipped each to its own end tag, counting the same tag nested in it, and most end there in a
//! browser too: a raw text element can hold no other tag, and a `<template>` nests in itself. Where a browser's tree
//! builder ends one elsewhere, the text after it would be lost, so the sanitizer follows the builder as far as that
//! takes: a `<head>` ends at what a head can't hold, and a `<form>`, `<button>` or `<select>` ends, or goes on, as a
//! browser reads one of its own nested in it and its own end tag. It follows nothing outside the element, so an
//! ancestor's end tag never ends one, and where what a browser does turns on what it doesn't follow, it keeps to the
//! end tag, as Python did. Each reading here is Chromium's, with scripting off.

/// The tags a browser keeps in a `<head>`. It ends the head at any other start tag, at text that isn't space outside
/// a raw text element, and at `</body>`, `</html>` and `</br>` as at `</head>`, and reads what ended it as the body's.
/// A `<template>` holds anything, and nothing in it ends the head.
const HEAD_HOLDS: &[&str] = &[
    "base", "basefont", "bgsound", "link", "meta", "title", "noscript", "noframes", "style", "script", "template",
    "head", "html",
];

/// The HTML elements a browser's scope stops at, of those a dropped form, button or select keeps track of: an end
/// tag doesn't reach past one, and the same tag nested behind one is an element of its own. A `<select>` is one too,
/// but a select in a select closes it instead.
const SCOPE: &[&str] = &["template", "table", "object", "applet", "marquee"];

/// The elements of an `<svg>` or `<math>` a browser's scope stops at, in which a start tag is HTML's again, but in
/// an `<annotation-xml>`, which the sanitizer reads as foreign whatever its encoding.
#[rustfmt::skip]
const INTEGRATION_POINTS: &[(&str, &[&str])] = &[
    ("svg", &["foreignobject", "desc", "title"]),
    ("math", &["mi", "mo", "mn", "ms", "mtext", "annotation-xml"]),
];

/// The elements a browser closes by itself before it ends a form, or before another's end tag.
const IMPLIED: &[&str] = &["dd", "dt", "li", "optgroup", "option", "p", "rb", "rp", "rt", "rtc"];

/// Start tags that leave nothing open in a form: the void elements, and those a browser ignores in a body.
const OPENS_NOTHING: &[&str] = &[
    "area", "base", "basefont", "bgsound", "br", "col", "embed", "frame", "hr", "image", "img", "input", "keygen",
    "link", "meta", "param", "source", "track", "wbr", "html", "head", "body", "frameset", "caption", "colgroup",
    "tbody", "tfoot", "thead", "tr", "td", "th",
];

/// Start tags that close a `<p>` open in reach before a browser opens them.
#[rustfmt::skip]
const CLOSES_P: &[&str] = &[
    "address", "article", "aside", "blockquote", "center", "details", "dialog", "dir", "div", "dl", "fieldset",
    "figcaption", "figure", "footer", "header", "hgroup", "main", "menu", "nav", "ol", "p", "search", "section",
    "summary", "ul", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "listing", "plaintext", "xmp", "li", "dd", "dt",
];

/// The headings, any of which ends any other.
const HEADINGS: &[&str] = &["h1", "h2", "h3", "h4", "h5", "h6"];

/// The formatting elements: a browser opens one again after an end tag closes it with another, and moves what is
/// open after one its own end tag closes, neither of which the sanitizer follows.
const FORMATTING: &[&str] =
    &["a", "b", "big", "code", "em", "font", "i", "nobr", "s", "small", "strike", "strong", "tt", "u"];

/// The special elements a form can hold open, of those the sanitizer keeps track of: an end tag of one closes all
/// open after it, and another end tag doesn't reach past one.
#[rustfmt::skip]
const SPECIAL: &[&str] = &[
    "address", "article", "aside", "blockquote", "button", "center", "dd", "details", "dialog", "dir", "div", "dl",
    "dt", "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header",
    "hgroup", "iframe", "li", "listing", "main", "menu", "nav", "noembed", "noframes", "noscript", "ol", "p",
    "plaintext", "pre", "script", "search", "section", "style", "summary", "textarea", "title", "ul", "xmp",
];

/// How many elements open in a form, button or select the sanitizer keeps track of. Past that it keeps to the end
/// tag, as Python did.
const OPEN_LIMIT: usize = 256;

/// An element dropped with all it holds, and what of its content decides where it ends.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) enum Dropped {
    /// One that ends at its end tag, counting how deep the same tag is nested in it.
    Counted { tag: &'static str, depth: usize },
    /// A `<head>`, which ends where a browser ends it (see `HEAD_HOLDS`), and how deep in `<template>`s it is.
    Head { templates: usize },
    /// A `<button>` or `<select>`, what is open in it as far as that decides where it ends, and how deep Python's
    /// count of its tag stands.
    Control { tag: &'static str, inside: Vec<Inside>, depth: usize },
    /// A `<form>`: see `Form`.
    Form(Form),
}

/// A dropped `<form>`. A browser ignores a form nested in a form but in a `<template>`, and at the form's end tag
/// takes the form alone off what is open, or, where the end tag doesn't reach it, forgets it. Whatever is left open
/// in the form then ends where an element the sanitizer doesn't follow ends, maybe one of the form's ancestors, so
/// the sanitizer ends the form there only when nothing is left open, and otherwise keeps to the end tag, as Python
/// did.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct Form {
    /// What is open in the form, as far as that decides where it ends.
    inside: Vec<Inside>,
    /// How deep Python's count of `<form>` and `</form>` stands.
    depth: usize,
    /// Whether what is open in the form is past telling: an end tag a browser may read otherwise.
    lost: bool,
}

/// What is open in a dropped form, button or select, as far as it decides where that ends.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) enum Inside {
    /// An element in `SCOPE`, a `<select>` in a form or button, an `<svg>` or `<math>`, in which every tag is an
    /// element of theirs, or one of their `INTEGRATION_POINTS`.
    Wall(&'static str),
    /// The dropped element's own tag, nested in it.
    Again,
    /// The dropped element's own tag in an `<svg>` or `<math>`, an element of theirs.
    Foreign,
    /// Any other element open in a form, while nothing above is open in it.
    Open(String),
}

/// What a tag does to the element being dropped.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum Step {
    /// It is part of what is dropped.
    Within,
    /// It ends the element, and is dropped with it.
    Ends,
    /// It ends the element, and is read as what follows it.
    EndsBefore,
}

/// Where a browser finds the dropped element's own tag, reading back from what is open last.
enum Reach {
    /// The dropped element itself.
    Root,
    /// One of its own nested in it, at this place.
    Nested(usize),
    /// Neither: a wall stands between.
    Blocked,
}

impl Dropped {
    /// The element `tag` starts, dropped with all it holds.
    pub(super) fn new(tag: &'static str) -> Self {
        match tag {
            "head" => Self::Head { templates: 0 },
            "button" | "select" => Self::Control { tag, inside: Vec::new(), depth: 1 },
            "form" => Self::Form(Form { inside: Vec::new(), depth: 1, lost: false }),
            _ => Self::Counted { tag, depth: 1 },
        }
    }

    /// Whether the tokenizer is in foreign content: in an `<svg>` or `<math>`, no tag switches it to raw text, a tag
    /// written to close itself does, and a CDATA section is one.
    pub(super) fn foreign(&self) -> bool {
        match self {
            Self::Counted { tag, .. } => is_foreign(tag),
            Self::Head { .. } => false,
            Self::Control { inside, .. } | Self::Form(Form { inside, .. }) => in_foreign(inside),
        }
    }

    /// Whether this is a `<head>` outside any `<template>` in it: its style sheets are kept, and text that isn't
    /// space ends it.
    pub(super) fn is_bare_head(&self) -> bool {
        matches!(self, Self::Head { templates: 0 })
    }

    /// A start tag read in the element.
    pub(super) fn start(&mut self, name: &str, self_closing: bool) -> Step {
        // In foreign content a tag written to close itself is closed at once.
        let counts = !(self.foreign() && self_closing);
        let (tag, inside, depth) = match self {
            Self::Counted { tag, depth } => {
                if name == *tag && counts {
                    *depth += 1;
                }
                return Step::Within;
            }
            Self::Head { templates } => {
                if name == "template" {
                    *templates += 1;
                } else if *templates == 0 && !HEAD_HOLDS.contains(&name) {
                    return Step::EndsBefore;
                }
                return Step::Within;
            }
            Self::Control { tag, inside, depth } => {
                if name == *tag && counts {
                    *depth += 1;
                }
                let step = control_start(tag, inside, name, self_closing);
                if step != Step::Within {
                    return step;
                }
                (*tag, inside, *depth)
            }
            Self::Form(form) => {
                if name == "form" && counts {
                    form.depth += 1;
                }
                form.start(name, self_closing);
                ("form", &mut form.inside, form.depth)
            }
        };
        if inside.len() <= OPEN_LIMIT {
            return Step::Within;
        }
        if depth == 0 {
            return Step::Ends;
        }
        *self = Self::Counted { tag, depth };
        Step::Within
    }

    /// An end tag read in the element.
    pub(super) fn end(&mut self, name: &str) -> Step {
        match self {
            Self::Counted { tag, depth } => {
                if name == *tag {
                    *depth -= 1;
                    if *depth == 0 {
                        return Step::Ends;
                    }
                }
                Step::Within
            }
            Self::Head { templates } => match name {
                "template" if *templates > 0 => {
                    *templates -= 1;
                    Step::Within
                }
                "head" | "body" | "html" | "br" if *templates == 0 => Step::Ends,
                _ => Step::Within,
            },
            Self::Control { tag, inside, depth } => {
                if name == *tag {
                    *depth = depth.saturating_sub(1);
                }
                control_end(tag, inside, name)
            }
            Self::Form(form) => {
                if name == "form" {
                    form.depth = form.depth.saturating_sub(1);
                }
                match form.end(name) {
                    Next::Step(step) => step,
                    Next::Counted(depth) => {
                        *self = Self::Counted { tag: "form", depth };
                        Step::Within
                    }
                }
            }
        }
    }
}

/// What an end tag in a form leads to.
enum Next {
    /// The form goes on, or ends.
    Step(Step),
    /// Where the form ends is past telling: it goes on to the end tag that ends it in Python's count, this deep.
    Counted(usize),
}

impl Form {
    /// A start tag read in the form.
    fn start(&mut self, name: &str, self_closing: bool) {
        if open_foreign("form", &mut self.inside, name, self_closing) {
            return;
        }
        if matches!(name, "select" | "input") {
            select_or_input(&mut self.inside, name);
            return;
        }
        if let Some(wall) = SCOPE.iter().find(|wall| **wall == name) {
            self.inside.push(Inside::Wall(wall));
            return;
        }
        if name == "form" {
            // A browser ignores a form in a form, but in a <template>.
            if self.inside.contains(&Inside::Wall("template")) {
                self.inside.push(Inside::Again);
            }
            return;
        }
        if !self.inside.iter().all(|open| matches!(open, Inside::Open(_))) {
            return;
        }
        self.close_before(name);
        if !OPENS_NOTHING.contains(&name) {
            self.inside.push(Inside::Open(name.to_owned()));
        }
    }

    /// What a browser closes in the form before it opens `name`.
    fn close_before(&mut self, name: &str) {
        if matches!(name, "a" | "nobr") {
            // A browser closes the one open as it does at its end tag.
            if let Some(open) = self.last_open(|open| open == name) {
                self.close_through(open, true);
            }
            return;
        }
        if name == "button" {
            if let Some(open) = self.last_open(|open| open == "button") {
                self.close_through(open, false);
            }
            return;
        }
        if CLOSES_P.contains(&name)
            && let Some(p) = self.last_open(|open| open == "p")
            && self.last_open(|open| open == "button") < Some(p)
        {
            pop_implied(&mut self.inside, "p");
            self.close_through(p, false);
        }
        if HEADINGS.contains(&name)
            && matches!(self.inside.last(), Some(Inside::Open(open)) if HEADINGS.contains(&open.as_str()))
        {
            self.inside.pop();
        }
    }

    /// An end tag read in the form.
    fn end(&mut self, name: &str) -> Next {
        if close_foreign("form", &mut self.inside, name) {
            return Next::Step(Step::Within);
        }
        if name == "form" {
            return self.own_end();
        }
        if !close_wall(&mut self.inside, name) && self.inside.iter().all(|open| matches!(open, Inside::Open(_))) {
            self.close_open(name);
        }
        Next::Step(Step::Within)
    }

    /// An end tag of an element open in the form, while nothing above is open in it. A browser ignores one nothing
    /// open answers, or, for a `</p>`, opens a `<p>` and closes it at once.
    fn close_open(&mut self, name: &str) {
        pop_implied(&mut self.inside, name);
        let heading = HEADINGS.contains(&name);
        let Some(at) = self.last_open(|open| open == name || (heading && HEADINGS.contains(&open))) else {
            return;
        };
        if FORMATTING.contains(&name) {
            self.close_through(at, true);
            return;
        }
        // A </p> doesn't reach past a <button>, a </li> past a list, nor any other end tag past a special element.
        let stops: &[&str] = match name {
            "p" => &["button"],
            "li" => &["ol", "ul"],
            _ if SPECIAL.contains(&name) => &[],
            _ => SPECIAL,
        };
        if !self.inside[at + 1..]
            .iter()
            .any(|open| matches!(open, Inside::Open(open) if stops.contains(&open.as_str())))
        {
            self.close_through(at, false);
        }
    }

    /// Closes the element open at `at` and all after it. A browser opens the formatting elements among those again
    /// where it reads more, and closes a formatting element not open last by moving what is open after it, neither of
    /// which the sanitizer follows.
    fn close_through(&mut self, at: usize, formatting: bool) {
        let after = &self.inside[at + 1..];
        if (formatting && !after.is_empty())
            || after.iter().any(|open| matches!(open, Inside::Open(open) if FORMATTING.contains(&open.as_str())))
        {
            self.lost = true;
        } else {
            self.inside.truncate(at);
        }
    }

    /// The form's own end tag.
    fn own_end(&mut self) -> Next {
        if self.inside.contains(&Inside::Wall("template")) {
            // In a <template> a browser closes the nested form in reach, with all it holds, and nothing else.
            if let Reach::Nested(again) = reach(&self.inside) {
                self.inside.truncate(again);
            }
            return Next::Step(Step::Within);
        }
        pop_implied(&mut self.inside, "");
        if (self.inside.is_empty() && !self.lost) || self.depth == 0 {
            return Next::Step(Step::Ends);
        }
        // Something is left open, or the end tag doesn't reach the form: see `Form`.
        Next::Counted(self.depth)
    }

    /// Where the last element open in the form whose name `pick` takes is.
    fn last_open(&self, pick: impl Fn(&str) -> bool) -> Option<usize> {
        self.inside.iter().rposition(|open| matches!(open, Inside::Open(open) if pick(open)))
    }
}

/// Whether `tag` holds foreign content.
fn is_foreign(tag: &str) -> bool {
    matches!(tag, "svg" | "math")
}

/// Whether what is open last is an element of an `<svg>` or `<math>` in which a tag is theirs.
fn in_foreign(inside: &[Inside]) -> bool {
    matches!(inside.last(), Some(Inside::Wall("svg" | "math" | "annotation-xml") | Inside::Foreign))
}

/// Whether a browser's scope stops at what is open.
fn blocks(open: &Inside) -> bool {
    match open {
        Inside::Wall(wall) => SCOPE.contains(wall) || *wall == "select" || is_integration_point(wall),
        _ => false,
    }
}

/// A start tag of an `<svg>` or a `<math>`, or one in foreign content: whether it was one. `tag` is the dropped
/// element's own.
fn open_foreign(tag: &str, inside: &mut Vec<Inside>, name: &str, self_closing: bool) -> bool {
    if let Some(foreign) = ["svg", "math"].into_iter().find(|foreign| *foreign == name) {
        // A browser closes an <svg/> or <math/> at once.
        if !self_closing {
            inside.push(Inside::Wall(foreign));
        }
        return true;
    }
    if !in_foreign(inside) {
        return false;
    }
    let within = inside.iter().rev().find_map(|open| match open {
        Inside::Wall(wall) if is_foreign(wall) => Some(*wall),
        _ => None,
    });
    let point = INTEGRATION_POINTS
        .iter()
        .filter(|(foreign, _)| Some(*foreign) == within)
        .find_map(|(_, points)| points.iter().find(|point| **point == name));
    if self_closing {
        return true;
    }
    if let Some(point) = point {
        inside.push(Inside::Wall(point));
    } else if name == tag {
        inside.push(Inside::Foreign);
    }
    true
}

/// An end tag in foreign content: a browser closes the element of that name open last, as long as it meets only
/// foreign elements on the way. Whether it did.
fn close_foreign(tag: &str, inside: &mut Vec<Inside>, name: &str) -> bool {
    for at in (0..inside.len()).rev() {
        let open = match &inside[at] {
            Inside::Wall(wall) if is_foreign(wall) || is_integration_point(wall) => *wall,
            Inside::Foreign => tag,
            _ => return false,
        };
        if open == name {
            inside.truncate(at);
            return true;
        }
    }
    false
}

/// Whether `wall` is one of the `INTEGRATION_POINTS`, which only foreign content holds.
fn is_integration_point(wall: &str) -> bool {
    INTEGRATION_POINTS.iter().any(|(_, points)| points.contains(&wall))
}

/// The end tag of a wall, which closes the last one of that name and all after it, but past no wall its scope stops
/// at. Whether `name` is one open.
fn close_wall(inside: &mut Vec<Inside>, name: &str) -> bool {
    let Some(at) = inside.iter().rposition(|open| matches!(open, Inside::Wall(wall) if *wall == name)) else {
        return false;
    };
    let reached = match name {
        "template" => true,
        "table" => !inside[at + 1..].contains(&Inside::Wall("template")),
        _ => !inside[at + 1..].iter().any(blocks),
    };
    if reached {
        inside.truncate(at);
    }
    true
}

/// A `<select>` or `<input>` in a dropped form or button: either closes the select open in reach and is ignored, and
/// a select with none opens one.
fn select_or_input(inside: &mut Vec<Inside>, name: &str) {
    let open = inside.iter().rposition(|open| *open == Inside::Wall("select"));
    match open.filter(|at| !inside[at + 1..].iter().any(blocks)) {
        Some(at) => inside.truncate(at),
        None if name == "select" => inside.push(Inside::Wall("select")),
        None => {}
    }
}

/// The elements a browser closes by itself at the end of what is open, but one named `except`.
fn pop_implied(inside: &mut Vec<Inside>, except: &str) {
    while let Some(Inside::Open(open)) = inside.last() {
        if open == except || !IMPLIED.contains(&open.as_str()) {
            break;
        }
        inside.pop();
    }
}

/// Where a browser finds the dropped element's own tag, reading back from what is open last.
fn reach(inside: &[Inside]) -> Reach {
    for (at, open) in inside.iter().enumerate().rev() {
        if *open == Inside::Again {
            return Reach::Nested(at);
        }
        if blocks(open) {
            return Reach::Blocked;
        }
    }
    Reach::Root
}

/// A start tag read in a dropped button or select.
fn control_start(tag: &str, inside: &mut Vec<Inside>, name: &str, self_closing: bool) -> Step {
    if open_foreign(tag, inside, name, self_closing) {
        return Step::Within;
    }
    if tag == "button" && matches!(name, "select" | "input") {
        select_or_input(inside, name);
        return Step::Within;
    }
    if let Some(wall) = SCOPE.iter().find(|wall| **wall == name) {
        inside.push(Inside::Wall(wall));
        return Step::Within;
    }
    if name == tag {
        match (tag, reach(inside)) {
            // A button closes the button in reach, and opens another in its place.
            ("button", Reach::Root | Reach::Nested(_)) => {}
            // A select closes the select in reach, and is ignored.
            (_, Reach::Root) => return Step::Ends,
            (_, Reach::Nested(again)) => inside.truncate(again),
            (_, Reach::Blocked) => inside.push(Inside::Again),
        }
        return Step::Within;
    }
    // An <input> closes the select in reach too, and is read after it.
    if tag == "select" && name == "input" {
        match reach(inside) {
            Reach::Root => return Step::EndsBefore,
            Reach::Nested(again) => inside.truncate(again),
            Reach::Blocked => {}
        }
    }
    Step::Within
}

/// An end tag read in a dropped button or select.
fn control_end(tag: &str, inside: &mut Vec<Inside>, name: &str) -> Step {
    if close_foreign(tag, inside, name) {
        return Step::Within;
    }
    if name == tag {
        // A browser closes the one in reach with all it holds, and ignores an end tag a wall stands before.
        match reach(inside) {
            Reach::Root => return Step::Ends,
            Reach::Nested(again) => inside.truncate(again),
            Reach::Blocked => {}
        }
        return Step::Within;
    }
    close_wall(inside, name);
    Step::Within
}
