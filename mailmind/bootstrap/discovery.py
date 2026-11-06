"""Discovery's wiring: the one place that binds its ports, to its own adapters and, until those contexts move, to the
legacy modules behind them. The legacy modules and the command line reach Discovery through here.
"""
from mailmind.discovery.adapters import views_file
from mailmind.discovery.application.views import SavedViews

__all__ = ["views"]

views = SavedViews(views_file)
