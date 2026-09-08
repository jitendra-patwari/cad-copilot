"""Tests for batch operation metadata descriptors and operation registry."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from batch.models import BatchConfigurationError
from batch.registry import (
    APPROVED_COLLISION_POLICIES,
    APPROVED_DOCUMENT_LIFECYCLES,
    APPROVED_SAFETY_CLASSES,
    OperationDescriptor,
    OperationRegistry,
    build_initial_registry,
)


class TestInitialRegistry:
    """Tests for the canonical initial registry and descriptor values."""

    def test_build_initial_registry_contains_exact_two_descriptors(self) -> None:
        registry = build_initial_registry()
        assert len(registry) == 2
        assert registry.operation_ids == ("export_3d", "publish_drawing")
        assert len(registry.descriptors) == 2

    def test_export_3d_descriptor_metadata(self) -> None:
        registry = build_initial_registry()
        desc = registry.get("export_3d")

        assert desc.operation_id == "export_3d"
        assert desc.input_extensions == (".par", ".psm", ".asm")
        assert desc.output_formats == ("step", "stl")
        assert desc.progress_label == "Export 3D CAD"
        assert desc.safety_class == "read_only_source"
        assert desc.document_lifecycle == "open_existing_close_without_save"
        assert desc.collision_policy == "fail_if_exists"

    def test_publish_drawing_descriptor_metadata(self) -> None:
        registry = build_initial_registry()
        desc = registry.get("publish_drawing")

        assert desc.operation_id == "publish_drawing"
        assert desc.input_extensions == (".dft",)
        assert desc.output_formats == ("pdf", "dxf")
        assert desc.progress_label == "Publish Drawing"
        assert desc.safety_class == "read_only_source"
        assert desc.document_lifecycle == "open_existing_close_without_save"
        assert desc.collision_policy == "fail_if_exists"

    def test_exact_lookup_and_subscription(self) -> None:
        registry = build_initial_registry()
        assert registry.get("export_3d") is registry["export_3d"]
        assert registry.get("publish_drawing") is registry["publish_drawing"]

    def test_membership_and_iteration(self) -> None:
        registry = build_initial_registry()
        assert "export_3d" in registry
        assert "publish_drawing" in registry
        assert "unknown_op" not in registry
        assert 12345 not in registry

        iterated = list(registry)
        assert len(iterated) == 2
        assert iterated[0].operation_id == "export_3d"
        assert iterated[1].operation_id == "publish_drawing"

    @pytest.mark.parametrize(
        "invalid_id",
        [
            "unknown",
            "EXPORT_3D",
            "export_step",
            "flat_pattern",
            "convert",
            "draft",
            "export_3d_v1",
        ],
    )
    def test_unknown_or_alias_ids_raise_configuration_error(self, invalid_id: str) -> None:
        registry = build_initial_registry()
        with pytest.raises(BatchConfigurationError, match="Unknown batch operation"):
            registry.get(invalid_id)
        with pytest.raises(BatchConfigurationError, match="Unknown batch operation"):
            _ = registry[invalid_id]

    def test_non_string_id_raises_configuration_error(self) -> None:
        registry = build_initial_registry()
        with pytest.raises(BatchConfigurationError, match="Operation ID must be a string"):
            registry.get(123)  # type: ignore[arg-type]


class TestDescriptorImmutabilityAndValidation:
    """Tests for OperationDescriptor immutability and invariant enforcement."""

    def test_descriptor_is_immutable(self) -> None:
        desc = OperationDescriptor(
            operation_id="test_op",
            input_extensions=(".par",),
            output_formats=("step",),
            progress_label="Test Op",
        )
        with pytest.raises(FrozenInstanceError):
            desc.operation_id = "mutated"  # type: ignore[misc]
        with pytest.raises(FrozenInstanceError):
            desc.progress_label = "mutated"  # type: ignore[misc]

    @pytest.mark.parametrize("bad_id", ["", "   ", " op", "op "])
    def test_invalid_operation_id(self, bad_id: str) -> None:
        with pytest.raises(ValueError, match="operation_id"):
            OperationDescriptor(
                operation_id=bad_id,
                input_extensions=(".par",),
                output_formats=("step",),
                progress_label="Label",
            )

    def test_empty_input_extensions(self) -> None:
        with pytest.raises(ValueError, match="at least one input extension"):
            OperationDescriptor(
                operation_id="test_op",
                input_extensions=(),
                output_formats=("step",),
                progress_label="Label",
            )

    @pytest.mark.parametrize(
        "bad_ext",
        ["par", ".", " .par", ".par ", ".PAR", "", "par.step"],
    )
    def test_invalid_input_extension_syntax(self, bad_ext: str) -> None:
        with pytest.raises(ValueError, match="extension"):
            OperationDescriptor(
                operation_id="test_op",
                input_extensions=(bad_ext,),
                output_formats=("step",),
                progress_label="Label",
            )

    def test_duplicate_or_case_colliding_extensions(self) -> None:
        with pytest.raises(ValueError, match="duplicate or case-colliding input extensions"):
            OperationDescriptor(
                operation_id="test_op",
                input_extensions=(".par", ".par"),
                output_formats=("step",),
                progress_label="Label",
            )

    def test_empty_output_formats(self) -> None:
        with pytest.raises(ValueError, match="at least one output format"):
            OperationDescriptor(
                operation_id="test_op",
                input_extensions=(".par",),
                output_formats=(),
                progress_label="Label",
            )

    @pytest.mark.parametrize(
        "bad_fmt",
        [".step", "STEP", " step", "step ", ""],
    )
    def test_invalid_output_format_syntax(self, bad_fmt: str) -> None:
        with pytest.raises(ValueError, match="format"):
            OperationDescriptor(
                operation_id="test_op",
                input_extensions=(".par",),
                output_formats=(bad_fmt,),
                progress_label="Label",
            )

    def test_duplicate_output_formats(self) -> None:
        with pytest.raises(ValueError, match="duplicate or case-colliding output formats"):
            OperationDescriptor(
                operation_id="test_op",
                input_extensions=(".par",),
                output_formats=("step", "step"),
                progress_label="Label",
            )

    @pytest.mark.parametrize("bad_label", ["", "   ", " Label", "Label "])
    def test_invalid_progress_label(self, bad_label: str) -> None:
        with pytest.raises(ValueError, match="progress_label"):
            OperationDescriptor(
                operation_id="test_op",
                input_extensions=(".par",),
                output_formats=("step",),
                progress_label=bad_label,
            )

    def test_unsupported_policies(self) -> None:
        with pytest.raises(ValueError, match="Unsupported safety_class"):
            OperationDescriptor(
                operation_id="test_op",
                input_extensions=(".par",),
                output_formats=("step",),
                progress_label="Label",
                safety_class="modify_source",  # type: ignore[arg-type]
            )

        with pytest.raises(ValueError, match="Unsupported document_lifecycle"):
            OperationDescriptor(
                operation_id="test_op",
                input_extensions=(".par",),
                output_formats=("step",),
                progress_label="Label",
                document_lifecycle="save_and_close",  # type: ignore[arg-type]
            )

        with pytest.raises(ValueError, match="Unsupported collision_policy"):
            OperationDescriptor(
                operation_id="test_op",
                input_extensions=(".par",),
                output_formats=("step",),
                progress_label="Label",
                collision_policy="overwrite",  # type: ignore[arg-type]
            )


class TestRegistryConstructionAndExtensibility:
    """Tests for registry construction, error cases, and extensibility proof."""

    def test_empty_registry_fails(self) -> None:
        with pytest.raises(ValueError, match="at least one descriptor"):
            OperationRegistry([])

    def test_non_descriptor_type_fails(self) -> None:
        with pytest.raises(TypeError, match="Expected OperationDescriptor"):
            OperationRegistry(["not_a_descriptor"])  # type: ignore[list-item]

    def test_duplicate_operation_id_fails(self) -> None:
        d1 = OperationDescriptor(
            operation_id="op_a",
            input_extensions=(".par",),
            output_formats=("step",),
            progress_label="Op A",
        )
        d2 = OperationDescriptor(
            operation_id="OP_A",
            input_extensions=(".psm",),
            output_formats=("stl",),
            progress_label="Op A upper",
        )
        with pytest.raises(ValueError, match="Duplicate or case-colliding operation ID"):
            OperationRegistry([d1, d2])

    def test_extensibility_proof_without_registry_code_modifications(self) -> None:
        """Prove that a test-only descriptor can be added to a new registry without changing registry.py."""
        initial = build_initial_registry()

        test_descriptor = OperationDescriptor(
            operation_id="test_custom_export",
            input_extensions=(".custom",),
            output_formats=("cust_out",),
            progress_label="Custom Export Label",
            safety_class="read_only_source",
            document_lifecycle="open_existing_close_without_save",
            collision_policy="fail_if_exists",
        )

        extended_registry = OperationRegistry((*initial.descriptors, test_descriptor))

        assert len(extended_registry) == 3
        assert "test_custom_export" in extended_registry
        assert extended_registry.get("test_custom_export") is test_descriptor
        assert extended_registry["test_custom_export"] is test_descriptor
        assert extended_registry.operation_ids == (
            "export_3d",
            "publish_drawing",
            "test_custom_export",
        )

    def test_approved_policy_constants(self) -> None:
        assert {"read_only_source"} == APPROVED_SAFETY_CLASSES
        assert {"open_existing_close_without_save"} == APPROVED_DOCUMENT_LIFECYCLES
        assert {"fail_if_exists"} == APPROVED_COLLISION_POLICIES
