"""
openings_rag.py — RAG system for chess opening knowledge

Builds a FAISS vector index over the enriched openings JSONL file.
Each opening entry has pre-computed search_text for embedding, plus
structured fields (summary, plans, key_squares, common_mistakes)
that get returned verbatim to the agent on retrieval.

Setup (run once):
  python openings_rag.py --build --jsonl openings_enriched.jsonl

Usage from code:
  from openings_rag import search_openings
  results = search_openings("aggressive openings for White against e5")
  # Returns a list of dicts with all structured fields

Dependencies:
  pip install faiss-cpu langchain-community langchain-openai
"""

import json
import argparse
import streamlit as st
import os

os.environ["OPENAI_API_KEY"] = st.secrets["OPENAI_API_KEY"]

# Graceful degradation if RAG deps aren't installed
try:
    from langchain_openai import OpenAIEmbeddings
    from langchain_community.vectorstores import FAISS
    from langchain_core.documents import Document
    RAG_AVAILABLE = True
except ImportError:
    RAG_AVAILABLE = False

_DIR      = os.path.dirname(os.path.abspath(__file__))
INDEX_DIR = os.path.join(_DIR, "openings_faiss_index")

# Module-level cache — loaded once per process
_vectorstore = None


# ══════════════════════════════════════════════════════════════════════════════
# INDEX BUILDER
# ══════════════════════════════════════════════════════════════════════════════

def build_index(jsonl_path: str, force: bool = False) -> None:
    """
    Build the FAISS index from the enriched openings JSONL file.

    Each line of the JSONL should have at minimum:
      id, eco, name, family, moves, summary, plans_white, plans_black,
      key_squares, common_mistakes, search_text

    The search_text field is used for embedding (it concatenates name,
    ECO code, moves, and all explanatory fields into one searchable string).

    The full structured data is stored as document metadata so it can be
    returned verbatim on retrieval.
    """
    if not RAG_AVAILABLE:
        print("[openings_rag] Missing deps. Run: pip install faiss-cpu langchain-community langchain-openai")
        return

    if os.path.exists(INDEX_DIR) and not force:
        print(f"[openings_rag] Index already exists at {INDEX_DIR}. Use --force to rebuild.")
        return

    if not os.path.exists(jsonl_path):
        print(f"[openings_rag] JSONL file not found: {jsonl_path}")
        return

    print(f"[openings_rag] Loading openings from {jsonl_path}...")
    docs = []
    skipped = 0

    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue

            # Use search_text for embedding content
            search_text = entry.get("search_text", "")
            if not search_text:
                # Fallback: build from available fields
                search_text = f"{entry.get('name','')} {entry.get('eco','')} {entry.get('moves','')}"

            # Store ALL structured fields as metadata for retrieval
            metadata = {
                "id":               entry.get("id", ""),
                "eco":              entry.get("eco", ""),
                "name":             entry.get("name", ""),
                "family":           entry.get("family", ""),
                "moves":            entry.get("moves", ""),
                "summary":          entry.get("summary", ""),
                "plans_white":      entry.get("plans_white", ""),
                "plans_black":      entry.get("plans_black", ""),
                "key_squares":      entry.get("key_squares", ""),
                "common_mistakes":  entry.get("common_mistakes", ""),
                "is_obscure":       entry.get("is_obscure", False),
            }

            docs.append(Document(page_content=search_text, metadata=metadata))

    if not docs:
        print("[openings_rag] No valid entries found.")
        return

    print(f"[openings_rag] Embedding {len(docs)} openings (skipped {skipped} bad lines)...")
    print("[openings_rag] This uses OpenAI embeddings and takes ~1-2 minutes...")

    embeddings = OpenAIEmbeddings(model="text-embedding-3-small")
    store = FAISS.from_documents(docs, embeddings)
    store.save_local(INDEX_DIR)

    print(f"[openings_rag] Index saved to {INDEX_DIR}")
    print(f"[openings_rag] Total openings indexed: {len(docs)}")


# ══════════════════════════════════════════════════════════════════════════════
# INDEX LOADER
# ══════════════════════════════════════════════════════════════════════════════

def _get_vectorstore():
    """Load the FAISS index (cached after first load)."""
    global _vectorstore
    if _vectorstore is not None:
        return _vectorstore

    if not RAG_AVAILABLE:
        return None

    if not os.path.exists(INDEX_DIR):
        return None

    embeddings = OpenAIEmbeddings(model="text-embedding-3-small")
    _vectorstore = FAISS.load_local(
        INDEX_DIR, embeddings, allow_dangerous_deserialization=True
    )
    return _vectorstore


# ══════════════════════════════════════════════════════════════════════════════
# SEARCH FUNCTION
# ══════════════════════════════════════════════════════════════════════════════

def search_openings(query: str, k: int = 5) -> list[dict]:
    """
    Semantic search over the openings index.

    Parameters
    ----------
    query : natural language query about chess openings
    k     : number of results to return (default 5)

    Returns a list of dicts, each containing:
      eco, name, family, moves, summary, plans_white, plans_black,
      key_squares, common_mistakes, is_obscure
    """
    store = _get_vectorstore()
    if store is None:
        return []

    try:
        results = store.similarity_search(query, k=k)
        return [doc.metadata for doc in results]
    except Exception:
        return []


def format_results_for_agent(results: list[dict]) -> str:
    """
    Format search results into a clean string the agent can read and
    present to the user.
    """
    if not results:
        return "No matching openings found."

    lines = []
    for i, r in enumerate(results, 1):
        lines.append(f"--- Opening {i} ---")
        lines.append(f"Name: {r.get('name', '?')}")
        lines.append(f"ECO: {r.get('eco', '?')}")
        lines.append(f"Moves: {r.get('moves', '?')}")
        lines.append(f"Summary: {r.get('summary', '')}")
        lines.append(f"Plans for White: {r.get('plans_white', '')}")
        lines.append(f"Plans for Black: {r.get('plans_black', '')}")
        lines.append(f"Key squares: {r.get('key_squares', '')}")
        lines.append(f"Common mistakes: {r.get('common_mistakes', '')}")
        lines.append("")

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build FAISS openings index")
    parser.add_argument("--build", action="store_true", help="Build the index")
    parser.add_argument("--jsonl", metavar="FILE", help="Path to enriched openings JSONL")
    parser.add_argument("--force", action="store_true", help="Rebuild even if exists")
    parser.add_argument("--test", metavar="QUERY", help="Test search with a query")
    parser.add_argument("--k", type=int, default=3, help="Number of results (default 3)")
    args = parser.parse_args()

    if args.build and args.jsonl:
        build_index(args.jsonl, force=args.force)
    elif args.test:
        results = search_openings(args.test, k=args.k)
        if results:
            print(format_results_for_agent(results))
        else:
            print("No results (is the index built?)")
    else:
        print("Usage:")
        print("  Build:  python openings_rag.py --build --jsonl openings_enriched.jsonl")
        print("  Test:   python openings_rag.py --test 'aggressive openings against e5'")
        print("  Rebuild: python openings_rag.py --build --jsonl file.jsonl --force")
