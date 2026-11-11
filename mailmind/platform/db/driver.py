"""SQLite, as MailMind runs it. Code takes SQLite's names from here (connect, Row, Connection, the errors), never from
the standard library's sqlite3 itself, so that what the database runs on is chosen in one place."""
from sqlite3 import (Connection, DatabaseError, Error, IntegrityError, OperationalError, ProgrammingError, Row,
                     complete_statement, connect, sqlite_version, sqlite_version_info)

__all__ = ["Connection", "DatabaseError", "Error", "IntegrityError", "OperationalError", "ProgrammingError", "Row",
           "complete_statement", "connect", "sqlite_version", "sqlite_version_info"]
