import os
import tempfile

# Point the app at a throwaway database before it is imported.
_db = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_db}")
os.environ["GEMINI_API_KEY"] = ""
os.environ["WHISPER_PRELOAD"] = "false"
os.environ["PIPER_PRELOAD"] = "false"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.database import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture()
def client():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with TestClient(app) as c:
        yield c
