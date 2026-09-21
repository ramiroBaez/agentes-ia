# RAG: Retrieval-Augmented Generation

An LLM only "knows" what it learned up to its training date. **RAG** makes a
model answer from *your own documents* by retrieving the relevant pieces at
query time and injecting them as context.

Use it for question answering over a private knowledge base (notes, PDFs,
docs) instead of retraining or fine-tuning.

## The pipeline

1. **Chunking**: split each document into pieces of ~N characters, with
   overlap so meaning isn't cut at boundaries.
2. **Embeddings**: convert every chunk into a vector with an embedding model
   (e.g. `gemini-embedding-001`). Similar meaning -> similar vector.
3. **Storage**: save the vectors in a vector database. ChromaDB keeps things
   simple with a `PersistentClient` on disk.
4. **Query**: embed the user's question, search the top-K most similar chunks
   using cosine similarity.
5. **Generation**: build a prompt with those chunks as context
   ("answer using ONLY the context given") and ask the LLM for the answer,
   citing the sources used.

## Design decisions

- **Chunk size and overlap** balance context density vs. retrieval precision.
- **Metadata** (source file, position) lets you show the user where each
  answer came from.
- **Emphasize grounding**: the system instruction should forbid the model
  from inventing facts outside the retrieved context.
- **Free-tier embeddings** are rate limited: batch the requests and respect
  the API's suggested retry delay on 429 responses.

Two separate concerns that must not be mixed: *retrieval* finds what is
similar, and *generation* answers using only that retrieved evidence.