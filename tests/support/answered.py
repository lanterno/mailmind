"""What a route over a use case would answer, for a test that calls the use case itself."""
from mailmind.http.shared import problem_answer
from mailmind.kernel.errors import Problem


def answered(call, *args, **kw):
    """(body, status), with a Problem it raised as the app says it."""
    try:
        got = call(*args, **kw)
    except Problem as e:
        return problem_answer(e)
    return got if isinstance(got, tuple) else (got, 200)
