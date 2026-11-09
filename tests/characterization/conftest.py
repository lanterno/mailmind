"""Fixtures the characterization tests share across their folders."""
import pytest


@pytest.fixture
def db(data_dir):
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    yield c
    c.close()
