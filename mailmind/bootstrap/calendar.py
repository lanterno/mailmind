"""Calendar's wiring: the one place that binds its ports, to its own adapters and, until those contexts move, to the
legacy modules behind them. The legacy modules reach Calendar through here.
"""
from mailmind.calendar.adapters import ics_reader as ics

__all__ = ["ics"]
