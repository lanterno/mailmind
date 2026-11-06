"""What's safe to show of a message's HTML. The sandboxed iframe is the actual boundary, this the layer behind it."""
from .html import sanitize, sanitize_css_stylesheet, sanitize_inline_style, without_fold

__all__ = ["sanitize", "sanitize_css_stylesheet", "sanitize_inline_style", "without_fold"]
