"""Composition entrypoint for generation IPC.

Provides request-aware resolver selection, output-root preflight,
and GenerationService orchestration.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from application.models import (
    ExampleGenerationRequest,
    GenerationRequest,
    PromptGenerationRequest,
)
from application.projection import build_failed_response
from application.service import GenerationService


def run_generation(request: GenerationRequest) -> Mapping[str, Any]:
    """Execute generation request via GenerationService.

    Args:
        request: Validated typed generation request.

    Returns:
        Schema-valid GenerationResponse dictionary.
    """
    raw_output_root = os.environ.get("CAD_OUTPUT_ROOT")
    if raw_output_root is None or not raw_output_root.strip():
        return build_failed_response(request.request_id, "OUTPUT_PATH_NOT_ALLOWED")

    if isinstance(request, ExampleGenerationRequest):
        from example_catalog import resolve_example_plan

        service = GenerationService(example_resolver=resolve_example_plan)
        return service.generate(request, output_root=raw_output_root)

    if isinstance(request, PromptGenerationRequest):
        api_key = os.environ.get("GEMINI_API_KEY")
        if api_key is None or not api_key.strip():
            service = GenerationService(prompt_resolver=None)
        else:
            model_env = os.environ.get("CAD_LLM_MODEL")
            model_id = "gemini-3.5-flash-lite" if model_env is None else model_env

            prompt_resolver = None
            try:
                from plan_providers.gemini import GeminiPlanResolver
                from plan_providers.proposal import ProposalError

                try:
                    prompt_resolver = GeminiPlanResolver(api_key=api_key, model_id=model_id)
                except ProposalError:
                    prompt_resolver = None
            except ImportError:
                prompt_resolver = None

            service = GenerationService(prompt_resolver=prompt_resolver)

        return service.generate(request, output_root=raw_output_root)

    service = GenerationService()
    return service.generate(request, output_root=raw_output_root)
