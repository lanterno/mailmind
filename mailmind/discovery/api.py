"""What the other contexts and the app's wiring may ask of Discovery, what a request may do to the saved views, and the kinds of document."""
from mailmind.discovery.domain.documents import DocumentGrouping, DocumentKind
from mailmind.discovery.domain.query import QuerySpec
from mailmind.discovery.domain.views import ViewVerb

__all__ = ["DocumentGrouping", "DocumentKind", "QuerySpec", "ViewVerb"]
