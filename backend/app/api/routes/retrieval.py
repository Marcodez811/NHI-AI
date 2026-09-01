"""Read-only retrieval-index readiness endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from app.models.documents import DocumentStatus
from app.models.retrieval import RetrievalIndexErrorCode, RetrievalIndexState
from app.services.documents.repository import DocumentRepository
from app.services.retrieval.registry import RetrievalIndexRegistry

router = APIRouter(tags=["retrieval"])


class RetrievalStatusResponse(BaseModel):
    """Safe status contract for the workspace bootstrap/empty state."""

    model_config = ConfigDict(use_enum_values=True)

    state: RetrievalIndexState
    can_retrieve: bool
    ready_document_count: int
    error_code: RetrievalIndexErrorCode | None = None
    warning_code: str | None = None


def get_retrieval_registry() -> RetrievalIndexRegistry:
    """Dependency hook overridden by the application singleton."""

    return RetrievalIndexRegistry()


def get_retrieval_document_repository() -> DocumentRepository:
    """Dependency hook overridden by the application database session."""

    from app.api.routes.documents import get_document_repository

    return get_document_repository()


@router.get("/retrieval/status", response_model=RetrievalStatusResponse)
async def retrieval_status(
    registry: Annotated[RetrievalIndexRegistry, Depends(get_retrieval_registry)],
    repository: Annotated[DocumentRepository, Depends(get_retrieval_document_repository)],
) -> RetrievalStatusResponse:
    """Report provider readiness separately from process health.

    An empty document catalog is not an infrastructure failure: the store may
    already be ready while the user has not uploaded a source yet.  Counting
    retrieval-ready rows here lets the frontend distinguish those states
    without exposing provider identifiers or SDK error details.
    """

    ready_documents = await repository.list_documents(
        status=DocumentStatus.READY,
        retrieval_enabled=True,
    )
    try:
        snapshot = registry.status()
    except Exception:
        # This can happen during a first request raced with table creation.
        # Return a stable safe state and let startup/worker bootstrap retry.
        snapshot = {
            "state": RetrievalIndexState.UNINITIALIZED.value,
            "can_retrieve": False,
            "error_code": None,
            "warning_code": None,
        }
    return RetrievalStatusResponse(
        state=snapshot.get("state", RetrievalIndexState.UNINITIALIZED.value),
        can_retrieve=bool(snapshot.get("can_retrieve", False)),
        ready_document_count=len(ready_documents),
        error_code=snapshot.get("error_code"),
        warning_code=snapshot.get("warning_code"),
    )
