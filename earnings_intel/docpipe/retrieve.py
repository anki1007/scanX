"""
Retrieval index (pipeline agents 11-12) — BM25 lexical search over document
chunks with NO vector database, NO embeddings and NO network.

scanX publishes a STATIC site, so retrieval has to survive as a plain JSON
artefact: `build_index` emits one JSON-serialisable dict (postings + df/idf +
the chunks themselves) that a bake can write to disk and a worker can load back
unchanged. Nothing here is provider-specific — swapping Gemini for OpenAI or a
local model never touches this file.

Embedding-ready, not embedding-dependent: `attach_embeddings(index, embed_fn)`
stores vectors alongside the postings and `search` then blends lexical + cosine.
With no embeddings attached (or after a JSON round-trip, where the callable is
gone) search silently stays purely lexical — same call, same result shape.

Every hit carries its own provenance (section, doc_kind, doc_date, url, plus any
extra chunk metadata such as page) so an answer can ALWAYS cite the document and
section it came from.

Usage:
    from earnings_intel.docpipe import retrieve

    chunks = [{"text": "EBITDA margin expanded to 14% in the March quarter.",
               "section": "Margins", "doc_kind": "concall_transcript",
               "doc_date": "2026-05-01", "url": "https://.../transcript.pdf"}]
    index = retrieve.build_index(chunks)
    json.dump(retrieve.to_serialisable(index), open("index.json", "w"))

    hits = retrieve.search(index, "what changed in margins?", k=5)
    hits[0]["section"], hits[0]["url"], hits[0]["score"]

    retrieve.mentions(index, "EV")            # every mention, newest doc first

    idx2 = retrieve.attach_embeddings(index, my_embed_fn)   # optional hybrid
    retrieve.search(idx2, "margin outlook")                 # lexical + cosine
"""
from __future__ import annotations

import logging
import math
import re
from typing import Any, Optional, Protocol, Sequence

log = logging.getLogger("technofunda.docpipe.retrieve")

__all__ = [
    "EmbeddingBackend",
    "build_index",
    "search",
    "mentions",
    "attach_embeddings",
    "to_serialisable",
    "tokenize",
    "INDEX_VERSION",
    "META_FIELDS",
    "DEFAULT_ALPHA",
    "K1",
    "B",
]

# reuse the filing-text normaliser (typography/whitespace) instead of copying it
try:  # pragma: no cover - exercised implicitly by every call
    from ..data.docanalysis import normalise as normalise_text
except Exception:  # noqa: BLE001 - docpipe must stay importable standalone
    def normalise_text(text: Any) -> str:  # type: ignore[misc]
        """Fallback: collapse whitespace only (docanalysis unavailable)."""
        return re.sub(r"\s+", " ", str(text or "")).strip()

INDEX_VERSION = 1
K1 = 1.5                 # BM25 term-frequency saturation
B = 0.75                 # BM25 length normalisation
DEFAULT_ALPHA = 0.5      # hybrid blend weight given to cosine similarity

#: metadata every hit is guaranteed to carry, so answers can cite their source
META_FIELDS = ("section", "doc_kind", "doc_date", "url")

_TOKEN = re.compile(r"[a-z0-9]+(?:\.[a-z0-9]+)*")

# dropped from QUERIES only — the index stays complete so mentions() is exhaustive
_STOP_WORDS = """
a an the of to for in on at by and or is was are were be been being with from as
that this these those it its we our you your they their he she what which who
whom how why when where does do did doing done can could should would will shall
may might must have has had there here about into over under please tell show me
"""


# ------------------------------------------------------------ pure: tokenising
def _fold(token: str) -> str:
    """One light plural fold: "margins"->"margin", "EVs"->"ev". Kept above the
    3-letter mark that most Indian-market acronyms live at (EV/PV/CV), and never
    applied to "-ss" endings."""
    if len(token) > 2 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


# folded with the same rule, so "was" cannot slip back in as the token "wa"
_QUERY_STOP = frozenset(_fold(w) for w in _STOP_WORDS.split())


def tokenize(text: Any) -> list[str]:
    """Text -> comparable tokens. Deterministic, language-agnostic, no deps.

    Lower-cases, folds PDF typography via docanalysis.normalise, keeps decimals
    ("14.5" stays one token) and folds plurals so "margins" matches "margin" and
    "EVs" matches "EV". The SAME function runs at index and query time, so the
    two sides can never disagree.
    """
    s = normalise_text(text).lower()
    return [_fold(t) for t in _TOKEN.findall(s)]


def _query_tokens(query: Any) -> list[str]:
    toks = tokenize(query)
    kept = [t for t in toks if t not in _QUERY_STOP]
    return kept or toks          # an all-stopword query still gets to search


# ---------------------------------------------------------------- pure: index
def _row(chunk: Any) -> dict:
    """Chunk -> stored record: text first, all metadata preserved, the four
    citation fields always present (empty string when the caller omitted them)."""
    d = chunk if isinstance(chunk, dict) else {"text": chunk}
    row: dict = {"text": str(d.get("text") or "")}
    for key, value in d.items():
        if key == "text":
            continue
        row[str(key)] = value
    for key in META_FIELDS:
        row.setdefault(key, "")
    return row


def build_index(chunks: list[dict], *, k1: float = K1, b: float = B) -> dict:
    """Chunks -> a BM25 index that is plain JSON. PURE.

    Chunks are dicts with "text" plus whatever metadata the chunker attached
    ("section", "doc_kind", "doc_date", "url", "page", ...); bare strings are
    accepted too. Terms are sorted so two bakes over the same corpus produce a
    byte-identical artefact (clean diffs, cacheable downloads).

    Returns {"version", "doc_count", "avg_len", "lengths", "df", "idf",
    "postings", "chunks", "params", "embeddings"} — postings are
    [[chunk_idx, term_frequency], ...] lists, which survive json round-trips
    exactly (dict keys would come back as strings).
    """
    rows: list[dict] = []
    lengths: list[int] = []
    raw_postings: dict = {}

    for i, chunk in enumerate(chunks or []):
        row = _row(chunk)
        toks = tokenize(row["text"])
        freqs: dict = {}
        for t in toks:
            freqs[t] = freqs.get(t, 0) + 1
        for t, n in freqs.items():
            raw_postings.setdefault(t, []).append([i, n])
        rows.append(row)
        lengths.append(len(toks))

    n_docs = len(rows)
    postings = {t: raw_postings[t] for t in sorted(raw_postings)}
    df = {t: len(p) for t, p in postings.items()}
    # Lucene's non-negative idf: a term in every chunk still scores > 0
    idf = {t: round(math.log(1.0 + (n_docs - d + 0.5) / (d + 0.5)), 6)
           for t, d in df.items()}
    avg_len = round(sum(lengths) / n_docs, 4) if n_docs else 0.0

    return {"version": INDEX_VERSION, "doc_count": n_docs, "avg_len": avg_len,
            "lengths": lengths, "df": df, "idf": idf, "postings": postings,
            "chunks": rows, "params": {"k1": float(k1), "b": float(b)},
            "embeddings": None}


def to_serialisable(index: dict) -> dict:
    """Copy of `index` without runtime-only keys (the attached embed callable),
    ready for json.dump. The result still searches — lexically."""
    return {k: v for k, v in (index or {}).items() if not str(k).startswith("_")}


# --------------------------------------------------------------- pure: scoring
def _bm25(index: dict, tokens: list[str]) -> dict:
    """{chunk_idx: score} for the tokens that exist in the index."""
    postings = index.get("postings") or {}
    idf_map = index.get("idf") or {}
    lengths = index.get("lengths") or []
    params = index.get("params") or {}
    k1 = float(params.get("k1", K1))
    b = float(params.get("b", B))
    avg_len = float(index.get("avg_len") or 0.0) or 1.0

    scores: dict = {}
    for t in tokens:
        plist = postings.get(t)
        if not plist:
            continue
        idf = float(idf_map.get(t, 0.0))
        if idf <= 0:
            continue
        for pair in plist:
            try:
                i, tf = int(pair[0]), float(pair[1])
            except Exception:  # noqa: BLE001 - tolerate a hand-edited artefact
                continue
            dl = float(lengths[i]) if 0 <= i < len(lengths) else avg_len
            denom = tf + k1 * (1.0 - b + b * (dl / avg_len))
            if denom <= 0:
                continue
            scores[i] = scores.get(i, 0.0) + idf * (tf * (k1 + 1.0) / denom)
    return scores


def _cosines(index: dict, qvec: Sequence[float]) -> dict:
    emb = index.get("embeddings") or {}
    vectors = emb.get("vectors") or []
    norms = emb.get("norms") or []
    qnorm = math.sqrt(sum(float(x) * float(x) for x in qvec))
    if qnorm <= 0:
        return {}
    out: dict = {}
    for i, vec in enumerate(vectors):
        norm = float(norms[i]) if i < len(norms) else 0.0
        if norm <= 0 or len(vec) != len(qvec):
            continue
        dot = sum(float(a) * float(b_) for a, b_ in zip(vec, qvec))
        out[i] = dot / (norm * qnorm)
    return out


def _hit(row: dict, idx: int, score: float, lexical: float,
         vector: Optional[float], extra: Optional[dict] = None) -> dict:
    """One result, always carrying enough provenance to cite the source."""
    hit = {"chunk_idx": int(idx), "score": round(float(score), 6),
           "text": row.get("text", ""),
           "section": row.get("section", ""),
           "doc_kind": row.get("doc_kind", ""),
           "doc_date": row.get("doc_date", ""),
           "url": row.get("url", ""),
           "lexical": round(float(lexical), 6),
           "vector": None if vector is None else round(float(vector), 6)}
    for key, value in row.items():
        if key != "text" and key not in hit:
            hit[key] = value
    if extra:
        hit.update(extra)
    return hit


def search(index: dict, query: str, k: int = 8, *,
           embed_fn: Optional["EmbeddingBackend"] = None,
           alpha: float = DEFAULT_ALPHA) -> list[dict]:
    """Top-`k` chunks for `query`, best first. PURE, no network.

    Pure BM25 by default. If the index carries embeddings AND a query vector can
    be produced (either `embed_fn` here, or the callable `attach_embeddings`
    kept on the index in-process), scores become
    `(1 - alpha) * normalised_lexical + alpha * cosine`; otherwise the lexical
    score is used untouched. Ties break on chunk_idx, so results are stable.

    Every hit is {"chunk_idx", "score", "text", "section", "doc_kind",
    "doc_date", "url", "lexical", "vector", ...any other chunk metadata} — the
    caller can always name the document and section behind an answer.
    """
    idx = index if isinstance(index, dict) else {}
    rows = idx.get("chunks") or []
    top = int(k or 0)
    if not rows or top <= 0:
        return []

    tokens = _query_tokens(query)
    lex = _bm25(idx, tokens) if tokens else {}

    qvec = None
    fn = embed_fn or idx.get("_embed_fn")
    if idx.get("embeddings") and fn is not None:
        qvec = _embed_one(fn, query)
    cos = _cosines(idx, qvec) if qvec else {}

    if cos:
        a = min(max(float(alpha), 0.0), 1.0)
        top_lex = max(lex.values()) if lex else 0.0
        candidates = set(lex) | set(cos)
        scored = []
        for i in candidates:
            lx = lex.get(i, 0.0)
            cs = max(0.0, cos.get(i, 0.0))
            norm = (lx / top_lex) if top_lex > 0 else 0.0
            scored.append((i, (1.0 - a) * norm + a * cs, lx, cos.get(i)))
    else:
        scored = [(i, s, s, None) for i, s in lex.items()]

    scored.sort(key=lambda t: (-t[1], t[0]))
    out: list[dict] = []
    for i, score, lx, cs in scored:
        if score <= 0 or not (0 <= i < len(rows)):
            continue
        out.append(_hit(rows[i], i, score, lx, cs))
        if len(out) >= top:
            break
    return out


# ------------------------------------------------------------ pure: mentions
def mentions(index: dict, term: str, *, limit: int = 0) -> list[dict]:
    """EVERY chunk mentioning `term`, newest document first. PURE.

    Answers "show me every mention of EV" exhaustively — no top-k cut-off unless
    you pass `limit`. Single words and phrases both work ("order book" only
    matches the words adjacent); matching is case-insensitive and tolerates the
    singular/plural of each word. Each hit adds "count", the number of
    occurrences inside that chunk. Ordered by doc_date descending, then by
    chunk order, which is stable for chunks that share a date.
    """
    idx = index if isinstance(index, dict) else {}
    rows = idx.get("chunks") or []
    tokens = tokenize(term)
    if not rows or not tokens:
        return []

    postings = idx.get("postings") or {}
    candidates: Optional[set] = None
    for t in tokens:
        ids = {int(p[0]) for p in (postings.get(t) or []) if p}
        candidates = ids if candidates is None else (candidates & ids)
        if not candidates:
            return []

    pattern = re.compile(r"\b" + r"[^a-z0-9]+".join(re.escape(t) + "s?"
                                                    for t in tokens) + r"\b")
    hits: list[dict] = []
    for i in sorted(candidates or set()):
        if not (0 <= i < len(rows)):
            continue
        count = len(pattern.findall(normalise_text(rows[i].get("text")).lower()))
        if count <= 0:
            continue
        hits.append(_hit(rows[i], i, float(count), float(count), None,
                         extra={"count": count, "term": " ".join(tokens)}))

    hits.sort(key=lambda h: str(h.get("doc_date") or ""), reverse=True)
    n = int(limit or 0)
    return hits[:n] if n > 0 else hits


# ----------------------------------------------------------------- embeddings
class EmbeddingBackend(Protocol):
    """Any callable turning texts into fixed-width float vectors.

    Deliberately structural: a plain function, a bound method or an SDK client
    wrapper all satisfy it, so a provider swap is a one-line change at the call
    site and never touches this module.

        def embed(texts: Sequence[str]) -> Sequence[Sequence[float]]: ...
    """

    def __call__(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        ...  # pragma: no cover - structural typing only


def _vectors(raw: Any, expected: int) -> Optional[list]:
    """Validate an embedding batch: `expected` rows of equal-width numbers."""
    if raw is None:
        return None
    try:
        rows = [[float(x) for x in vec] for vec in raw]
    except Exception:  # noqa: BLE001
        return None
    if len(rows) != expected or not rows:
        return None
    dim = len(rows[0])
    if dim <= 0 or any(len(r) != dim for r in rows):
        return None
    return rows


def _embed_one(embed_fn: Any, text: Any) -> Optional[list]:
    """One query vector, or None when the backend is unavailable/misbehaving."""
    try:
        rows = _vectors(embed_fn([str(text or "")]), 1)
    except Exception as e:  # noqa: BLE001 - retrieval must degrade, not fail
        log.warning("query embedding failed, staying lexical: %s", e)
        return None
    return rows[0] if rows else None


def attach_embeddings(index: dict, embed_fn: EmbeddingBackend, *,
                      batch: int = 64) -> dict:
    """Optional hybrid path: store a vector per chunk beside the postings.

    Returns a NEW index (the input is never mutated) with
    "embeddings" = {"dim", "vectors", "norms"} — still plain JSON — plus a
    runtime-only "_embed_fn" so `search` can embed queries in-process.
    `to_serialisable` drops that callable before publishing; the artefact keeps
    working as a lexical index. If the backend fails or returns a malformed
    batch, the index comes back UNCHANGED and search stays lexical.
    """
    idx = dict(index or {})
    rows = idx.get("chunks") or []
    if not rows or embed_fn is None:
        return idx

    size = max(1, int(batch or 1))
    vectors: list = []
    for start in range(0, len(rows), size):
        texts = [str(r.get("text") or "") for r in rows[start:start + size]]
        try:
            got = _vectors(embed_fn(texts), len(texts))
        except Exception as e:  # noqa: BLE001 - degrade to lexical-only
            log.warning("embedding batch failed (%d), keeping lexical index: %s",
                        start, e)
            return dict(index or {})
        if got is None:
            log.warning("embedding backend returned a malformed batch at %d; "
                        "keeping lexical index", start)
            return dict(index or {})
        vectors.extend(got)

    dim = len(vectors[0])
    if any(len(v) != dim for v in vectors):
        log.warning("embedding dimensions differ across batches; "
                    "keeping lexical index")
        return dict(index or {})

    idx["embeddings"] = {
        "dim": dim,
        "vectors": vectors,
        "norms": [round(math.sqrt(sum(x * x for x in v)), 8) for v in vectors],
    }
    idx["_embed_fn"] = embed_fn
    return idx
