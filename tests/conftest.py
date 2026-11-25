import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("A4S_DB", os.path.join(tempfile.gettempdir(), "a4s_pytest_default.db"))

import pytest

from app import config, db, kb


@pytest.fixture()
def conn(tmp_path):
    path = tmp_path / "test.db"
    connection = db.connect(str(path))
    db.init_db(connection)
    kb.seed_from_file(connection, config.FAQ_FILE)
    yield connection
    connection.close()


@pytest.fixture()
def client(tmp_path):
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app(str(tmp_path / "api.db"))
    with TestClient(app) as test_client:
        yield test_client
