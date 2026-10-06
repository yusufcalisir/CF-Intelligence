"""Validation test for the 258-pair Canonical Defect x Historical Artifact Applicability Matrix.

Strictly verifies Section 52 schema, concrete exclusion evidence, and defect-specific predicates.
"""

from __future__ import annotations

import json
from pathlib import Path


def test_historical_defect_applicability_matrix_integrity() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    registry_path = repo_root / "verification" / "inventories" / "canonical_defect_registry.json"
    matrix_path = repo_root / "verification" / "inventories" / "historical_defect_applicability.json"

    assert registry_path.exists(), f"Registry missing: {registry_path}"
    assert matrix_path.exists(), f"Applicability matrix missing: {matrix_path}"

    with open(registry_path, encoding="utf-8") as f:
        registry_data = json.load(f)
    canonical_defects = registry_data["defects"]
    canonical_ids = {d["stable_id"] for d in canonical_defects}
    assert len(canonical_ids) == 43, f"Expected 43 canonical defects, got {len(canonical_ids)}"

    with open(matrix_path, encoding="utf-8") as f:
        matrix_data = json.load(f)

    artifacts = matrix_data["artifacts_profile"]
    artifact_ids = {a["id"] for a in artifacts}
    assert len(artifact_ids) == 6, f"Expected 6 historical artifacts, got {len(artifact_ids)}"

    pairs = matrix_data["pairs"]
    expected_total = len(canonical_ids) * len(artifact_ids)
    assert len(pairs) == expected_total, f"Expected {expected_total} pairs, found {len(pairs)}"

    seen_pairs: set[tuple[str, str]] = set()
    applicable_pairs: set[tuple[str, str]] = set()

    for row in pairs:
        art = row["artifact"]
        def_id = row["defect_id"]
        applicability = row["applicability"]
        reason = row["applicability_reason"]
        impact = row["impact"]
        evidence = row["historical_evidence"]

        # Section 52 Schema Verification
        assert "canonical_root_cause" in row, f"Missing canonical_root_cause in row: {(art, def_id)}"
        assert "required_execution_path" in row and isinstance(row["required_execution_path"], list), (
            f"required_execution_path must be list in row: {(art, def_id)}"
        )
        assert "historical_path_evidence" in row and isinstance(row["historical_path_evidence"], list), (
            f"historical_path_evidence must be list in row: {(art, def_id)}"
        )
        assert "historical_version_evidence" in row and isinstance(row["historical_version_evidence"], list), (
            f"historical_version_evidence must be list in row: {(art, def_id)}"
        )
        assert row.get("provenance_quality") in ("STRONG", "PARTIAL", "WEAK", "INSUFFICIENT"), (
            f"Invalid provenance_quality in row: {(art, def_id)}"
        )
        assert "confidence" in row, f"Missing confidence in row: {(art, def_id)}"

        # 1. No unknown artifacts or defects
        assert art in artifact_ids, f"Unknown artifact: {art}"
        assert def_id in canonical_ids, f"Unknown defect ID: {def_id}"

        # 2. No duplicate pairs
        pair_key = (art, def_id)
        assert pair_key not in seen_pairs, f"Duplicate pair found: {pair_key}"
        seen_pairs.add(pair_key)

        # 3. Valid applicability enums
        assert applicability in ("APPLICABLE", "NOT_APPLICABLE", "UNKNOWN_INSUFFICIENT_PROVENANCE"), (
            f"Invalid applicability: {applicability}"
        )

        # 4. NOT_APPLICABLE requires concrete exclusion reason
        if applicability == "NOT_APPLICABLE":
            assert reason and len(reason.strip()) > 5, f"Missing/empty reason for N/A pair: {pair_key}"
            assert impact == "NOT_APPLICABLE", f"Expected impact NOT_APPLICABLE for N/A pair: {pair_key}"
            # Concrete exclusion: must mention unreachable path, absent subsystem, or inactive daemon
            assert any(
                term in reason.lower()
                for term in ("unreachable", "absent", "inactive", "not executing", "not reachable")
            ), f"N/A reason lacks concrete exclusion predicate: {reason}"

        # 5. APPLICABLE requires valid impact classification and positive evidence
        if applicability == "APPLICABLE":
            applicable_pairs.add(pair_key)
            assert impact in (
                "CONFIRMED_AFFECTED",
                "POTENTIALLY_AFFECTED",
                "UNAFFECTED_WITH_EVIDENCE",
                "INSUFFICIENT_PROVENANCE",
            ), f"Invalid impact for applicable pair {pair_key}: {impact}"
            assert reason and len(reason.strip()) > 5, f"Missing impact description for pair: {pair_key}"
            assert len(row["historical_path_evidence"]) > 0, f"Missing path evidence for applicable pair: {pair_key}"

        # 6. UNAFFECTED_WITH_EVIDENCE requires positive historical evidence
        if impact == "UNAFFECTED_WITH_EVIDENCE":
            assert evidence and len(evidence) > 0, f"UNAFFECTED_WITH_EVIDENCE missing positive evidence: {pair_key}"

    # 7. Exact pair coverage: exactly all combinations present
    for art_id in artifact_ids:
        for def_id in canonical_ids:
            assert (art_id, def_id) in seen_pairs, f"Missing pair: {(art_id, def_id)}"

    # 8. Summary counts integrity
    summary = matrix_data["summary_by_artifact"]
    total_applicable = 0
    total_not_applicable = 0
    for art_id, s in summary.items():
        assert s["total_canonical_defects"] == 43
        assert s["applicable"] + s["not_applicable"] + s["unknown_applicability"] == 43
        assert (
            s["confirmed_affected"]
            + s["potentially_affected"]
            + s["unaffected_with_evidence"]
            + s["insufficient_provenance"]
            == s["applicable"]
        )
        assert s["must_rerun_later"] == s["confirmed_affected"] + s["potentially_affected"]
        total_applicable += s["applicable"]
        total_not_applicable += s["not_applicable"]

    assert total_applicable == 9, f"Expected 9 reconciled applicable pairs, got {total_applicable}"
    assert total_not_applicable == 249, f"Expected 249 not applicable pairs, got {total_not_applicable}"

    # 9. Verify suspicious pair resolutions
    # CreditCard x ML-004 must be NOT_APPLICABLE (run_creditcard_benchmark does not execute evaluate_thresholds main)
    assert ("run_creditcard_benchmark.py", "ML-004") not in applicable_pairs

    # AMLSim x ML-003 must be NOT_APPLICABLE (evaluate_patterns does not call GraphService.normalize_adjacency)
    assert ("evaluate_patterns.py", "ML-003") not in applicable_pairs

    # GRAPH-0004 must be NOT_APPLICABLE to all 6 artifacts (GraphEngine service methods not reached by standalone benchmarks)
    for art_id in artifact_ids:
        assert (art_id, "GRAPH-0004") not in applicable_pairs

    # DATA-002 must be APPLICABLE ONLY to run_creditcard_benchmark.py (CreditCard split ratio ambiguity)
    assert ("run_creditcard_benchmark.py", "DATA-002") in applicable_pairs
    for art_id in artifact_ids - {"run_creditcard_benchmark.py"}:
        assert (art_id, "DATA-002") not in applicable_pairs
