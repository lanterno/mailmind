"""What the page asks of Attention: snooze, mute, follow-ups and reply later."""
from mailmind.http.routing import Routes


def routes(attention):
    r = Routes()

    @r.post("/api/snooze")
    def snooze(req):
        return attention.snoozing.snooze(req.body)

    @r.post("/api/unsnooze")
    def unsnooze(req):
        return attention.snoozing.unsnooze(req.body)

    @r.post("/api/mute")
    def mute(req):
        return attention.muting.mute(req.body)

    @r.post("/api/unmute")
    def unmute(req):
        return attention.muting.unmute(req.body)

    @r.post("/api/followups/dismiss")
    def dismiss_followup(req):
        return attention.followups.dismiss(req.body)

    @r.post("/api/replylater")
    def reply_later(req):
        return attention.followups.reply_later(req.body)

    return r
