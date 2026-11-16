"""What Settings keeps in the login Keychain beside each account's own password, and what it answers: each AI provider's API
key and the Proton Bridge's password. A request holds one once, to be kept, and stays out of a repr; no answer declares a
field that could hold one, so under test an answer that held any is a failure."""
from typing import Literal

from pydantic import Field

from mailmind.http.schemas.common import Answer, RequestBody
from mailmind.kernel.ai_provider import AiProvider


class ProviderBody(RequestBody):
    """An AI provider, whose key is taken out."""
    provider: AiProvider


class KeyBody(ProviderBody):
    """An AI provider's API key, as typed or pasted."""
    key: str = Field(repr=False)


class PasswordBody(RequestBody):
    """The Proton Bridge's password, as the Bridge shows it."""
    password: str = Field(repr=False)


class Kept(Answer):
    """Whether one is set: never what it is, how long, or any part of it."""
    ok: Literal[True]
    set: bool


class KeysKept(Answer):
    """Each AI provider whose key the config names, in the providers' order: never a key, how long, or any part of it."""
    ok: Literal[True]
    kept: list[AiProvider]
