from pathlib import Path

path = Path("backend/app/api/routes/chat.py")
text = path.read_text()
needle = '''    return request\n\n\n@router.get("/qa-modes", response_model=list[QaModeInfo])\n'''
replacement = '''    return request\n\n\nasync def _validate_document_scope(request: ChatRequest, repository: DocumentRepository) -> None:\n    """Backward-compatible validator retained for existing callers/tests."""\n\n    await _resolve_document_scope(request, repository)\n\n\n@router.get("/qa-modes", response_model=list[QaModeInfo])\n'''
if needle not in text:
    raise SystemExit("compatibility patch context not found")
path.write_text(text.replace(needle, replacement, 1))
