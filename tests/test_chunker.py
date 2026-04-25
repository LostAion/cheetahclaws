"""Unit tests for Phase 2.5 — sliding window chunker."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from repo_context.rag_cache import _sliding_window_chunks, CHUNK_SIZE, CHUNK_OVERLAP

def _make_content(n_chars: int, char: str = "x") -> str:
    return (char * n_chars)

# ── Test 1: Small file → single chunk ────────────────────────────────────────
small = _make_content(500)
chunks = _sliding_window_chunks(small, "small.py")
assert len(chunks) == 1, f"Expected 1 chunk, got {len(chunks)}"
assert chunks[0][1]["total_chunks"] == 1
assert chunks[0][2] == "small.py::0"
print("PASS: small file → 1 chunk")

# ── Test 2: Large file → multiple overlapping chunks ─────────────────────────
n = CHUNK_SIZE * 3 + 100
large = _make_content(n)
chunks = _sliding_window_chunks(large, "large.py")
assert len(chunks) > 1, "Expected multiple chunks"
# Check overlap: end of chunk[0] should overlap with start of chunk[1]
step = CHUNK_SIZE - CHUNK_OVERLAP
c0_end = large[CHUNK_SIZE - CHUNK_OVERLAP: CHUNK_SIZE]  # last OVERLAP chars of chunk 0
c1_start = chunks[1][0][:CHUNK_OVERLAP]                  # first OVERLAP chars of chunk 1
assert c0_end == c1_start, "Overlap mismatch"
print(f"PASS: large file ({n} chars) → {len(chunks)} chunks with correct overlap")

# ── Test 3: All chunks report same total_chunks ───────────────────────────────
totals = {c[1]["total_chunks"] for c in chunks}
assert totals == {len(chunks)}, f"Inconsistent total_chunks: {totals}"
print("PASS: total_chunks consistent across all chunks")

# ── Test 4: Empty file → no chunks ───────────────────────────────────────────
empty_chunks = _sliding_window_chunks("", "empty.py")
assert empty_chunks == [], f"Expected [], got {empty_chunks}"
print("PASS: empty file → no chunks")

# ── Test 5: Exact CHUNK_SIZE → 1 chunk ───────────────────────────────────────
exact = _make_content(CHUNK_SIZE)
exact_chunks = _sliding_window_chunks(exact, "exact.py")
assert len(exact_chunks) == 1, f"Expected 1, got {len(exact_chunks)}"
print("PASS: file exactly == CHUNK_SIZE → 1 chunk")

# ── Test 6: Chunk IDs are unique ─────────────────────────────────────────────
all_ids = [c[2] for c in chunks]
assert len(all_ids) == len(set(all_ids)), "Duplicate chunk IDs detected!"
print("PASS: all chunk IDs unique")

print()
print("All chunker tests passed.")
