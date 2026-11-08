"""A person's preferences: what the Settings page shows and changes, read and written through the Directory."""
from mailmind.http import route
from mailmind.http.schemas.common import answers
from mailmind.http.schemas.preferences import ChangedPreferences, HeldPreferences, PreferencesAnswer


def router(accounts, assistant):
    r = route.router()
    config = accounts.config

    def answer(preferences):
        return {"ok": True, "preferences": {**preferences, "ai_daily_limit_usd": assistant.limit_micros() / 1_000_000}}

    @r.get("/api/preferences", responses=answers(PreferencesAnswer))
    def current():
        return answer(config.preferences())

    @r.post("/api/preferences", responses=answers(PreferencesAnswer))
    def change(body: ChangedPreferences):
        return answer(config.set_preferences(body.model_dump(exclude_unset=True)))

    @r.post("/api/preferences/import", responses=answers(PreferencesAnswer))
    def bring_in(body: HeldPreferences):
        return answer(config.import_preferences(body.held))

    return r
