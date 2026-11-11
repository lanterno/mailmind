"""Who a request tells that someone is here (mailmind.http.shared.touched): whoever mailmind.bootstrap.web names, for a
request of the API that a person makes, and no one for what a hidden tab polls for. Until someone is named, a request
tells no one and goes on as ever: the http layer stands without bootstrap."""
import pytest

from mailmind.http import shared


@pytest.fixture
def told(monkeypatch):
    monkeypatch.setattr(shared, "_someone_here", shared._someone_here)
    seen = []
    shared.tell_of_someone(lambda: seen.append(1))
    return seen


@pytest.mark.parametrize("path", ["/api/threads", "/api/archive", "/api/ai/draft", "/api/nowhere"])
def test_a_request_of_the_api_tells(told, path):
    shared.touched(path)
    assert told == [1]


@pytest.mark.parametrize("path", sorted(shared.UI_QUIET) + ["/", "/assets/app.js", "/api", "/apix/threads"])
def test_what_a_hidden_tab_polls_for_and_what_isnt_the_api_tell_no_one(told, path):
    shared.touched(path)
    assert told == []


def test_until_someone_is_named_a_request_tells_no_one(monkeypatch):
    monkeypatch.setattr(shared, "_someone_here", shared._no_one)
    shared.touched("/api/threads")
