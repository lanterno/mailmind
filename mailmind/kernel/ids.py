"""What names what. Each is a plain int or str at run time and to the database, and a type of its own to pyright, so that a
port taking several of them can't be handed one for another. What a stored row or a request holds is Any, and goes in as it
is; a value that has been through a plain `str` or `int` goes in through the name, which costs nothing and does nothing."""
from typing import NewType

Pk = NewType("Pk", int)                     # a row of `messages`: one copy, for now. Reconcile can renumber it
MessageKey = NewType("MessageKey", int)     # a message's key: every copy of it, for as long as any is kept (store.mint_key)
AccountId = NewType("AccountId", str)       # an account, by its address
ThreadRef = NewType("ThreadRef", str)       # a conversation within an account: `rf:` or `gm:`, or a lone message's key
FolderName = NewType("FolderName", str)     # a folder, in the form the server's LIST gave it

INBOX = FolderName("INBOX")                 # the one name IMAP itself fixes
