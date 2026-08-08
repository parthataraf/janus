"""Domain-agnostic RAG engine.

This package knows nothing about FastAPI-the-docs or League of Legends — it
only deals in chunks, embeddings, and vectors. The corpus-specific logic lives
in `ingestion/`.
"""
