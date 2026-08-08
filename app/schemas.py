"""Pydantic request/response models for the API.

Only the request body needs validation; the SSE stream and the JSON endpoints
return plain dicts (documented in routes.py).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    """Body for POST /ask."""

    question: str = Field(..., min_length=1, description="The user's question.")
    corpus: str = Field(..., min_length=1, description="Corpus to search, e.g. 'fastapi'.")
    doc_version: str | None = Field(
        None, description="Optional pinned doc version; omit to search all versions."
    )
    # Conversation carryover. The server mints this on a turn that answered and
    # returns it in the `done` event; the client hands the LAST one back so a
    # referential follow-up ("what about more champs he's good against") can
    # resolve. Opaque to the client — it echoes the blob, it doesn't build it.
    # Kept in the request rather than a server session so /ask stays stateless.
    context: dict | None = Field(
        None, description="Frame from the previous answered turn; echo back verbatim."
    )
