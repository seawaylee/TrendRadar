import unittest

from trendradar.__main__ import NewsAnalyzer


class BrowserBehaviorTests(unittest.TestCase):
    def test_should_not_open_browser_by_default(self):
        analyzer = NewsAnalyzer.__new__(NewsAnalyzer)
        analyzer.is_github_actions = False
        analyzer.is_docker_container = False
        analyzer.ctx = type("Ctx", (), {"config": {"OPEN_BROWSER": False}})()

        self.assertFalse(analyzer._should_open_browser())

    def test_should_open_browser_only_when_explicitly_enabled(self):
        analyzer = NewsAnalyzer.__new__(NewsAnalyzer)
        analyzer.is_github_actions = False
        analyzer.is_docker_container = False
        analyzer.ctx = type("Ctx", (), {"config": {"OPEN_BROWSER": True}})()

        self.assertTrue(analyzer._should_open_browser())


if __name__ == "__main__":
    unittest.main()
