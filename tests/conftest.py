import os
import tempfile
from pathlib import Path

import pytest

TEST_UPLOAD_DIR = tempfile.mkdtemp(prefix="asistensi_test_")
TEST_SETTINGS_PATH = Path(tempfile.mkdtemp(prefix="asistensi_settings_")) / "settings.json"
os.environ["UPLOAD_DIR"] = TEST_UPLOAD_DIR
os.environ["SETTINGS_PATH"] = str(TEST_SETTINGS_PATH)
os.environ["FLASK_SECRET_KEY"] = "test-secret-key"
os.environ.pop("GEMINI_API_KEY", None)
os.environ.pop("GOOGLE_API_KEY", None)
os.environ.pop("ADMIN_PASSWORD", None)
os.environ.pop("LLM_ENABLED", None)

BASE_DIR = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def client():
    from app import app

    app.config.update(TESTING=True)
    with app.test_client() as test_client:
        yield test_client


@pytest.fixture(scope="session")
def sample_dir():
    return BASE_DIR / "sample"


@pytest.fixture(scope="session")
def master():
    from master_nomenklatur import MasterNomenklatur

    return MasterNomenklatur.load_default(BASE_DIR / "data" / "nomenklatur_kab_kota.json")
