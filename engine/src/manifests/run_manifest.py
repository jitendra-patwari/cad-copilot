"""Run manifest assembly, schema validation, and persistence routines."""

from __future__ import annotations

import copy
import functools
import importlib.resources
import json
from typing import Any

from jsonschema import Draft202012Validator

from manifests.models import ManifestConfigurationError

SCHEMA_RESOURCE_NAME = "run-manifest-v1.schema.json"


@functools.cache
def _load_cached_schema() -> dict[str, Any]:
    """Load and cache canonical RunManifest Draft 2020-12 schema object."""
    try:
        schema_path = importlib.resources.files("manifests").joinpath("schemas", SCHEMA_RESOURCE_NAME)
        schema_bytes = schema_path.read_bytes()
    except Exception as exc:
        raise ManifestConfigurationError(
            f"Failed to load run manifest schema resource '{SCHEMA_RESOURCE_NAME}'"
        ) from exc

    try:
        schema_obj = json.loads(schema_bytes.decode("utf-8"))
    except Exception as exc:
        raise ManifestConfigurationError(f"Run manifest schema '{SCHEMA_RESOURCE_NAME}' is not valid JSON") from exc

    if not isinstance(schema_obj, dict):
        raise ManifestConfigurationError(f"Run manifest schema '{SCHEMA_RESOURCE_NAME}' root must be a JSON object")

    return schema_obj


def load_run_manifest_schema() -> dict[str, Any]:
    """Load canonical RunManifest Draft 2020-12 schema from package data.

    Returns an isolated deep copy of the cached schema to prevent mutation.
    """
    return copy.deepcopy(_load_cached_schema())


@functools.cache
def get_run_manifest_validator() -> Draft202012Validator:
    """Create and return a compiled Draft202012Validator for the canonical schema."""
    schema = _load_cached_schema()
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)
