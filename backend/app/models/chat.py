"""Public contracts for source-grounded question answering."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


MAX_CHAT_DOCUMENTS = 20


class QaMode(StrEnum):
    """Supported retrieval scopes.

    The values intentionally match the metadata written to the retrieval
    index.  Keeping this enum small also prevents accidentally exposing a
    retired retrieval workflow through the API.
    """

    LEGISLATIVE_QA = "legislative_qa"
    PUBLIC_OPINION = "public_opinion"
    BEI_CAN = "bei_can"


class ChatRequest(BaseModel):
    """Question and explicit source scope submitted by a client.

    Extra fields are forbidden so clients cannot supply ``vector_store_id``
    or other provider-internal identifiers through the public transport.
    Vector-store configuration is done through server-side provider injection.
    """

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=20_000)
    mode: QaMode
    document_ids: list[UUID] = Field(default_factory=list, max_length=MAX_CHAT_DOCUMENTS)
    max_num_results: int = Field(default=12, ge=1, le=50)
    include_search_results: bool = True

    @field_validator("question")
    @classmethod
    def question_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("question cannot be empty or whitespace")
        return value

    @field_validator("document_ids")
    @classmethod
    def document_ids_unique(cls, value: list[UUID]) -> list[UUID]:
        if len(value) != len(set(value)):
            raise ValueError("document_ids must be unique")
        return value


class Citation(BaseModel):
    """Provider-neutral citation returned with an answer."""

    type: Literal["file_citation"] = "file_citation"
    text: str
    filename: str | None = None
    document_id: UUID | None = None
    # Kept optional for migration/debugging; callers should prefer document_id.
    file_id: str | None = None
    start_index: int | None = None
    end_index: int | None = None
    page: int | None = None


class ChatResponse(BaseModel):
    answer: str
    mode: QaMode
    citations: list[Citation] = Field(default_factory=list)


class QaModeInfo(BaseModel):
    mode: QaMode
    label: str
    description: str
