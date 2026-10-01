from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from bounty_watchdog.contribution_policy import UNKNOWN_POLICY, ContributionPolicy
from bounty_watchdog.paid_bounties import (
    CompetitionEvidence,
    FundedIssue,
    rank_opportunities,
    scan_funded_issues,
)

_NOW = datetime.now(UTC)
_FRESH_PUSH = (_NOW - timedelta(days=5)).isoformat().replace("+00:00", "Z")
_STALE_PUSH = (_NOW - timedelta(days=400)).isoformat().replace("+00:00", "Z")
_FRESH_TERMS_AT = (_NOW - timedelta(days=5)).isoformat().replace("+00:00", "Z")
_STALE_TERMS_AT = (_NOW - timedelta(days=400)).isoformat().replace("+00:00", "Z")
_ONE_QUERY = ("test-query",)  # pins every test to a single discovery query

_ORG_META = {
    "fork": False,
    "stars": 50,
    "owner_type": "Organization",
    "owner_login": "realcorp",
    "pushed_at": _FRESH_PUSH,
    "language": "Python",
}
_BOT_TERMS = {
    "user": {"login": "algora-pbc[bot]"},
    "body": "$250 bounty -- steps to solve...",
    "created_at": _FRESH_TERMS_AT,
}
_OWNER_TERMS = {
    "user": {"login": "realcorp"},
    "body": "I'll pay $300 for this fix.",
    "created_at": _FRESH_TERMS_AT,
}
_NO_COMPETITION = CompetitionEvidence(0, 0, False, 0, (), 0, 0)


def _issue(
    repo: str, number: int, assignees: list | None = None, body: str = ""
) -> dict:
    return {
        "number": number,
        "title": f"issue {number}",
        "html_url": f"https://github.com/{repo}/issues/{number}",
        "repository_url": f"https://api.github.com/repos/{repo}",
        "labels": [],
        "assignees": assignees or [],
        "comments": 2,
        "body": body,
    }


def _pr(number: int, *, state: str = "open", title: str = "", body: str = "") -> dict:
    return {"number": number, "state": state, "title": title, "body": body}


def _scan(**kwargs):
    kwargs.setdefault("queries", _ONE_QUERY)
    return scan_funded_issues(**kwargs)


class ScanFundedIssuesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy_patcher = patch(
            "bounty_watchdog.paid_bounties.inspect_contribution_policy",
            return_value=UNKNOWN_POLICY,
        )
        self.timeline_patcher = patch(
            "bounty_watchdog.paid_bounties.list_issue_timeline_prs",
            return_value=[],
        )
        self.semantic_patcher = patch(
            "bounty_watchdog.paid_bounties.search_semantic_prs",
            return_value=[],
        )
        self.inspect_policy = self.policy_patcher.start()
        self.timeline_prs = self.timeline_patcher.start()
        self.semantic_prs = self.semantic_patcher.start()
        self.addCleanup(self.policy_patcher.stop)
        self.addCleanup(self.timeline_patcher.stop)
        self.addCleanup(self.semantic_patcher.stop)

    @patch("bounty_watchdog.paid_bounties.list_referencing_prs", return_value=[])
    @patch(
        "bounty_watchdog.paid_bounties.list_issue_comments", return_value=[_BOT_TERMS]
    )
    @patch("bounty_watchdog.paid_bounties.get_repo_meta")
    @patch("bounty_watchdog.paid_bounties.search_funded_issues")
    def test_fully_eligible_issue_via_algora_bot(
        self, search, get_meta, _comments, _prs
    ) -> None:
        search.side_effect = [[_issue("realcorp/product", 1)], []]
        get_meta.return_value = _ORG_META
        report = _scan(pages=2)
        self.assertEqual(len(report.eligible), 1)
        self.assertEqual(report.eligible[0].amount_usd, 250.0)
        self.assertEqual(report.eligible[0].language, "Python")

    @patch("bounty_watchdog.paid_bounties.list_referencing_prs", return_value=[])
    @patch(
        "bounty_watchdog.paid_bounties.list_issue_comments", return_value=[_OWNER_TERMS]
    )
    @patch("bounty_watchdog.paid_bounties.get_repo_meta")
    @patch("bounty_watchdog.paid_bounties.search_funded_issues")
    def test_repo_owner_stated_terms_also_accepted(
        self, search, get_meta, _comments, _prs
    ) -> None:
        search.side_effect = [[_issue("realcorp/product", 1)], []]
        get_meta.return_value = _ORG_META
        report = _scan(pages=2)
        self.assertEqual(len(report.eligible), 1)
        self.assertEqual(report.eligible[0].amount_usd, 300.0)

    @patch("bounty_watchdog.paid_bounties.list_referencing_prs", return_value=[])
    @patch("bounty_watchdog.paid_bounties.list_issue_comments")
    @patch("bounty_watchdog.paid_bounties.get_repo_meta")
    @patch("bounty_watchdog.paid_bounties.search_funded_issues")
    def test_issuehunt_style_multi_funder_amounts_are_summed(
        self, search, get_meta, comments, _prs
    ) -> None:
        # Verified live on TriliumNext/Trilium#4956: five separate IssueHunt
        # funding comments ($2 + $50 + $500 + $50 + $20) totaling $622, not
        # any single one of those numbers.
        comments.return_value = [
            {
                "user": {"login": "issuehunt-oss[bot]"},
                "body": "@a has funded $2.00 to this issue.",
                "created_at": _FRESH_TERMS_AT,
            },
            {
                "user": {"login": "issuehunt-oss[bot]"},
                "body": "@b has funded $50.00 to this issue.",
                "created_at": _FRESH_TERMS_AT,
            },
            {
                "user": {"login": "issuehunt-oss[bot]"},
                "body": "@c has funded $500.00 to this issue.",
                "created_at": _FRESH_TERMS_AT,
            },
        ]
        search.side_effect = [[_issue("realcorp/product", 1)], []]
        get_meta.return_value = _ORG_META
        report = _scan(pages=2)
        self.assertEqual(len(report.eligible), 1)
        self.assertEqual(report.eligible[0].amount_usd, 552.0)

    @patch("bounty_watchdog.paid_bounties.list_issue_comments", return_value=[])
    @patch("bounty_watchdog.paid_bounties.get_repo_meta")
    @patch("bounty_watchdog.paid_bounties.search_funded_issues")
    def test_random_commenter_stating_amount_is_not_terms(
        self, search, get_meta, comments
    ) -> None:
        comments.return_value = [
            {"user": {"login": "randomuser"}, "body": "I think this is worth $300"}
        ]
        search.side_effect = [[_issue("realcorp/product", 1)], []]
        get_meta.return_value = _ORG_META
        report = _scan(pages=2)
        self.assertEqual(report.eligible, [])
        self.assertEqual(report.rejection_reasons["no_verifiable_terms"], 1)

    @patch("bounty_watchdog.paid_bounties.list_referencing_prs", return_value=[])
    @patch(
        "bounty_watchdog.paid_bounties.list_issue_comments", return_value=[_BOT_TERMS]
    )
    @patch("bounty_watchdog.paid_bounties.get_repo_meta")
    @patch("bounty_watchdog.paid_bounties.search_funded_issues")
    def test_fork_is_no_longer_excluded(
        self, search, get_meta, _comments, _prs
    ) -> None:
        search.side_effect = [[_issue("farmer/realcorp-product", 1)], []]
        get_meta.return_value = {**_ORG_META, "fork": True}
        report = _scan(pages=2)
        self.assertEqual(len(report.eligible), 1)
        self.assertTrue(report.eligible[0].is_fork)

    @patch("bounty_watchdog.paid_bounties.list_referencing_prs")
    @patch(
        "bounty_watchdog.paid_bounties.list_issue_comments", return_value=[_BOT_TERMS]
    )
    @patch("bounty_watchdog.paid_bounties.get_repo_meta", return_value=_ORG_META)
    @patch("bounty_watchdog.paid_bounties.search_funded_issues")
    def test_prohibited_ai_policy_rejects_before_competition(
        self, search, _meta, _comments, referencing_prs
    ) -> None:
        search.side_effect = [[_issue("realcorp/product", 1)], []]
        self.inspect_policy.return_value = ContributionPolicy(
            "prohibited",
            "https://github.com/realcorp/product/pull/2#issuecomment-3",
            "We do not accept AI-generated pull requests.",
        )

        report = _scan(pages=2)

        self.assertEqual(report.eligible, [])
        self.assertEqual(report.rejection_reasons["ai_contributions_prohibited"], 1)
        self.assertEqual(report.policy_rejections[0]["status"], "prohibited")
        self.assertIn("issuecomment-3", report.policy_rejections[0]["source"])
        referencing_prs.assert_not_called()

    # --- the real competition-handling tests ---

    @patch("bounty_watchdog.paid_bounties.list_referencing_prs")
    @patch(
        "bounty_watchdog.paid_bounties.list_issue_comments", return_value=[_BOT_TERMS]
    )
    @patch("bounty_watchdog.paid_bounties.get_repo_meta")
    @patch("bounty_watchdog.paid_bounties.search_funded_issues")
    def test_many_open_competing_prs_does_not_disqualify(
        self, search, get_meta, _comments, prs
    ) -> None:
        # Verified live on onyx-dot-app/onyx#2281: 77 referencing PRs, zero
        # merged. Crowded is not the same as unavailable.
        prs.return_value = [
            _pr(n, state="open", title=f"feat: fix #1 ({n})") for n in range(1, 30)
        ]
        search.side_effect = [[_issue("realcorp/product", 1)], []]
        get_meta.return_value = _ORG_META
        report = _scan(pages=2)
        self.assertEqual(len(report.eligible), 1)
        self.assertEqual(report.eligible[0].competition.strong_open, 29)

    @patch("bounty_watchdog.paid_bounties.list_referencing_prs", return_value=[])
    @patch(
        "bounty_watchdog.paid_bounties.list_issue_comments", return_value=[_BOT_TERMS]
    )
    @patch("bounty_watchdog.paid_bounties.get_repo_meta")
    @patch("bounty_watchdog.paid_bounties.search_funded_issues")
    def test_timeline_cross_references_reveal_studenthub_style_competition(
        self, search, get_meta, _comments, _references
    ) -> None:
        # Regression for BAWES-Universe/studenthub#55: ordinary issue-number
        # search found zero strong PRs, while the timeline linked 24.
        self.timeline_prs.return_value = [
            _pr(n, state="open", title=f"AWS hardening slice {n}")
            for n in range(56, 80)
        ]
        search.side_effect = [[_issue("BAWES-Universe/studenthub", 55)], []]
        get_meta.return_value = _ORG_META

        report = _scan(pages=2)

        self.assertEqual(len(report.eligible), 1)
        self.assertEqual(report.eligible[0].competition.strong_open, 24)
        self.assertEqual(report.eligible[0].competition.total_referencing, 24)

    @patch("bounty_watchdog.paid_bounties.list_referencing_prs")
    @patch(
        "bounty_watchdog.paid_bounties.list_issue_comments", return_value=[_BOT_TERMS]
    )
    @patch("bounty_watchdog.paid_bounties.get_repo_meta")
    @patch("bounty_watchdog.paid_bounties.search_funded_issues")
    def test_competition_channels_are_deduplicated(
        self, search, get_meta, _comments, references
    ) -> None:
        same_pr = _pr(7, state="open", title="Jira Service Management Connector")
        references.return_value = [same_pr]
        self.timeline_prs.return_value = [same_pr]
        self.semantic_prs.return_value = [same_pr]
        search.side_effect = [[_issue("realcorp/product", 1)], []]
        get_meta.return_value = _ORG_META

        report = _scan(pages=2)

        self.assertEqual(report.eligible[0].competition.strong_open, 1)
        self.assertEqual(report.eligible[0].competition.total_referencing, 1)

    @patch("bounty_watchdog.paid_bounties.get_pr_merged", return_value=True)
    @patch("bounty_watchdog.paid_bounties.list_referencing_prs")
    @patch(
        "bounty_watchdog.paid_bounties.list_issue_comments", return_value=[_BOT_TERMS]
    )
    @patch("bounty_watchdog.paid_bounties.get_repo_meta")
    @patch("bounty_watchdog.paid_bounties.search_funded_issues")
    def test_merged_competing_pr_is_already_solved(
        self, search, get_meta, _comments, prs, _merged
    ) -> None:
        prs.return_value = [_pr(5, state="closed", title="fix #1")]
        search.side_effect = [[_issue("realcorp/product", 1)], []]
        get_meta.return_value = _ORG_META
        report = _scan(pages=2)
        self.assertEqual(report.eligible, [])
        self.assertEqual(report.rejection_reasons["already_solved"], 1)
        self.assertEqual(len(report.already_solved), 1)

    @patch("bounty_watchdog.paid_bounties.get_pr_merged", return_value=False)
    @patch("bounty_watchdog.paid_bounties.list_referencing_prs")
    @patch(
        "bounty_watchdog.paid_bounties.list_issue_comments", return_value=[_BOT_TERMS]
    )
    @patch("bounty_watchdog.paid_bounties.get_repo_meta")
    @patch("bounty_watchdog.paid_bounties.search_funded_issues")
    def test_closed_but_unmerged_pr_does_not_disqualify(
        self, search, get_meta, _comments, prs, merged
    ) -> None:
        prs.return_value = [_pr(5, state="closed", title="fix #1")]
        search.side_effect = [[_issue("realcorp/product", 1)], []]
        get_meta.return_value = _ORG_META
        report = _scan(pages=2)
        self.assertEqual(len(report.eligible), 1)
        self.assertEqual(report.eligible[0].competition.strong_closed_unmerged, 1)
        merged.assert_called_once()

    @patch("bounty_watchdog.paid_bounties.list_referencing_prs")
    @patch(
        "bounty_watchdog.paid_bounties.list_issue_comments", return_value=[_BOT_TERMS]
    )
    @patch("bounty_watchdog.paid_bounties.get_repo_meta")
    @patch("bounty_watchdog.paid_bounties.search_funded_issues")
    def test_weak_mention_is_not_counted_as_competition(
        self, search, get_meta, _comments, prs
    ) -> None:
        prs.return_value = [
            _pr(
                5,
                state="open",
                title="unrelated PR",
                body="see also issue 1 for context",
            )
        ]
        search.side_effect = [[_issue("realcorp/product", 1)], []]
        get_meta.return_value = _ORG_META
        report = _scan(pages=2)
        self.assertEqual(len(report.eligible), 1)
        self.assertEqual(report.eligible[0].competition.strong_open, 0)
        self.assertEqual(report.eligible[0].competition.weak_mentions, 1)

    @patch("bounty_watchdog.paid_bounties.list_referencing_prs")
    @patch(
        "bounty_watchdog.paid_bounties.list_issue_comments", return_value=[_BOT_TERMS]
    )
    @patch("bounty_watchdog.paid_bounties.get_repo_meta")
    @patch("bounty_watchdog.paid_bounties.search_funded_issues")
    def test_qualified_closing_reference_counts_as_competition(
        self, search, get_meta, _comments, prs
    ) -> None:
        # Verified live on awesome-lint#37 / PR #225. Fork-authored PRs
        # often use GitHub's fully-qualified closing syntax; missing this
        # made a real open competitor invisible to ranking.
        prs.return_value = [
            _pr(
                225,
                state="open",
                body="Fixes sindresorhus/awesome-lint#37",
            )
        ]
        search.side_effect = [[_issue("sindresorhus/awesome-lint", 37)], []]
        get_meta.return_value = _ORG_META
        report = _scan(pages=2)
        self.assertEqual(len(report.eligible), 1)
        self.assertEqual(report.eligible[0].competition.strong_open, 1)
        self.assertEqual(report.eligible[0].competition.weak_mentions, 0)

    @patch("bounty_watchdog.paid_bounties.get_pr_merged")
    @patch("bounty_watchdog.paid_bounties.list_referencing_prs")
    @patch(
        "bounty_watchdog.paid_bounties.list_issue_comments", return_value=[_BOT_TERMS]
    )
    @patch("bounty_watchdog.paid_bounties.get_repo_meta")
    @patch("bounty_watchdog.paid_bounties.search_funded_issues")
    def test_merge_check_stops_at_first_confirmed_merge(
        self, search, get_meta, _comments, prs, merged
    ) -> None:
        prs.return_value = [
            _pr(n, state="closed", title=f"fix #1 ({n})") for n in range(1, 6)
        ]
        merged.side_effect = [False, True, False, False, False]
        search.side_effect = [[_issue("realcorp/product", 1)], []]
        get_meta.return_value = _ORG_META
        report = _scan(pages=2)
        self.assertEqual(report.eligible, [])
        self.assertEqual(merged.call_count, 2)

    @patch("bounty_watchdog.paid_bounties.list_referencing_prs", return_value=[])
    def test_empty_competition_lookup_is_not_a_penalty(self, _prs) -> None:
        from bounty_watchdog.paid_bounties import _classify_competing_prs

        evidence = _classify_competing_prs("o/r", 1)
        self.assertFalse(evidence.already_merged)
        self.assertEqual(evidence.total_strong, 0)

    # --- everything below unaffected by the competition rewrite ---

    @patch("bounty_watchdog.paid_bounties.list_issue_comments")
    @patch("bounty_watchdog.paid_bounties.get_repo_meta")
    @patch("bounty_watchdog.paid_bounties.search_funded_issues")
    def test_already_awarded_is_filtered(self, search, get_meta, comments) -> None:
        comments.return_value = [
            _BOT_TERMS,
            {
                "user": {"login": "algora-pbc[bot]"},
                "body": "@someone has been awarded **$250**!",
            },
        ]
        search.side_effect = [[_issue("realcorp/product", 1)], []]
        get_meta.return_value = _ORG_META
        report = _scan(pages=2)
        self.assertEqual(report.eligible, [])
        self.assertEqual(report.rejection_reasons["already_awarded"], 1)

    @patch("bounty_watchdog.paid_bounties.list_issue_comments")
    @patch("bounty_watchdog.paid_bounties.get_repo_meta", return_value=_ORG_META)
    @patch("bounty_watchdog.paid_bounties.search_funded_issues")
    def test_paused_bounty_requires_payment_reverification(
        self, search, _meta, comments
    ) -> None:
        comments.return_value = [
            _BOT_TERMS,
            {"user": {"login": "realcorp"}, "body": "Update: Bounty Paused"},
        ]
        search.side_effect = [[_issue("realcorp/product", 1)], []]

        report = _scan(pages=2)

        self.assertEqual(report.eligible, [])
        self.assertEqual(report.rejection_reasons["payment_reverify"], 1)

    @patch("bounty_watchdog.paid_bounties.list_issue_comments")
    @patch("bounty_watchdog.paid_bounties.get_repo_meta", return_value=_ORG_META)
    @patch("bounty_watchdog.paid_bounties.search_funded_issues")
    def test_merged_rewardable_notice_requires_payment_reverification(
        self, search, _meta, comments
    ) -> None:
        comments.return_value = [
            _BOT_TERMS,
            {
                "user": {"login": "algora-pbc[bot]"},
                "body": "The pull request of @winner has been merged. The bounty can be rewarded here.",
            },
        ]
        search.side_effect = [[_issue("realcorp/product", 1)], []]

        report = _scan(pages=2)

        self.assertEqual(report.eligible, [])
        self.assertEqual(report.rejection_reasons["payment_reverify"], 1)

    @patch("bounty_watchdog.paid_bounties.list_referencing_prs", return_value=[])
    @patch("bounty_watchdog.paid_bounties.list_issue_comments")
    @patch("bounty_watchdog.paid_bounties.get_repo_meta", return_value=_ORG_META)
    @patch("bounty_watchdog.paid_bounties.search_funded_issues")
    def test_non_terms_bot_chatter_does_not_stale_current_payment(
        self, search, _meta, comments, _references
    ) -> None:
        comments.return_value = [
            _BOT_TERMS,
            {
                "user": {"login": "algora-pbc[bot]"},
                "body": "Someone else is already attempting this issue.",
                "created_at": _STALE_TERMS_AT,
            },
        ]
        search.side_effect = [[_issue("realcorp/product", 1)], []]

        report = _scan(pages=2)

        self.assertEqual(len(report.eligible), 1)

    @patch("bounty_watchdog.paid_bounties.list_issue_comments")
    @patch("bounty_watchdog.paid_bounties.get_repo_meta", return_value=_ORG_META)
    @patch("bounty_watchdog.paid_bounties.search_funded_issues")
    def test_stale_terms_require_payment_reverification(
        self, search, _meta, comments
    ) -> None:
        comments.return_value = [{**_BOT_TERMS, "created_at": _STALE_TERMS_AT}]
        search.side_effect = [[_issue("realcorp/product", 1)], []]

        report = _scan(pages=2)

        self.assertEqual(report.eligible, [])
        self.assertEqual(report.rejection_reasons["payment_reverify"], 1)

    @patch("bounty_watchdog.paid_bounties.list_issue_comments")
    @patch("bounty_watchdog.paid_bounties.get_repo_meta", return_value=_ORG_META)
    @patch("bounty_watchdog.paid_bounties.search_funded_issues")
    def test_missing_terms_timestamp_requires_payment_reverification(
        self, search, _meta, comments
    ) -> None:
        comments.return_value = [
            {k: v for k, v in _BOT_TERMS.items() if k != "created_at"}
        ]
        search.side_effect = [[_issue("realcorp/product", 1)], []]

        report = _scan(pages=2)

        self.assertEqual(report.eligible, [])
        self.assertEqual(report.rejection_reasons["payment_reverify"], 1)

    @patch("bounty_watchdog.paid_bounties.get_repo_meta")
    @patch("bounty_watchdog.paid_bounties.search_funded_issues")
    def test_assigned_issue_is_claimed(self, search, get_meta) -> None:
        search.side_effect = [
            [_issue("realcorp/product", 1, assignees=[{"login": "x"}])],
            [],
        ]
        get_meta.return_value = _ORG_META
        report = _scan(pages=2)
        self.assertEqual(report.rejection_reasons["already_claimed"], 1)

    @patch("bounty_watchdog.paid_bounties.get_repo_meta")
    @patch("bounty_watchdog.paid_bounties.search_funded_issues")
    def test_stale_repo_fails_maintainer_activity(self, search, get_meta) -> None:
        search.side_effect = [[_issue("realcorp/product", 1)], []]
        get_meta.return_value = {**_ORG_META, "pushed_at": _STALE_PUSH}
        report = _scan(pages=2)
        self.assertEqual(report.rejection_reasons["maintainer_inactive"], 1)

    @patch("bounty_watchdog.paid_bounties.get_repo_meta")
    @patch("bounty_watchdog.paid_bounties.search_funded_issues")
    def test_missing_pushed_at_fails_closed(self, search, get_meta) -> None:
        search.side_effect = [[_issue("realcorp/product", 1)], []]
        get_meta.return_value = {**_ORG_META, "pushed_at": None}
        report = _scan(pages=2)
        self.assertEqual(report.rejection_reasons["maintainer_inactive"], 1)

    @patch(
        "bounty_watchdog.paid_bounties.list_issue_comments", return_value=[_BOT_TERMS]
    )
    @patch("bounty_watchdog.paid_bounties.get_repo_meta")
    @patch("bounty_watchdog.paid_bounties.search_funded_issues")
    def test_below_reward_floor_is_a_near_miss_not_eligible(
        self, search, get_meta, _c
    ) -> None:
        search.side_effect = [[_issue("realcorp/product", 1)], []]
        get_meta.return_value = _ORG_META
        with patch(
            "bounty_watchdog.paid_bounties.list_issue_comments",
            return_value=[
                {
                    "user": {"login": "algora-pbc[bot]"},
                    "body": "$5 bounty",
                    "created_at": _FRESH_TERMS_AT,
                }
            ],
        ):
            report = _scan(pages=2)
        self.assertEqual(report.eligible, [])
        self.assertEqual(len(report.near_misses), 1)
        self.assertEqual(report.near_misses[0]["amount"], 5.0)

    @patch("bounty_watchdog.paid_bounties.get_repo_meta")
    @patch("bounty_watchdog.paid_bounties.search_funded_issues")
    def test_unverifiable_repo_fails_closed(self, search, get_meta) -> None:
        search.side_effect = [[_issue("ghost/repo", 1)], []]
        get_meta.return_value = None
        report = _scan(pages=2)
        self.assertEqual(report.rejection_reasons["repo_unverifiable"], 1)

    @patch("bounty_watchdog.paid_bounties.get_repo_meta")
    @patch("bounty_watchdog.paid_bounties.search_funded_issues")
    def test_denylisted_repo_rejected_before_any_api_calls(
        self, search, get_meta
    ) -> None:
        search.side_effect = [[_issue("SecureBananaLabs/bug-bounty", 1)], []]
        report = _scan(pages=2)
        self.assertEqual(report.rejection_reasons["denylisted"], 1)
        get_meta.assert_not_called()

    @patch("bounty_watchdog.paid_bounties.list_referencing_prs", return_value=[])
    @patch(
        "bounty_watchdog.paid_bounties.list_issue_comments", return_value=[_BOT_TERMS]
    )
    @patch("bounty_watchdog.paid_bounties.get_repo_meta")
    @patch("bounty_watchdog.paid_bounties.search_funded_issues")
    def test_per_repo_cap_stops_one_repo_consuming_budget(
        self, search, get_meta, _c, _prs
    ) -> None:
        flood = [_issue("noisy/repo", n) for n in range(1, 11)]
        search.side_effect = [flood, []]
        get_meta.return_value = _ORG_META
        report = _scan(pages=2, per_repo_cap=3)
        self.assertEqual(report.evaluated, 3)
        self.assertEqual(report.repo_cap_skips, 7)
        self.assertEqual(len(report.eligible), 3)

    @patch("bounty_watchdog.paid_bounties.list_referencing_prs", return_value=[])
    @patch(
        "bounty_watchdog.paid_bounties.list_issue_comments", return_value=[_BOT_TERMS]
    )
    @patch("bounty_watchdog.paid_bounties.get_repo_meta")
    @patch("bounty_watchdog.paid_bounties.search_funded_issues")
    def test_multi_page_results_are_merged(self, search, get_meta, _c, _prs) -> None:
        search.side_effect = [[_issue("repo/a", 1)], [_issue("repo/b", 2)], []]
        get_meta.return_value = _ORG_META
        report = _scan(pages=3)
        self.assertEqual(report.total_fetched, 2)
        self.assertEqual(len(report.eligible), 2)

    @patch("bounty_watchdog.paid_bounties.search_funded_issues")
    def test_empty_page_stops_further_paging(self, search) -> None:
        search.side_effect = [[], [_issue("should/not/be/fetched", 1)]]
        report = _scan(pages=3)
        self.assertEqual(report.total_fetched, 0)
        search.assert_called_once()

    @patch("bounty_watchdog.paid_bounties.search_funded_issues")
    def test_two_queries_both_run(self, search) -> None:
        search.side_effect = [[], []]
        report = _scan(queries=("query-a", "query-b"), pages=1)
        self.assertEqual(report.total_fetched, 0)
        self.assertEqual(search.call_count, 2)
        called_queries = [c.args[0] for c in search.call_args_list]
        self.assertEqual(called_queries, ["query-a", "query-b"])

    @patch(
        "bounty_watchdog.paid_bounties.list_issue_comments", return_value=[_BOT_TERMS]
    )
    @patch("bounty_watchdog.paid_bounties.get_repo_meta")
    @patch("bounty_watchdog.paid_bounties.search_funded_issues")
    def test_cache_hit_skips_re_evaluation(self, search, get_meta, _comments) -> None:
        search.side_effect = [[_issue("realcorp/product", 1)], []]
        get_meta.return_value = _ORG_META

        class _FakeCache:
            def get_fresh(self, url, logic_version=None):
                class _Cached:
                    outcome = "already_awarded"

                return _Cached()

            def record(self, url, outcome, logic_version=None, detail=None):
                pass

        report = _scan(pages=2, cache=_FakeCache())
        self.assertEqual(report.cache_hits, 1)
        self.assertEqual(report.evaluated, 0)
        get_meta.assert_not_called()
        self.assertEqual(report.rejection_reasons["already_awarded"], 1)

    def test_rank_opportunities_sorts_by_amount_then_competition(self) -> None:
        lots_of_competition = CompetitionEvidence(20, 0, False, 0, (), 0, 0)
        low = FundedIssue(
            repo="a/b",
            number=1,
            title="t",
            url="u1",
            amount_usd=50.0,
            stars=1,
            is_fork=False,
            language="Python",
            comment_count=0,
            body_length=0,
            competition=_NO_COMPETITION,
        )
        high_crowded = FundedIssue(
            repo="a/b",
            number=2,
            title="t",
            url="u2",
            amount_usd=500.0,
            stars=1,
            is_fork=False,
            language="Python",
            comment_count=0,
            body_length=0,
            competition=lots_of_competition,
        )
        high_clean = FundedIssue(
            repo="a/b",
            number=3,
            title="t",
            url="u3",
            amount_usd=500.0,
            stars=1,
            is_fork=False,
            language="Python",
            comment_count=0,
            body_length=0,
            competition=_NO_COMPETITION,
        )
        ranked = rank_opportunities([low, high_crowded, high_clean])
        self.assertEqual([f.url for f in ranked], ["u3", "u2", "u1"])


if __name__ == "__main__":
    unittest.main()
