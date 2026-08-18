"""Require successful GitHub checks for each checked-out submodule commit."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_GITHUB_REPOSITORY = re.compile(
    r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$"
)
_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_SUCCESS = "success"


class SubmoduleCheckError(RuntimeError):
    """Raised when exact-revision CI evidence is absent or unsuccessful."""


@dataclass(frozen=True)
class SubmoduleCheckPolicy:
    path: Path
    repository: str
    required_checks: tuple[str, ...]


CheckFetcher = Callable[[str, str], list[Mapping[str, Any]]]


def _parse_arguments(arguments: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--policy",
        type=Path,
        default=Path(".github/submodule-required-checks.json"),
    )
    return parser.parse_args(arguments)


def _load_policies(root: Path, policy_path: Path) -> tuple[SubmoduleCheckPolicy, ...]:
    resolved_policy = (
        policy_path if policy_path.is_absolute() else root / policy_path
    ).resolve()
    try:
        payload = json.loads(resolved_policy.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SubmoduleCheckError(
            f"Submodule check policy is unreadable: {error}"
        ) from error
    schema_version = (
        payload.get("schema_version") if isinstance(payload, Mapping) else None
    )
    projects = payload.get("projects") if isinstance(payload, Mapping) else None
    if schema_version != 1 or not isinstance(projects, Mapping):
        raise SubmoduleCheckError("Submodule check policy schema is invalid.")

    policies: list[SubmoduleCheckPolicy] = []
    for raw_path, raw_policy in projects.items():
        if not isinstance(raw_path, str) or not isinstance(raw_policy, Mapping):
            raise SubmoduleCheckError("Submodule check policy entry is invalid.")
        project_path = Path(raw_path)
        resolved_project = (root / project_path).resolve()
        if (
            project_path.is_absolute()
            or resolved_project == root
            or not resolved_project.is_relative_to(root)
        ):
            raise SubmoduleCheckError(
                f"Submodule check path escapes the workspace: {raw_path}"
            )
        repository = raw_policy.get("repository")
        required_checks = raw_policy.get("required_checks")
        if not isinstance(repository, str) or not _GITHUB_REPOSITORY.fullmatch(
            repository
        ):
            raise SubmoduleCheckError(
                f"Invalid GitHub repository for submodule {raw_path}."
            )
        if (
            not isinstance(required_checks, list)
            or not required_checks
            or any(
                not isinstance(check, str) or not check.strip()
                for check in required_checks
            )
            or len(set(required_checks)) != len(required_checks)
        ):
            raise SubmoduleCheckError(
                f"Required checks are invalid for submodule {raw_path}."
            )
        policies.append(
            SubmoduleCheckPolicy(
                path=project_path,
                repository=repository,
                required_checks=tuple(required_checks),
            )
        )
    if not policies:
        raise SubmoduleCheckError("Submodule check policy has no projects.")
    return tuple(policies)


def _submodule_head_sha(root: Path, relative_path: Path) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root / relative_path), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise SubmoduleCheckError(
            f"Unable to read submodule revision for {relative_path}: {error}"
        ) from error
    revision = completed.stdout.strip().lower()
    if not _GIT_SHA.fullmatch(revision):
        raise SubmoduleCheckError(
            f"Submodule revision is invalid for {relative_path}: {revision!r}"
        )
    return revision


def _fetch_check_runs(repository: str, revision: str) -> list[Mapping[str, Any]]:
    owner, name = repository.split("/", maxsplit=1)
    encoded_repository = "/".join(
        urllib.parse.quote(segment, safe="") for segment in (owner, name)
    )
    runs: list[Mapping[str, Any]] = []
    page = 1
    while True:
        url = (
            f"https://api.github.com/repos/{encoded_repository}/commits/"
            f"{revision}/check-runs?filter=all&per_page=100&page={page}"
        )
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "yolo11-workspace-exact-sha-gate",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        token = os.environ.get("SUBMODULE_CHECKS_TOKEN", "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(url, headers=headers)
        payload = _read_github_json(request, repository, revision)
        page_runs = payload.get("check_runs") if isinstance(payload, Mapping) else None
        total_count = payload.get("total_count") if isinstance(payload, Mapping) else None
        if not isinstance(page_runs, list) or type(total_count) is not int:
            raise SubmoduleCheckError(
                f"GitHub returned invalid check data for {repository}@{revision}."
            )
        if any(not isinstance(run, Mapping) for run in page_runs):
            raise SubmoduleCheckError(
                f"GitHub returned an invalid check run for {repository}@{revision}."
            )
        runs.extend(page_runs)
        if len(runs) >= total_count:
            return runs
        if not page_runs:
            raise SubmoduleCheckError(
                f"GitHub check pagination stopped early for {repository}@{revision}."
            )
        page += 1


def _read_github_json(
    request: urllib.request.Request,
    repository: str,
    revision: str,
) -> Any:
    """Read GitHub JSON with bounded retry for transient infrastructure errors."""

    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=20.0) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            retryable = error.code == 429 or 500 <= error.code < 600
            if not retryable or attempt == 2:
                raise SubmoduleCheckError(
                    f"Unable to read GitHub checks for "
                    f"{repository}@{revision}: {error}"
                ) from error
        except (OSError, urllib.error.URLError) as error:
            if attempt == 2:
                raise SubmoduleCheckError(
                    f"Unable to read GitHub checks for "
                    f"{repository}@{revision}: {error}"
                ) from error
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise SubmoduleCheckError(
                f"GitHub returned unreadable check data for "
                f"{repository}@{revision}: {error}"
            ) from error
        time.sleep(0.5 * (2**attempt))
    raise AssertionError("GitHub retry loop exited without a result.")


def _successful_evidence(
    check_runs: Sequence[Mapping[str, Any]],
) -> dict[str, str]:
    evidence: dict[str, str] = {}
    for run in check_runs:
        app = run.get("app")
        if not isinstance(app, Mapping) or app.get("slug") != "github-actions":
            continue
        name = run.get("name")
        if (
            isinstance(name, str)
            and run.get("status") == "completed"
            and run.get("conclusion") == _SUCCESS
        ):
            details_url = run.get("details_url")
            evidence[name] = details_url if isinstance(details_url, str) else ""
    return evidence


def verify_submodule_checks(
    root: Path,
    policy_path: Path,
    *,
    fetch_checks: CheckFetcher = _fetch_check_runs,
) -> dict[str, Any]:
    workspace_root = root.expanduser().resolve()
    verified: list[dict[str, Any]] = []
    for policy in _load_policies(workspace_root, policy_path):
        revision = _submodule_head_sha(workspace_root, policy.path)
        runs = fetch_checks(policy.repository, revision)
        evidence = _successful_evidence(runs)
        missing = [
            name for name in policy.required_checks if name not in evidence
        ]
        if missing:
            observed = sorted(
                str(run.get("name"))
                for run in runs
                if isinstance(run.get("name"), str)
            )
            raise SubmoduleCheckError(
                f"Exact-SHA checks are not successful for "
                f"{policy.repository}@{revision}: missing={missing!r}; "
                f"observed={observed!r}"
            )
        verified.append(
            {
                "path": str(policy.path),
                "repository": policy.repository,
                "revision": revision,
                "checks": list(policy.required_checks),
            }
        )
    return {"event": "submodule_exact_sha_checks_verified", "projects": verified}


def main(arguments: Sequence[str] | None = None) -> int:
    parsed = _parse_arguments(arguments)
    try:
        result = verify_submodule_checks(parsed.root, parsed.policy)
    except SubmoduleCheckError as error:
        print(
            json.dumps(
                {"event": "submodule_exact_sha_checks_failed", "error": str(error)},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
