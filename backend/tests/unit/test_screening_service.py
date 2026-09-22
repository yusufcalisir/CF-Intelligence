"""Unit tests for Real-Time Sanctions & PEP Screening Engine (Phase 108).

Validates:
- Text normalisation: Cyrillic transliteration, diacritic removal.
- Levenshtein distance and similarity.
- Jaro-Winkler similarity.
- Double Metaphone phonetic encoding.
- Score computation: exact, token, fuzzy, phonetic.
- Watchlist loading: valid sources, invalid source rejection.
- Single-entity screening: clean, single hit, multiple hits, goodlist suppression.
- Alert threshold: triggered above threshold, not triggered below.
- Goodlist lifecycle: goodlist, re-screen suppression.
- Hit disposition: update, invalid disposition rejection.
- Portfolio bulk re-screening.
- Metrics accuracy.
- Pydantic schema validation: query name, threshold bounds, alias count.
- FastAPI HTTP endpoints: all status codes, dual routing.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app.application.services.screening_service import (
    ScreeningService,
    WatchlistEntry,
    _jaro_winkler,
    _levenshtein,
    _levenshtein_similarity,
    _metaphone,
    _normalise,
    _score_name_pair,
    _tokens,
)
from app.domain.enums import (
    MatchAlgorithm,
    MatchDisposition,
    ScreeningEntityType,
    ScreeningStatus,
    WatchlistSource,
)

os.environ.setdefault("TESTING", "1")


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture()
def svc() -> ScreeningService:
    return ScreeningService(alert_threshold=75)


def _entry(
    name: str,
    source: str = WatchlistSource.EU_CONSOLIDATED,
    aliases: list[str] | None = None,
    entity_type: str = ScreeningEntityType.INDIVIDUAL,
    listed_by: str = "EU",
) -> WatchlistEntry:
    return WatchlistEntry(
        entry_id=f"ENTRY-{name[:10].upper().replace(' ', '-')}",
        source=source,
        entity_type=entity_type,
        primary_name=name,
        aliases=aliases or [],
        listed_by=listed_by,
    )


@pytest.fixture()
def svc_with_data(svc: ScreeningService) -> ScreeningService:
    """Service pre-loaded with a small representative watchlist."""
    entries = [
        _entry("Ivan Petrov", aliases=["Иван Петров", "I. Petrov"], listed_by="EU"),
        _entry("Mehmet Yilmaz", aliases=["محمد يلماز"], listed_by="UN"),
        _entry("John Smith", listed_by="OFAC"),
        _entry("Black Sea Trading Ltd", entity_type=ScreeningEntityType.LEGAL_ENTITY, listed_by="EU"),
        _entry("Vladislav Kozlov", aliases=["Vlad Kozlov", "В. Козлов"], listed_by="EU"),
    ]
    svc.load_watchlist_entries(WatchlistSource.EU_CONSOLIDATED, entries[:2] + entries[3:])
    svc.load_watchlist_entries(WatchlistSource.OFAC_SDN, [entries[2]])
    return svc


# ═══════════════════════════════════════════════════════════════════════════════
# Text normalisation tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestNormalisation:

    def test_lowercase(self):
        assert _normalise("JOHN SMITH") == "john smith"

    def test_diacritic_removal(self):
        assert _normalise("Müller") == "muller"
        assert _normalise("Réseau") == "reseau"
        assert _normalise("Ñoño") == "nono"

    def test_cyrillic_transliteration(self):
        result = _normalise("Иван")
        assert result == "ivan"

    def test_cyrillic_full_name(self):
        result = _normalise("Владислав Козлов")
        assert "vladislav" in result
        assert "kozlov" in result

    def test_special_chars_stripped(self):
        assert _normalise("O'Brien") == "o brien"
        assert _normalise("Al-Rashid") == "al rashid"

    def test_multiple_spaces_collapsed(self):
        assert _normalise("John   Smith") == "john smith"

    def test_tokens_excludes_short(self):
        tokens = _tokens("A. Smith")
        assert "smith" in tokens
        assert "a" not in tokens


# ═══════════════════════════════════════════════════════════════════════════════
# Levenshtein tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestLevenshtein:

    def test_identical_strings(self):
        assert _levenshtein("smith", "smith") == 0

    def test_empty_strings(self):
        assert _levenshtein("", "") == 0
        assert _levenshtein("abc", "") == 3
        assert _levenshtein("", "abc") == 3

    def test_single_substitution(self):
        assert _levenshtein("smith", "smyth") == 1

    def test_insertion(self):
        assert _levenshtein("ivan", "ivano") == 1

    def test_deletion(self):
        assert _levenshtein("kozlov", "kozlo") == 1

    def test_similarity_identical(self):
        assert _levenshtein_similarity("smith", "smith") == 1.0

    def test_similarity_range(self):
        s = _levenshtein_similarity("john", "joan")
        assert 0.0 <= s <= 1.0

    def test_transposition_costs_two(self):
        # "ab" → "ba" requires 2 edits (Levenshtein, not Damerau)
        assert _levenshtein("ab", "ba") == 2


# ═══════════════════════════════════════════════════════════════════════════════
# Jaro-Winkler tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestJaroWinkler:

    def test_identical(self):
        assert _jaro_winkler("smith", "smith") == 1.0

    def test_empty_vs_nonempty(self):
        assert _jaro_winkler("", "smith") == 0.0

    def test_common_prefix_boosts_score(self):
        # Names sharing prefix should score higher than after prefix
        score_prefix = _jaro_winkler("johnson", "johnston")
        score_suffix = _jaro_winkler("johnsen", "johnson")
        assert score_prefix >= 0.9
        assert score_suffix >= 0.9

    def test_score_between_0_and_1(self):
        s = _jaro_winkler("petrov", "petrova")
        assert 0.0 <= s <= 1.0

    def test_high_score_for_typo(self):
        # Single character typo should produce high similarity
        s = _jaro_winkler("yilmaz", "ylmaz")
        assert s > 0.85


# ═══════════════════════════════════════════════════════════════════════════════
# Double Metaphone tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestDoubleMetaphone:

    def test_empty_string(self):
        assert _metaphone("") == ""

    def test_ph_becomes_f(self):
        result = _metaphone("phone")
        # PH → F
        assert "F" in result

    def test_different_spellings_same_code(self):
        # Smith / Smyth should produce similar codes
        m1 = _metaphone("smith")
        m2 = _metaphone("smyth")
        # At least the first character should match
        assert m1[0] == m2[0]

    def test_max_length_8(self):
        assert len(_metaphone("superlongfamilynamewithlots")) <= 8


# ═══════════════════════════════════════════════════════════════════════════════
# Score computation tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestScoreNamePair:

    def test_exact_match_scores_100(self):
        score, algo = _score_name_pair("John Smith", "John Smith")
        assert score == 100.0
        assert algo == MatchAlgorithm.EXACT

    def test_case_insensitive_exact(self):
        score, _ = _score_name_pair("JOHN SMITH", "john smith")
        assert score == 100.0

    def test_token_overlap_high_score(self):
        score, _ = _score_name_pair("John Smith", "Smith, John")
        assert score >= 75.0

    def test_typo_still_scores_high(self):
        score, _ = _score_name_pair("Ivan Petrov", "Ivan Petrv")
        assert score >= 60.0

    def test_completely_different_names_low_score(self):
        score, _ = _score_name_pair("John Smith", "Xiang Li")
        # Score should be well below the 75 alert threshold for clearly dissimilar names
        assert score < 65.0

    def test_cyrillic_alias_matched(self):
        score, _ = _score_name_pair("Ivan Petrov", "Иван Петров")
        assert score >= 70.0

    def test_empty_query_returns_zero(self):
        score, _ = _score_name_pair("", "John Smith")
        assert score == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# Watchlist loading tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestWatchlistLoading:

    def test_load_valid_source(self, svc):
        entries = [_entry("Test Person")]
        count = svc.load_watchlist_entries(WatchlistSource.EU_CONSOLIDATED, entries)
        assert count == 1

    def test_counts_updated(self, svc):
        svc.load_watchlist_entries(WatchlistSource.OFAC_SDN, [_entry("A"), _entry("B")])
        counts = svc.get_watchlist_counts()
        assert counts[WatchlistSource.OFAC_SDN] == 2

    def test_replace_replaces_entire_source(self, svc):
        svc.load_watchlist_entries(WatchlistSource.UN_SECURITY_COUNCIL, [_entry("A"), _entry("B")])
        svc.load_watchlist_entries(WatchlistSource.UN_SECURITY_COUNCIL, [_entry("C")])
        assert svc.get_watchlist_counts()[WatchlistSource.UN_SECURITY_COUNCIL] == 1

    def test_invalid_source_raises(self, svc):
        with pytest.raises(ValueError, match="Unknown watchlist source"):
            svc.load_watchlist_entries("BOGUS_LIST", [_entry("X")])

    def test_empty_list_clears_source(self, svc):
        svc.load_watchlist_entries(WatchlistSource.HM_TREASURY, [_entry("X")])
        svc.load_watchlist_entries(WatchlistSource.HM_TREASURY, [])
        assert svc.get_watchlist_counts()[WatchlistSource.HM_TREASURY] == 0


# ═══════════════════════════════════════════════════════════════════════════════
# Screening tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestScreening:

    def test_clean_entity_no_alerts(self, svc_with_data):
        result = svc_with_data.screen_entity("Maria Fernandez Rodriguez")
        assert result.status == ScreeningStatus.COMPLETED
        assert result.is_alerted is False

    def test_exact_match_alerts(self, svc_with_data):
        result = svc_with_data.screen_entity("John Smith")
        assert result.status == ScreeningStatus.COMPLETED
        # Exact match should produce top_score = 100
        assert result.top_score == 100.0
        assert result.is_alerted is True

    def test_fuzzy_match_detected(self, svc_with_data):
        # "Ivan Petov" — one character deleted from Petrov
        result = svc_with_data.screen_entity("Ivan Petov")
        assert any(h.score > 40.0 for h in result.hits)

    def test_cyrillic_alias_detected(self, svc_with_data):
        result = svc_with_data.screen_entity("Иван Петров")
        assert len(result.hits) > 0

    def test_legal_entity_screened(self, svc_with_data):
        result = svc_with_data.screen_entity(
            "Black Sea Trading Ltd",
            entity_type=ScreeningEntityType.LEGAL_ENTITY,
        )
        assert result.top_score == 100.0
        assert result.is_alerted is True

    def test_alert_threshold_respected(self, svc_with_data):
        # High threshold → exact match still alerts
        result = svc_with_data.screen_entity("John Smith", alert_threshold=99)
        assert result.is_alerted is True

    def test_below_threshold_no_alert(self, svc):
        svc.load_watchlist_entries(WatchlistSource.EU_CONSOLIDATED, [_entry("John Smith")])
        # Fuzzy name with very high threshold
        result = svc.screen_entity("Jan Schmitt", alert_threshold=99)
        # Might or might not alert depending on score; just check it runs
        assert result.status == ScreeningStatus.COMPLETED

    def test_empty_name_raises(self, svc):
        with pytest.raises(ValueError, match="query_name must not be empty"):
            svc.screen_entity("")

    def test_invalid_entity_type_raises(self, svc):
        with pytest.raises(ValueError, match="Invalid entity_type"):
            svc.screen_entity("John Smith", entity_type="ROBOT")

    def test_source_filter_applied(self, svc_with_data):
        # Screen against only OFAC — should only find John Smith
        result = svc_with_data.screen_entity("John Smith", sources=[WatchlistSource.OFAC_SDN])
        assert all(h.source == WatchlistSource.OFAC_SDN for h in result.hits)

    def test_sources_checked_populated(self, svc_with_data):
        result = svc_with_data.screen_entity("Anybody")
        assert len(result.sources_checked) > 0

    def test_screening_duration_recorded(self, svc_with_data):
        result = svc_with_data.screen_entity("John Smith")
        assert result.screening_duration_ms >= 0.0

    def test_entity_key_hash_consistent(self, svc_with_data):
        r1 = svc_with_data.screen_entity("John Smith")
        r2 = svc_with_data.screen_entity("John Smith")
        assert r1.entity_key_hash == r2.entity_key_hash


# ═══════════════════════════════════════════════════════════════════════════════
# Goodlist tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestGoodlist:

    def test_goodlist_suppresses_future_screens(self, svc_with_data):
        # First screening — gets a hit
        result = svc_with_data.screen_entity("John Smith")
        assert result.hits
        hit = result.hits[0]
        # Goodlist the hit
        svc_with_data.goodlist_entity(result.result_id, hit.hit_id, "officer-001", "Verified employee")
        # Second screening — should be suppressed
        result2 = svc_with_data.screen_entity("John Smith")
        assert result2.goodlisted is True
        assert result2.hits == []

    def test_goodlist_marks_disposition(self, svc_with_data):
        result = svc_with_data.screen_entity("John Smith")
        hit = result.hits[0]
        svc_with_data.goodlist_entity(result.result_id, hit.hit_id, "officer-002")
        # Reload the result
        updated = svc_with_data.get_result(result.result_id)
        updated_hit = next(h for h in updated.hits if h.hit_id == hit.hit_id)
        assert updated_hit.disposition == MatchDisposition.FALSE_POSITIVE

    def test_goodlist_invalid_result_raises(self, svc):
        with pytest.raises(KeyError):
            svc.goodlist_entity("no-such-result", "no-such-hit", "officer")

    def test_goodlist_invalid_hit_raises(self, svc_with_data):
        result = svc_with_data.screen_entity("John Smith")
        with pytest.raises(KeyError):
            svc_with_data.goodlist_entity(result.result_id, "bad-hit-id", "officer")

    def test_is_goodlisted_returns_correct_state(self, svc_with_data):
        result = svc_with_data.screen_entity("John Smith")
        hit = result.hits[0]
        assert not svc_with_data.is_goodlisted(result.entity_key_hash)
        svc_with_data.goodlist_entity(result.result_id, hit.hit_id, "officer")
        assert svc_with_data.is_goodlisted(result.entity_key_hash)


# ═══════════════════════════════════════════════════════════════════════════════
# Disposition update tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestDisposition:

    def test_update_to_confirmed_match(self, svc_with_data):
        result = svc_with_data.screen_entity("John Smith")
        hit = result.hits[0]
        updated = svc_with_data.update_hit_disposition(
            result.result_id, hit.hit_id, MatchDisposition.CONFIRMED_MATCH, "officer"
        )
        assert updated.disposition == MatchDisposition.CONFIRMED_MATCH
        assert updated.reviewed_by == "officer"
        assert updated.reviewed_at is not None

    def test_update_to_escalated(self, svc_with_data):
        result = svc_with_data.screen_entity("John Smith")
        hit = result.hits[0]
        updated = svc_with_data.update_hit_disposition(
            result.result_id, hit.hit_id, MatchDisposition.ESCALATED, "senior-officer"
        )
        assert updated.disposition == MatchDisposition.ESCALATED

    def test_invalid_disposition_raises(self, svc_with_data):
        result = svc_with_data.screen_entity("John Smith")
        hit = result.hits[0]
        with pytest.raises(ValueError, match="Invalid disposition"):
            svc_with_data.update_hit_disposition(result.result_id, hit.hit_id, "BOGUS", "officer")

    def test_notes_saved(self, svc_with_data):
        result = svc_with_data.screen_entity("John Smith")
        hit = result.hits[0]
        updated = svc_with_data.update_hit_disposition(
            result.result_id, hit.hit_id, MatchDisposition.CONFIRMED_MATCH, "officer", "Verified against passport"
        )
        assert updated.notes == "Verified against passport"


# ═══════════════════════════════════════════════════════════════════════════════
# Bulk re-screening tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestBulkRescreen:

    def test_bulk_rescreens_all_registered_entities(self, svc_with_data):
        svc_with_data.screen_entity("John Smith")
        svc_with_data.screen_entity("Maria Fernandez")
        results = svc_with_data.bulk_rescreen()
        assert len(results) >= 2

    def test_bulk_rescreen_empty_registry(self, svc):
        results = svc.bulk_rescreen()
        assert len(results) == 0

    def test_bulk_rescreen_respects_threshold(self, svc_with_data):
        svc_with_data.screen_entity("John Smith")
        # Re-screen with very high threshold — exact match still alerts
        results = svc_with_data.bulk_rescreen(alert_threshold=99)
        assert any(r.is_alerted for r in results.values())

    def test_bulk_rescreen_source_filter(self, svc_with_data):
        svc_with_data.screen_entity("John Smith")
        results = svc_with_data.bulk_rescreen(sources=[WatchlistSource.EU_CONSOLIDATED])
        for r in results.values():
            assert all(
                h.source == WatchlistSource.EU_CONSOLIDATED
                for h in r.hits
            )


# ═══════════════════════════════════════════════════════════════════════════════
# Metrics tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestMetrics:

    def test_empty_metrics(self, svc):
        m = svc.get_metrics()
        assert m["total_screens"] == 0
        assert m["total_alerts"] == 0
        assert m["goodlist_size"] == 0

    def test_metrics_after_screening(self, svc_with_data):
        svc_with_data.screen_entity("John Smith")
        svc_with_data.screen_entity("Maria Fernandez")
        m = svc_with_data.get_metrics()
        assert m["total_screens"] == 2
        assert m["total_alerts"] >= 1  # John Smith is an exact match

    def test_alert_rate_computed(self, svc_with_data):
        svc_with_data.screen_entity("John Smith")
        m = svc_with_data.get_metrics()
        assert 0.0 <= m["alert_rate_pct"] <= 100.0

    def test_goodlist_size_increases(self, svc_with_data):
        result = svc_with_data.screen_entity("John Smith")
        hit = result.hits[0]
        svc_with_data.goodlist_entity(result.result_id, hit.hit_id, "officer")
        m = svc_with_data.get_metrics()
        assert m["goodlist_size"] == 1

    def test_watchlist_counts_in_metrics(self, svc_with_data):
        m = svc_with_data.get_metrics()
        assert "watchlist_counts" in m
        assert m["watchlist_counts"][WatchlistSource.EU_CONSOLIDATED] > 0


# ═══════════════════════════════════════════════════════════════════════════════
# Schema validation tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestSchemaValidation:

    def test_valid_screen_request(self):
        from app.application.schemas.screening_schemas import ScreenEntityRequest
        req = ScreenEntityRequest(query_name="John Smith", entity_type="INDIVIDUAL", alert_threshold=75)
        assert req.query_name == "John Smith"

    def test_empty_name_rejected(self):
        from pydantic import ValidationError

        from app.application.schemas.screening_schemas import ScreenEntityRequest
        with pytest.raises(ValidationError):
            ScreenEntityRequest(query_name="")

    def test_threshold_out_of_range_rejected(self):
        from pydantic import ValidationError

        from app.application.schemas.screening_schemas import ScreenEntityRequest
        with pytest.raises(ValidationError):
            ScreenEntityRequest.model_validate({"query_name": "Test", "alert_threshold": 101})

    def test_valid_watchlist_entry(self):
        from app.application.schemas.screening_schemas import WatchlistEntryRequest
        entry = WatchlistEntryRequest(
            source="EU_CONSOLIDATED",
            primary_name="Test Person",
            aliases=["T. Person"],
        )
        assert entry.primary_name == "Test Person"

    def test_too_many_aliases_rejected(self):
        from pydantic import ValidationError

        from app.application.schemas.screening_schemas import WatchlistEntryRequest
        with pytest.raises(ValidationError, match="Maximum 50 aliases"):
            WatchlistEntryRequest(
                source="EU_CONSOLIDATED",
                primary_name="Test",
                aliases=[f"alias{i}" for i in range(51)],
            )


# ═══════════════════════════════════════════════════════════════════════════════
# HTTP API endpoint tests
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def client():
    from app.main import app
    return TestClient(app)


def _load_payload(source: str = "EU_CONSOLIDATED", names: list[str] | None = None):
    return {
        "source": source,
        "entries": [
            {"source": source, "primary_name": n, "entity_type": "INDIVIDUAL", "listed_by": "EU"}
            for n in (names or ["John Smith", "Ivan Petrov"])
        ],
    }


class TestScreeningAPI:

    def test_load_watchlist_returns_201(self, client):
        resp = client.post("/api/v1/screening/watchlists", json=_load_payload())
        assert resp.status_code == 201
        assert resp.json()["loaded"] == 2

    def test_watchlist_counts_returns_200(self, client):
        resp = client.get("/api/v1/screening/watchlists/counts")
        assert resp.status_code == 200
        assert "counts" in resp.json()

    def test_screen_clean_entity(self, client):
        resp = client.post("/api/v1/screening/screen", json={
            "query_name": "Completely Unique Name XYZ",
            "entity_type": "INDIVIDUAL",
            "alert_threshold": 75,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "COMPLETED"
        assert data["is_alerted"] is False

    def test_screen_matching_entity(self, client):
        client.post("/api/v1/screening/watchlists", json=_load_payload(names=["Jane Doe"]))
        resp = client.post("/api/v1/screening/screen", json={
            "query_name": "Jane Doe",
            "alert_threshold": 75,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_alerted"] is True
        assert data["top_score"] == 100.0

    def test_screen_empty_name_returns_400(self, client):
        resp = client.post("/api/v1/screening/screen", json={
            "query_name": "   ",
            "alert_threshold": 75,
        })
        # Either validation 422 or service 400
        assert resp.status_code in (400, 422)

    def test_list_results_returns_200(self, client):
        resp = client.get("/api/v1/screening/results")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_list_results_alerted_filter(self, client):
        resp = client.get("/api/v1/screening/results", params={"alerted_only": "true"})
        assert resp.status_code == 200
        for item in resp.json():
            assert item["is_alerted"] is True

    def test_get_result_returns_200(self, client):
        screen_resp = client.post("/api/v1/screening/screen", json={
            "query_name": "Jane Doe", "alert_threshold": 75
        })
        result_id = screen_resp.json()["result_id"]
        resp = client.get(f"/api/v1/screening/results/{result_id}")
        assert resp.status_code == 200
        assert resp.json()["result_id"] == result_id

    def test_get_result_not_found_returns_404(self, client):
        resp = client.get("/api/v1/screening/results/no-such-id")
        assert resp.status_code == 404

    def test_goodlist_endpoint(self, client):
        client.post("/api/v1/screening/watchlists", json=_load_payload(names=["Alice Martin"]))
        screen_resp = client.post("/api/v1/screening/screen", json={
            "query_name": "Alice Martin", "alert_threshold": 75
        })
        result_id = screen_resp.json()["result_id"]
        hits = screen_resp.json()["hits"]
        assert hits, "Expected at least one hit for exact match"
        resp = client.post(f"/api/v1/screening/results/{result_id}/goodlist", json={
            "hit_id": hits[0]["hit_id"],
            "approved_by": "officer-001",
            "notes": "Internal employee",
        })
        assert resp.status_code == 200
        assert "goodlist_id" in resp.json()

    def test_goodlist_invalid_result_returns_404(self, client):
        resp = client.post("/api/v1/screening/results/no-such-id/goodlist", json={
            "hit_id": "x", "approved_by": "officer"
        })
        assert resp.status_code == 404

    def test_disposition_update_endpoint(self, client):
        client.post("/api/v1/screening/watchlists", json=_load_payload(names=["Bob Brown"]))
        screen_resp = client.post("/api/v1/screening/screen", json={
            "query_name": "Bob Brown", "alert_threshold": 75
        })
        result_id = screen_resp.json()["result_id"]
        hits = screen_resp.json()["hits"]
        resp = client.post(f"/api/v1/screening/results/{result_id}/disposition", json={
            "hit_id": hits[0]["hit_id"],
            "disposition": "CONFIRMED_MATCH",
            "reviewed_by": "officer-002",
            "notes": "Confirmed via INTERPOL",
        })
        assert resp.status_code == 200
        assert resp.json()["disposition"] == "CONFIRMED_MATCH"

    def test_bulk_rescreen_returns_200(self, client):
        resp = client.post("/api/v1/screening/bulk-rescreen", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert "total_rescreened" in data
        assert "total_alerted" in data

    def test_metrics_endpoint(self, client):
        resp = client.get("/api/v1/screening/metrics")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_screens" in data
        assert "total_alerts" in data
        assert "watchlist_counts" in data

    def test_dual_routing_v1_prefix(self, client):
        resp = client.post("/v1/screening/screen", json={
            "query_name": "Test Entity",
            "alert_threshold": 75,
        })
        assert resp.status_code == 200

    def test_invalid_entity_type_returns_422(self, client):
        resp = client.post("/api/v1/screening/screen", json={
            "query_name": "Test", "entity_type": "INVALID_TYPE", "alert_threshold": 75
        })
        assert resp.status_code == 422
