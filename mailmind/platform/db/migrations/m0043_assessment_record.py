"""Migration 43 (2026-11-07): an assessment says how it came to be.

`assessments.model` is the label shown beside a summary, the model asked for or `parse-error`, so nothing said which model
served a reply after a server-side fallback, which prompt it answered, or whether a stored fallback was a refusal or an
answer that wouldn't do. Now `served_by`, `prompt_version` and `outcome` (`assessed`, `unusable` or `refused`) do. A row
from before has them NULL, which reads as unknown: none of it can be worked out afterwards, so nothing is filled in.

A batch's answers can come in after an upgrade, so `ai_batches.prompt_version` says which words its requests were sent with.
"""
from mailmind.platform.db.migrations.m0001_m0020 import _addcol


def migrate(c):
    _addcol(c, "assessments", "served_by TEXT")
    _addcol(c, "assessments", "prompt_version INTEGER")
    _addcol(c, "assessments", "outcome TEXT")
    _addcol(c, "ai_batches", "prompt_version INTEGER")
