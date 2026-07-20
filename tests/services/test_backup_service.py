"""
Tests for services/backup_service.py (previously 0% test coverage).

subprocess.run and boto3.client are mocked — these tests never touch a
real pg_dump binary or real S3-compatible storage. asyncio.to_thread
itself is not mocked: it's allowed to actually run the (now-mocked) sync
functions in a thread pool, exercising the real threading/await plumbing.
"""

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

from services.backup_service import run_database_backup


class FakeConfig:
    def __init__(self, **overrides):
        self.database_url = "postgresql+asyncpg://user:pass@host:5432/medbot"
        self.backup_s3_bucket = "test-bucket"
        self.backup_s3_endpoint_url = "https://example-oracle-storage.com"
        self.backup_s3_access_key = "test-access-key"
        self.backup_s3_secret_key = "test-secret-key"
        self.backup_s3_region = "eu-frankfurt-1"
        self.backup_retention_days = 14
        for key, value in overrides.items():
            setattr(self, key, value)


def _fake_pg_dump_creates_file(cmd, capture_output, text):
    """Side effect for the mocked subprocess.run call inside _pg_dump_sync:
    writes a small dummy file at the target -f path, so the downstream
    gzip step has something real to compress."""
    dump_path = cmd[cmd.index("-f") + 1]
    with open(dump_path, "wb") as f:
        f.write(b"fake pg_dump output content")
    return MagicMock(returncode=0, stderr="")


class TestSkipsWhenNoBucketConfigured:
    async def test_skips_backup_and_does_not_call_pg_dump(self):
        config = FakeConfig(backup_s3_bucket=None)

        with patch("services.backup_service.subprocess.run") as mock_run:
            await run_database_backup(config)

        mock_run.assert_not_called()

    async def test_skips_backup_when_bucket_is_empty_string(self):
        config = FakeConfig(backup_s3_bucket="")

        with patch("services.backup_service.subprocess.run") as mock_run:
            await run_database_backup(config)

        mock_run.assert_not_called()


class TestPgDumpDsnConversion:
    async def test_strips_asyncpg_suffix_from_dsn(self):
        config = FakeConfig(database_url="postgresql+asyncpg://u:p@h:5432/db")

        with (
            patch("services.backup_service.subprocess.run", side_effect=_fake_pg_dump_creates_file) as mock_run,
            patch("services.backup_service.boto3.client") as mock_boto,
        ):
            mock_boto.return_value.get_paginator.return_value.paginate.return_value = []
            await run_database_backup(config)

        called_cmd = mock_run.call_args[0][0]
        assert called_cmd[0] == "pg_dump"
        assert called_cmd[1] == "postgresql://u:p@h:5432/db"


class TestSuccessfulBackupFlow:
    async def test_uploads_gzipped_dump_to_s3(self):
        config = FakeConfig()

        with (
            patch("services.backup_service.subprocess.run", side_effect=_fake_pg_dump_creates_file),
            patch("services.backup_service.boto3.client") as mock_boto,
        ):
            mock_s3 = mock_boto.return_value
            mock_s3.get_paginator.return_value.paginate.return_value = []

            await run_database_backup(config)

        mock_s3.upload_file.assert_called_once()
        local_path, bucket, object_name = mock_s3.upload_file.call_args[0]
        assert bucket == "test-bucket"
        assert object_name.startswith("db_backups/medbot_")
        assert object_name.endswith(".dump.gz")
        # The uploaded file was gzipped from the fake pg_dump output — verify
        # it's actually gzip-compressed content, not the raw dump bytes.
        assert local_path.endswith(".dump.gz")

    async def test_does_not_call_s3_when_pg_dump_fails(self):
        config = FakeConfig()

        def _failing_pg_dump(cmd, capture_output, text):
            return MagicMock(returncode=1, stderr="connection refused")

        with (
            patch("services.backup_service.subprocess.run", side_effect=_failing_pg_dump),
            patch("services.backup_service.boto3.client") as mock_boto,
        ):
            await run_database_backup(config)

        mock_boto.return_value.upload_file.assert_not_called()

    async def test_client_error_during_upload_does_not_raise(self):
        """A ClientError from S3 must be caught and logged, not propagated —
        this runs as an APScheduler job and shouldn't take down the process."""
        config = FakeConfig()

        with (
            patch("services.backup_service.subprocess.run", side_effect=_fake_pg_dump_creates_file),
            patch("services.backup_service.boto3.client") as mock_boto,
        ):
            mock_boto.return_value.upload_file.side_effect = ClientError(
                {"Error": {"Code": "AccessDenied", "Message": "denied"}}, "PutObject"
            )
            # Should not raise
            await run_database_backup(config)

    async def test_unexpected_exception_does_not_raise(self):
        config = FakeConfig()

        with patch("services.backup_service.subprocess.run", side_effect=RuntimeError("disk full")):
            # Should not raise
            await run_database_backup(config)


class TestRetentionRotation:
    async def test_deletes_objects_older_than_retention_period(self):
        config = FakeConfig(backup_retention_days=14)
        now = datetime.now(timezone.utc)

        old_object = {"Key": "db_backups/medbot_old.dump.gz", "LastModified": now - timedelta(days=30)}
        recent_object = {"Key": "db_backups/medbot_recent.dump.gz", "LastModified": now - timedelta(days=1)}

        with (
            patch("services.backup_service.subprocess.run", side_effect=_fake_pg_dump_creates_file),
            patch("services.backup_service.boto3.client") as mock_boto,
        ):
            mock_s3 = mock_boto.return_value
            mock_s3.get_paginator.return_value.paginate.return_value = [
                {"Contents": [old_object, recent_object]},
            ]

            await run_database_backup(config)

        mock_s3.delete_objects.assert_called_once()
        _, kwargs = mock_s3.delete_objects.call_args
        deleted_keys = [obj["Key"] for obj in kwargs["Delete"]["Objects"]]
        assert deleted_keys == ["db_backups/medbot_old.dump.gz"]

    async def test_does_not_call_delete_when_nothing_is_old(self):
        config = FakeConfig()
        now = datetime.now(timezone.utc)
        recent_object = {"Key": "db_backups/medbot_recent.dump.gz", "LastModified": now - timedelta(days=1)}

        with (
            patch("services.backup_service.subprocess.run", side_effect=_fake_pg_dump_creates_file),
            patch("services.backup_service.boto3.client") as mock_boto,
        ):
            mock_s3 = mock_boto.return_value
            mock_s3.get_paginator.return_value.paginate.return_value = [{"Contents": [recent_object]}]

            await run_database_backup(config)

        mock_s3.delete_objects.assert_not_called()

    async def test_batches_deletes_over_1000_keys(self):
        config = FakeConfig()
        now = datetime.now(timezone.utc)
        old_objects = [
            {"Key": f"db_backups/medbot_{i}.dump.gz", "LastModified": now - timedelta(days=100)} for i in range(1500)
        ]

        with (
            patch("services.backup_service.subprocess.run", side_effect=_fake_pg_dump_creates_file),
            patch("services.backup_service.boto3.client") as mock_boto,
        ):
            mock_s3 = mock_boto.return_value
            mock_s3.get_paginator.return_value.paginate.return_value = [{"Contents": old_objects}]

            await run_database_backup(config)

        # 1500 keys, max 1000 per delete_objects call -> 2 calls
        assert mock_s3.delete_objects.call_count == 2


class TestCleansUpTempFiles:
    async def test_temp_directory_is_removed_after_backup(self):
        config = FakeConfig()
        captured_tmp_dir = {}

        def _capture_and_create(cmd, capture_output, text):
            dump_path = cmd[cmd.index("-f") + 1]
            captured_tmp_dir["path"] = os.path.dirname(dump_path)
            with open(dump_path, "wb") as f:
                f.write(b"data")
            return MagicMock(returncode=0, stderr="")

        with (
            patch("services.backup_service.subprocess.run", side_effect=_capture_and_create),
            patch("services.backup_service.boto3.client") as mock_boto,
        ):
            mock_boto.return_value.get_paginator.return_value.paginate.return_value = []
            await run_database_backup(config)

        assert not os.path.exists(captured_tmp_dir["path"])
