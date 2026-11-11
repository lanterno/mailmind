"""SQLite as MailMind runs it: SQLCipher's build (the sqlcipher3 package), which encrypts the database file page by
page, its write-ahead log too.

Code takes SQLite's names from here (connect, Row, Connection, the errors), never from the standard library's
sqlite3: that one can't read an encrypted file, and its errors are other classes, so an `except` naming one would
never catch what this driver raises. The API is the standard library's own, which the stub in typings/ says to
the type checker. A connection is opened, and keyed, by mailmind.platform.db.database.
"""
from sqlcipher3.dbapi2 import (Connection, DatabaseError, Error, IntegrityError, OperationalError, ProgrammingError,
                               Row, complete_statement, connect, sqlite_version, sqlite_version_info)

__all__ = ["Connection", "DatabaseError", "Error", "IntegrityError", "OperationalError", "ProgrammingError", "Row",
           "complete_statement", "connect", "sqlite_version", "sqlite_version_info"]
