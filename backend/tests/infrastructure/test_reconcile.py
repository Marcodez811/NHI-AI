from types import SimpleNamespace
from uuid import uuid4

from app.models.documents import Document, IngestionJob
from app.models.retrieval import RetrievalIndex, RetrievalIndexState
import scripts.reconcile as reconcile


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Session:
    def __init__(self):
        self.document = Document(
            id=uuid4(),
            original_filename="a.pdf",
            display_name="a.pdf",
            mime_type="application/pdf",
            extension=".pdf",
            size_bytes=1,
            checksum="checksum",
            category="legislative_qa",
            storage_key="a.pdf",
            remote_file_id="file-referenced",
            remote_vector_store_file_id="att-referenced",
        )
        self.record = RetrievalIndex(
            state=RetrievalIndexState.READY.value,
            vector_store_id="vs-primary",
        )

    def exec(self, statement):
        entity = statement.column_descriptions[0].get("entity")
        return _Rows([self.document] if entity is Document else [])

    def get(self, model, key):
        return self.record

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None


def test_apply_never_deletes_referenced_file(monkeypatch):
    class Files:
        def __init__(self):
            self.deleted_attachments = []
            self.deleted_files = []

        def list(self, **kwargs):
            return SimpleNamespace(
                data=[
                    SimpleNamespace(id="att-referenced", file_id="file-referenced"),
                    SimpleNamespace(id="att-orphan", file_id="file-orphan"),
                ]
            )

        def delete(self, **kwargs):
            self.deleted_attachments.append(kwargs)

    class Client:
        def __init__(self):
            self.vector_stores = SimpleNamespace(files=Files())
            self.files = SimpleNamespace(delete=lambda **kwargs: self.vector_stores.files.deleted_files.append(kwargs))

    client = Client()
    monkeypatch.setattr(reconcile, "Session", lambda engine: _Session())

    assert reconcile.reconcile(apply=True, client=client) == 0
    assert [item["file_id"] for item in client.vector_stores.files.deleted_attachments] == ["att-orphan"]
    assert client.vector_stores.files.deleted_files == [{"file_id": "file-orphan"}]
