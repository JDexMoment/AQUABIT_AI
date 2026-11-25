"""RAG-слой нормативной документации СП 31.13330.2012 / 2021 на базе Qdrant."""
from __future__ import annotations
from rag.chunker import load_builtin_sp31_clauses, parse_sp31_text_to_chunks, extract_sp31_pdf_chunks
from rag.ingest import ingest_sp31_into_qdrant
from rag.retriever import QdrantNormativeRetriever, get_default_normative_retriever, retrieve_norms_for_facts

__all__ = [
    "load_builtin_sp31_clauses",
    "parse_sp31_text_to_chunks",
    "extract_sp31_pdf_chunks",
    "ingest_sp31_into_qdrant",
    "QdrantNormativeRetriever",
    "get_default_normative_retriever",
    "retrieve_norms_for_facts",
]
