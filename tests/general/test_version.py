"""Tests for version resolution."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

from packaging.version import InvalidVersion, Version
import pytest

from cgt_calc.version import (
    PLACEHOLDER_VERSION,
    RELEASE_TAG_GLOB,
    _source_repo_dir,
    get_version,
)

if TYPE_CHECKING:
    from pathlib import Path

GIT_DESCRIBE_COMMAND = [
    "git",
    "describe",
    "--tags",
    "--always",
    "--dirty",
    "--match",
    RELEASE_TAG_GLOB,
]


class FakeGit:
    """Replacement for subprocess.run that replays a canned git outcome."""

    def __init__(
        self,
        stdout: str = "",
        returncode: int = 0,
        error: Exception | None = None,
    ) -> None:
        """Store the outcome to replay and prepare the call log."""
        self.stdout = stdout
        self.returncode = returncode
        self.error = error
        self.calls: list[list[str]] = []

    def __call__(
        self, args: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        """Record the call and replay the canned outcome."""
        self.calls.append(args)
        if self.error is not None:
            raise self.error
        return subprocess.CompletedProcess(args, self.returncode, self.stdout, "")


def _patch_version_sources(
    monkeypatch: pytest.MonkeyPatch,
    git: FakeGit,
    metadata_version: str = PLACEHOLDER_VERSION,
    repo_dir: Path | None = None,
) -> None:
    """Point version resolution at the given metadata, checkout and git."""
    monkeypatch.setattr(
        "cgt_calc.version.importlib.metadata.version", lambda _name: metadata_version
    )
    monkeypatch.setattr("cgt_calc.version._source_repo_dir", lambda: repo_dir)
    monkeypatch.setattr("cgt_calc.version.subprocess.run", git)


def test_stamped_version_is_returned_verbatim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test that a stamped metadata version is used without consulting git."""
    git = FakeGit(stdout="v2.0.0-157-g3473d67")
    _patch_version_sources(
        monkeypatch, git, metadata_version="2.1.0", repo_dir=tmp_path
    )

    assert get_version() == "2.1.0"
    assert git.calls == []


@pytest.mark.parametrize(
    ("described", "expected"),
    [
        ("v2.0.0", "2.0.0"),
        ("v2.0.0-dirty", "2.0.0+dirty"),
        ("v2.0.0-157-g3473d67", "2.0.0.post157+g3473d67"),
        ("v2.0.0-157-g3473d67-dirty", "2.0.0.post157+g3473d67.dirty"),
        ("v2.1.0rc1", "2.1.0rc1"),
        ("3473d67", "0.0.0+g3473d67"),
        ("3473d67-dirty", "0.0.0+g3473d67.dirty"),
        # Git is asked for release tags only, but a tag naming no release must
        # never reach the version either way.
        ("backup/tts-prerebase6", "0.0.0"),
        ("backup/tts-prerebase6-dirty", "0.0.0+dirty"),
        ("backup/tts-prerebase6-5-g3473d67", "0.0.0+g3473d67"),
    ],
)
def test_placeholder_version_is_described_by_git(
    described: str,
    expected: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that git describe output becomes a PEP 440 version."""
    git = FakeGit(stdout=f"{described}\n")
    _patch_version_sources(monkeypatch, git, repo_dir=tmp_path)

    version = get_version()

    assert version == expected
    assert git.calls == [GIT_DESCRIBE_COMMAND]
    try:
        Version(version)
    except InvalidVersion as err:  # pragma: no cover - only on a regression
        pytest.fail(f"{version} is not a valid version: {err}")


@pytest.mark.parametrize(
    "git",
    [
        pytest.param(FakeGit(returncode=128), id="git-failed"),
        pytest.param(FakeGit(stdout="  \n"), id="empty-output"),
        pytest.param(FakeGit(error=FileNotFoundError("git")), id="git-missing"),
        pytest.param(FakeGit(error=PermissionError("cwd")), id="unreadable-cwd"),
        pytest.param(
            FakeGit(error=subprocess.TimeoutExpired(GIT_DESCRIBE_COMMAND, 5)),
            id="timed-out",
        ),
    ],
)
def test_placeholder_version_survives_git_failures(
    git: FakeGit, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test that the placeholder stands when git cannot describe the checkout."""
    _patch_version_sources(monkeypatch, git, repo_dir=tmp_path)

    assert get_version() == PLACEHOLDER_VERSION


def test_placeholder_version_outside_a_checkout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that git is not run at all when there is no checkout around."""
    git = FakeGit(stdout="v2.0.0-157-g3473d67")
    _patch_version_sources(monkeypatch, git, repo_dir=None)

    assert get_version() == PLACEHOLDER_VERSION
    assert git.calls == []


def test_source_repo_dir_requires_a_git_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test that the package directory counts only inside a git checkout."""
    package_dir = tmp_path / "cgt_calc"
    package_dir.mkdir()
    monkeypatch.setattr("cgt_calc.version.__file__", str(package_dir / "version.py"))

    assert _source_repo_dir() is None

    # Worktrees keep a .git file rather than a directory.
    (tmp_path / ".git").write_text("gitdir: /elsewhere\n", encoding="utf-8")

    assert _source_repo_dir() == package_dir
