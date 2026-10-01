"""Conservative contribution-policy discovery for bounty candidates.

Only explicit repository guidance or statements from repository owners,
members, and collaborators can establish a policy. Silence is ``unknown``;
it is never quietly upgraded to permission. Explicit prohibition always
wins over conditional or permissive language.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from .github_client import get_repo_file, list_recent_repo_comments

PolicyStatus = Literal["allowed", "conditional", "prohibited", "unknown"]


@dataclass(frozen=True)
class ContributionPolicy:
    status: PolicyStatus
    source_url: str
    excerpt: str


UNKNOWN_POLICY = ContributionPolicy(
    status="unknown",
    source_url="",
    excerpt="No explicit AI contribution policy found.",
)

# Maintainer statements can live outside versioned guidance. Preserve known,
# manually verified statements with their exact permalink instead of hoping a
# bounded recent-comment query rediscovers them forever.
_POLICY_OVERRIDES = {
    "gbdev/gbdev.github.io": ContributionPolicy(
        status="unknown",
        source_url="https://github.com/gbdev/gbdev.github.io/issues/103",
        excerpt=(
            "Open RFC on adopting an AI policy. Its proposal rejects all levels of AI "
            "involvement, but the repository has not adopted a final policy."
        ),
    ),
    "sindresorhus/awesome-lint": ContributionPolicy(
        status="prohibited",
        source_url="https://github.com/sindresorhus/awesome-lint/pull/234#issuecomment-4432977488",
        excerpt="I don't accept fully AI-generated PRs.",
    ),
}

_GUIDANCE_PATHS = (
    "CONTRIBUTING.md",
    ".github/CONTRIBUTING.md",
    "docs/CONTRIBUTING.md",
    "README.md",
    ".github/copilot-instructions.md",
    "AGENTS.md",
    "CLAUDE.md",
)
_AUTHORITATIVE_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}

_PROHIBITED = (
    re.compile(r"(?:do not|don't|does not|doesn't|won't|cannot|can't)\s+accept.{0,80}\b(?:ai|llm)[-\s]generated", re.IGNORECASE),
    re.compile(r"\b(?:ai|llm)[-\s]generated.{0,80}\b(?:prohibited|forbidden|not (?:allowed|accepted)|will be closed)", re.IGNORECASE),
    re.compile(r"\bno\s+(?:fully\s+)?(?:ai|llm)[-\s]generated\s+(?:pull requests?|prs?|contributions?)", re.IGNORECASE),
)
_CONDITIONAL = (
    re.compile(r"\b(?:ai|llm)[-\s](?:assisted|generated).{0,100}\b(?:must|required|requirement)\b.{0,80}\b(?:disclos|review|understand|verify|test)", re.IGNORECASE),
    re.compile(r"\b(?:ai|llm)[-\s](?:assisted|generated).{0,80}\b(?:allowed|permitted|accepted)\b.{0,40}\b(?:if|provided|when)\b", re.IGNORECASE),
)
_ALLOWED = (
    re.compile(r"\b(?:ai|llm)[-\s](?:assisted|generated).{0,80}\b(?:allowed|permitted|welcome|accepted)\b", re.IGNORECASE),
    re.compile(r"\b(?:allow|permit|welcome|accept).{0,80}\b(?:ai|llm)[-\s](?:assisted|generated)\b", re.IGNORECASE),
)


def _excerpt(text: str, match: re.Match[str]) -> str:
    compact = " ".join(text.split())
    phrase = " ".join(match.group(0).split())
    start = max(0, compact.lower().find(phrase.lower()) - 80)
    return compact[start : start + len(phrase) + 160].strip()


def classify_policy_text(text: str, source_url: str) -> ContributionPolicy | None:
    """Classify only explicit AI-contribution language in one source."""
    for status, patterns in (
        ("prohibited", _PROHIBITED),
        ("conditional", _CONDITIONAL),
        ("allowed", _ALLOWED),
    ):
        for pattern in patterns:
            match = pattern.search(text)
            if match:
                return ContributionPolicy(status, source_url, _excerpt(text, match))
    return None


def inspect_contribution_policy(
    repo: str, *, issue_comments: list[dict] | None = None
) -> ContributionPolicy:
    """Inspect explicit policy sources, returning ``unknown`` on silence.

    Precedence is deliberately strict: a known or discovered prohibition
    cannot be overridden by an allowed/conditional statement elsewhere.
    """
    if repo in _POLICY_OVERRIDES:
        return _POLICY_OVERRIDES[repo]

    findings: list[ContributionPolicy] = []
    checked_urls: list[str] = []

    comments = list(issue_comments or []) + list_recent_repo_comments(repo)
    for comment in comments:
        if comment.get("author_association") not in _AUTHORITATIVE_ASSOCIATIONS:
            continue
        source_url = comment.get("html_url", "")
        body = comment.get("body", "") or ""
        finding = classify_policy_text(body, source_url)
        # Permission is too easy to mention while discussing some other
        # project ("CPython accepts..."). Require a direct maintainer voice
        # before a comment can establish allowed/conditional status.
        direct_permission = re.search(
            r"\b(?:I|we|this project|our project)\s+"
            r"(?:allow|accept|welcome|permit)",
            body,
            re.IGNORECASE,
        )
        if finding and (
            finding.status == "prohibited"
            or direct_permission
        ):
            findings.append(finding)

    for path in _GUIDANCE_PATHS:
        source = get_repo_file(repo, path)
        if source is None:
            continue
        text, source_url = source
        checked_urls.append(source_url)
        finding = classify_policy_text(text, source_url)
        if finding:
            findings.append(finding)

    priority = {"prohibited": 0, "conditional": 1, "allowed": 2}
    if findings:
        return min(findings, key=lambda finding: priority[finding.status])

    return ContributionPolicy(
        status="unknown",
        source_url=checked_urls[0] if checked_urls else f"https://github.com/{repo}",
        excerpt="No explicit AI contribution policy found in the checked guidance and maintainer comments.",
    )
