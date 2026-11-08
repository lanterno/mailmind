"""A bulk command in words: plan, preview, confirm.

An untrusted natural-language command maps to a CONSTRAINED {verb, filter} via Claude. The filter is validated again
through the same injection-safe search grammar and what it resolves to is frozen, capped, as a plan (plans.py). Planning
is READ-ONLY: it never mutates. The client shows a mandatory confirm gate, and only an explicit click sends the plan's
id back to be carried out.
"""
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from mailmind.assistant.application import plans
from mailmind.assistant.application.calls import Calls
from mailmind.assistant.application.ports import NotSent, Result, Search
from mailmind.assistant.domain import action_plan, prompts
from mailmind.kernel.errors import Refused, Unreachable
from mailmind.kernel.snoozed import SNOOZE_PRESETS

DO_PLAN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["intent"],
    "properties": {
        # 'act' only when it maps to exactly one safe, well-scoped action; else 'search' (degrade).
        "intent": {"type": "string", "enum": ["act", "search"]},
        # a query in the search-operator grammar ONLY — never SQL, never message ids.
        "filter": {"type": "string", "maxLength": 400},
        # the verb enum IS the allowlist; the model cannot emit anything else.
        "verb": {"type": "string", "enum": list(action_plan.VERBS)},
        "snooze_preset": {"type": "string", "enum": list(SNOOZE_PRESETS)},
    },
}


class BulkCommands:
    def __init__(self, calls: Calls, search: Search):
        self._calls = calls
        self._search = search
        self._DO_PLANS = plans.PlanBook()

    def plan(self, body: Mapping[str, Any]) -> Result:
        """Plan an NL bulk command → a validated, capped PREVIEW with the id of the plan it froze. READ-ONLY:
        generates no mutation. Degrades to {intent:'search'} whenever it isn't a clean, allowlisted,
        safely-scoped action."""
        cmd = action_plan.command_of(body.get("cmd"))
        if not cmd:
            raise Refused("empty command")
        today = datetime.now().astimezone().date().isoformat()
        prompt = prompts.PLAN.fill(today=today, cmd=cmd)
        try:
            plan = self._calls.call_claude_json(prompt, DO_PLAN_SCHEMA, feature="plan") or {}
        except NotSent:                 # AI off, or over the daily limit: the route answers it
            raise
        except Exception:
            raise Unreachable("planner unavailable", retry=True)
        filt = action_plan.filter_told(plan)
        verb = action_plan.verb_told(plan)
        if verb is None:                                   # degrade: just show a search (act on nothing)
            return {"ok": True, "intent": "search", "filter": filt}
        # RE-PARSE the model's filter through the real grammar — its output is never trusted raw
        spec = self._search.parse(filt)
        if not spec.narrows():                             # empty / scope-only / negation-only → not a scoped action
            return {"ok": True, "intent": "search", "filter": filt}
        cap = action_plan.cap_of(verb)
        apart = action_plan.out_of_reach(verb, spec.names_folder())
        count, messages, sample, matched = self._search.reach(spec, cap, apart, action_plan.SAMPLE)
        if count is None:                                  # unparseable resolved query → degrade
            return {"ok": True, "intent": "search", "filter": filt}
        if not messages:                                   # nothing matches → nothing to confirm
            return {"ok": True, "intent": "search", "filter": filt, "empty": True}
        if self._search.sweeps_whole_mailbox(matched):  # blast-radius backstop: refuse what's still most of the mailbox
            return {"ok": True, "intent": "search", "filter": filt}
        preset = action_plan.preset_told(plan, verb)
        frozen = self._DO_PLANS.open(verb, preset, messages)
        return {"ok": True, "intent": "act", "verb": verb, "filter": filt,
                "snooze_preset": preset,
                "count": count, "cap": cap, "capped": count > cap,
                "plan": frozen.id, "sample": sample}

    def take(self, plan_id: str) -> action_plan.ActionPlan | None:
        """What a preview froze, once, or None when its plan has expired. The Assistant only says what it was: the mail's
        own contexts carry it out."""
        return self._DO_PLANS.take(plan_id)
