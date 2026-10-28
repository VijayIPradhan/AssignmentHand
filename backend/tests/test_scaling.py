"""S3/R2 object storage, the shared rate limiter and worker concurrency."""

import threading

import boto3
import pytest
from moto import mock_aws

from app.storage import LocalStorage, S3Storage, StorageError, storage_from_settings


@pytest.fixture()
def s3():
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket="files-test")
        yield client


def test_s3_storage_round_trip_and_prefix(s3):
    st = S3Storage("files-test", prefix="/env1/", client=s3)
    st.put("users/u1/a/x.pdf", b"pdf")
    assert st.get("users/u1/a/x.pdf") == b"pdf"
    assert st.exists("users/u1/a/x.pdf") and not st.exists("users/u1/a/y.pdf")
    assert [o["Key"] for o in s3.list_objects_v2(Bucket="files-test")["Contents"]] == ["env1/users/u1/a/x.pdf"]
    assert st.local_path("users/u1/a/x.pdf") is None
    with pytest.raises(StorageError, match="not found"):
        st.get("users/u1/a/missing.pdf")
    st.delete("users/u1/a/x.pdf")
    st.delete("users/u1/a/x.pdf")  # deleting twice is fine
    assert not st.exists("users/u1/a/x.pdf")
    with pytest.raises(StorageError):
        st.put("../escape", b"x")  # keys are validated like the local backend
    st.check()


def test_s3_delete_prefix_removes_only_that_folder_beyond_one_page(s3):
    st = S3Storage("files-test", client=s3)
    for i in range(1005):  # more than one 1000-key list/delete page
        st.put(f"users/u1/profiles/p1/samples/{i}.png", b"x")
    st.put("users/u1/profiles/p10/keep.png", b"x")  # shares the text prefix "p1" but is another folder
    st.put("users/u1/profiles/p1", b"x")
    st.delete_prefix("users/u1/profiles/p1/")
    keys = [o["Key"] for o in s3.list_objects_v2(Bucket="files-test").get("Contents", [])]
    assert keys == ["users/u1/profiles/p10/keep.png"]


def test_r2_settings_build_the_account_endpoint(monkeypatch):
    from app.config import Settings

    for name in ("S3_REGION", "S3_ENDPOINT_URL", "S3_PREFIX", "S3_BUCKET", "STORAGE_BACKEND", "R2_ACCOUNT_ID"):
        monkeypatch.delenv(name, raising=False)  # independent of TEST_STORAGE=s3
    s = Settings(
        _env_file=None,
        storage_backend="r2",
        s3_bucket="files",
        r2_account_id="abc123",
        s3_access_key_id="k",
        s3_secret_access_key="s",
    )
    st = storage_from_settings(s)
    assert isinstance(st, S3Storage)
    assert st.client.meta.endpoint_url == "https://abc123.r2.cloudflarestorage.com"
    assert st.client.meta.region_name == "auto"
    with pytest.raises(RuntimeError, match="R2_ACCOUNT_ID"):
        storage_from_settings(Settings(_env_file=None, storage_backend="r2", s3_bucket="files"))
    with pytest.raises(RuntimeError, match="S3_BUCKET"):
        storage_from_settings(Settings(_env_file=None, storage_backend="s3"))


def test_copy_local_files_into_the_bucket_is_resumable(s3, tmp_path):
    from app.storage.copy import copy_tree

    src = LocalStorage(tmp_path / "files")
    src.put("users/u1/a/doc.pdf", b"pdf-bytes")
    src.put("users/u1/p/lib.json", b"{}")
    src.put("health/probe", b"ok")
    target = S3Storage("files-test", client=s3)
    assert copy_tree(src, target) == {"copied": 2, "skipped": 0}
    assert target.get("users/u1/a/doc.pdf") == b"pdf-bytes"
    assert copy_tree(src, target) == {"copied": 0, "skipped": 2}  # re-running skips what is there
    src.put("users/u1/a/doc.pdf", b"changed, longer content")
    assert copy_tree(src, target) == {"copied": 1, "skipped": 1}


def test_rate_limit_is_shared_by_every_process(tmp_env):
    from app import ratelimit
    from app.errors import RateLimited

    for _ in range(3):
        ratelimit.check_rate("user-1", "render", 3)
    with pytest.raises(RateLimited) as exc:
        ratelimit.check_rate("user-1", "render", 3)  # the hits live in the database, not this process
    assert 1 <= exc.value.details["retry_after"] <= 61
    ratelimit.check_rate("user-2", "render", 3)  # other users and buckets are separate
    ratelimit.check_rate("user-1", "generate", 3)


def test_rate_limit_counts_concurrent_requests(tmp_env):
    from app import ratelimit
    from app.errors import RateLimited

    passed, limited = [], []

    def hit():
        try:
            ratelimit.check_rate("user-1", "login", 5)
            passed.append(1)
        except RateLimited:
            limited.append(1)

    threads = [threading.Thread(target=hit) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(passed) + len(limited) == 12
    assert 5 <= len(passed) <= 7 and limited  # at most a small overshoot at the window edge


def test_old_rate_limit_hits_are_purged(tmp_env):
    from datetime import timedelta

    from app import ratelimit

    ratelimit.check_rate("user-1", "render", 5)
    assert ratelimit.purge(older_than=timedelta(seconds=-1)) == 1


def test_worker_concurrency_is_one_on_sqlite(monkeypatch):
    from app import config
    from app.jobs import worker

    monkeypatch.setenv("WORKER_CONCURRENCY", "4")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///x.db")
    config.get_settings.cache_clear()
    assert worker.concurrency() == 1
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@db/app")
    config.get_settings.cache_clear()
    assert worker.concurrency() == 4
    config.get_settings.cache_clear()


def test_parallel_workers_never_run_a_job_twice(tmp_env):
    from app.db import session_factory
    from app.jobs.queue import claim_next, enqueue
    from app.models import User

    with session_factory()() as db:
        user = User(email="w@example.com", password_hash="x")
        db.add(user)
        db.commit()
        for i in range(8):
            enqueue(db, user_id=user.id, type="noop", payload={"i": i}, dedupe=False)
    claimed, lock = [], threading.Lock()

    def claim_all():
        while True:
            with session_factory()() as db:
                job = claim_next(db)
                if job is None:
                    return
                with lock:
                    claimed.append(job.id)

    threads = [threading.Thread(target=claim_all) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(claimed) == 8 and len(set(claimed)) == 8


def test_database_copy_moves_every_row_and_refuses_to_merge(tmp_env, auth_client, tmp_path):
    from sqlalchemy import create_engine, func, select

    from app import db as appdb
    from app.db import Base
    from app.db_copy import copy_database
    from tests.conftest import run_jobs
    from tests.pdfs import assignment_pdf

    auth_client.post("/api/v1/assignments", files={"file": ("a.pdf", assignment_pdf())})
    run_jobs()
    source_url = str(appdb.get_engine().url)
    target_url = f"sqlite:///{(tmp_path / 'target.db').as_posix()}"
    target = create_engine(target_url)
    Base.metadata.create_all(target)  # an empty database with the latest schema, like a new server
    counts = copy_database(source_url, target)
    assert counts["users"] == 1 and counts["assignments"] == 1 and counts["extracted_questions"] >= 1
    with target.connect() as c:
        for t in Base.metadata.sorted_tables:
            assert c.execute(select(func.count()).select_from(t)).scalar() == counts[t.name]
    with pytest.raises(SystemExit, match="already has 1 user"):
        copy_database(source_url, target)  # never merged twice by accident
    assert copy_database(source_url, target, replace=True)["users"] == 1
