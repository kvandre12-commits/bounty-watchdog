from __future__ import annotations

import unittest
from unittest.mock import patch

from bounty_watchdog.contribution_policy import (
    classify_policy_text,
    inspect_contribution_policy,
)


class ClassifyPolicyTextTests(unittest.TestCase):
    def test_prohibited_policy(self) -> None:
        policy = classify_policy_text(
            "I don't accept fully AI-generated PRs.",
            "https://example.test/comment",
        )
        self.assertEqual(policy.status, "prohibited")
        self.assertEqual(policy.source_url, "https://example.test/comment")

    def test_conditional_policy(self) -> None:
        policy = classify_policy_text(
            "AI-assisted contributions are allowed if you disclose their use.",
            "https://example.test/contributing",
        )
        self.assertEqual(policy.status, "conditional")

    def test_allowed_policy(self) -> None:
        policy = classify_policy_text(
            "AI-assisted contributions are welcome.",
            "https://example.test/contributing",
        )
        self.assertEqual(policy.status, "allowed")

    def test_unrelated_ai_text_is_not_a_policy(self) -> None:
        self.assertIsNone(
            classify_policy_text(
                "This project provides an AI-generated image API.",
                "https://example.test/readme",
            )
        )


class InspectContributionPolicyTests(unittest.TestCase):
    @patch("bounty_watchdog.contribution_policy.list_recent_repo_comments")
    @patch("bounty_watchdog.contribution_policy.get_repo_file")
    def test_prohibition_beats_permission(self, get_file, comments) -> None:
        comments.return_value = [
            {
                "author_association": "OWNER",
                "html_url": "https://example.test/prohibition",
                "body": "AI-generated pull requests are not accepted.",
            }
        ]
        get_file.side_effect = lambda _repo, path: (
            ("AI-assisted contributions are welcome.", f"https://example.test/{path}")
            if path == "CONTRIBUTING.md"
            else None
        )

        policy = inspect_contribution_policy("example/project")

        self.assertEqual(policy.status, "prohibited")
        self.assertEqual(policy.source_url, "https://example.test/prohibition")

    @patch("bounty_watchdog.contribution_policy.list_recent_repo_comments", return_value=[])
    @patch("bounty_watchdog.contribution_policy.get_repo_file")
    def test_unknown_records_checked_guidance_source(self, get_file, _comments) -> None:
        get_file.side_effect = lambda _repo, path: (
            ("Normal contribution instructions.", "https://example.test/contributing")
            if path == "CONTRIBUTING.md"
            else None
        )

        policy = inspect_contribution_policy("example/project")

        self.assertEqual(policy.status, "unknown")
        self.assertEqual(policy.source_url, "https://example.test/contributing")

    @patch("bounty_watchdog.contribution_policy.list_recent_repo_comments")
    @patch("bounty_watchdog.contribution_policy.get_repo_file")
    def test_known_awesome_lint_prohibition_is_preserved_without_network(
        self, get_file, comments
    ) -> None:
        policy = inspect_contribution_policy("sindresorhus/awesome-lint")

        self.assertEqual(policy.status, "prohibited")
        self.assertIn("issuecomment-4432977488", policy.source_url)
        self.assertEqual(policy.excerpt, "I don't accept fully AI-generated PRs.")
        get_file.assert_not_called()
        comments.assert_not_called()

    @patch("bounty_watchdog.contribution_policy.list_recent_repo_comments")
    @patch("bounty_watchdog.contribution_policy.get_repo_file")
    def test_gbdev_open_rfc_is_unknown_not_allowed(self, get_file, comments) -> None:
        policy = inspect_contribution_policy("gbdev/gbdev.github.io")

        self.assertEqual(policy.status, "unknown")
        self.assertEqual(policy.source_url, "https://github.com/gbdev/gbdev.github.io/issues/103")
        self.assertIn("rejects all levels", policy.excerpt)
        get_file.assert_not_called()
        comments.assert_not_called()

    @patch("bounty_watchdog.contribution_policy.list_recent_repo_comments")
    @patch("bounty_watchdog.contribution_policy.get_repo_file", return_value=None)
    def test_other_projects_policy_mentioned_by_maintainer_is_not_ours(
        self, _files, comments
    ) -> None:
        comments.return_value = [
            {
                "author_association": "MEMBER",
                "html_url": "https://example.test/discussion",
                "body": "CPython has accepted LLM-generated contributions after version 3.14.",
            }
        ]

        policy = inspect_contribution_policy("example/project")

        self.assertEqual(policy.status, "unknown")

    @patch("bounty_watchdog.contribution_policy.list_recent_repo_comments")
    @patch("bounty_watchdog.contribution_policy.get_repo_file", return_value=None)
    def test_non_maintainer_comment_cannot_set_policy(self, _files, comments) -> None:
        comments.return_value = [
            {
                "author_association": "NONE",
                "html_url": "https://example.test/random-comment",
                "body": "AI-generated contributions are forbidden.",
            }
        ]

        policy = inspect_contribution_policy("example/project")

        self.assertEqual(policy.status, "unknown")
