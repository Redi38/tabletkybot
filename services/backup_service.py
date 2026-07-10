import asyncio
import gzip
import logging
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone

import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

BACKUP_PREFIX = "db_backups/"


def _build_s3_client(config):
    return boto3.client(
        "s3",
        endpoint_url=config.backup_s3_endpoint_url,
        aws_access_key_id=config.backup_s3_access_key,
        aws_secret_access_key=config.backup_s3_secret_key,
        region_name=config.backup_s3_region,
        config=BotoConfig(
            signature_version="s3v4",
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        ),
    )


def _pg_dump_sync(database_url: str, dump_path: str) -> None:
    """
    Runs pg_dump against the database and writes a custom-format dump to dump_path.
    database_url is in the form postgresql+asyncpg://user:pass@host:port/db —
    pg_dump doesn't understand the +asyncpg driver suffix, so it's stripped.
    """
    dsn = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)

    cmd = ["pg_dump", dsn, "-F", "c", "-f", dump_path]
    proc = subprocess.run(cmd, capture_output=True, text=True)

    if proc.returncode != 0:
        raise RuntimeError(f"pg_dump failed: {proc.stderr}")


def _gzip_file(src_path: str, dst_path: str) -> None:
    with open(src_path, "rb") as f_in, gzip.open(dst_path, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)


def _upload_and_rotate_sync(config, dump_gz_path: str, object_name: str) -> None:
    s3 = _build_s3_client(config)
    bucket = config.backup_s3_bucket

    s3.upload_file(dump_gz_path, bucket, object_name)
    logger.info(f"✅ Backup uploaded to bucket: s3://{bucket}/{object_name}")

    # ── Rotation: delete objects older than retention_days ──
    cutoff = datetime.now(timezone.utc) - timedelta(days=config.backup_retention_days)

    paginator = s3.get_paginator("list_objects_v2")
    to_delete = []
    for page in paginator.paginate(Bucket=bucket, Prefix=BACKUP_PREFIX):
        for obj in page.get("Contents", []):
            if obj["LastModified"] < cutoff:
                to_delete.append({"Key": obj["Key"]})

    if to_delete:
        # delete_objects accepts at most 1000 keys per call
        for i in range(0, len(to_delete), 1000):
            batch = to_delete[i:i + 1000]
            s3.delete_objects(Bucket=bucket, Delete={"Objects": batch})
        logger.info(f"🗑️ Deleted {len(to_delete)} old backup(s)")


async def run_database_backup(config) -> None:
    """
    Dumps the database with pg_dump, gzips it, and uploads it to Oracle
    Object Storage (S3-compatible). Intended to be called from APScheduler
    on a cron schedule.
    """
    if not config.backup_s3_bucket:
        logger.warning("⚠️ BACKUP_S3_BUCKET is not set — skipping backup")
        return

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
    object_name = f"{BACKUP_PREFIX}medbot_{timestamp}.dump.gz"

    with tempfile.TemporaryDirectory() as tmp_dir:
        dump_path = os.path.join(tmp_dir, "medbot.dump")
        dump_gz_path = os.path.join(tmp_dir, "medbot.dump.gz")

        try:
            await asyncio.to_thread(_pg_dump_sync, config.database_url, dump_path)
            await asyncio.to_thread(_gzip_file, dump_path, dump_gz_path)
            await asyncio.to_thread(_upload_and_rotate_sync, config, dump_gz_path, object_name)
        except ClientError as e:
            logger.error(f"❌ S3 error while uploading backup: {e}", exc_info=True)
        except Exception as e:
            logger.error(f"❌ Error creating/uploading database backup: {e}", exc_info=True)
