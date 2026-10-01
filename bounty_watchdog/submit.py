"""Test-gated draft PR submission -- the one place code leaves the phone.

Rule (same as SharpEdge-Robinhood-Bridge's approval gate): a fix only ever
becomes a PR if its own test command passes locally first, and it always
lands as a DRAFT. Marking it ready-for-review is a separate, explicit,
human-triggered step (`ready`). Automation tightens; it never loosens.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from .github_client import create_draft_pr
from .notify import notify


@dataclass(frozen=True)
class SubmitResult:
    status: str  # "tests_failed" | "pr_failed" | "draft_opened"
    detail: str
    pr_url: str | None = None


def _run_tests(repo_path: Path, test_command: str, timeout: float) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            test_command.split(),
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"could not run '{test_command}': {exc}"
    tail = "\n".join((result.stdout + result.stderr).strip().splitlines()[-15:])
    return result.returncode == 0, tail


def submit_fix(
    repo_path: Path,
    *,
    title: str,
    body: str,
    base: str = "main",
    test_command: str = "pytest -q",
    test_timeout: float = 300.0,
) -> SubmitResult:
    """Run tests in ``repo_path``; only on green does it open a draft PR.

    Assumes the caller (agent or human) already committed the fix to the
    current branch of ``repo_path``. This function never pushes/PRs a fix
    whose own tests fail -- that gate is non-negotiable.
    """
    passed, log_tail = _run_tests(repo_path, test_command, test_timeout)
    if not passed:
        notify("Bounty fix: tests FAILED", f"{repo_path.name}: not submitting", url=None)
        return SubmitResult(status="tests_failed", detail=log_tail)

    pr_url = create_draft_pr(repo_path, title=title, body=body, base=base)
    if not pr_url:
        notify("Bounty fix: PR create failed", repo_path.name, url=None)
        return SubmitResult(status="pr_failed", detail="gh pr create failed", pr_url=None)

    notify("Bounty fix: draft PR ready for your review", title, url=pr_url)
    return SubmitResult(status="draft_opened", detail="tests passed, opened as draft", pr_url=pr_url)
