"""Validate the shared contract between the training and inference projects."""

from __future__ import annotations

import argparse
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Sequence


WORKSPACE_ENVIRONMENT_VARIABLE = "YOLO11_WORKSPACE_ROOT"


class WorkspaceValidationError(RuntimeError):
    """Raised when the checked-out workspace violates its integration contract."""


def _parse_arguments(arguments: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Workspace root containing workspace.yaml and both projects.",
    )
    return parser.parse_args(arguments)


@contextmanager
def _configured_workspace(root: Path) -> Iterator[None]:
    previous_value = os.environ.get(WORKSPACE_ENVIRONMENT_VARIABLE)
    os.environ[WORKSPACE_ENVIRONMENT_VARIABLE] = str(root)
    try:
        yield
    finally:
        if previous_value is None:
            os.environ.pop(WORKSPACE_ENVIRONMENT_VARIABLE, None)
        else:
            os.environ[WORKSPACE_ENVIRONMENT_VARIABLE] = previous_value


def _require_equal_path(label: str, first: Path, second: Path) -> None:
    if first.resolve() != second.resolve():
        raise WorkspaceValidationError(
            f"Workspace implementations disagree on {label}: {first} != {second}"
        )


def validate_workspace(root: Path) -> dict[str, str]:
    """Load both implementations and require one consistent path contract."""
    workspace_root = root.expanduser().resolve()
    manifest_path = workspace_root / "workspace.yaml"
    training_root = workspace_root / "Yolo11_auto_train"
    inference_root = workspace_root / "yolo11_inference"
    training_source = training_root / "src"

    if not manifest_path.is_file():
        raise WorkspaceValidationError(f"Workspace manifest is missing: {manifest_path}")
    if not training_source.is_dir():
        raise WorkspaceValidationError(
            f"Training submodule is not initialized: {training_root}"
        )
    if not inference_root.is_dir():
        raise WorkspaceValidationError(
            f"Inference submodule is not initialized: {inference_root}"
        )

    sys.path[:0] = [str(training_source), str(inference_root)]
    from core.workspace import (  # pylint: disable=import-outside-toplevel
        WorkspaceConfigurationError as InferenceWorkspaceConfigurationError,
        load_workspace_paths,
    )
    from picture_tool.workspace_paths import (  # pylint: disable=import-outside-toplevel
        WorkspaceConfigurationError as TrainingWorkspaceConfigurationError,
        WorkspacePaths as TrainingWorkspacePaths,
    )

    try:
        training_paths = TrainingWorkspacePaths.from_manifest(manifest_path)
        with _configured_workspace(workspace_root):
            inference_paths = load_workspace_paths(inference_root)
    except (
        TrainingWorkspaceConfigurationError,
        InferenceWorkspaceConfigurationError,
    ) as error:
        raise WorkspaceValidationError(str(error)) from error

    _require_equal_path(
        "workspace root", training_paths.workspace_root, inference_paths.root
    )
    _require_equal_path(
        "training project",
        training_paths.training_project,
        inference_paths.training_project,
    )
    _require_equal_path(
        "inference project",
        training_paths.inference_project,
        inference_paths.inference_project,
    )
    _require_equal_path(
        "training data", training_paths.training_data, inference_paths.training_data
    )
    _require_equal_path(
        "inference models",
        training_paths.inference_models,
        inference_paths.inference_models,
    )
    _require_equal_path("training checkout", training_paths.training_project, training_root)
    _require_equal_path(
        "inference checkout", training_paths.inference_project, inference_root
    )

    return {
        "event": "workspace_contract_validated",
        "workspace_root": str(workspace_root),
        "training_project": str(training_paths.training_project),
        "inference_project": str(training_paths.inference_project),
    }


def main(arguments: Sequence[str] | None = None) -> int:
    parsed_arguments = _parse_arguments(arguments)
    try:
        result = validate_workspace(parsed_arguments.root)
    except WorkspaceValidationError as error:
        print(
            json.dumps(
                {"event": "workspace_contract_invalid", "error": str(error)},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
