"""Facade re-exporting schema, validation, parsing, and projection contracts."""

from __future__ import annotations

from batch.accounting import (
    validate_summary_accounting,
)
from batch.parsing import (
    parse_batch_manifest,
    parse_batch_request,
    parse_batch_response,
)
from batch.paths import (
    validate_and_canonicalize_files,
    validate_and_canonicalize_input_path,
    validate_batch_request_semantics,
)
from batch.projection import (
    project_batch_manifest,
    project_batch_request,
    project_batch_response,
)
from batch.schemas import (
    get_batch_manifest_validator,
    get_batch_request_validator,
    get_batch_response_validator,
    load_batch_manifest_schema,
    load_batch_request_schema,
    load_batch_response_schema,
)
from batch.terminal import (
    validate_batch_manifest_semantics,
    validate_batch_response_semantics,
)

__all__ = [
    "get_batch_manifest_validator",
    "get_batch_request_validator",
    "get_batch_response_validator",
    "load_batch_manifest_schema",
    "load_batch_request_schema",
    "load_batch_response_schema",
    "parse_batch_manifest",
    "parse_batch_request",
    "parse_batch_response",
    "project_batch_manifest",
    "project_batch_request",
    "project_batch_response",
    "validate_and_canonicalize_files",
    "validate_and_canonicalize_input_path",
    "validate_batch_manifest_semantics",
    "validate_batch_request_semantics",
    "validate_batch_response_semantics",
    "validate_summary_accounting",
]
