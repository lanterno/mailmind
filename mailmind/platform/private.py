"""Files that hold what is in someone's mail or what was done with it, in the data directory or exported from it:
readable by their owner only, as the database, the rules, views and snippets files and the log are."""
import os
from typing import BinaryIO, TextIO


def append(path: str) -> TextIO:
    """`path` opened to append text, its owner's alone: made so when new, and brought into line when an older MailMind
    left it readable by others."""
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.fchmod(fd, 0o600)
        return os.fdopen(fd, "a", encoding="utf-8")
    except BaseException:
        os.close(fd)
        raise


def create(path: str) -> BinaryIO:
    """`path` opened to write bytes afresh, its owner's alone."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        return os.fdopen(fd, "wb")
    except BaseException:
        os.close(fd)
        raise


def folder(path: str) -> None:
    """`path` as a folder, and each folder above it that isn't there, made its owner's alone, so no one else can list
    the names inside (an account's address, a folder's). One already there is left as it is."""
    missing = []
    here = os.path.abspath(path)
    while not os.path.isdir(here):
        missing.append(here)
        above = os.path.dirname(here)
        if above == here:
            break
        here = above
    for each in reversed(missing):
        try:
            os.mkdir(each, 0o700)
        except FileExistsError:
            if not os.path.isdir(each):
                raise
