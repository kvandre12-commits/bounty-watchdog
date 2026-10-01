"""Thin wrapper around the `gh` CLI -- the only place that shells out to GitHub.

Fails soft everywhere: a broken repo, a network blip, or a missing `gh` must
never crash the watch loop. Every function returns an empty/None/False
sentinel on failure and never raises, mirroring the fail-soft rule already
used by `analytics_context.py` / `whale_context.py` in the sister repos.
"""

from __future__ import annotations

import base64
import json
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

DEFAULT_TIMEOUT = 30.0


def _run(args: list[str], *, cwd: Path | None = None, timeout: float = DEFAULT_TIMEOUT):
    try:
        return subprocess.run(
            args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _list_issues_call(
    repo: str, label: str | None, limit: int, timeout: float
) -> list[dict[str, Any]]:
    args = [
        "gh",
        "issue",
        "list",
        "--repo",
        repo,
        "--state",
        "open",
        "--limit",
        str(limit),
        "--json",
        "number,title,url,labels,createdAt",
    ]
    if label:
        args += ["--label", label]
    result = _run(args, timeout=timeout)
    if result is None or result.returncode != 0:
        return []
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def list_issues(
    repo: str,
    labels: tuple[str, ...] = (),
    *,
    limit: int = 20,
    timeout: float = DEFAULT_TIMEOUT,
) -> list[dict[str, Any]]:
    """List open issues for ``repo`` matching ANY of ``labels``.

    `gh issue list --label a,b` ANDs the labels together, which is *not*
    what a watchlist filter means here -- we want issues tagged with at
    least one interesting label. So this issues one call per label and
    unions the results (deduped by issue number). With no labels, it's a
    single unfiltered call.

    Do NOT "fix" this by switching to repeated `--label a --label b` flags
    thinking that's an OR -- verified live against gh 2.97.0 that repeated
    flags produce the exact same AND-intersection result as the comma form
    (e.g. simonw/llm: label=bug -> 13, label=enhancement -> 57, either
    combined form -> 1). GitHub's search API ANDs `label:` qualifiers
    regardless of how the CLI flag was spelled. The per-label-call-and-union
    approach below is the only version that's correct independent of that.

    Returns [] on any failure (repo typo'd, rate-limited, gh missing, bad
    JSON) -- one bad watchlist entry never takes down the whole poll pass.
    """
    if not labels:
        return _list_issues_call(repo, None, limit, timeout)
    by_number: dict[int, dict[str, Any]] = {}
    for label in labels:
        for issue in _list_issues_call(repo, label, limit, timeout):
            by_number.setdefault(issue.get("number"), issue)
    return list(by_number.values())


def search_funded_issues(
    query: str,
    *,
    limit: int = 30,
    page: int = 1,
    timeout: float = DEFAULT_TIMEOUT,
    exclude_repos: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    """Global GitHub search for open issues matching ``query`` -- a raw
    search qualifier the caller builds (e.g. ``label:"\U0001f48e Bounty"`` for
    Algora, or ``commenter:issuehunt-oss[bot]`` for IssueHunt -- different
    platforms are discoverable through entirely different qualifiers, not
    just different labels, verified live: IssueHunt doesn't use a GitHub
    label at all).

    Unlike `list_issues` (per-repo, OR-of-labels, curated watchlist), this
    hits GitHub's global issue search.

    ``exclude_repos`` is applied server-side via GitHub's own `-repo:`
    qualifier, not filtered out client-side after fetching. A known-bad
    repo that posts issues faster than legitimate orgs do can otherwise eat
    most of a small ``limit`` before the caller ever gets to see anything
    else -- verified live: one denylisted repo alone consumed 30 of a
    37-candidate sample. Excluding it up front means ``limit`` is spent on
    candidates actually worth evaluating.

    Returns [] on any failure -- same fail-soft contract as `list_issues`.

    Deliberately builds a single pre-encoded query-string endpoint rather
    than passing `-f` fields: `gh api` silently switches from GET to POST
    the moment any `-f`/`-F` flag is present, and GitHub's search endpoint
    only accepts GET -- that combo 404s every time. Verified live while
    building this (same call shape as the recon that found this endpoint
    in the first place).
    """
    query_parts = [query, "state:open"] + [f"-repo:{repo}" for repo in exclude_repos]
    full_query = " ".join(query_parts)
    params = urlencode(
        {
            "q": full_query,
            "per_page": limit,
            "page": page,
            "sort": "created",
            "order": "desc",
        }
    )
    args = ["gh", "api", f"search/issues?{params}"]
    result = _run(args, timeout=timeout)
    if result is None or result.returncode != 0:
        return []
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    items = data.get("items") if isinstance(data, dict) else None
    return items if isinstance(items, list) else []


def get_repo_meta(
    repo: str, *, timeout: float = DEFAULT_TIMEOUT
) -> dict[str, Any] | None:
    """Fetch the legitimacy signals for ``repo``: fork status, stars, owner type.

    Returns None on any failure -- callers must treat that as "can't verify,
    don't trust it" (fail CLOSED here, not open, since this gates a spam filter).
    """
    result = _run(["gh", "api", f"repos/{repo}"], timeout=timeout)
    if result is None or result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    owner = data.get("owner") or {}
    return {
        "fork": bool(data.get("fork", False)),
        "stars": int(data.get("stargazers_count", 0) or 0),
        "owner_type": owner.get("type", ""),
        "owner_login": owner.get("login", ""),
        "pushed_at": data.get("pushed_at"),
        "language": data.get("language"),
    }


def list_issue_comments(
    repo: str, number: int, *, timeout: float = DEFAULT_TIMEOUT
) -> list[dict[str, Any]]:
    """Fetch an issue's comment thread. Returns [] on any failure -- same
    fail-soft contract as everything else here.

    This exists specifically to catch the "already awarded but never closed"
    trap: Algora's bot leaves an unmistakable comment on payout, but the
    GitHub issue itself is never auto-closed and the funded label is never
    removed. Verified live -- daytona/content#11 paid out $150 to a real
    contributor ~16 months ago and is still sitting there looking claimable.
    """
    endpoint = f"repos/{repo}/issues/{number}/comments?per_page=100"
    result = _run(["gh", "api", "--paginate", "--slurp", endpoint], timeout=timeout)
    if result is None or result.returncode != 0:
        return []
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    if data and all(isinstance(page, list) for page in data):
        return [comment for page in data for comment in page]
    return data


def get_repo_file(
    repo: str, path: str, *, timeout: float = DEFAULT_TIMEOUT
) -> tuple[str, str] | None:
    """Return repository file text and its GitHub source URL, or ``None``.

    Used for contribution-policy checks. Keeping the API call here preserves
    the module boundary: policy interpretation belongs elsewhere.
    """
    result = _run(["gh", "api", f"repos/{repo}/contents/{path}"], timeout=timeout)
    if result is None or result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
        content = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None
    return content, data.get("html_url", f"https://github.com/{repo}/blob/HEAD/{path}")


def list_recent_repo_comments(
    repo: str, *, limit: int = 100, timeout: float = DEFAULT_TIMEOUT
) -> list[dict[str, Any]]:
    """Return recent issue/PR comments for authoritative policy statements."""
    params = urlencode({"sort": "updated", "direction": "desc", "per_page": limit})
    result = _run(
        ["gh", "api", f"repos/{repo}/issues/comments?{params}"], timeout=timeout
    )
    if result is None or result.returncode != 0:
        return []
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def list_referencing_prs(
    repo: str, number: int, *, limit: int = 100, timeout: float = DEFAULT_TIMEOUT
) -> list[dict[str, Any]]:
    """List PRs anywhere in ``repo`` whose body mentions issue ``number`` --
    candidates only, not confirmed competitors. An issue can look wide open
    (no assignee, no award comment) while actually having dozens of
    submissions already fighting over it -- verified live: a "clean"
    bounty with zero assignees turned out to have 77 referencing PRs.

    This is deliberately a raw candidate list, not a verdict: the search
    endpoint matches on a bare number appearing anywhere in the body, which
    can false-positive (a line number, an unrelated issue reference). The
    caller must still check each one for a real closing-keyword reference
    before treating it as actual competition -- see paid_bounties.py's
    `_classify_competing_prs`.

    Returns [] on any failure -- same fail-soft contract as everything else.
    """
    query = f"repo:{repo} is:pr {number} in:body"
    params = urlencode({"q": query, "per_page": limit})
    result = _run(["gh", "api", f"search/issues?{params}"], timeout=timeout)
    if result is None or result.returncode != 0:
        return []
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    items = data.get("items") if isinstance(data, dict) else None
    return items if isinstance(items, list) else []


def list_issue_timeline_prs(
    repo: str, number: int, *, limit: int = 100, timeout: float = DEFAULT_TIMEOUT
) -> list[dict[str, Any]]:
    """Return PRs cross-referenced in an issue's timeline.

    Timeline references are first-class GitHub linkage even when a PR omits
    closing syntax and the issue number entirely. That makes this channel
    independent of search-based reference detection.
    """
    params = urlencode({"per_page": limit})
    result = _run(
        [
            "gh",
            "api",
            "-H",
            "Accept: application/vnd.github+json",
            f"repos/{repo}/issues/{number}/timeline?{params}",
        ],
        timeout=timeout,
    )
    if result is None or result.returncode != 0:
        return []
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    prs: list[dict[str, Any]] = []
    for event in data:
        source_issue = (event.get("source") or {}).get("issue") or {}
        if event.get("event") == "cross-referenced" and source_issue.get(
            "pull_request"
        ):
            prs.append(source_issue)
    return prs


def search_semantic_prs(
    repo: str, issue_title: str, *, limit: int = 100, timeout: float = DEFAULT_TIMEOUT
) -> list[dict[str, Any]]:
    """Find PRs containing the issue's exact title phrase in title/body.

    This intentionally stays conservative. Exact phrase matching catches
    direct implementations that forgot to link the issue without inventing
    fuzzy competitors from a couple of generic shared words.
    """
    phrase = " ".join(issue_title.replace('"', " ").split())
    if not phrase:
        return []
    query = f'repo:{repo} is:pr "{phrase}" in:title,body'
    params = urlencode({"q": query, "per_page": limit})
    result = _run(["gh", "api", f"search/issues?{params}"], timeout=timeout)
    if result is None or result.returncode != 0:
        return []
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    items = data.get("items") if isinstance(data, dict) else None
    return items if isinstance(items, list) else []


def get_pr_merged(
    repo: str, number: int, *, timeout: float = DEFAULT_TIMEOUT
) -> bool | None:
    """True/False if ``number`` is a merged/unmerged PR, None if it can't be
    determined. GitHub's issue-search view of a PR only ever shows
    open/closed, never merged -- merged status requires the real pulls
    endpoint. Only called for closed PRs that already look like a real
    competing attempt, to keep this bounded (an open PR can't be merged by
    definition, so there's no reason to spend a call checking one).
    """
    result = _run(["gh", "api", f"repos/{repo}/pulls/{number}"], timeout=timeout)
    if result is None or result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or "merged" not in data:
        return None
    return bool(data["merged"])


def create_draft_pr(
    repo_path: Path,
    *,
    title: str,
    body: str,
    base: str = "main",
    timeout: float = DEFAULT_TIMEOUT,
) -> str | None:
    """Open a DRAFT pull request from the current branch of ``repo_path``.

    Always drafts -- this bot never opens a PR ready for review on its own.
    Returns the PR URL on success, None on any failure.
    """
    args = [
        "gh",
        "pr",
        "create",
        "--draft",
        "--base",
        base,
        "--title",
        title,
        "--body",
        body,
    ]
    result = _run(args, cwd=repo_path, timeout=timeout)
    if result is None or result.returncode != 0:
        return None
    url = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
    return url or None


def mark_pr_ready(pr_url: str, *, timeout: float = DEFAULT_TIMEOUT) -> bool:
    """Flip a draft PR to ready-for-review. The one and only "go live" step."""
    result = _run(["gh", "pr", "ready", pr_url], timeout=timeout)
    return result is not None and result.returncode == 0
