"""
Step 4 (vector half) -- Document Store (Chroma DB).

Chunks a document's raw text, embeds each chunk, and stores it with
metadata (document id, filename, chunk index) for source tagging and
retrieval-augmented generation, per the proposal.
"""
import chromadb

import config
from vectorstore.embeddings import get_embedding_function

_client = None
_collection = None


def _get_collection():
    global _client, _collection
    if _collection is None:
        _client = chromadb.PersistentClient(path=config.VECTOR_DB_PATH)
        _collection = _client.get_or_create_collection(
            "risk_report_chunks", embedding_function=get_embedding_function()
        )
    return _collection


def _chunk_text(text: str, chunk_size: int = 800, overlap: int = 100):
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start += chunk_size - overlap
    return chunks or [text]


def index_document(document_id: int, filename: str, text: str) -> int:
    collection = _get_collection()
    chunks = _chunk_text(text)
    ids = [f"doc{document_id}_chunk{i}" for i in range(len(chunks))]
    metadatas = [{"document_id": document_id, "filename": filename, "chunk_index": i} for i in range(len(chunks))]
    # Replace any prior chunks for this document (idempotent re-indexing).
    try:
        collection.delete(where={"document_id": document_id})
    except Exception:
        pass
    collection.add(documents=chunks, ids=ids, metadatas=metadatas)
    return len(chunks)


def query(text: str, k: int = 5):
    collection = _get_collection()
    if collection.count() == 0:
        return []
    result = collection.query(query_texts=[text], n_results=min(k, collection.count()))
    hits = []
    for doc, meta, dist in zip(result["documents"][0], result["metadatas"][0], result["distances"][0]):
        hits.append({"text": doc, "metadata": meta, "distance": dist})
    return hits
