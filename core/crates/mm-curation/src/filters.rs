//! A person's filters (`mailmind.curation.application.filters`): what to do with new mail that fits a search. They
//! live in the rule book; whether a search can be one is Discovery's to say.

use std::sync::Arc;

use mm_kernel::errors::{Failure, Problem};
use serde_json::{Value, json};

use crate::ports::{Conditions, FilterIds, RuleBook};
use crate::rulebook::{Filter, MOST_FILTERS};
use crate::text::strip;

/// What `/api/filters` may be asked to do.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Change {
    Create {
        q: Option<String>,
        star: bool,
        read: bool,
    },
    Update {
        id: Option<String>,
        q: Option<String>,
        star: bool,
        read: bool,
    },
    Delete {
        id: Option<String>,
    },
    /// Any other verb, or none.
    Other,
}

#[derive(Debug)]
pub struct Filters {
    rules: Arc<dyn RuleBook>,
    conditions: Arc<dyn Conditions>,
    ids: Arc<dyn FilterIds>,
}

impl Filters {
    pub fn new(rules: Arc<dyn RuleBook>, conditions: Arc<dyn Conditions>, ids: Arc<dyn FilterIds>) -> Filters {
        Filters { rules, conditions, ids }
    }

    /// What a create or an update says, or the refusal in words for a person.
    fn checked(&self, q: Option<&str>, star: bool, read: bool) -> Result<String, Problem> {
        let q = strip(q.unwrap_or_default()).to_owned();
        if let Some(why) = self.conditions.refusal(&q) {
            return Err(Problem::refused(why));
        }
        if !star && !read {
            return Err(Problem::refused("Choose what the filter does: star the mail, mark it read, or both."));
        }
        Ok(q)
    }

    /// A change `/api/filters` asked for, and the filters after it.
    pub fn post(&self, change: Change) -> Result<Value, Failure> {
        let gone = || Failure::from(Problem::not_found("no such filter"));
        match change {
            Change::Create { q, star, read } => {
                let q = self.checked(q.as_deref(), star, read)?;
                let made = Filter { id: self.ids.filter_id()?, q, star, read };
                self.rules.change(&mut |rules| {
                    if rules.filters.len() >= MOST_FILTERS {
                        return Err(Problem::refused(format!(
                            "That's as many filters as MailMind keeps ({MOST_FILTERS})."
                        ))
                        .into());
                    }
                    rules.filters.push(made.clone());
                    Ok(())
                })?;
            }
            Change::Update { id, q, star, read } => {
                let q = self.checked(q.as_deref(), star, read)?;
                let changed = Filter { id: id.unwrap_or_default(), q, star, read };
                self.rules.change(&mut |rules| {
                    let held = rules.filters.iter_mut().find(|f| f.id == changed.id).ok_or_else(gone)?;
                    *held = changed.clone();
                    Ok(())
                })?;
            }
            Change::Delete { id } => {
                self.rules.change(&mut |rules| {
                    let before = rules.filters.len();
                    rules.filters.retain(|f| Some(&f.id) != id.as_ref());
                    if rules.filters.len() == before { Err(gone()) } else { Ok(()) }
                })?;
            }
            Change::Other => return Err(Problem::refused("verb must be create|update|delete").into()),
        }
        let filters: Vec<Value> = self.rules.load()?.filters.iter().map(Filter::to_json).collect();
        Ok(json!({"ok": true, "filters": filters}))
    }
}
