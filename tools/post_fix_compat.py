from pathlib import Path

path = Path("backend/app/api/routes/chat.py")
text = path.read_text()
needle = '''    return request\n\n\n@router.get("/qa-modes", response_model=list[QaModeInfo])\n'''
replacement = '''    return request\n\n\nasync def _validate_document_scope(request: ChatRequest, repository: DocumentRepository) -> None:\n    """Backward-compatible validator retained for existing callers/tests."""\n\n    await _resolve_document_scope(request, repository)\n\n\n@router.get("/qa-modes", response_model=list[QaModeInfo])\n'''
if needle not in text:
    raise SystemExit("compatibility patch context not found")
path.write_text(text.replace(needle, replacement, 1))

# Existing streaming tests used the removed public vector_store_id override.
stream_path = Path("backend/tests/services/chat/test_stream.py")
stream_text = stream_path.read_text()
stream_text = stream_text.replace(
    'ChatRequest(question="問題", mode=QaMode.LEGISLATIVE_QA, vector_store_id="vs")',
    'ChatRequest(question="問題", mode=QaMode.LEGISLATIVE_QA)',
)
stream_text = stream_text.replace(
    'ResponseService(client=FakeClient(response), model="test")',
    'ResponseService(client=FakeClient(response), model="test", vector_store_id="vs")',
)
stream_path.write_text(stream_text)
