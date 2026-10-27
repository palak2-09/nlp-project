import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "data",
        max_upload_bytes=2 * 1024 * 1024,
        chunk_size_chars=500,
        chunk_overlap_chars=50,
        cors_origins=("http://localhost:5173",),
    )
    application = create_app(settings)
    with TestClient(application) as test_client:
        yield test_client


def upload_txt(client, filename="research.txt", contents="PaperGraph connects ideas to evidence."):
    return client.post(
        "/api/documents",
        files={"file": (filename, contents.encode("utf-8"), "text/plain")},
    )


@pytest.fixture
def upload_text_file():
    return upload_txt
