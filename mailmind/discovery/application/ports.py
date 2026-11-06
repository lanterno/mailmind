"""What Discovery needs from outside it. Each is bound in mailmind.bootstrap.discovery."""
from typing import Protocol


class ViewsFile(Protocol):
    """Where a person's saved views are kept."""
    def read(self): ...
    def write(self, user_views: list) -> None: ...
