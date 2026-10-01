"""Paid-bounty discovery: diversified global search + eligibility gate.

Separate module from `github_client.py` on purpose -- that file is a thin,
opinion-free `gh` wrapper; this one holds the judgment call about what
counts as a *real*, *worth-pursuing* funded bounty.

Disqualify ONLY on evidence that the bounty is: awarded, exclusively
claimed, closed to submissions, or already solved under its terms.
Everything else -- including how crowded it looks -- is a RANKING factor,
not a gate.

That last point replaced an earlier, cruder version of this module that
hard-rejected anything with more than N referencing PRs. Audited live on
onyx-dot-app/onyx#2281 (looked clean: zero assignees, no award comment) and
found 77 PRs referencing it, going back nearly two years. But a raw
reference count is not competition -- it's just a number mention. Checked
properly: which PRs actually carry a closing-keyword reference to THIS
issue (not a coincidental number match), and of those, whether ANY is
actually MERGED. Exhaustively verified: none were, across all 32
closed-and-demonstrably-matching PRs. So the real finding isn't "too
crowded to bother" -- it's "genuinely still unsolved despite many
attempts," a different, more interesting signal worth a maintainer
question (see `MAINTAINER_QUESTION_TEMPLATE`), not an automatic
disqualifier.

Two earlier corrections, still true:
  - Fork status is NOT a hard gate. `is_fork` is recorded for a human to
    weigh, never filtered on by itself.
  - "No Algora comment" means "unverified through Algora specifically," not
    "unpaid." `_terms_comments` also accepts the repo owner's own account
    stating a dollar amount directly.

Discovery runs multiple platform-specific search queries
(`config.DISCOVERY_QUERIES`), each verified live before being added, not
assumed. IssueHunt uses no GitHub label at all (discovered via
`commenter:issuehunt-oss[bot]` instead), and funds an issue through
potentially MANY separate per-funder comments that must be SUMMED, not just
the first one taken (verified live: a real issue with five separate
$2/$50/$500/$50/$20 funding comments totaling $622). Polar.sh was checked
and dropped -- zero real activity found under the guessed bot login.

Discovery is also diversified against single-repo flooding, because a
single noisy repo can otherwise consume an entire search budget before
anything else is even evaluated (confirmed live: 90+ of a ~100 "newest"
results from one repo):
  - a per-repo cap during evaluation (PAID_BOUNTY_PER_REPO_CAP)
  - multi-page fetching past that repo's flood (PAID_BOUNTY_SEARCH_PAGES)
Plus a persistent evaluation cache so repeat hunts don't re-spend API calls
re-checking the same already-rejected candidates.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum

from .candidate_cache import CandidateCache
from .config import (
    DISCOVERY_QUERIES,
    EVALUATION_LOGIC_VERSION,
    KNOWN_BOUNTY_BOTS,
    MAINTAINER_ACTIVITY_MAX_DAYS,
    MAX_MERGE_CHECKS_PER_ISSUE,
    MIN_BOUNTY_USD,
    PAID_BOUNTY_DENYLIST,
    PAID_BOUNTY_FETCH_LIMIT,
    PAID_BOUNTY_PER_REPO_CAP,
    PAID_BOUNTY_SEARCH_PAGES,
    PAYMENT_PROOF_MAX_DAYS,
    SUMMED_AMOUNT_BOTS,
)
from .contribution_policy import (
    UNKNOWN_POLICY,
    ContributionPolicy,
    inspect_contribution_policy,
)
from .github_client import (
    get_pr_merged,
    get_repo_meta,
    list_issue_comments,
    list_issue_timeline_prs,
    list_referencing_prs,
    search_funded_issues,
    search_semantic_prs,
)

_ALREADY_AWARDED = re.compile(r"has been awarded", re.IGNORECASE)
_BOUNTY_PAUSED = re.compile(
    r"(?:\bbount(?:y|ies)\b.{0,50}\b(?:paused?|suspended|on hold)\b|"
    r"\b(?:paused?|suspended|on hold)\b.{0,50}\bbount(?:y|ies)\b)",
    re.IGNORECASE | re.DOTALL,
)
_MERGED_REWARDABLE = re.compile(
    r"(?:pull request|\bpr\b).{0,160}\bmerged\b.{0,160}"
    r"\bbount(?:y|ies)\b.{0,80}\b(?:can be rewarded|rewardable)\b",
    re.IGNORECASE | re.DOTALL,
)
_BOUNTY_AMOUNT = re.compile(r"\$([\d,]+(?:\.\d+)?)")


class BountyState(str, Enum):
    OPEN = "open"
    ALREADY_AWARDED = "already_awarded"
    PAYMENT_REVERIFY = "payment_reverify"


MAINTAINER_QUESTION_TEMPLATE = (
    "Hi! I noticed this issue has {n} prior PRs referencing it, none merged "
    "(oldest from {oldest}). Before I invest time in another attempt: is "
    "this bounty still open for new submissions, and is there anything "
    "specific blocking review (e.g. CI/author allowlist) that a new PR "
    "should account for?"
)


@dataclass(frozen=True)
class CompetitionEvidence:
    """What real competition looks like for one issue -- not a bare count.

    `strong_*` counts only PRs that demonstrably reference THIS issue (a
    closing keyword in the body, or the issue number in the title) --
    distinct from a bare number match anywhere in a PR's body, which is
    noise, not evidence. `already_merged` is the one fact that actually
    disqualifies a candidate; everything else here is for ranking/display.
    """

    strong_open: int
    strong_closed_unmerged: int
    already_merged: bool
    weak_mentions: int
    sample_urls: tuple[str, ...]
    checked_merge_status_for: int  # how many closed PRs we actually verified
    total_referencing: int  # how many closed+strong PRs exist in total

    @property
    def total_strong(self) -> int:
        return self.strong_open + self.strong_closed_unmerged


_NO_COMPETITION_DATA = CompetitionEvidence(0, 0, False, 0, (), 0, 0)


@dataclass(frozen=True)
class FundedIssue:
    repo: str
    number: int
    title: str
    url: str
    amount_usd: float
    stars: int
    is_fork: bool
    language: str | None
    comment_count: int
    body_length: int
    competition: CompetitionEvidence
    contribution_policy: ContributionPolicy = UNKNOWN_POLICY
    bounty_label: str | None = None

    def __post_init__(self) -> None:
        if self.bounty_label is None:
            object.__setattr__(self, "bounty_label", f"${self.amount_usd:,.0f}")


@dataclass(frozen=True)
class ScanReport:
    """Full accounting of one scan -- not just the winners."""

    eligible: list[FundedIssue]
    total_fetched: int
    evaluated: int
    cache_hits: int
    repo_cap_skips: int
    rejection_reasons: Counter = field(default_factory=Counter)
    near_misses: list[dict] = field(default_factory=list)
    already_solved: list[dict] = field(default_factory=list)
    policy_rejections: list[dict] = field(default_factory=list)


def _repo_from_issue(issue: dict) -> str:
    repo_url = issue.get("repository_url", "")
    parts = repo_url.rstrip("/").split("/")
    return "/".join(parts[-2:]) if len(parts) >= 2 else ""


def _comment_datetime(comment: dict) -> datetime | None:
    raw = comment.get("created_at") or comment.get("createdAt")
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _classify_bounty_state(
    comments: list[dict], terms_comments: list[dict] | None = None
) -> BountyState:
    """Classify explicit terminal states and payment-proof freshness.

    Paused and merged-but-only-rewardable notices are deliberately not
    called "awarded": payment may still be recoverable, but a contributor
    must reverify it before the issue can become eligible again.
    """
    bodies = [c.get("body", "") or "" for c in comments]
    if any(_ALREADY_AWARDED.search(body) for body in bodies):
        return BountyState.ALREADY_AWARDED
    if any(
        _BOUNTY_PAUSED.search(body) or _MERGED_REWARDABLE.search(body)
        for body in bodies
    ):
        return BountyState.PAYMENT_REVERIFY
    if terms_comments is None:
        return BountyState.OPEN

    cutoff = datetime.now(UTC) - timedelta(days=PAYMENT_PROOF_MAX_DAYS)
    timestamps = [_comment_datetime(comment) for comment in terms_comments]
    if not timestamps or any(
        timestamp is None or timestamp < cutoff for timestamp in timestamps
    ):
        return BountyState.PAYMENT_REVERIFY
    return BountyState.OPEN


def _terms_comments(comments: list[dict], owner_login: str) -> list[dict]:
    """ALL comments that establish payment terms -- plural, since some
    platforms (IssueHunt) let multiple separate funders each post their own
    comment, all of which must be considered together. Returns every
    matching known-bot comment if any exist; otherwise falls back to a
    single repo-owner-stated comment, if any.
    """
    bot_comments = [
        c
        for c in comments
        if c.get("user", {}).get("login", "") in KNOWN_BOUNTY_BOTS
        and _BOUNTY_AMOUNT.search(c.get("body", "") or "")
    ]
    if bot_comments:
        return bot_comments
    owner_comment = next(
        (
            c
            for c in comments
            if owner_login
            and c.get("user", {}).get("login", "") == owner_login
            and _BOUNTY_AMOUNT.search(c.get("body", "") or "")
        ),
        None,
    )
    return [owner_comment] if owner_comment else []


def _bounty_amount(terms_comments: list[dict]) -> float | None:
    """Sum amounts from SUMMED_AMOUNT_BOTS (IssueHunt: many separate
    funders, verified live a real issue totaled $622 across five separate
    comments) -- otherwise take the first match only (Algora: one terms
    comment states the whole bounty already).
    """
    if not terms_comments:
        return None
    summed: list[float] = []
    first_other: float | None = None
    for c in terms_comments:
        match = _BOUNTY_AMOUNT.search(c.get("body", "") or "")
        if not match:
            continue
        amount = float(match.group(1).replace(",", ""))
        if c.get("user", {}).get("login", "") in SUMMED_AMOUNT_BOTS:
            summed.append(amount)
        elif first_other is None:
            first_other = amount
    return sum(summed) if summed else first_other


def _is_exclusively_claimed(issue: dict) -> bool:
    return bool(issue.get("assignees"))


def _maintainer_active(pushed_at: str | None) -> bool:
    if not pushed_at:
        return False
    try:
        pushed = datetime.fromisoformat(pushed_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    return (datetime.now(UTC) - pushed) <= timedelta(days=MAINTAINER_ACTIVITY_MAX_DAYS)


def _is_strong_match(pr: dict, number: int) -> bool:
    """Does this PR demonstrably address THIS issue, not just coincidentally
    mention its number somewhere? Two independent signals, either enough:
    a GitHub auto-close keyword in the body ("fixes #920"), or the issue
    number explicitly called out in the title ("[BOUNTY #2281] ...").
    """
    title = pr.get("title", "") or ""
    body = pr.get("body", "") or ""
    if re.search(rf"#{number}\b", title):
        return True
    # GitHub accepts both local (``Fixes #37``) and repository-qualified
    # (``Fixes owner/repo#37``) closing references. The qualified form is
    # common in PRs opened from forks; treating it as weak made a real open
    # competitor on awesome-lint#37 invisible to ranking.
    closing = re.compile(
        rf"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s*"
        rf"(?:[\w.-]+/[\w.-]+)?#{number}\b",
        re.IGNORECASE,
    )
    return bool(closing.search(body))


def _pr_key(pr: dict) -> str:
    number = pr.get("number")
    return f"number:{number}" if number is not None else f"url:{pr.get('html_url', '')}"


def _classify_competing_prs(
    repo: str, number: int, issue_title: str = ""
) -> CompetitionEvidence:
    """Union three independent competition channels, deduplicated by PR.

    Search references still require strong closing/title evidence. Timeline
    links and exact issue-title semantic search are independently strong;
    neither requires authors to repeat the issue number correctly.
    """
    references = list_referencing_prs(repo, number)
    timeline = list_issue_timeline_prs(repo, number)
    semantic = search_semantic_prs(repo, issue_title) if issue_title else []

    strong_by_key: dict[str, dict] = {}
    for pr in references:
        if _is_strong_match(pr, number):
            strong_by_key[_pr_key(pr)] = pr
    for pr in timeline + semantic:
        strong_by_key.setdefault(_pr_key(pr), pr)

    reference_by_key = {_pr_key(pr): pr for pr in references}
    weak_count = len(set(reference_by_key) - set(strong_by_key))
    strong = list(strong_by_key.values())
    open_prs = [c for c in strong if c.get("state") == "open"]
    closed_prs = [c for c in strong if c.get("state") != "open"]

    checked = 0
    unmerged_count = 0
    already_merged = False
    for pr in closed_prs[:MAX_MERGE_CHECKS_PER_ISSUE]:
        checked += 1
        merged = get_pr_merged(repo, pr.get("number", 0))
        if merged:
            already_merged = True
            break  # one confirmed merge is enough to disqualify -- stop spending calls
        unmerged_count += 1

    sample = tuple(c.get("html_url", "") for c in (open_prs + closed_prs)[:5])
    return CompetitionEvidence(
        strong_open=len(open_prs),
        strong_closed_unmerged=unmerged_count,
        already_merged=already_merged,
        weak_mentions=weak_count,
        sample_urls=sample,
        checked_merge_status_for=checked,
        total_referencing=len(strong),
    )


def _evaluate_one(
    issue: dict, meta: dict
) -> tuple[
    FundedIssue | None,
    str,
    float | None,
    CompetitionEvidence | None,
    ContributionPolicy | None,
]:
    """Run every eligibility check, including policy before competition."""
    if not _maintainer_active(meta["pushed_at"]):
        return None, "maintainer_inactive", None, None, None
    if _is_exclusively_claimed(issue):
        return None, "already_claimed", None, None, None
    repo = _repo_from_issue(issue)
    number = issue.get("number", 0)
    comments = list_issue_comments(repo, number)
    state = _classify_bounty_state(comments)
    if state is BountyState.ALREADY_AWARDED:
        return None, state.value, None, None, None
    if state is BountyState.PAYMENT_REVERIFY:
        return None, state.value, None, None, None
    terms = _terms_comments(comments, meta.get("owner_login", ""))
    if not terms:
        return None, "no_verifiable_terms", None, None, None
    state = _classify_bounty_state(comments, terms)
    if state is BountyState.PAYMENT_REVERIFY:
        return None, state.value, None, None, None
    amount = _bounty_amount(terms)
    if amount is None or amount < MIN_BOUNTY_USD:
        return None, "below_reward_floor", amount, None, None

    # Contribution requirements are checked before competition and, more
    # importantly, before any implementation work. An explicit prohibition
    # is final; human review is not treated as a loophole.
    policy = inspect_contribution_policy(repo, issue_comments=comments)
    if policy.status == "prohibited":
        return None, "ai_contributions_prohibited", amount, None, policy

    # Competition is checked LAST and only for candidates that already
    # cleared everything else -- it's the most expensive check (can cost
    # several API calls), no sense spending it on a candidate that would
    # be rejected anyway.
    competition = _classify_competing_prs(repo, number, issue.get("title", ""))
    if competition.already_merged:
        return None, "already_solved", amount, competition, policy

    result = FundedIssue(
        repo=repo,
        number=number,
        title=issue.get("title", "(untitled)"),
        url=issue.get("html_url", ""),
        amount_usd=amount,
        stars=meta["stars"],
        is_fork=meta["fork"],
        language=meta.get("language"),
        comment_count=issue.get("comments", 0) or 0,
        body_length=len(issue.get("body") or ""),
        competition=competition,
        contribution_policy=policy,
    )
    return result, "eligible", amount, competition, policy


def scan_funded_issues(
    *,
    queries: tuple[str, ...] = DISCOVERY_QUERIES,
    per_page: int = PAID_BOUNTY_FETCH_LIMIT,
    pages: int = PAID_BOUNTY_SEARCH_PAGES,
    per_repo_cap: int = PAID_BOUNTY_PER_REPO_CAP,
    cache: CandidateCache | None = None,
) -> ScanReport:
    """The full diversified scan across every discovery query (one per
    known platform). Never raises -- every failure mode inside collapses to
    "skip this candidate". `per_repo_cap` and the dedup-by-URL set are
    shared ACROSS queries, not reset per query -- the same repo showing up
    via two different platforms still only gets evaluated up to the cap
    once, and an issue found by one query is never double-counted if a
    second query also happens to surface it.
    """
    meta_cache: dict[str, dict | None] = {}
    repo_counts: Counter = Counter()
    seen_urls: set[str] = set()
    rejection_reasons: Counter = Counter()
    near_misses: list[dict] = []
    already_solved: list[dict] = []
    policy_rejections: list[dict] = []
    eligible: list[FundedIssue] = []
    total_fetched = evaluated = cache_hits = repo_cap_skips = 0

    for query in queries:
        for page in range(1, pages + 1):
            candidates = search_funded_issues(
                query,
                limit=per_page,
                page=page,
                exclude_repos=tuple(PAID_BOUNTY_DENYLIST),
            )
            if not candidates:
                break  # exhausted this query's results; move to the next query
            total_fetched += len(candidates)

            for issue in candidates:
                url = issue.get("html_url", "")
                repo = _repo_from_issue(issue)
                if not repo or not url or url in seen_urls:
                    continue
                seen_urls.add(url)

                if repo in PAID_BOUNTY_DENYLIST:
                    rejection_reasons["denylisted"] += 1
                    continue

                if cache is not None:
                    cached = cache.get_fresh(
                        url, logic_version=EVALUATION_LOGIC_VERSION
                    )
                    if cached is not None:
                        cache_hits += 1
                        if cached.outcome != "eligible":
                            rejection_reasons[cached.outcome] += 1
                            continue

                if repo_counts[repo] >= per_repo_cap:
                    repo_cap_skips += 1
                    continue
                repo_counts[repo] += 1

                if repo not in meta_cache:
                    meta_cache[repo] = get_repo_meta(repo)
                meta = meta_cache[repo]
                if meta is None:
                    rejection_reasons["repo_unverifiable"] += 1
                    if cache is not None:
                        cache.record(
                            url,
                            "repo_unverifiable",
                            logic_version=EVALUATION_LOGIC_VERSION,
                        )
                    continue

                evaluated += 1
                result, reason, amount, competition, policy = _evaluate_one(issue, meta)
                if cache is not None:
                    detail = (
                        f"{policy.status}|{policy.source_url}|{policy.excerpt}"
                        if policy
                        else (f"${amount}" if amount else None)
                    )
                    cache.record(
                        url,
                        reason,
                        logic_version=EVALUATION_LOGIC_VERSION,
                        detail=detail,
                    )

                if result is not None:
                    eligible.append(result)
                elif reason == "below_reward_floor":
                    rejection_reasons[reason] += 1
                    near_misses.append(
                        {
                            "repo": repo,
                            "number": issue.get("number"),
                            "amount": amount,
                            "title": issue.get("title", ""),
                        }
                    )
                elif reason == "ai_contributions_prohibited":
                    rejection_reasons[reason] += 1
                    policy_rejections.append(
                        {
                            "repo": repo,
                            "number": issue.get("number"),
                            "status": policy.status if policy else "unknown",
                            "source": policy.source_url if policy else "",
                            "excerpt": policy.excerpt if policy else "",
                        }
                    )
                elif reason == "already_solved":
                    rejection_reasons[reason] += 1
                    already_solved.append(
                        {
                            "repo": repo,
                            "number": issue.get("number"),
                            "amount": amount,
                            "title": issue.get("title", ""),
                            "evidence": competition.sample_urls if competition else (),
                        }
                    )
                else:
                    rejection_reasons[reason] += 1

    return ScanReport(
        eligible=eligible,
        total_fetched=total_fetched,
        evaluated=evaluated,
        cache_hits=cache_hits,
        repo_cap_skips=repo_cap_skips,
        rejection_reasons=rejection_reasons,
        near_misses=near_misses,
        already_solved=already_solved,
        policy_rejections=policy_rejections,
    )


def rank_opportunities(eligible: list[FundedIssue]) -> list[FundedIssue]:
    """Rank by confirmed reward first, then by LESS competition as a
    tiebreaker (fewer demonstrably-competing PRs is strictly better at the
    same reward) -- competition is a visible, reported ranking factor, not
    a fake composite score. Effort/language are left as plain fields for a
    human (or me) to weigh directly.
    """
    policy_rank = {"allowed": 0, "conditional": 1, "unknown": 2}
    return sorted(
        eligible,
        key=lambda f: (
            policy_rank[f.contribution_policy.status],
            -f.amount_usd,
            f.competition.total_strong,
        ),
    )


def search_legit_funded_issues(
    *,
    queries: tuple[str, ...] = DISCOVERY_QUERIES,
    limit: int = PAID_BOUNTY_FETCH_LIMIT,
) -> list[FundedIssue]:
    """Backward-compatible thin wrapper around `scan_funded_issues`."""
    return scan_funded_issues(queries=queries, per_page=limit, pages=1).eligible
