from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import verify_submodule_checks as checks_module
from scripts.verify_submodule_checks import (
    SubmoduleCheckError,
    _fetch_check_runs,
    _load_policies,
    _successful_evidence,
    verify_submodule_checks,
)


def _write_policy(tmp_path: Path, *, project_path: str = "child") -> Path:
    policy = tmp_path / "checks.json"
    policy.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "projects": {
                    project_path: {
                        "repository": "owner/repository",
                        "required_checks": ["Quality", "Tests"],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return policy


def _check(name: str, conclusion: str, *, app: str = "github-actions") -> dict:
    return {
        "name": name,
        "status": "completed",
        "conclusion": conclusion,
        "details_url": f"https://example.invalid/{name}",
        "app": {"slug": app},
    }


def test_successful_evidence_uses_exact_github_actions_successes() -> None:
    evidence = _successful_evidence(
        [
            _check("Quality", "failure"),
            _check("Quality", "success"),
            _check("Tests", "success", app="untrusted-app"),
        ]
    )

    assert evidence == {"Quality": "https://example.invalid/Quality"}


def test_verify_requires_every_named_check_for_the_exact_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = _write_policy(tmp_path)
    child = tmp_path / "child"
    child.mkdir()
    revision = "a" * 40
    monkeypatch.setattr(checks_module, "_submodule_head_sha", lambda *_: revision)
    requested: list[tuple[str, str]] = []

    def fetch(repository: str, sha: str):
        requested.append((repository, sha))
        return [_check("Quality", "success"), _check("Tests", "success")]

    result = verify_submodule_checks(tmp_path, policy, fetch_checks=fetch)

    assert requested == [("owner/repository", revision)]
    assert result["projects"][0]["revision"] == revision


def test_verify_rejects_failed_or_missing_checks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = _write_policy(tmp_path)
    (tmp_path / "child").mkdir()
    monkeypatch.setattr(
        checks_module,
        "_submodule_head_sha",
        lambda *_: "b" * 40,
    )

    with pytest.raises(SubmoduleCheckError, match="Tests"):
        verify_submodule_checks(
            tmp_path,
            policy,
            fetch_checks=lambda *_: [_check("Quality", "success")],
        )


def test_policy_rejects_project_path_escape(tmp_path: Path) -> None:
    policy = _write_policy(tmp_path, project_path="../outside")

    with pytest.raises(SubmoduleCheckError, match="escapes"):
        _load_policies(tmp_path.resolve(), policy)


def test_fetch_check_runs_uses_optional_read_only_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[str, str | None, float]] = []
    response_payload = {
        "total_count": 1,
        "check_runs": [_check("Quality", "success")],
    }

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self) -> bytes:
            return json.dumps(response_payload).encode("utf-8")

    def fake_urlopen(request, *, timeout: float):
        captured.append(
            (
                request.full_url,
                request.get_header("Authorization"),
                timeout,
            )
        )
        return FakeResponse()

    monkeypatch.setenv("SUBMODULE_CHECKS_TOKEN", "read-only-token")
    monkeypatch.setattr(checks_module.urllib.request, "urlopen", fake_urlopen)

    runs = _fetch_check_runs("owner/repository", "c" * 40)

    assert runs == response_payload["check_runs"]
    assert captured == [
        (
            "https://api.github.com/repos/owner/repository/commits/"
            f"{'c' * 40}/check-runs?filter=all&per_page=100&page=1",
            "Bearer read-only-token",
            20.0,
        )
    ]


def test_policy_rejects_non_mapping_document(tmp_path: Path) -> None:
    policy = tmp_path / "checks.json"
    policy.write_text("[]", encoding="utf-8")

    with pytest.raises(SubmoduleCheckError, match="schema"):
        _load_policies(tmp_path.resolve(), policy)
