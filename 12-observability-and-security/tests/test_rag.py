"""Seam: the notes folder must exist wherever the agent runs.

Specification:
  - inside the repo the notes are the shared ones of Project 4
    (`04-sistema-rag/notas`), found by walking up from the project folder;
  - the import cannot depend on the depth of the tree. In the container the code
    lives at `/app/rag.py` and `parents[2]` blew up with IndexError at import
    time, leaving the API down with the error buried in the log;
  - when there are no notes around (loose copy, CI) the project folder itself is
    used: there is nothing to index and `notes.py` degrades to "no results"
    instead of breaking the turn.
"""

from pathlib import Path

import pytest

import notes
import rag


def test_the_notes_folder_is_a_real_directory():
    assert rag.DOCS_DIR.is_dir()


def test_it_walks_up_to_find_the_shared_notes(tmp_path):
    shared = tmp_path / "04-sistema-rag" / "notas"
    project = tmp_path / "12-observability-and-security"
    shared.mkdir(parents=True)
    project.mkdir()

    assert rag._default_notes_dir(project) == shared


def test_without_shared_notes_it_uses_the_project_folder(tmp_path):
    loose = tmp_path / "app"
    loose.mkdir()

    assert rag._default_notes_dir(loose) == loose


def test_it_does_not_break_on_a_path_without_ancestors():
    """The guarantee that matters for the container bug: never IndexError.

    The old code did `parents[2]`, which for `/app/rag.py` has no third ancestor
    and blew up at import. With `/app` there are not even two levels above.
    """
    roots = [Path("C:/app"), Path("/app"), Path("C:/")]
    for path in roots:
        result = rag._default_notes_dir(path)
        assert result == path or result.is_dir()


def test_detects_whether_there_are_notes_to_index(tmp_path, monkeypatch):
    empty = tmp_path / "notes"
    empty.mkdir()
    monkeypatch.setattr(rag, "DOCS_DIR", empty)

    assert rag.has_notes_to_index() is False

    (empty / "Agentes Practica.md").write_text("# Agentes", encoding="utf-8")
    (empty / "sub").mkdir()

    assert rag.has_notes_to_index() is True


def test_without_notes_chroma_is_not_touched(tmp_path, monkeypatch):
    """With no .md files the tool says there is nothing, without opening Chroma.

    Before, `get_collection` opened the database anyway and ended up indexing
    zero documents: `count() == 0` made every query retry the indexing.
    """
    empty = tmp_path / "notes"
    empty.mkdir()
    monkeypatch.setattr(rag, "DOCS_DIR", empty)

    with pytest.raises(FileNotFoundError, match="notes"):
        rag.get_collection()


def test_search_says_the_index_is_being_prepared(monkeypatch):
    """The tool cannot answer 'not initialized' forever.

    Before, nobody set `ACTIVE_COLLECTION`: semantic search always answered
    "the notes index is not initialized", i.e. the tool was dead code. Now it
    starts on its own and says it is getting ready.
    """
    monkeypatch.setattr(notes, "ACTIVE_COLLECTION", None)
    monkeypatch.setattr(notes, "_index_error", "")
    started = []
    monkeypatch.setattr(notes, "start_index_in_background", lambda: started.append(1))

    response = notes.search_notes("¿qué es MCP?")

    assert started, "the first search has to start the indexing"
    assert "preparando" in response.lower()


def test_search_uses_the_injected_collection(monkeypatch):
    """With the collection already built, it returns fragments with source and distance."""

    class CollectionFake:
        def count(self):
            return 42

        def query(self, **_):
            return {
                "documents": [["el loop ReAct piensa, actúa y observa"]],
                "metadatas": [[{"source": "Agentes Practica.md", "position": 3}]],
                "distances": [[0.12]],
            }

    monkeypatch.setattr(notes, "ACTIVE_COLLECTION", CollectionFake())
    monkeypatch.setattr(rag, "has_notes_to_index", lambda: True)
    monkeypatch.setattr(rag, "embedder", lambda texts: [[0.1, 0.2, 0.3]])

    response = notes.search_notes("¿qué es el loop ReAct?")

    assert "Agentes Practica.md" in response
    assert "0.120" in response
    assert "ReAct" in response


def test_a_partial_indexing_run_is_not_thrown_away(tmp_path, monkeypatch):
    """If the embeddings get cut off (Gemini quota), the next run carries on.

    The real case: 410 chunks and a 429 at batch 5. Before, the batches already
    paid for were lost and every restart repeated the first ones, never getting
    to the end.
    """
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    (notes_dir / "uno.md").write_text("primera nota sobre MCP", encoding="utf-8")
    (notes_dir / "dos.md").write_text("segunda nota sobre guardrails", encoding="utf-8")
    monkeypatch.setattr(rag, "DOCS_DIR", notes_dir)

    class CollectionFake:
        def __init__(self):
            self.saved = {}
            self.batches = []

        def get(self, ids=None, **_):
            return {"ids": [i for i in ids if i in self.saved]}

        def count(self):
            return len(self.saved)

        def add(self, ids, documents, embeddings, metadatas, **_):
            self.batches.append(len(ids))
            for i, d, v, m in zip(ids, documents, embeddings, metadatas, strict=False):
                self.saved[i] = (d, v, m)

    monkeypatch.setattr(rag, "embedder", lambda texts: [[0.1, 0.2] for _ in texts])
    collection = CollectionFake()

    rag.index(collection)
    first = dict(collection.saved)
    assert len(first) == 2, "a short note is one chunk per document"

    rag.index(collection)
    assert collection.batches == [1, 1], "the second pass must not re-embed anything"
    assert dict(collection.saved) == first

    # And if something was left unsaved, it is completed without touching what was there.
    collection.saved.pop("dos.md#0")
    rag.index(collection)
    assert collection.batches[-1] == 1
    assert dict(collection.saved) == first


def test_get_collection_checks_what_is_missing(tmp_path, monkeypatch):
    """Non-empty but incomplete collection: it is indexed anyway (not only if count()==0)."""
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    (notes_dir / "uno.md").write_text("nota de MCP", encoding="utf-8")
    monkeypatch.setattr(rag, "DOCS_DIR", notes_dir)
    monkeypatch.setattr(rag, "embedder", lambda texts: [[0.1, 0.2] for _ in texts])

    class CollectionFake:
        def __init__(self):
            self.saved = {}

        def get(self, ids=None, **_):
            return {"ids": [i for i in ids if i in self.saved]}

        def count(self):
            return len(self.saved)

        def add(self, ids, documents, embeddings, metadatas, **_):
            for i, d, v, m in zip(ids, documents, embeddings, metadatas, strict=False):
                self.saved[i] = (d, v, m)

    collection = CollectionFake()
    import chromadb

    persistent = type(
        "P",
        (),
        {
            "get_or_create_collection": staticmethod(lambda **_: collection),
            "delete_collection": staticmethod(lambda n: None),
        },
    )()
    monkeypatch.setattr(chromadb, "PersistentClient", lambda path: persistent)

    rag.get_collection()
    assert collection.count() == 1

    rag.get_collection()
    assert collection.count() == 1


def test_without_notes_search_says_so_and_does_not_invent(monkeypatch):
    class CollectionEmpty:
        def count(self):
            return 0

    monkeypatch.setattr(notes, "ACTIVE_COLLECTION", CollectionEmpty())
    monkeypatch.setattr(rag, "has_notes_to_index", lambda: False)

    response = notes.search_notes("garantías")

    assert "no hay notas" in response.lower()
