# 🐆 Kevlar-Claws v2.0: The Verifiable Co-Pilot Refactor

> **Branch:** `v2-refactor`  
> **Status:** Feature Complete / Production Hardened  
> **Mission:** Transform `cheetahclaws` from a heuristic-based agent into a high-velocity, interactive co-pilot with verifiable context and safe state management.

---

## 🎯 The Why: Solving the "Brittle Agent" Problem

Before v2.0, autonomous coding agents (including earlier versions of `cheetahclaws`) suffered from three fundamental bottlenecks that made them "brittle" in production environments:

### 1. The State Desync (The "Clobbering" Issue)
Agents often operate on a stale view of the disk. If a user manually edits a file while the agent is "thinking," the agent might overwrite those changes (clobbering), leading to data loss and frustration.
*   **V2 Solution:** A **Hybrid Merge Pipeline** that turns every edit into a verifiable transaction.

### 2. The Context Bloat (Token Toxicity)
Dumping entire source files into an LLM context is expensive, slow, and "toxic" to reasoning. Large files drown the important logic in boilerplate, causing the model to miss subtle architectural patterns.
*   **V2 Solution:** **Skeleton AST Mapping**—using Tree-sitter to "fold" the codebase into a dense architectural summary while keeping the model's eyes on the structure.

### 3. The Semantic Blindness
Heuristic "grep" and "glob" tools are good for finding exact strings, but bad at finding *logic*. If an agent doesn't know where the "user authentication pipeline" is, it has to scan everything.
*   **V2 Solution:** **Two-Speed RAG**—background vector indexing that builds a semantic "brain" for the repo without slowing down the initial boot.

---

## 🛠️ The What: Key Architectural Pillars

### 🏗️ 1. Context Skeletonization (AST-Native)
We replaced blunt line-truncation with a language-aware **Tree-sitter** engine.
*   **Dense Mapping:** Functions and classes are automatically collapsed into "skeletons" (e.g., signatures + docstrings only). 
*   **Lazy Expansion:** A new `ExpandNode` tool allows the agent to selectively "unfold" specific function bodies only when it needs to edit or deeply understand them.
*   **Result:** ~54% average reduction in token usage for project-wide reads without losing architectural awareness.

### 🔄 2. Hybrid Merge Pipeline (Terminal DX)
The REPL is no longer a "black box" that blind-writes to your disk.
*   **Verification:** Every edit is hashed against a snapshot. If the file changed on disk, the agent triggers a **StateDesyncError**.
*   **Tier 1 (Auto):** Attempt an automated 3-way merge using `git merge-file`.
*   **Tier 2 (Manual):** If conflicts exist, the system launches your `$EDITOR` (VS Code, Vim, Notepad) to let you resolve the "Agent Intent" vs. "Disk Reality" in real-time.

### 🧠 3. Two-Speed RAG (ChromaDB)
Semantic search is now a first-class citizen using a dual-track strategy:
*   **Speed 1 (Startup):** Immediate AST skeleton map for architectural hits.
*   **Speed 2 (Background):** A background daemon threads through the repo, generating **sliding-window chunks** (1500 chars/200 overlap) and indexing them into a local **ChromaDB** store.
*   **Semantic Tooling:** The agent can now use `RAGSearch` to find logic purely by description (e.g., *"Where do we handle JWT signing?"*).

---

## 🚀 Technical Specifications

| Component | Technology | Benefit |
|-----------|------------|---------|
| **AST Parser** | `tree-sitter` (Py, JS, TS, HTML) | Language-accurate code folding |
| **Vector DB** | `ChromaDB` (Persistent) | Local, zero-config semantic memory |
| **Embeddings** | `all-MiniLM-L6-v2` | Fast, local execution (no API cost) |
| **Merge Logic** | `git merge-file` + `shlex` | Standardized, safe conflict resolution |
| **UI/UX** | `rich` + `prompt_toolkit` | High-fidelity interactive terminal |

---

## 📖 How to Use the V2 Features

### Semantic Querying
```bash
> /call RAGSearch query="database connection logic"
```

### Navigating the Skeleton
When you see `... [BODY EXCLUDED] ...` in a file read, the agent will automatically use:
```bash
> /call ExpandNode file_path="core/engine.py" node_name="run_loop"
```

### Handling a Desync
If you edit a file while `cheetahclaws` is working, you'll see:
```text
State Desync Detected! The file core/state_manager.py has changed on disk.
How do you want to resolve this?
  [O]verwrite  : Ignore your changes and force the Agent's version.
  [M]erge      : Enter interactive merge resolution.
  [A]bort      : Reject the edit.
```

---

*This branch represents the evolution of CheetahClaws into a production-ready developer assistant. It prioritizes safety, token efficiency, and semantic depth over simple autonomous scripting.*
