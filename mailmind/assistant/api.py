"""What the rest of the app may name of the Assistant without its wiring: how a call that was never sent is told, and letting
one request go over the day's limit."""
from mailmind.assistant.application.governor import allowing_over
from mailmind.assistant.application.ports import AIOff, NotSent, OverLimit

__all__ = ["AIOff", "NotSent", "OverLimit", "allowing_over"]
