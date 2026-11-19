"""Migration 52 (2026-11-19): a draft row remembers the message it answers.

Reply on a message reopens the reply already saved to it. While a save waits on this Mac its own bytes say what it
answers, and once its copy in Drafts is listed its `messages` row does. Between the two, after the push and before Drafts
is next caught up, nothing here said it, so Reply started an empty second reply. `drafts.in_reply_to` holds the
Message-ID a save names in its In-Reply-To, written by every save, as its mode and subject are.
"""
from mailmind.platform.db.migrations.m0001_m0020 import _addcol


def migrate(c):
    _addcol(c, "drafts", "in_reply_to TEXT")
