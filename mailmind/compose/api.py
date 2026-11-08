"""What the rest of the app may ask of Compose by name: how a message is built, what a composer may be, and how long Undo Send has. What it does, it does
through the use cases in mailmind.bootstrap.compose."""
from mailmind.compose.domain.attachments import PictureType
from mailmind.compose.domain.builder import Built, MessageSpec, build_message, reference_chain, thread_ids
from mailmind.compose.domain.errors import Unanswered
from mailmind.compose.domain.events import DraftCopyRemoved, Failed, Sent
from mailmind.compose.domain.forward import forward_bodies
from mailmind.compose.domain.limits import MAX_ATTACH_BYTES, MAX_SEND_POST
from mailmind.compose.domain.reply import reply_bodies
from mailmind.compose.domain.resume import ComposeMode, lf, resume_split

__all__ = ["Built", "ComposeMode", "DraftCopyRemoved", "Failed", "MAX_ATTACH_BYTES", "MAX_SEND_POST", "MessageSpec", "PictureType", "Sent", "Unanswered", "build_message", "forward_bodies", "lf", "reference_chain", "reply_bodies", "resume_split",
           "thread_ids"]
