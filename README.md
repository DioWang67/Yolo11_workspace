# YOLO11 Workspace

This directory is the integration boundary for the production inference and
training applications. The two child directories remain independent Git
repositories; `workspace.yaml` is the single source of truth for cross-project
paths.

```text
yolo11_workspace/
├── .gitmodules
├── workspace.yaml
├── Yolo11_auto_train/
└── yolo11_inference/
```

- Use `start_inference.bat` for production inference.
- Use `start_training.bat` for the training GUI or pass an operator handoff JSON
  path as its first argument.
- Do not rename either child directory without updating `workspace.yaml`.
- Runtime models remain owned by `yolo11_inference/models`; reviewed datasets,
  operator jobs, and training runs remain owned by `Yolo11_auto_train`.
- Inspection images and the inspection-history database are stored in the
  workspace-level `Result` directory configured by `paths.inference_results`.

## Clone

Clone the workspace and both child repositories together:

```powershell
git clone --recurse-submodules https://github.com/DioWang67/Yolo11_workspace.git
```

For an existing clone:

```powershell
git submodule update --init --recursive
```

## Development workflow

Commit and push changes inside the owning child repository first. Then update
the workspace pointer in a separate commit:

```powershell
git add Yolo11_auto_train yolo11_inference
git commit -m "Update workspace project revisions"
```

This repository intentionally does not copy child repository history, model
weights, datasets, inspection results, or local runtime logs.

## Continuous integration

The workspace workflow checks out the exact submodule revisions, rejects
uninitialized or mismatched submodules, validates `workspace.yaml` through both
projects' path implementations, and publishes JUnit reports. Each child
repository then owns its full lint, type-check, Linux/Windows test, coverage,
and package gates.

CI actions are pinned to immutable commit SHAs. Dependabot groups the scheduled
GitHub Actions, Python dependency, and submodule updates so those pins remain
maintainable.
