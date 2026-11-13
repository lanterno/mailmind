//! Saved searches, "Views" (`mailmind.discovery.domain.views` and `application.views`): named search-grammar queries
//! shown in the folder rail. The built-in defaults and lanes are merged in as they are read: they always appear,
//! can't be deleted, and are never saved.

use std::sync::Arc;

use mm_kernel::errors::{Failure, Problem};
use mm_kernel::text::strip;
use serde_json::{Map, Value, json};

use crate::ports::ViewsStore;
use crate::search::cut;

/// A view: its id, name and query, and for a built-in whose query the page expands, what kind it is.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct View {
    pub id: String,
    pub name: String,
    pub q: String,
    pub dynamic: Option<&'static str>,
}

const fn builtin(id: &'static str, name: &'static str, q: &'static str) -> (&'static str, &'static str, &'static str) {
    (id, name, q)
}

/// The built-in smart defaults. `__LAST7__` the page expands to `after:` a week ago.
const DEFAULTS: [(&str, &str, &str); 4] = [
    builtin("__unread__", "Unread", "is:unread"),
    builtin("__flagged__", "Flagged", "is:flagged"),
    builtin("__attach__", "Has attachments", "has:attachment"),
    builtin("__last7__", "Last 7 days", "__LAST7__"),
];

/// The auto-label lanes.
const LANES: [(&str, &str, &str); 5] = [
    builtin("__lane_feed__", "Feed", "is:newsletter"),
    builtin("__lane_papertrail__", "Paper trail", "is:receipt"),
    builtin("__lane_notifications__", "Notifications", "is:notification"),
    builtin("__lane_action__", "Action needed", "category:action-needed"),
    builtin("__lane_finance__", "Finance", "category:finance"),
];

fn is_builtin(id: &str) -> bool {
    DEFAULTS.iter().chain(LANES.iter()).any(|(b, _, _)| *b == id)
}

/// Python's `str()` of what was saved: text as it is, anything else as Python writes it.
fn py_str(value: Option<&Value>, default: &str) -> String {
    match value {
        None => default.to_owned(),
        Some(Value::String(s)) => s.clone(),
        Some(other) => crate::known::py_repr(other),
    }
}

/// The person's views out of whatever was saved, builtins left out. It never fails: what is malformed is dropped.
pub fn clean(raw: &Value) -> Vec<View> {
    let Value::Array(saved) = raw else { return Vec::new() };
    saved
        .iter()
        .filter_map(Value::as_object)
        .filter_map(|v| {
            let id = py_str(v.get("id"), "");
            let name = py_str(v.get("name"), "");
            let q = py_str(v.get("q"), "");
            let (name, q) = (strip(&name), strip(&q));
            (!id.is_empty() && !name.is_empty() && !q.is_empty() && !is_builtin(&id)).then(|| View {
                id,
                name: cut(name, 60),
                q: cut(q, 512),
                dynamic: None,
            })
        })
        .collect()
}

/// What the page sees: the defaults, then the lanes, then the person's own, builtins flagged.
pub fn merged(user_views: &[View]) -> Vec<Value> {
    let builtins = DEFAULTS.iter().chain(LANES.iter()).map(|(id, name, q)| {
        let mut row = Map::new();
        row.insert("id".into(), json!(id));
        row.insert("name".into(), json!(name));
        row.insert("q".into(), json!(q));
        if *id == "__last7__" {
            row.insert("dynamic".into(), json!("last7"));
        }
        row.insert("builtin".into(), json!(true));
        Value::Object(row)
    });
    let own = user_views.iter().map(|v| json!({"id": v.id, "name": v.name, "q": v.q, "builtin": false}));
    builtins.chain(own).collect()
}

/// The views as they are kept: the person's own, each its id, name and query.
fn saved(user_views: &[View]) -> Value {
    Value::Array(user_views.iter().map(|v| json!({"id": v.id, "name": v.name, "q": v.q})).collect())
}

/// What `/api/views` may be asked to do.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Change {
    Create {
        name: Option<String>,
        q: Option<String>,
    },
    Delete {
        id: Option<String>,
    },
    /// Any other verb, or none.
    Other,
}

/// Where a new view's id comes from: `v` and eight hex digits, as `secrets.token_hex(4)` draws them.
pub trait Ids: Send + Sync + std::fmt::Debug {
    fn view_id(&self) -> Result<String, mm_kernel::errors::Fault>;
}

#[derive(Debug)]
pub struct SavedViews {
    store: Arc<dyn ViewsStore>,
    ids: Arc<dyn Ids>,
}

impl SavedViews {
    pub fn new(store: Arc<dyn ViewsStore>, ids: Arc<dyn Ids>) -> SavedViews {
        SavedViews { store, ids }
    }

    pub fn load(&self) -> Result<Vec<View>, Failure> {
        Ok(clean(&self.store.read()?))
    }

    /// `GET /api/views`.
    pub fn listed(&self) -> Result<Value, Failure> {
        Ok(json!({"ok": true, "views": merged(&self.load()?)}))
    }

    /// A change `/api/views` asked for, and the views after it, each change read, made and written in one write block.
    pub fn post(&self, change: Change) -> Result<Value, Failure> {
        match change {
            Change::Create { name, q } => {
                let name = cut(strip(name.as_deref().unwrap_or_default()), 60);
                let q = cut(strip(q.as_deref().unwrap_or_default()), 512);
                if name.is_empty() || q.is_empty() {
                    return Err(Problem::refused("need a name and a query").into());
                }
                let view = View { id: self.ids.view_id()?, name, q, dynamic: None };
                self.store.change(&mut |kept| {
                    let mut views = clean(&kept);
                    views.push(view.clone());
                    Some(saved(&views))
                })?;
            }
            Change::Delete { id } => {
                // A built-in or a lane can't go, whatever was saved.
                if !id.as_deref().is_some_and(is_builtin) {
                    self.store.change(&mut |kept| {
                        let views = clean(&kept);
                        let left: Vec<View> = views.iter().filter(|v| Some(&v.id) != id.as_ref()).cloned().collect();
                        (left.len() != views.len()).then(|| saved(&left))
                    })?;
                }
            }
            Change::Other => return Err(Problem::refused("verb must be create|delete").into()),
        }
        self.listed()
    }
}
