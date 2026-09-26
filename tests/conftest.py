from datetime import date

import pytest

from scripts.make_mock_data import generate
from snapsort import db
from snapsort.config import Settings


@pytest.fixture
def settings(tmp_path):
    s = Settings(root=tmp_path.resolve())
    s.ensure_dirs()
    return s


@pytest.fixture
def conn(settings):
    c = db.connect(settings.db_path)
    yield c
    c.close()


@pytest.fixture(scope="session")
def mock_dir(tmp_path_factory):
    root = tmp_path_factory.mktemp("gen").resolve() / "mock"
    generate(root, today=date.today())
    return root
