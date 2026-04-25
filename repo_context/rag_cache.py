"""
repo_context/rag_cache.py — Local ChromaDB-backed RAG cache with sliding-window chunking.
"""
import os
import threading

try:
    import chromadb
except ImportError:
    chromadb = None

# ── Chunking constants ────────────────────────────────────────────────────────
# CHUNK_SIZE: max chars per chunk. ~1500 chars ≈ ~350 tokens — safe for embedding.
# CHUNK_OVERLAP: overlap between consecutive chunks to preserve semantic continuity
# across boundaries (e.g., a function definition split across a chunk edge).
CHUNK_SIZE = 1500
CHUNK_OVERLAP = 200

_INDEXABLE_EXTENSIONS = (
    '.py', '.js', '.ts', '.html', '.jsx', '.tsx',
    '.go', '.rs', '.java', '.c', '.cpp', '.h', '.md',
)

_EXCLUDED_DIRS = {'.git', '.cheetahclaws', 'node_modules', '.venv', '__pycache__', '.mypy_cache'}


def _sliding_window_chunks(content: str, file_path: str) -> list[tuple[str, dict, str]]:
    """
    Produces (document, metadata, id) triples for a file using a sliding window.

    Returns a list of (text_chunk, metadata_dict, unique_chunk_id) tuples.
    Each chunk is CHUNK_SIZE characters with CHUNK_OVERLAP characters of overlap
    with its predecessor, ensuring nothing is semantically invisible to the embedder.
    """
    chunks = []
    step = CHUNK_SIZE - CHUNK_OVERLAP
    total = len(content)

    if total == 0:
        return []

    # For small files, emit as a single chunk — no padding overhead.
    if total <= CHUNK_SIZE:
        return [(content, {"path": file_path, "chunk_index": 0, "total_chunks": 1}, f"{file_path}::0")]

    chunk_index = 0
    pos = 0
    while pos < total:
        chunk_text = content[pos: pos + CHUNK_SIZE]
        if not chunk_text.strip():
            pos += step
            continue

        # We don't know total_chunks upfront, so we patch it below.
        chunks.append((chunk_text, {"path": file_path, "chunk_index": chunk_index}, f"{file_path}::{chunk_index}"))
        chunk_index += 1
        pos += step

    # Patch total_chunks into all metadata dicts now that we know the count.
    total_chunks = len(chunks)
    chunks = [
        (text, {**meta, "total_chunks": total_chunks}, uid)
        for text, meta, uid in chunks
    ]
    return chunks


class RAGCache:
    def __init__(self, workspace_root: str):
        self.workspace_root = workspace_root
        self.db_dir = os.path.join(workspace_root, ".cheetahclaws", "chromadb")
        self.client = None
        self.collection = None

        if chromadb:
            os.makedirs(self.db_dir, exist_ok=True)
            try:
                self.client = chromadb.PersistentClient(path=self.db_dir)
                self.collection = self.client.get_or_create_collection(name="workspace")
            except Exception as e:
                try:
                    import logging_utils
                    logging_utils.warn(f"RAG: PersistentClient initialization failed: {e}")
                except ImportError:
                    print(f"  [RAG] Warning: ChromaDB init failed: {e}")

    def background_index(self):
        """Speed 2: Walk the workspace and upsert all chunks into the vector store."""
        if not self.collection:
            return

        def _index_job():
            all_docs, all_metas, all_ids = [], [], []

            for root, dirs, files in os.walk(self.workspace_root):
                # Prune excluded directories in-place so os.walk doesn't descend into them.
                dirs[:] = [d for d in dirs if d not in _EXCLUDED_DIRS]

                for fname in files:
                    if not fname.endswith(_INDEXABLE_EXTENSIONS):
                        continue
                    file_path = os.path.join(root, fname)
                    try:
                        with open(file_path, "r", encoding="utf-8", errors="replace") as fh:
                            content = fh.read()

                        for text, meta, uid in _sliding_window_chunks(content, file_path):
                            all_docs.append(text)
                            all_metas.append(meta)
                            all_ids.append(uid)

                    except Exception as e:
                        try:
                            import logging_utils
                            logging_utils.debug(f"RAG: Skipping {file_path}: {e}")
                        except ImportError:
                            pass

            if not all_docs:
                return

            # Upsert in batches of 50 to avoid hitting ChromaDB's per-call limits.
            batch = 50
            for i in range(0, len(all_docs), batch):
                try:
                    self.collection.upsert(
                        documents=all_docs[i: i + batch],
                        metadatas=all_metas[i: i + batch],
                        ids=all_ids[i: i + batch],
                    )
                except Exception as e:
                    try:
                        import logging_utils
                        logging_utils.warn(f"RAG: Upsert batch {i//batch} failed: {e}")
                    except ImportError:
                        pass

        t = threading.Thread(target=_index_job, daemon=True, name="rag-indexer")
        t.start()

    def semantic_search(self, query: str, n_results: int = 5) -> str:
        """Perform a semantic query and return the matching chunks with provenance."""
        if not self.collection:
            return "RAG Cache unavailable (ChromaDB not initialised)."

        try:
            results = self.collection.query(
                query_texts=[query],
                n_results=n_results,
            )
            if not results["documents"] or not results["documents"][0]:
                return "No relevant context found."

            out = []
            for doc, meta in zip(results["documents"][0], results["metadatas"][0]):
                chunk_label = f"chunk {meta.get('chunk_index', '?')}/{meta.get('total_chunks', '?')}"
                out.append(f"--- {meta['path']} ({chunk_label}) ---\n{doc}")
            return "\n\n".join(out)

        except Exception as e:
            return f"RAG search error: {e}"


# ── Module-level singleton ────────────────────────────────────────────────────

_rag_instance: "RAGCache | None" = None


def get_rag(workspace_root: str | None = None) -> RAGCache:
    """Return (or lazily create) the singleton RAGCache for this process."""
    global _rag_instance
    if _rag_instance is None:
        _rag_instance = RAGCache(workspace_root or os.getcwd())
    return _rag_instance


def init_two_speed(workspace_root: str) -> None:
    """
    Two-Speed startup:
      Speed 1 — Skeleton AST map is built lazily on first Read() call (no work here).
      Speed 2 — Full sliding-window vector indexing starts in a background thread now.
    """
    rag = get_rag(workspace_root)
    rag.background_index()


def rag_search(params: dict, config: dict) -> str:
    """Tool entry-point: semantic search across the entire indexed codebase."""
    query = params.get("query", "").strip()
    if not query:
        return "Error: 'query' parameter is required."
    return get_rag().semantic_search(query, n_results=params.get("n_results", 5))
