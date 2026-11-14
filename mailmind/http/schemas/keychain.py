"""What Settings keeps in the login Keychain beside each account's own password, and what it answers: the Claude API key and
the Proton Bridge's password. A request holds one once, to be kept, and stays out of a repr; no answer declares a field that
could hold one, so under test an answer that held any is a failure."""
from typing import Literal

from pydantic import Field

from mailmind.http.schemas.common import Answer, RequestBody


class KeyBody(RequestBody):
    """The Claude API key, as typed or pasted."""
    key: str = Field(repr=False)


class PasswordBody(RequestBody):
    """The Proton Bridge's password, as the Bridge shows it."""
    password: str = Field(repr=False)


class Kept(Answer):
    """Whether one is set: never what it is, how long, or any part of it."""
    ok: Literal[True]
    set: bool
