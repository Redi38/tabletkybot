"""
Tests for admin/logs_viewer.py: the tail/filter helpers and both API
endpoints (/api/admin/logs, /api/admin/logs/download). Uses real
temporary files on disk — LOG_FILES is monkeypatched to point at them
rather than mocking file I/O itself.
"""

from unittest.mock import patch

from starlette.testclient import TestClient

from admin.app import app
from admin.logs_viewer import _log_line_matches, _tail_lines


def _write_log(path, lines: list[str]):
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class TestTailLines:
    def test_returns_empty_list_for_missing_file(self, tmp_path):
        result = _tail_lines(str(tmp_path / "does_not_exist.log"), max_lines=10)
        assert result == []

    def test_returns_all_lines_when_file_has_fewer_than_max(self, tmp_path):
        log_file = tmp_path / "test.log"
        _write_log(log_file, ["line1", "line2", "line3"])

        result = _tail_lines(str(log_file), max_lines=10)

        assert result == ["line1", "line2", "line3"]

    def test_returns_only_the_last_n_lines(self, tmp_path):
        log_file = tmp_path / "test.log"
        _write_log(log_file, [f"line{i}" for i in range(100)])

        result = _tail_lines(str(log_file), max_lines=5)

        assert result == ["line95", "line96", "line97", "line98", "line99"]

    def test_handles_a_large_file_spanning_multiple_chunks(self, tmp_path):
        log_file = tmp_path / "big.log"
        # Force multiple chunk reads with a tiny chunk_size
        _write_log(log_file, [f"entry-{i}" for i in range(50)])

        result = _tail_lines(str(log_file), max_lines=3, chunk_size=64)

        assert result == ["entry-47", "entry-48", "entry-49"]


class TestLogLineMatches:
    def test_matches_when_no_filters_given(self):
        assert _log_line_matches("2026-07-19 | INFO | something happened") is True

    def test_filters_by_level(self):
        line = "2026-07-19 | WARNING | disk space low"
        assert _log_line_matches(line, level="WARNING") is True
        assert _log_line_matches(line, level="ERROR") is False

    def test_level_filter_is_case_insensitive_on_input(self):
        line = "2026-07-19 | ERROR | crashed"
        assert _log_line_matches(line, level="error") is True

    def test_filters_by_search_text_case_insensitively(self):
        line = "2026-07-19 | INFO | User 12345 pressed med_archive_ask_5"
        assert _log_line_matches(line, search="ARCHIVE") is True
        assert _log_line_matches(line, search="delete") is False

    def test_combines_level_and_search_with_and_semantics(self):
        line = "2026-07-19 | INFO | User pressed archive"
        assert _log_line_matches(line, level="INFO", search="archive") is True
        assert _log_line_matches(line, level="ERROR", search="archive") is False
        assert _log_line_matches(line, level="INFO", search="delete") is False


class TestGetAdminLogsEndpoint:
    def test_returns_error_for_invalid_source(self):
        client = TestClient(app)
        response = client.get("/api/admin/logs?source=nonexistent")
        assert response.json() == {"error": "invalid source", "lines": []}

    def test_returns_recent_lines_for_bot_source(self, tmp_path):
        log_file = tmp_path / "bot.log"
        _write_log(log_file, ["line1", "line2", "line3"])

        client = TestClient(app)
        with patch("admin.logs_viewer.LOG_FILES", {"bot": str(log_file), "admin": str(tmp_path / "admin.log")}):
            response = client.get("/api/admin/logs?source=bot")

        assert response.status_code == 200
        data = response.json()
        assert data["source"] == "bot"
        assert data["lines"] == ["line1", "line2", "line3"]

    def test_filters_by_level_via_query_param(self, tmp_path):
        log_file = tmp_path / "bot.log"
        _write_log(
            log_file,
            [
                "2026-07-19 | INFO | normal stuff",
                "2026-07-19 | ERROR | something broke",
                "2026-07-19 | INFO | more normal stuff",
            ],
        )

        client = TestClient(app)
        with patch("admin.logs_viewer.LOG_FILES", {"bot": str(log_file), "admin": str(tmp_path / "admin.log")}):
            response = client.get("/api/admin/logs?source=bot&level=ERROR")

        data = response.json()
        assert len(data["lines"]) == 1
        assert "something broke" in data["lines"][0]

    def test_caps_requested_lines_at_max(self, tmp_path):
        log_file = tmp_path / "bot.log"
        _write_log(log_file, [f"line{i}" for i in range(2000)])

        client = TestClient(app)
        with patch("admin.logs_viewer.LOG_FILES", {"bot": str(log_file), "admin": str(tmp_path / "admin.log")}):
            response = client.get("/api/admin/logs?source=bot&lines=5000")

        assert len(response.json()["lines"]) == 1000  # _MAX_LINES


class TestDownloadLogsEndpoint:
    def test_returns_400_for_invalid_source(self):
        client = TestClient(app)
        response = client.get("/api/admin/logs/download?source=nonexistent")
        assert response.status_code == 400

    def test_returns_404_when_file_does_not_exist(self, tmp_path):
        client = TestClient(app)
        with patch(
            "admin.logs_viewer.LOG_FILES",
            {"bot": str(tmp_path / "missing.log"), "admin": str(tmp_path / "admin.log")},
        ):
            response = client.get("/api/admin/logs/download?source=bot")
        assert response.status_code == 404

    def test_downloads_full_file_content(self, tmp_path):
        log_file = tmp_path / "bot.log"
        _write_log(log_file, ["line1", "line2", "line3"])

        client = TestClient(app)
        with patch("admin.logs_viewer.LOG_FILES", {"bot": str(log_file), "admin": str(tmp_path / "admin.log")}):
            response = client.get("/api/admin/logs/download?source=bot")

        assert response.status_code == 200
        assert "line1" in response.text
        assert "line2" in response.text
        assert "line3" in response.text

    def test_download_respects_filters(self, tmp_path):
        log_file = tmp_path / "bot.log"
        _write_log(
            log_file,
            [
                "2026-07-19 | INFO | normal",
                "2026-07-19 | ERROR | broken",
            ],
        )

        client = TestClient(app)
        with patch("admin.logs_viewer.LOG_FILES", {"bot": str(log_file), "admin": str(tmp_path / "admin.log")}):
            response = client.get("/api/admin/logs/download?source=bot&level=ERROR")

        assert "broken" in response.text
        assert "normal" not in response.text

    def test_filename_includes_source_and_level(self, tmp_path):
        log_file = tmp_path / "bot.log"
        _write_log(log_file, ["line1"])

        client = TestClient(app)
        with patch("admin.logs_viewer.LOG_FILES", {"bot": str(log_file), "admin": str(tmp_path / "admin.log")}):
            response = client.get("/api/admin/logs/download?source=bot&level=error")

        disposition = response.headers["content-disposition"]
        assert "logs_bot_error_" in disposition


class TestLogsView:
    def test_page_requires_login(self):
        client = TestClient(app, follow_redirects=False)
        response = client.get("/admin/logs-view")
        assert response.status_code in (302, 307)
