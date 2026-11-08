"""What the page asks of Attention: snooze, mute, follow-ups, reply later and tasks."""
from mailmind.http import named, route
from mailmind.http.schemas.attention import (
    MuteBody,
    NamesAThread,
    NamesThreadOrMessages,
    OnThread,
    RepliedLater,
    ReplyLaterBody,
    SnoozeBody,
    Snoozed,
    TaskAdded,
    TaskBody,
    TaskDoneBody,
    TaskGone,
    TaskRemoveBody,
    TaskList,
    Unsnoozed,
)
from mailmind.http.schemas.common import answers


def router(attention, mailbox):
    r = route.router()

    def copies(body):
        """What a snooze names, with `pks` for the copies it names that are still there, and `pk` for the one `key` and
        `folder` name. A thread named by its account and id goes before them, as it always did."""
        asked = body.model_dump(exclude_unset=True)
        asked["pks"] = None if body.copies is None else named.copies_pks(mailbox, asked["copies"])
        return named.with_copy(mailbox, asked, gone=None)

    def thread(body):
        """What names a thread, with `pk` for the message of it the page has. One with no copy left names none, as a thread
        the request names by its id goes first."""
        return named.with_message(mailbox, body.model_dump(exclude_unset=True), gone=None)

    @r.post("/api/snooze", responses=answers(Snoozed))
    def snooze(body: SnoozeBody):
        return attention.snoozing.snooze(copies(body))

    @r.post("/api/unsnooze", responses=answers(Unsnoozed))
    def unsnooze(body: NamesThreadOrMessages):
        return attention.snoozing.unsnooze(copies(body))

    @r.post("/api/mute", responses=answers(OnThread))
    def mute(body: MuteBody):
        return attention.muting.mute(thread(body))

    @r.post("/api/unmute", responses=answers(OnThread))
    def unmute(body: NamesAThread):
        return attention.muting.unmute(thread(body))

    @r.post("/api/followups/dismiss", responses=answers(OnThread))
    def dismiss_followup(body: NamesAThread):
        return attention.followups.dismiss(thread(body))

    @r.post("/api/replylater", responses=answers(RepliedLater))
    def reply_later(body: ReplyLaterBody):
        return attention.followups.reply_later(thread(body))

    @r.get("/api/tasks", responses=answers(TaskList))
    def tasks():
        return attention.tasks.listing()

    @r.post("/api/tasks", responses=answers(TaskAdded))     # one someone confirmed, from the message `key` names
    def add_task(body: TaskBody):
        return attention.tasks.add(named.with_message(mailbox, body.model_dump(exclude_unset=True)))

    @r.post("/api/tasks/done", responses=answers(TaskGone))
    def task_done(body: TaskDoneBody):
        return attention.tasks.mark(body.model_dump(exclude_unset=True))

    @r.post("/api/tasks/remove", responses=answers(TaskGone))
    def remove_task(body: TaskRemoveBody):
        return attention.tasks.remove(body.model_dump(exclude_unset=True))

    return r
