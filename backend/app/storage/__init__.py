"""Object storage abstraction: a local directory, or any S3-compatible object store (Cloudflare R2,
AWS S3, MinIO) so several API and worker servers can share files.

Keys are opaque, server-generated paths of the form ``users/<uid>/<area>/<id>/<name>``. Files are
never served directly; downloads go through authenticated API endpoints. Buckets stay private.
"""

import re
import shutil
from abc import ABC, abstractmethod
from functools import lru_cache
from pathlib import Path

from app.config import get_settings

_SAFE_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-/]*$")


class StorageError(Exception):
    pass


def safe_filename(name: str, default: str = "file") -> str:
    """Reduce an untrusted filename to a harmless display name."""
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r"[^A-Za-z0-9._ \-]", "_", name).strip(" .")
    return (name or default)[:120]


def validate_key(key: str) -> str:
    if not _SAFE_KEY.match(key) or ".." in key.split("/") or "//" in key:
        raise StorageError("invalid storage key")
    return key


class Storage(ABC):
    @abstractmethod
    def put(self, key: str, data: bytes) -> None: ...

    @abstractmethod
    def get(self, key: str) -> bytes: ...

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...

    @abstractmethod
    def delete_prefix(self, prefix: str) -> None: ...

    @abstractmethod
    def local_path(self, key: str) -> Path | None:
        """Filesystem path if the backend is local (lets FileResponse stream large files)."""

    def check(self) -> None:
        """Raise if the storage is unreachable (readiness probe)."""
        self.put("health/probe", b"ok")
        self.delete("health/probe")


class LocalStorage(Storage):
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = (self.root / validate_key(key)).resolve()
        if self.root not in p.parents:
            raise StorageError("invalid storage key")
        return p

    def put(self, key: str, data: bytes) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(p)

    def get(self, key: str) -> bytes:
        p = self._path(key)
        if not p.is_file():
            raise StorageError("object not found")
        return p.read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def delete(self, key: str) -> None:
        p = self._path(key)
        if p.is_file():
            p.unlink()

    def delete_prefix(self, prefix: str) -> None:
        p = self._path(prefix.rstrip("/"))
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        elif p.is_file():
            p.unlink()

    def local_path(self, key: str) -> Path | None:
        return self._path(key)


_MISSING = {"NoSuchKey", "NotFound", "404"}


class S3Storage(Storage):
    """Any S3-compatible object store. Cloudflare R2: endpoint ``https://<account_id>.r2.cloudflarestorage.com``,
    region ``auto``. The boto3 client is thread-safe and shared by all threads."""

    def __init__(
        self,
        bucket: str,
        *,
        endpoint_url: str | None = None,
        region: str = "auto",
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        prefix: str = "",
        client=None,
    ):
        self.bucket = bucket
        self.prefix = f"{prefix.strip('/')}/" if prefix.strip("/") else ""
        if client is None:
            import boto3
            from botocore.config import Config

            client = boto3.client(
                "s3",
                endpoint_url=endpoint_url or None,
                region_name=region or "auto",
                aws_access_key_id=access_key_id or None,  # None: the standard AWS credential chain
                aws_secret_access_key=secret_access_key or None,
                config=Config(
                    signature_version="s3v4",
                    s3={"addressing_style": "path"},
                    retries={"max_attempts": 5, "mode": "standard"},
                    max_pool_connections=32,
                    connect_timeout=10,
                    read_timeout=120,
                    # Only send/verify checksums the API requires: S3-compatible stores (R2, MinIO) do not
                    # all accept the extra checksum headers newer SDKs add by default.
                    request_checksum_calculation="when_required",
                    response_checksum_validation="when_required",
                ),
            )
        self.client = client

    def _key(self, key: str) -> str:
        return self.prefix + validate_key(key)

    @staticmethod
    def _missing(exc) -> bool:
        err = getattr(exc, "response", {}).get("Error", {})
        return str(err.get("Code")) in _MISSING

    def put(self, key: str, data: bytes) -> None:
        self.client.put_object(Bucket=self.bucket, Key=self._key(key), Body=data)

    def get(self, key: str) -> bytes:
        from botocore.exceptions import ClientError

        try:
            return self.client.get_object(Bucket=self.bucket, Key=self._key(key))["Body"].read()
        except ClientError as exc:
            if self._missing(exc):
                raise StorageError("object not found") from exc
            raise StorageError(f"storage read failed: {exc}") from exc

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self.client.head_object(Bucket=self.bucket, Key=self._key(key))
            return True
        except ClientError as exc:
            if self._missing(exc):
                return False
            raise

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=self._key(key))  # idempotent

    def delete_prefix(self, prefix: str) -> None:
        base = self._key(prefix.rstrip("/"))
        self.client.delete_object(Bucket=self.bucket, Key=base)  # a single object at the prefix itself
        pages = self.client.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=base + "/")
        for page in pages:
            keys = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if keys:  # at most 1000 per page, the DeleteObjects limit
                self.client.delete_objects(Bucket=self.bucket, Delete={"Objects": keys, "Quiet": True})

    def local_path(self, key: str) -> Path | None:
        return None

    def check(self) -> None:
        self.client.head_bucket(Bucket=self.bucket)  # a read, so health checks do not add billed writes


def storage_from_settings(settings) -> Storage:
    if settings.storage_backend == "local":
        return LocalStorage(settings.storage_root)
    if settings.storage_backend in ("s3", "r2"):
        endpoint = settings.s3_endpoint_url
        if not endpoint and settings.r2_account_id:
            endpoint = f"https://{settings.r2_account_id}.r2.cloudflarestorage.com"
        if not settings.s3_bucket:
            raise RuntimeError("Set S3_BUCKET for the s3/r2 storage backend")
        if settings.storage_backend == "r2" and not endpoint:
            raise RuntimeError("Set R2_ACCOUNT_ID (or S3_ENDPOINT_URL) for the r2 storage backend")
        return S3Storage(
            settings.s3_bucket,
            endpoint_url=endpoint,
            region=settings.s3_region,
            access_key_id=settings.s3_access_key_id,
            secret_access_key=settings.s3_secret_access_key,
            prefix=settings.s3_prefix,
        )
    raise RuntimeError(f"Unsupported STORAGE_BACKEND: {settings.storage_backend}")


@lru_cache
def get_storage() -> Storage:
    return storage_from_settings(get_settings())
