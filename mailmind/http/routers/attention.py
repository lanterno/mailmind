"""What the page asks of Attention: snooze, mute, follow-ups and reply later."""
from mailmind.http import route
from mailmind.http.schemas.attention import (
    MuteBody,
    NamesAThread,
    NamesThreadOrMessages,
    OnThread,
    RepliedLater,
    ReplyLaterBody,
    SnoozeBody,
    Snoozed,
    Unsnoozed,
)
from mailmind.http.schemas.common import answers


def router(attention):
    r = route.router()

    @r.post("/api/snooze", responses=answers(Snoozed))
    def snooze(body: SnoozeBody):
        return attention.snoozing.snooze(body.model_dump(exclude_unset=True))

    @r.post("/api/unsnooze", responses=answers(Unsnoozed))
    def unsnooze(body: NamesThreadOrMessages):
        return attention.snoozing.unsnooze(body.model_dump(exclude_unset=True))

    @r.post("/api/mute", responses=answers(OnThread))
    def mute(body: MuteBody):
        return attention.muting.mute(body.model_dump(exclude_unset=True))

    @r.post("/api/unmute", responses=answers(OnThread))
    def unmute(body: NamesAThread):
        return attention.muting.unmute(body.model_dump(exclude_unset=True))

    @r.post("/api/followups/dismiss", responses=answers(OnThread))
    def dismiss_followup(body: NamesAThread):
        return attention.followups.dismiss(body.model_dump(exclude_unset=True))

    @r.post("/api/replylater", responses=answers(RepliedLater))
    def reply_later(body: ReplyLaterBody):
        return attention.followups.reply_later(body.model_dump(exclude_unset=True))

    return r
