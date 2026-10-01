from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

from bounty_watchdog import github_client as gc


def _fake_run(returncode: int, stdout: str = "", stderr: str = "") -> MagicMock:
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


class ListIssuesTests(unittest.TestCase):
    @patch("bounty_watchdog.github_client._run")
    def test_returns_parsed_list_on_success(self, run) -> None:
        payload = [{"number": 1, "title": "t", "url": "https://x/1"}]
        run.return_value = _fake_run(0, stdout=json.dumps(payload))
        result = gc.list_issues("o/r", ("bug",))
        self.assertEqual(result, payload)

    @patch("bounty_watchdog.github_client._run")
    def test_returns_empty_on_nonzero_exit(self, run) -> None:
        run.return_value = _fake_run(1, stderr="not found")
        self.assertEqual(gc.list_issues("o/r"), [])

    @patch("bounty_watchdog.github_client._run")
    def test_returns_empty_when_gh_missing(self, run) -> None:
        run.return_value = None
        self.assertEqual(gc.list_issues("o/r"), [])

    @patch("bounty_watchdog.github_client._run")
    def test_returns_empty_on_bad_json(self, run) -> None:
        run.return_value = _fake_run(0, stdout="not json")
        self.assertEqual(gc.list_issues("o/r"), [])


class DraftPrTests(unittest.TestCase):
    @patch("bounty_watchdog.github_client._run")
    def test_returns_url_on_success(self, run) -> None:
        run.return_value = _fake_run(0, stdout="https://github.com/o/r/pull/9\n")
        url = gc.create_draft_pr(
            __file__ and __import__("pathlib").Path("."), title="t", body="b"
        )
        self.assertEqual(url, "https://github.com/o/r/pull/9")

    @patch("bounty_watchdog.github_client._run")
    def test_returns_none_on_failure(self, run) -> None:
        run.return_value = _fake_run(1, stderr="boom")
        url = gc.create_draft_pr(__import__("pathlib").Path("."), title="t", body="b")
        self.assertIsNone(url)


class SearchFundedIssuesTests(unittest.TestCase):
    @patch("bounty_watchdog.github_client._run")
    def test_returns_items_on_success(self, run) -> None:
        payload = {"items": [{"number": 1, "html_url": "https://x/1"}]}
        run.return_value = _fake_run(0, stdout=json.dumps(payload))
        result = gc.search_funded_issues('label:"a"')
        self.assertEqual(result, payload["items"])

    @patch("bounty_watchdog.github_client._run")
    def test_returns_empty_on_failure(self, run) -> None:
        run.return_value = _fake_run(1, stderr="rate limited")
        self.assertEqual(gc.search_funded_issues('label:"a"'), [])

    @patch("bounty_watchdog.github_client._run")
    def test_returns_empty_on_missing_items_key(self, run) -> None:
        run.return_value = _fake_run(0, stdout=json.dumps({"total_count": 0}))
        self.assertEqual(gc.search_funded_issues('label:"a"'), [])

    @patch("bounty_watchdog.github_client._run")
    def test_exclude_repos_builds_negative_repo_qualifiers(self, run) -> None:
        run.return_value = _fake_run(0, stdout=json.dumps({"items": []}))
        gc.search_funded_issues('label:"a"', exclude_repos=("bad/repo1", "bad/repo2"))
        query_arg = run.call_args.args[0][2]  # ["gh", "api", "search/issues?q=..."]
        self.assertIn("-repo%3Abad%2Frepo1", query_arg)
        self.assertIn("-repo%3Abad%2Frepo2", query_arg)

    @patch("bounty_watchdog.github_client._run")
    def test_page_param_is_included_in_query(self, run) -> None:
        run.return_value = _fake_run(0, stdout=json.dumps({"items": []}))
        gc.search_funded_issues('label:"a"', page=3)
        query_arg = run.call_args.args[0][2]
        self.assertIn("page=3", query_arg)


class GetRepoMetaTests(unittest.TestCase):
    @patch("bounty_watchdog.github_client._run")
    def test_returns_parsed_meta_on_success(self, run) -> None:
        payload = {
            "fork": False,
            "stargazers_count": 42,
            "owner": {"type": "Organization", "login": "realcorp"},
            "pushed_at": "2026-01-01T00:00:00Z",
            "language": "Python",
        }
        run.return_value = _fake_run(0, stdout=json.dumps(payload))
        meta = gc.get_repo_meta("o/r")
        expected = {
            "fork": False,
            "stars": 42,
            "owner_type": "Organization",
            "owner_login": "realcorp",
            "pushed_at": "2026-01-01T00:00:00Z",
            "language": "Python",
        }
        self.assertEqual(meta, expected)

    @patch("bounty_watchdog.github_client._run")
    def test_returns_none_on_failure(self, run) -> None:
        run.return_value = _fake_run(1, stderr="not found")
        self.assertIsNone(gc.get_repo_meta("o/r"))

    @patch("bounty_watchdog.github_client._run")
    def test_returns_none_when_gh_missing(self, run) -> None:
        run.return_value = None
        self.assertIsNone(gc.get_repo_meta("o/r"))


class ListIssueCommentsTests(unittest.TestCase):
    @patch("bounty_watchdog.github_client._run")
    def test_returns_parsed_list_on_success(self, run) -> None:
        payload = [{"body": "hello"}]
        run.return_value = _fake_run(0, stdout=json.dumps(payload))
        self.assertEqual(gc.list_issue_comments("o/r", 1), payload)

    @patch("bounty_watchdog.github_client._run")
    def test_flattens_all_paginated_comment_pages(self, run) -> None:
        pages = [[{"body": "first page"}], [{"body": "pause on second page"}]]
        run.return_value = _fake_run(0, stdout=json.dumps(pages))

        self.assertEqual(
            gc.list_issue_comments("o/r", 1),
            [{"body": "first page"}, {"body": "pause on second page"}],
        )
        self.assertIn("--paginate", run.call_args.args[0])
        self.assertIn("--slurp", run.call_args.args[0])

    @patch("bounty_watchdog.github_client._run")
    def test_returns_empty_on_failure(self, run) -> None:
        run.return_value = _fake_run(1, stderr="not found")
        self.assertEqual(gc.list_issue_comments("o/r", 1), [])

    @patch("bounty_watchdog.github_client._run")
    def test_returns_empty_when_gh_missing(self, run) -> None:
        run.return_value = None
        self.assertEqual(gc.list_issue_comments("o/r", 1), [])


class ListReferencingPrsTests(unittest.TestCase):
    @patch("bounty_watchdog.github_client._run")
    def test_returns_items_on_success(self, run) -> None:
        payload = {"items": [{"number": 5, "state": "open", "title": "fix #1"}]}
        run.return_value = _fake_run(0, stdout=json.dumps(payload))
        self.assertEqual(gc.list_referencing_prs("o/r", 1), payload["items"])

    @patch("bounty_watchdog.github_client._run")
    def test_returns_empty_on_failure(self, run) -> None:
        run.return_value = _fake_run(1, stderr="rate limited")
        self.assertEqual(gc.list_referencing_prs("o/r", 1), [])

    @patch("bounty_watchdog.github_client._run")
    def test_returns_empty_when_gh_missing(self, run) -> None:
        run.return_value = None
        self.assertEqual(gc.list_referencing_prs("o/r", 1), [])


class ListIssueTimelinePrsTests(unittest.TestCase):
    @patch("bounty_watchdog.github_client._run")
    def test_returns_only_cross_referenced_prs(self, run) -> None:
        pr = {"number": 56, "pull_request": {"html_url": "https://x/pull/56"}}
        payload = [
            {"event": "cross-referenced", "source": {"issue": pr}},
            {"event": "mentioned", "source": {"issue": pr}},
            {"event": "cross-referenced", "source": {"issue": {"number": 99}}},
        ]
        run.return_value = _fake_run(0, stdout=json.dumps(payload))
        self.assertEqual(gc.list_issue_timeline_prs("o/r", 55), [pr])

    @patch("bounty_watchdog.github_client._run")
    def test_returns_empty_on_failure(self, run) -> None:
        run.return_value = _fake_run(1, stderr="rate limited")
        self.assertEqual(gc.list_issue_timeline_prs("o/r", 55), [])


class SearchSemanticPrsTests(unittest.TestCase):
    @patch("bounty_watchdog.github_client._run")
    def test_searches_exact_title_phrase(self, run) -> None:
        payload = {
            "items": [{"number": 7, "title": "Jira Service Management Connector"}]
        }
        run.return_value = _fake_run(0, stdout=json.dumps(payload))
        self.assertEqual(
            gc.search_semantic_prs("o/r", "Jira Service Management Connector"),
            payload["items"],
        )
        endpoint = run.call_args.args[0][2]
        self.assertIn("%22Jira+Service+Management+Connector%22", endpoint)
        self.assertIn("in%3Atitle%2Cbody", endpoint)

    @patch("bounty_watchdog.github_client._run")
    def test_empty_title_avoids_api_call(self, run) -> None:
        self.assertEqual(gc.search_semantic_prs("o/r", "  "), [])
        run.assert_not_called()


class GetPrMergedTests(unittest.TestCase):
    @patch("bounty_watchdog.github_client._run")
    def test_returns_true_when_merged(self, run) -> None:
        run.return_value = _fake_run(0, stdout=json.dumps({"merged": True}))
        self.assertTrue(gc.get_pr_merged("o/r", 5))

    @patch("bounty_watchdog.github_client._run")
    def test_returns_false_when_closed_unmerged(self, run) -> None:
        run.return_value = _fake_run(0, stdout=json.dumps({"merged": False}))
        self.assertFalse(gc.get_pr_merged("o/r", 5))

    @patch("bounty_watchdog.github_client._run")
    def test_returns_none_on_failure(self, run) -> None:
        run.return_value = _fake_run(1, stderr="not found")
        self.assertIsNone(gc.get_pr_merged("o/r", 5))

    @patch("bounty_watchdog.github_client._run")
    def test_returns_none_when_gh_missing(self, run) -> None:
        run.return_value = None
        self.assertIsNone(gc.get_pr_merged("o/r", 5))


class MarkReadyTests(unittest.TestCase):
    @patch("bounty_watchdog.github_client._run")
    def test_true_on_success(self, run) -> None:
        run.return_value = _fake_run(0)
        self.assertTrue(gc.mark_pr_ready("https://x/pr/9"))

    @patch("bounty_watchdog.github_client._run")
    def test_false_when_gh_missing(self, run) -> None:
        run.return_value = None
        self.assertFalse(gc.mark_pr_ready("https://x/pr/9"))


if __name__ == "__main__":
    unittest.main()
