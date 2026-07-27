"""BM25 retrieval index — ranking, citations, exhaustive mentions, hybrid path.

Fixture chunks only: no network, no API key, no embedding service. The optional
embedding backend is a deterministic bag-of-words stub, so the hybrid path is
exercised without any vector database.
"""
import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from earnings_intel.docpipe import retrieve as R   # noqa: E402

TRANSCRIPT = "https://example.com/q4fy26-transcript.pdf"
PPT = "https://example.com/q4fy26-ppt.pdf"
ANNUAL = "https://example.com/fy25-annual-report.pdf"

CHUNKS = [
    {"text": "EBITDA margin expanded to 14% in the March quarter on a better mix.",
     "section": "Margins", "doc_kind": "concall_transcript",
     "doc_date": "2026-05-01", "url": TRANSCRIPT, "page": 3},
    {"text": "The order book stood at 45,000 units at the end of March 2026.",
     "section": "Order book", "doc_kind": "concall_ppt",
     "doc_date": "2026-05-01", "url": PPT, "page": 7},
    {"text": "EV volumes doubled year on year and EV penetration reached 12%.",
     "section": "Demand", "doc_kind": "concall_transcript",
     "doc_date": "2026-05-01", "url": TRANSCRIPT, "page": 4},
    {"text": "We will invest Rs 18,000 crore of capex in FY27 across products.",
     "section": "Capex", "doc_kind": "annual_report",
     "doc_date": "2025-06-30", "url": ANNUAL, "page": 22},
    {"text": "EV exports remain small but the EVs pipeline is strong.",
     "section": "Exports", "doc_kind": "annual_report",
     "doc_date": "2025-06-30", "url": ANNUAL, "page": 24},
]

VOCAB = ("margin", "order", "ev", "capex", "export")


def fake_embed(texts):
    """Deterministic stand-in for a real embedding backend: counts of a tiny
    vocabulary plus a bias term so no vector is ever all-zero."""
    return [[float(str(t).lower().count(w)) for w in VOCAB] + [1.0] for t in texts]


def _index():
    return R.build_index(CHUNKS)


# ---------------------------------------------------------------- build_index
def test_build_index_shape_is_plain_json():
    idx = _index()
    assert idx["version"] == R.INDEX_VERSION
    assert idx["doc_count"] == 5
    assert len(idx["chunks"]) == 5 and len(idx["lengths"]) == 5
    assert idx["avg_len"] > 0
    assert idx["embeddings"] is None
    assert idx["params"] == {"k1": R.K1, "b": R.B}
    assert json.loads(json.dumps(idx)) == idx          # publishable as-is


def test_build_index_postings_carry_term_frequency_and_df_matches():
    idx = _index()
    assert idx["postings"]["ev"] == [[2, 2], [4, 2]]   # "EVs" folds onto "ev"
    assert idx["df"]["ev"] == 2
    assert idx["idf"]["ev"] > 0
    for term, plist in idx["postings"].items():
        assert idx["df"][term] == len(plist)
        assert idx["idf"][term] > 0                    # never negative (Lucene idf)


def test_build_index_is_byte_stable_across_bakes():
    a, b = R.build_index(CHUNKS), R.build_index(list(CHUNKS))
    assert json.dumps(a, sort_keys=False) == json.dumps(b, sort_keys=False)
    assert list(a["postings"]) == sorted(a["postings"])


def test_build_index_keeps_all_metadata_and_defaults_the_citation_fields():
    idx = R.build_index([{"text": "margin commentary", "page": 9},
                         "a bare string chunk"])
    assert idx["chunks"][0]["page"] == 9
    for field in R.META_FIELDS:
        assert idx["chunks"][0][field] == ""
        assert idx["chunks"][1][field] == ""
    assert idx["chunks"][1]["text"] == "a bare string chunk"


def test_tokenize_folds_case_typography_and_plurals():
    assert R.tokenize("EBITDA Margins") == ["ebitda", "margin"]
    assert R.tokenize("EV’s   14.5%") == ["ev", "s", "14.5"]
    assert R.tokenize(None) == [] and R.tokenize("") == []


# --------------------------------------------------------------------- search
def test_search_ranks_a_chunk_with_the_term_above_one_without():
    hits = R.search(_index(), "margin")
    assert hits[0]["chunk_idx"] == 0
    found = {h["chunk_idx"] for h in hits}
    assert 3 not in found and 1 not in found      # zero-score chunks are not hits
    assert hits[0]["score"] > 0


def test_search_ranks_the_denser_match_first():
    hits = R.search(_index(), "EV penetration")
    assert [h["chunk_idx"] for h in hits] == [2, 4]
    assert hits[0]["score"] > hits[1]["score"]


def test_search_ignores_question_words_so_natural_questions_work():
    hits = R.search(_index(), "what changed in margins?")
    assert hits and hits[0]["chunk_idx"] == 0
    assert hits[0]["section"] == "Margins"


def test_every_hit_carries_its_source_metadata():
    hit = R.search(_index(), "order book")[0]
    for field in ("chunk_idx", "score", "text", "section", "doc_kind",
                  "doc_date", "url"):
        assert field in hit
    assert hit["section"] == "Order book"
    assert hit["doc_kind"] == "concall_ppt"
    assert hit["doc_date"] == "2026-05-01"
    assert hit["url"] == PPT
    assert hit["page"] == 7                       # extra metadata is carried too
    assert hit["text"] == CHUNKS[1]["text"]


def test_search_respects_k_and_orders_by_score_then_index():
    idx = _index()
    assert len(R.search(idx, "EV", k=1)) == 1
    hits = R.search(idx, "march quarter order book EV capex", k=10)
    scores = [h["score"] for h in hits]
    assert scores == sorted(scores, reverse=True)
    assert R.search(idx, "EV", k=0) == []


def test_search_is_empty_when_nothing_matches():
    assert R.search(_index(), "cryptocurrency") == []
    assert R.search(_index(), "") == []
    assert R.search(_index(), None) == []


def test_search_survives_an_empty_or_missing_index():
    empty = R.build_index([])
    assert empty["doc_count"] == 0 and empty["avg_len"] == 0.0
    assert R.search(empty, "margin") == []
    assert R.mentions(empty, "margin") == []
    assert R.search(None, "margin") == []
    assert R.search({}, "margin") == []
    assert json.loads(json.dumps(empty)) == empty


def test_search_survives_a_json_round_trip_of_the_published_artefact():
    idx = _index()
    reloaded = json.loads(json.dumps(R.to_serialisable(idx)))
    assert R.search(reloaded, "EV penetration") == R.search(idx, "EV penetration")


# ------------------------------------------------------------------- mentions
def test_mentions_is_exhaustive_and_counts_occurrences():
    hits = R.mentions(_index(), "EV")
    assert [h["chunk_idx"] for h in hits] == [2, 4]        # every mention, not top-k
    assert [h["count"] for h in hits] == [2, 2]            # "EV exports" + "EVs pipeline"
    assert hits[0]["url"] == TRANSCRIPT and hits[1]["url"] == ANNUAL


def test_mentions_orders_newest_document_first_then_chunk_order():
    hits = R.mentions(_index(), "march")
    assert [h["doc_date"] for h in hits] == ["2026-05-01", "2026-05-01"]
    assert [h["chunk_idx"] for h in hits] == [0, 1]        # stable inside a date
    older_first = R.mentions(_index(), "EV")
    assert older_first[0]["doc_date"] > older_first[-1]["doc_date"]


def test_mentions_matches_phrases_adjacently_only():
    idx = _index()
    assert [h["chunk_idx"] for h in R.mentions(idx, "order book")] == [1]
    # both words exist in the corpus but never next to each other
    assert R.mentions(idx, "margin book") == []


def test_mentions_is_case_insensitive_and_handles_plurals():
    idx = _index()
    assert [h["chunk_idx"] for h in R.mentions(idx, "ev")] == [2, 4]
    assert [h["chunk_idx"] for h in R.mentions(idx, "EVs")] == [2, 4]


def test_mentions_of_an_absent_term_is_empty():
    idx = _index()
    assert R.mentions(idx, "hydrogen") == []
    assert R.mentions(idx, "") == []
    assert R.mentions(idx, None) == []
    assert R.mentions(None, "EV") == []


def test_mentions_can_be_capped_when_a_caller_wants_a_preview():
    assert len(R.mentions(_index(), "EV", limit=1)) == 1


# ----------------------------------------------------------------- embeddings
def test_attach_embeddings_does_not_mutate_the_input_index():
    idx = _index()
    hybrid = R.attach_embeddings(idx, fake_embed)
    assert idx["embeddings"] is None and "_embed_fn" not in idx
    assert hybrid["embeddings"]["dim"] == len(VOCAB) + 1
    assert len(hybrid["embeddings"]["vectors"]) == 5
    assert all(n > 0 for n in hybrid["embeddings"]["norms"])


def test_search_is_identical_with_and_without_embeddings_when_lexical_only():
    idx = _index()
    published = R.to_serialisable(R.attach_embeddings(idx, fake_embed))
    assert "_embed_fn" not in published
    assert json.loads(json.dumps(published)) == published
    # the published artefact has vectors but no way to embed a query: pure lexical
    assert R.search(published, "margin") == R.search(idx, "margin")
    assert R.mentions(published, "EV") == R.mentions(idx, "EV")


def test_hybrid_search_keeps_the_best_lexical_hit_on_top():
    idx = _index()
    hybrid = R.attach_embeddings(idx, fake_embed)
    assert R.search(hybrid, "margin")[0]["chunk_idx"] == \
        R.search(idx, "margin")[0]["chunk_idx"]
    top = R.search(hybrid, "margin")[0]
    assert top["vector"] is not None and top["lexical"] > 0
    for field in ("chunk_idx", "score", "text", "section", "doc_kind",
                  "doc_date", "url"):
        assert field in top


def test_hybrid_search_can_recall_a_chunk_the_lexical_side_misses():
    idx = _index()
    hybrid = R.attach_embeddings(idx, fake_embed)
    assert R.search(idx, "exporting overseas") == []        # no shared token
    recalled = R.search(hybrid, "exporting overseas")
    assert recalled and recalled[0]["chunk_idx"] == 4       # the Exports chunk
    assert recalled[0]["lexical"] == 0.0
    assert recalled[0]["url"] == ANNUAL                     # still cites its source


def test_alpha_of_zero_reduces_the_hybrid_back_to_pure_ranking():
    idx = _index()
    hybrid = R.attach_embeddings(idx, fake_embed)
    lexical_first = R.search(idx, "EV penetration")
    blended_first = R.search(hybrid, "EV penetration", alpha=0.0)
    assert [h["chunk_idx"] for h in blended_first][:2] == \
        [h["chunk_idx"] for h in lexical_first][:2]


def test_embed_fn_can_be_passed_per_call_and_is_ignored_without_vectors():
    idx = _index()                                     # no embeddings attached
    assert R.search(idx, "margin", embed_fn=fake_embed) == R.search(idx, "margin")


def test_a_broken_embedding_backend_degrades_to_lexical(monkeypatch):
    idx = _index()

    def broken(texts):
        raise RuntimeError("embedding service unavailable")

    same = R.attach_embeddings(idx, broken)
    assert same["embeddings"] is None
    assert R.search(same, "margin") == R.search(idx, "margin")

    ragged = R.attach_embeddings(idx, lambda t: [[1.0], [2.0, 3.0]])
    assert ragged["embeddings"] is None
    assert R.attach_embeddings(idx, lambda t: None)["embeddings"] is None
    assert R.attach_embeddings(idx, None)["embeddings"] is None


def test_a_query_embedding_failure_still_returns_lexical_hits():
    hybrid = R.attach_embeddings(_index(), fake_embed)

    def broken(texts):
        raise RuntimeError("embedding service unavailable")

    hits = R.search(hybrid, "margin", embed_fn=broken)
    assert [h["chunk_idx"] for h in hits] == \
        [h["chunk_idx"] for h in R.search(_index(), "margin")]


def test_embeddings_are_batched_without_changing_the_result():
    idx = _index()
    one = R.attach_embeddings(idx, fake_embed, batch=1)
    many = R.attach_embeddings(idx, fake_embed, batch=64)
    assert one["embeddings"] == many["embeddings"]


def test_retrieval_never_opens_a_socket(monkeypatch):
    import socket

    def _boom(*a, **kw):
        raise AssertionError("docpipe.retrieve must never touch the network")

    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom)
    idx = R.attach_embeddings(R.build_index(CHUNKS), fake_embed)
    assert R.search(idx, "what changed in margins?")
    assert R.mentions(idx, "EV")
    assert CHUNKS[0]["text"].startswith("EBITDA margin")     # inputs untouched
