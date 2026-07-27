"""
Deterministic document pipeline for company filings — the LLM-free half.

Everything in this package is a pure function over text/bytes: document-type
detection, PDF text cleaning, section splitting, chunking, financial extraction
and table extraction. No model, no provider SDK, no global state, no hidden IO —
so the batch bake that publishes the static site and a future FastAPI/Celery
worker run the exact same code, and swapping the model adapter never touches it.

    from earnings_intel.docpipe import (detect_type, clean_text, split_sections,
                                        chunk, extract_financials, extract_tables)

    kind = detect_type(doc["title"], doc["url"])          # 'concall_transcript'
    text = clean_text(raw_pdf_text)
    chunks = chunk(text, target_chars=1800, overlap=200, section="MD&A")
    facts = extract_financials(text)                      # every value quoted

Pairs with `earnings_intel.data.documents` (discovery) and
`earnings_intel.data.deepfetch` (bot-resistant PDF -> text) upstream, and with
`earnings_intel.data.docanalysis` (grounded model reading) downstream.
"""
from __future__ import annotations

from .parse import (
    DOC_TYPES,
    SECTION_TITLES,
    Chunk,
    Section,
    chunk,
    chunk_document,
    clean_text,
    detect_type,
    extract_financials,
    extract_tables,
    parse_amount,
    sentences,
    split_sections,
)

from .compare import (  # noqa: E402
    MetricDiff,
    compare_metric,
    diff_financials,
    diff_guidance,
    diff_risks,
    summarise_changes,
)
from .retrieve import (  # noqa: E402
    EmbeddingBackend,
    attach_embeddings,
    build_index,
    mentions,
    search,
    to_serialisable,
    tokenize,
)

__all__ = [
    # parse — deterministic document understanding
    "DOC_TYPES", "SECTION_TITLES", "Chunk", "Section",
    "chunk", "chunk_document", "clean_text", "detect_type",
    "extract_financials", "extract_tables", "parse_amount", "sentences",
    "split_sections",
    # compare — historical comparison agent
    "MetricDiff", "compare_metric", "diff_financials", "diff_guidance",
    "diff_risks", "summarise_changes",
    # retrieve — lexical index, embedding-ready
    "EmbeddingBackend", "attach_embeddings", "build_index", "mentions",
    "search", "to_serialisable", "tokenize",
]
