# RAG Implementation in SAGE — Complete Technical Document

## Overview

SAGE implements **Hybrid RAG** (FTS5 keyword search + vector embeddings) with **async, fire-and-forget embedding generation**. The RAG context is auto-injected into the system prompt on every turn.

**Key principle:** Embedding generation is **asynchronous and non-blocking**. The LLM call proceeds with whatever context is available; embeddings are generated in the background for future turns.

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           SAGE RAG PIPELINE                                  │
└─────────────────────────────────────────────────────────────────────────────┘

    USER MESSAGE
         │
         ▼
    ┌──────────────────────────────────────────┐
    │ MessageStore.append_user()               │
    │   ├─ INSERT INTO messages                │
    │   ├─ INSERT INTO messages_fts (FTS5)     │
    │   └─ asyncio.create_task(embed_passage)  │──► NVIDIA API (async, non-blocking)
    └──────────────────────────────────────────┘
         │
         ▼
    ┌──────────────────────────────────────────┐
    │ ContextBuilder.build()                   │
    │   1. Get recent messages (tail)          │
    │   2. Get last user query                 │
    │   3. HYBRID RETRIEVAL (parallel):        │
    │      ├─ FTS5: message_store.search()     │──► Local SQLite (keyword)
    │      └─ Embedding: provider.embed_query()│──► NVIDIA API (vector)
    │           │                              │
    │           ▼                              │
    │      EmbeddingStore.search()             │──► Local cosine similarity
    │           │                              │
    │           ▼                              │
    │      MERGE: 40% FTS5 + 60% embedding     │
    │           │                              │
    │           ▼                              │
    │      Inject as system message            │
    └──────────────────────────────────────────┘
         │
         ▼
    ┌──────────────────────────────────────────┐
    │ ConversationLoop → Nebius LLM            │
    │   POST /chat/completions                 │
    │   DeepSeek-V4-Flash (streaming)          │
    └──────────────────────────────────────────┘
```

---

## Exact API Calls

### 1. Embedding Generation (NVIDIA) — Background Task

**When:** `MessageStore.append_user()` / `append_assistant()` / `append_tool_result()`

**Request:**
```http
POST https://integrate.api.nvidia.com/v1/embeddings
Authorization: Bearer $NVIDIA_API_KEY
Content-Type: application/json

{
  "model": "nvidia/nemotron-3-embed-1b",
  "input": "User message text here",
  "input_type": "passage",      // "query" for searches, "passage" for indexing
  "encoding_format": "float"
}
```

**Response:**
```json
{
  "object": "list",
  "data": [
    {
      "object": "embedding",
      "embedding": [0.12, -0.45, 0.78, ...],  // 1024 floats (float32)
      "index": 0
    }
  ],
  "model": "nvidia/nemotron-3-embed-1b",
  "usage": {"prompt_tokens": 12, "total_tokens": 12}
}
```

**Storage:** Serialized to float32 BLOB in `message_embeddings` table
```sql
CREATE TABLE message_embeddings (
    message_id TEXT PRIMARY KEY,    -- FK to messages.id
    embedding BLOB NOT NULL,        -- 1024 × 4 bytes = 4KB
    model TEXT NOT NULL,
    dim INTEGER NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);
```

---

### 2. Embedding Search (NVIDIA) — During Context Build

**When:** `ContextBuilder.build()` → `_build_rag_context()`

**Request:**
```http
POST https://integrate.api.nvidia.com/v1/embeddings
{
  "model": "nvidia/nemotron-3-embed-1b",
  "input": "What Python framework did I say I use?",
  "input_type": "query",
  "encoding_format": "float"
}
```

**Local Cosine Similarity:**
```python
# EmbeddingStore.search()
query_norm = sqrt(sum(q*q for q in query_vec))
for msg_id, blob in stored_embeddings:
    emb = deserialize(blob)  # float32[1024]
    emb_norm = sqrt(sum(e*e for e in emb))
    sim = dot(query_vec, emb) / (query_norm * emb_norm)
    # Returns top-K by similarity desc
```

---

### 3. FTS5 Keyword Search (Local SQLite)

**When:** `ContextBuilder.build()` → `_build_rag_context()`

**Query:**
```sql
SELECT m.id, m.role, m.content
FROM messages_fts f
JOIN messages m ON m.id = f.rowid
WHERE messages_fts MATCH ?
ORDER BY rank
LIMIT 15
```

**Input:** Escaped query string (special chars quoted)
```python
# "python framework" → '"python framework"'
# "what's up?" → '"what\'s up?"'
```

---

### 4. LLM Generation (Nebius) — Main Response

**When:** `ConversationLoop._run_turn()` → `inference_manager.stream()`

**Request:**
```http
POST https://api.tokenfactory.nebius.com/v1/chat/completions
Authorization: Bearer $NEBIUS_API_KEY
Content-Type: application/json

{
  "model": "deepseek-ai/DeepSeek-V4-Flash-0731",
  "messages": [
    {"role": "system", "content": "SOUL.md..."},
    {"role": "system", "content": "Relevant context: [score=1.000] I use FastAPI..."},
    {"role": "user", "content": "What Python framework did I say I use?"}
  ],
  "tools": [...],  // 20 tool definitions
  "stream": true,
  "temperature": 0.2
}
```

**Streaming Response (SSE):**
```
data: {"choices":[{"delta":{"content":"Let me"}}]}
data: {"choices":[{"delta":{"content":" record that"}}]}
...
data: {"choices":[{"delta":{"tool_calls":[...]}}]}
data: {"choices":[{"delta":{}}],"usage":{"prompt_tokens":13703,"completion_tokens":134}}
```

---

## Timing Breakdown (Typical Turn)

| Step | Operation | Time | Blocking? |
|------|-----------|------|-----------|
| 1 | `append_user()` + FTS5 insert | ~2ms | Yes |
| 2 | `append_user()` → `create_task(embed_passage)` | ~0.1ms | **No** (fire-and-forget) |
| 2 | `ContextBuilder.build()` | ~5ms | Yes |
| 3 | `embed_query()` → NVIDIA | ~80ms | **Yes** (part of context build) |
| 4 | FTS5 search (local) | ~2ms | Yes |
| 5 | Cosine similarity (local) | ~3ms | Yes |
| 6 | Merge + format RAG context | ~1ms | Yes |
| 7 | LLM call → Nebius | ~2000ms | **Yes** (main latency) |
| **Total perceived** | **~2100ms** | | |

**Key insight:** Only `embed_query()` (step 3) blocks the turn. `embed_passage()` (step 2) runs in background.

---

## Code Locations

| Component | File | Key Methods |
|-----------|------|-------------|
| MessageStore | `app/persistence/store.py` | `append_user()`, `_append()`, `_generate_embedding()` |
| EmbeddingProvider | `app/embeddings/provider.py` | `embed_query()`, `embed_passage()`, `embed_batch()` |
| EmbeddingStore | `app/embeddings/store.py` | `save()`, `search()`, `has_embedding()` |
| ContextBuilder | `app/context/builder.py` | `build()`, `_build_rag_context()`, `_merge_results()` |
| ConversationLoop | `app/conversation/loop.py` | `_run_turn()`, `handle_stream()` |
| InferenceManager | `app/inference/manager.py` | `stream()`, `generate()` |

---

## Configuration

```python
# app/config.py
rag_enabled: bool = True
rag_top_k: int = 5
rag_fts_weight: float = 0.4
rag_embedding_weight: float = 0.6
rag_similarity_threshold: float = 0.65
rag_exclude_recent_turns: int = 20
rag_embedding_model: str = "nvidia/nemotron-3-embed-1b"
rag_fallback_model: str = "Qwen/Qwen3-Embedding-8B"
rag_embedding_dim: int = 1024
```

---

## Edge Cases Handled

| Scenario | Behavior |
|----------|----------|
| Embedding API fails | Logs warning, continues with FTS5 only |
| FTS5 syntax error (special chars) | Auto-escape with `_escape_fts5()` |
| No embeddings yet | Skip embedding search, use FTS5 only |
| Query too short | Still works, returns lower scores |
| Dimension mismatch | Auto-truncate/pad to `rag_embedding_dim` |
| First message (no history) | Returns empty RAG context |

---

## Verification

```bash
# Check embeddings exist
sqlite3 data/sage.db "SELECT COUNT(*) FROM message_embeddings;"

# Test embedding generation
python -c "
from app.application import SAGEApplication
import asyncio
async def t():
    app = SAGEApplication(); await app.start()
    emb = await app.embedding_provider.embed_query('test')
    print(f'Dim: {len(emb)}')
    await app.stop()
asyncio.run(t())
"

# Run backfill
python scripts/backfill_embeddings.py
```

---

## Summary

| Question | Answer |
|----------|--------|
| **Is RAG local?** | Yes — vectors & search in SQLite; only embedding *generation* uses API |
| **When is embedding created?** | Fire-and-forget on every message append |
| **When is embedding used for search?** | During `ContextBuilder.build()`, before LLM call |
| **What goes to NVIDIA?** | Text only; returns 1024-dim vector |
| **What goes to Nebius?** | Full prompt + RAG context; returns LLM response |
| **Why fast?** | Embedding search is local cosine sim; only LLM call is slow (~2s) |