"""Copy every stored file from a local storage directory into the configured storage backend.

Used once when moving to Cloudflare R2 (or another S3-compatible store): set STORAGE_BACKEND=r2 and the
S3_* / R2_* settings, then run

    python -m app.storage.copy --from ./data/storage

Existing objects with the same size are skipped, so the command can be re-run after an interruption.
"""

import argparse
import sys
from pathlib import Path

from app.config import get_settings
from app.storage import LocalStorage, S3Storage, get_storage


def copy_tree(source: LocalStorage, target, dry_run: bool = False) -> dict:
    copied = skipped = 0
    for path in sorted(p for p in source.root.rglob("*") if p.is_file() and not p.name.endswith(".tmp")):
        key = path.relative_to(source.root).as_posix()
        if key.startswith("health/"):
            continue
        if isinstance(target, S3Storage) and _same_size(target, key, path.stat().st_size):
            skipped += 1
            continue
        if not dry_run:
            target.put(key, path.read_bytes())
        copied += 1
    return {"copied": copied, "skipped": skipped}


def _same_size(target: S3Storage, key: str, size: int) -> bool:
    from botocore.exceptions import ClientError

    try:
        head = target.client.head_object(Bucket=target.bucket, Key=target._key(key))
    except ClientError as exc:
        if target._missing(exc):
            return False
        raise
    return int(head.get("ContentLength", -1)) == size


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="source", type=Path, default=Path("./data/storage"), help="local storage root")
    ap.add_argument("--dry-run", action="store_true", help="only count what would be copied")
    args = ap.parse_args(argv)
    if get_settings().storage_backend == "local":
        print("STORAGE_BACKEND is local: set it to r2 or s3 (and the bucket settings) first.", file=sys.stderr)
        return 2
    if not args.source.is_dir():
        print(f"No storage directory at {args.source}", file=sys.stderr)
        return 2
    target = get_storage()
    target.check()
    result = copy_tree(LocalStorage(args.source), target, dry_run=args.dry_run)
    verb = "would copy" if args.dry_run else "copied"
    print(f"{verb} {result['copied']} file(s), {result['skipped']} already present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
