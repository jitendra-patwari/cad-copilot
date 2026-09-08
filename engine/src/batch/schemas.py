"""Package schema resources, loading, and validator caching for batch operations."""

from __future__ import annotations

import copy
import functools
import importlib.resources
import json
import re
from typing import Any

from jsonschema import Draft202012Validator

from batch.models import BatchConfigurationError

REQUEST_SCHEMA_NAME: str = "batch-request.schema.json"
RESPONSE_SCHEMA_NAME: str = "batch-response.schema.json"
MANIFEST_SCHEMA_NAME: str = "batch-manifest-v1.schema.json"

REQUEST_ID_PATTERN: re.Pattern[str] = re.compile(r"^[A-Za-z0-9._-]+$")


@functools.cache
def _load_cached_schema(resource_name: str) -> dict[str, Any]:
    """Load and cache Draft 2020-12 schema object from package data."""
    try:
        schema_path = importlib.resources.files("batch").joinpath("schemas", resource_name)
        schema_bytes = schema_path.read_bytes()
    except Exception as exc:
        raise BatchConfigurationError(f"Failed to load batch schema resource '{resource_name}'") from exc

    try:
        schema_obj = json.loads(schema_bytes.decode("utf-8"))
    except Exception as exc:
        raise BatchConfigurationError(f"Batch schema '{resource_name}' is not valid JSON") from exc

    if not isinstance(schema_obj, dict):
        raise BatchConfigurationError(f"Batch schema '{resource_name}' root must be a JSON object")

    return schema_obj


def load_batch_request_schema() -> dict[str, Any]:
    """Load canonical BatchRequest schema from package data."""
    return copy.deepcopy(_load_cached_schema(REQUEST_SCHEMA_NAME))


def load_batch_response_schema() -> dict[str, Any]:
    """Load canonical BatchResponse schema from package data."""
    return copy.deepcopy(_load_cached_schema(RESPONSE_SCHEMA_NAME))


def load_batch_manifest_schema() -> dict[str, Any]:
    """Load canonical BatchManifest schema from package data."""
    return copy.deepcopy(_load_cached_schema(MANIFEST_SCHEMA_NAME))


@functools.cache
def get_batch_request_validator() -> Draft202012Validator:
    """Return cached compiled Draft202012Validator for BatchRequest."""
    schema = _load_cached_schema(REQUEST_SCHEMA_NAME)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


@functools.cache
def get_batch_response_validator() -> Draft202012Validator:
    """Return cached compiled Draft202012Validator for BatchResponse."""
    schema = _load_cached_schema(RESPONSE_SCHEMA_NAME)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


@functools.cache
def get_batch_manifest_validator() -> Draft202012Validator:
    """Return cached compiled Draft202012Validator for BatchManifest."""
    schema = _load_cached_schema(MANIFEST_SCHEMA_NAME)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def safe_request_id(payload: Any) -> str:
    """Safely extract request_id from payload mapping or return 'unknown'."""
    if isinstance(payload, dict):
        val = payload.get("request_id")
        if isinstance(val, str) and (1 <= len(val) <= 96) and REQUEST_ID_PATTERN.fullmatch(val):
            return val
    return "unknown"


__all__ = [
    "MANIFEST_SCHEMA_NAME",
    "REQUEST_ID_PATTERN",
    "REQUEST_SCHEMA_NAME",
    "RESPONSE_SCHEMA_NAME",
    "get_batch_manifest_validator",
    "get_batch_request_validator",
    "get_batch_response_validator",
    "load_batch_manifest_schema",
    "load_batch_request_schema",
    "load_batch_response_schema",
    "safe_request_id",
]
