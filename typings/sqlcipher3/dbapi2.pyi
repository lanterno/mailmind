# sqlcipher3 ships no types. Its dbapi2 is a build of the standard library's sqlite3 module (pysqlite3) with
# SQLCipher linked in, with the same API, so the type checker reads it as that.
from sqlite3 import *  # noqa: F403  (the whole module, as sqlcipher3 re-exports it)
from sqlite3 import (Connection as Connection, DatabaseError as DatabaseError, Error as Error,
                     IntegrityError as IntegrityError, OperationalError as OperationalError,
                     ProgrammingError as ProgrammingError, Row as Row, complete_statement as complete_statement,
                     connect as connect, sqlite_version as sqlite_version, sqlite_version_info as sqlite_version_info)
