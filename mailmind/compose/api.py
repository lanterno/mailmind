"""What the rest of the app may ask of Compose by name: how a message is built. What it does, it does
through the use cases in mailmind.bootstrap.compose."""
from mailmind.compose.domain.builder import build_message, reference_chain, thread_ids
from mailmind.compose.domain.errors import Unanswered
from mailmind.compose.domain.events import DraftCopyRemoved, Failed, Sent
from mailmind.compose.domain.forward import forward_bodies
from mailmind.compose.domain.reply import reply_bodies
from mailmind.compose.domain.resume import lf, resume_split

__all__ = ["DraftCopyRemoved", "Failed", "Sent", "Unanswered", "build_message", "forward_bodies", "lf", "reference_chain", "reply_bodies", "resume_split",
           "thread_ids"]
