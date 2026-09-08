"""Unit tests for batch summary accounting, diagnostics validation, and bounded non-reflection."""

from __future__ import annotations

from typing import Any

import pytest

from batch.accounting import validate_summary_accounting
from batch.contracts import parse_batch_response
from batch.models import (
    BatchArtifactRecord,
    BatchDiagnostic,
    BatchFileResult,
    BatchSummary,
    BatchValidationError,
)


class TestSummaryAccountingValidation:
    """Full accounting arithmetic, list length matching, and input uniqueness."""

    def test_accounting_success(self) -> None:
        summary = BatchSummary(total=4, accepted=1, partial=1, failed=1, unprocessed=1, cancelled=0)
        art = BatchArtifactRecord(format="step", path="out/p.step")
        diag = BatchDiagnostic(code="INTERNAL_ERROR", message="fail")
        results = [
            BatchFileResult(input="f1.par", status="accepted", artifacts=(art,)),
            BatchFileResult(input="f2.par", status="partial", artifacts=(art,), errors=(diag,)),
            BatchFileResult(input="f3.par", status="failed", errors=(diag,)),
        ]
        validate_summary_accounting(summary, results, unprocessed_files=["f4.par"], cancelled_files=[])

    def test_accounting_results_length_mismatch(self) -> None:
        summary = BatchSummary(total=3, accepted=2, partial=0, failed=0, unprocessed=1, cancelled=0)
        art = BatchArtifactRecord(format="step", path="out/p.step")
        results = [BatchFileResult(input="f1.par", status="accepted", artifacts=(art,))]
        with pytest.raises(BatchValidationError, match="Results length"):
            validate_summary_accounting(summary, results, unprocessed_files=["f2.par"], cancelled_files=[])

    def test_accounting_category_counts_mismatch(self) -> None:
        summary = BatchSummary(total=2, accepted=2, partial=0, failed=0, unprocessed=0, cancelled=0)
        art = BatchArtifactRecord(format="step", path="out/p.step")
        diag = BatchDiagnostic(code="INTERNAL_ERROR", message="fail")
        results = [
            BatchFileResult(input="f1.par", status="accepted", artifacts=(art,)),
            BatchFileResult(input="f2.par", status="failed", errors=(diag,)),
        ]
        with pytest.raises(BatchValidationError, match="Results accepted count"):
            validate_summary_accounting(summary, results, unprocessed_files=[], cancelled_files=[])

    def test_accounting_duplicate_across_results_and_unprocessed(self) -> None:
        summary = BatchSummary(total=2, accepted=1, partial=0, failed=0, unprocessed=1, cancelled=0)
        art = BatchArtifactRecord(format="step", path="out/p.step")
        results = [BatchFileResult(input="f1.par", status="accepted", artifacts=(art,))]
        with pytest.raises(BatchValidationError, match="Overlapping or duplicate unprocessed"):
            validate_summary_accounting(summary, results, unprocessed_files=["f1.par"], cancelled_files=[])


class TestParserAdversarialSanitization:
    """Verifies that parse_batch_response produces bounded, non-reflecting error messages."""

    def test_parser_rejects_duplicate_results_without_reflecting_input(self) -> None:
        long_path = "a/" * 240 + "part.par"  # ~968 chars
        payload: dict[str, Any] = {
            "contract_version": "1.0",
            "request_id": "req-adv-1",
            "status": "completed",
            "summary": {
                "total": 2,
                "accepted": 2,
                "partial": 0,
                "failed": 0,
                "unprocessed": 0,
                "cancelled": 0,
            },
            "results": [
                {"input": long_path, "status": "accepted", "artifacts": [{"format": "step", "path": "out/1.step"}]},
                {"input": long_path, "status": "accepted", "artifacts": [{"format": "step", "path": "out/2.step"}]},
            ],
            "manifest": {"path": "out/manifest.json"},
        }
        with pytest.raises(BatchValidationError) as exc_info:
            parse_batch_response(payload)

        msg = exc_info.value.message
        assert len(msg) <= 128
        assert long_path not in msg
        assert "Duplicate input in results" in msg

    def test_parser_rejects_duplicate_unprocessed_without_reflecting_input(self) -> None:
        long_path = "b/" * 240 + "part.par"
        payload: dict[str, Any] = {
            "contract_version": "1.0",
            "request_id": "req-adv-2",
            "status": "completed",
            "summary": {
                "total": 2,
                "accepted": 1,
                "partial": 0,
                "failed": 0,
                "unprocessed": 1,
                "cancelled": 0,
            },
            "results": [
                {"input": long_path, "status": "accepted", "artifacts": [{"format": "step", "path": "out/1.step"}]},
            ],
            "unprocessed_files": [long_path],
            "manifest": {"path": "out/manifest.json"},
        }
        with pytest.raises(BatchValidationError) as exc_info:
            parse_batch_response(payload)

        msg = exc_info.value.message
        assert len(msg) <= 128
        assert long_path not in msg
        assert "Overlapping or duplicate unprocessed file" in msg

    def test_parser_rejects_duplicate_cancelled_without_reflecting_input(self) -> None:
        long_path = "c/" * 240 + "part.par"
        payload: dict[str, Any] = {
            "contract_version": "1.0",
            "request_id": "req-adv-3",
            "status": "cancelled",
            "summary": {
                "total": 2,
                "accepted": 1,
                "partial": 0,
                "failed": 0,
                "unprocessed": 0,
                "cancelled": 1,
            },
            "results": [
                {"input": long_path, "status": "accepted", "artifacts": [{"format": "step", "path": "out/1.step"}]},
            ],
            "cancelled_files": [long_path],
            "manifest": {"path": "out/manifest.json"},
        }
        with pytest.raises(BatchValidationError) as exc_info:
            parse_batch_response(payload)

        msg = exc_info.value.message
        assert len(msg) <= 128
        assert long_path not in msg
        assert "Overlapping or duplicate cancelled file" in msg
