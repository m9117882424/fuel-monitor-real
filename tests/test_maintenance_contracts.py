"""Offline maintenance contracts; no network or production DB is permitted."""
from __future__ import annotations

import io
import os
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["TELEGRAM_ENABLED"] = "false"
os.environ["SCHEDULER_ENABLED"] = "false"

import requests

import cli_sync
import telegram_limit_bot as bot
from app.services import sync_service as sync

TEST_TOKEN = "123456789:" + "A" * 35
TEST_URL = "https://api.telegram.org/bot" + TEST_TOKEN + "/getUpdates?offset=1"


class OfflineContract(unittest.TestCase):
    def setUp(self):
        for target in (
            "socket.create_connection",
            "socket.socket.connect",
            "sqlalchemy.engine.Engine.connect",
        ):
            guard = patch(target, side_effect=AssertionError("External I/O forbidden in offline tests"))
            guard.start()
            self.addCleanup(guard.stop)


class SyncExitContract(OfflineContract):
    def run_cli(self, results):
        db = Mock()
        stdout = io.StringIO()
        with patch.object(cli_sync.Base.metadata, "create_all"), \
             patch.object(cli_sync, "SessionLocal", return_value=db), \
             patch.object(cli_sync, "sync_all", return_value=(results, "report.xlsx")) as run, \
             redirect_stdout(stdout):
            status = cli_sync.main()
        run.assert_called_once_with(db, build_report=True, send_report=True)
        db.close.assert_called_once_with()
        return status, stdout.getvalue()

    def test_partial_source_failure_returns_nonzero_after_all_results(self):
        results = [
            sync.SourceSyncResult("shell_excel", 6),
            sync.SourceSyncResult("petrol", 1),
            sync.SourceSyncResult("turpak", 0, detail="HTTP404", status="error"),
        ]
        status, stdout = self.run_cli(results)
        self.assertEqual(status, 1)
        for result in results:
            self.assertIn(result.source, stdout)
        self.assertIn("Report: report.xlsx", stdout)

    def test_zero_rows_and_optional_skipped_source_are_not_failures(self):
        results = [
            sync.SourceSyncResult("turpak", 0, duplicate_rows=4),
            sync.SourceSyncResult("petrol", 0, status="skipped"),
        ]
        status, _ = self.run_cli(results)
        self.assertEqual(status, 0)

    def test_unexpected_failure_closes_database_without_false_success(self):
        db = Mock()
        with patch.object(cli_sync.Base.metadata, "create_all"), \
             patch.object(cli_sync, "SessionLocal", return_value=db), \
             patch.object(cli_sync, "sync_all", side_effect=RuntimeError("test failure")):
            with self.assertRaisesRegex(RuntimeError, "test failure"):
                cli_sync.main()
        db.close.assert_called_once_with()


class SourceContract(OfflineContract):
    def test_shell_transport_error_does_not_skip_remaining_sources(self):
        cfg = SimpleNamespace(
            shell_enabled=True, petrol_enabled=True, turpak_enabled=True,
            shell_customer_code="test", shell_user_id="test", shell_password="test",
            shell_branch_code="test", shell_use_api=True,
            shell_base_url="https://example.invalid", shell_timeout_seconds=1,
            shell_file_fallback_enabled=False,
        )
        db = Mock()
        now = datetime.now(timezone.utc)
        with patch.object(sync, "settings", cfg), \
             patch.object(sync, "_zero_turpak_amounts", return_value=0), \
             patch.object(sync, "_track_run_start", return_value=SimpleNamespace()), \
             patch.object(sync, "_track_run_finish") as finish, \
             patch.object(sync, "_build_source_window", return_value=(now, now)), \
             patch.object(sync, "ShellTtsClient") as client, \
             patch.object(sync, "sync_petrol", return_value=sync.SourceSyncResult("petrol", 1)) as petrol, \
             patch.object(sync, "sync_turpak", return_value=sync.SourceSyncResult("turpak", 4)) as turpak:
            client.return_value.get_customer_sales_transactions.side_effect = requests.HTTPError("HTTP503")
            results, report = sync.sync_all(db, build_report=False, send_report=False)
        self.assertEqual([r.source for r in results], ["shell_excel", "petrol", "turpak"])
        self.assertEqual([r.status for r in results], ["error", "ok", "ok"])
        self.assertIsNone(report)
        petrol.assert_called_once_with(db)
        turpak.assert_called_once_with(db)
        self.assertEqual(finish.call_args.kwargs["status"], "error")
        db.rollback.assert_called_once_with()

    def test_turpak_404_records_error_and_returns_explicit_error_status(self):
        cfg = SimpleNamespace(
            turpak_company_name="test", turpak_password="test",
            turpak_base_url="https://example.invalid", turpak_group_name=None,
        )
        db = Mock()
        run = SimpleNamespace()
        with patch.object(sync, "settings", cfg), \
             patch.object(sync, "_track_run_start", return_value=run), \
             patch.object(sync, "_format_turpak_window", return_value=("start", "end")), \
             patch.object(sync, "TurpakClient") as client, \
             patch.object(sync, "save_events") as save:
            client.return_value.get_sales.side_effect = requests.HTTPError("404 Client Error")
            result = sync.sync_turpak(db)
        self.assertEqual(result.status, "error")
        self.assertEqual(result.rows_loaded, 0)
        self.assertEqual(run.status, "error")
        self.assertIn("404", run.detail)
        save.assert_not_called()
        db.rollback.assert_called_once_with()

    def test_unconfigured_sources_preserve_skipped_status(self):
        cfg = SimpleNamespace(
            shell_customer_code="", shell_user_id="", shell_password="", shell_branch_code="",
            shell_use_api=False, shell_file_fallback_enabled=False,
            petrol_use_api=False, petrol_input_path="", petrol_input_dir="",
            turpak_company_name="", turpak_password="",
        )
        with patch.object(sync, "settings", cfg), \
             patch.object(sync, "_track_run_start", return_value=SimpleNamespace()), \
             patch.object(sync, "_track_run_finish"), \
             patch.object(sync, "_format_petrol_window", return_value=("start", "end")):
            for source in (sync.sync_shell, sync.sync_petrol, sync.sync_turpak):
                with self.subTest(source=source.__name__):
                    result = source(Mock())
                    self.assertEqual(result.status, "skipped")

    def test_finished_timestamp_is_aware_utc_and_does_not_rewrite_history(self):
        db = Mock()
        run = SimpleNamespace()
        before = datetime.now(timezone.utc)
        sync._track_run_finish(db, run, 4)
        after = datetime.now(timezone.utc)
        self.assertEqual(run.finished_at.utcoffset(), timedelta(0))
        self.assertLessEqual(before, run.finished_at)
        self.assertLessEqual(run.finished_at, after)
        db.add.assert_called_once_with(run)
        db.commit.assert_called_once_with()
        db.execute.assert_not_called()

    def test_success_result_keeps_duplicate_metrics_and_explicit_status(self):
        db = Mock()
        result = sync._finish_source_sync(
            db, SimpleNamespace(), source="turpak", rows_loaded=0,
            rows_received=4, rows_normalized=4, status="ok",
        )
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.duplicate_rows, 4)
        self.assertIn("inserted=0", result.detail)


class TelegramErrorContract(OfflineContract):
    def test_http_exception_url_and_bot_token_are_redacted(self):
        result = bot._safe_error_text(requests.HTTPError("502 Bad Gateway: " + TEST_URL))
        self.assertIn("HTTPError", result)
        self.assertIn("502", result)
        self.assertNotIn(TEST_TOKEN, result)
        self.assertNotIn("https://", result)
        self.assertIn("[redacted-url]", result)

    def test_token_without_url_and_configured_token_are_redacted(self):
        with patch.dict(os.environ, {"TELEGRAM_LIMIT_BOT_TOKEN": "configured-test-secret"}):
            result = bot._safe_error_text("token=" + TEST_TOKEN + " alternate=configured-test-secret")
        self.assertNotIn(TEST_TOKEN, result)
        self.assertNotIn("configured-test-secret", result)

    def test_error_text_is_single_line_and_bounded(self):
        result = bot._safe_error_text("first\nsecond\r" + "x" * 1000)
        self.assertNotIn("\n", result)
        self.assertNotIn("\r", result)
        self.assertLessEqual(len(result), 500)

    def test_send_failure_logs_use_same_redaction(self):
        response = SimpleNamespace(ok=False, status_code=502, text="upstream " + TEST_URL)
        stdout = io.StringIO()
        with patch.object(bot, "_bot_token", return_value=TEST_TOKEN), \
             patch.object(bot.requests, "post", return_value=response), \
             redirect_stdout(stdout):
            result = bot._send_message("synthetic-chat", "synthetic-message")
        self.assertFalse(result)
        self.assertNotIn(TEST_TOKEN, stdout.getvalue())
        self.assertNotIn(TEST_URL, stdout.getvalue())

    def test_polling_error_is_redacted_and_startup_sends_no_messages(self):
        stdout = io.StringIO()
        with patch.object(bot, "_bot_token", return_value=TEST_TOKEN), \
             patch.object(bot, "_allowed_chat_ids", return_value={"synthetic-chat"}), \
             patch.object(bot, "_warm_caches"), \
             patch.object(bot, "_read_offset", return_value=1), \
             patch.object(bot, "_get_updates", side_effect=[requests.HTTPError(TEST_URL), KeyboardInterrupt]), \
             patch.object(bot, "_send_message") as send, \
             patch.object(bot.time, "sleep"), \
             redirect_stdout(stdout):
            result = bot.main()
        self.assertEqual(result, 0)
        self.assertNotIn(TEST_TOKEN, stdout.getvalue())
        self.assertNotIn(TEST_URL, stdout.getvalue())
        self.assertIn("polling failed", stdout.getvalue())
        send.assert_not_called()

    def test_restart_resumes_pending_update_processing(self):
        update = {"update_id": 12, "message": {"text": "/help"}}
        with patch.object(bot, "_bot_token", return_value=TEST_TOKEN), \
             patch.object(bot, "_allowed_chat_ids", return_value={"synthetic-chat"}), \
             patch.object(bot, "_warm_caches"), \
             patch.object(bot, "_read_offset", return_value=12), \
             patch.object(bot, "_get_updates", side_effect=[[update], KeyboardInterrupt]), \
             patch.object(bot, "_write_offset") as offset, \
             patch.object(bot, "_handle_update") as handle, \
             redirect_stdout(io.StringIO()):
            result = bot.main()
        self.assertEqual(result, 0)
        offset.assert_called_once_with(13)
        handle.assert_called_once_with(update, {"synthetic-chat"})


if __name__ == "__main__":
    unittest.main()
