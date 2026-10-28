"""Copy every row of one AssignmentHand database into another, e.g. a local SQLite file into the
production PostgreSQL when moving to a server.

    python -m app.db_copy --from sqlite:////data/import/app.db      # a file the container can read
    python -m app.db_copy --from-storage migration/app.db           # a SQLite file uploaded to the storage (R2)

The target is DATABASE_URL. It is migrated to the latest schema first and must hold no users, so data is
never merged or duplicated by accident (``--replace`` empties it first). The source must already be at
the latest schema (start the old app once to migrate it). Files are not copied: they live in the storage
(see app.storage.copy). Afterwards the row counts of every table are compared.
"""

import argparse
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Integer, create_engine, delete, func, inspect, select, text

from app.config import get_settings
from app.db import Base, get_engine

BATCH = 500


def _head_revision() -> str:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    ini = Path(__file__).resolve().parents[1] / "alembic.ini"
    cfg = Config(str(ini))
    cfg.set_main_option("script_location", str(ini.parent / "alembic"))
    return ScriptDirectory.from_config(cfg).get_current_head()


def _revision(engine) -> str | None:
    if "alembic_version" not in inspect(engine).get_table_names():
        return None
    with engine.connect() as c:
        return c.execute(text("SELECT version_num FROM alembic_version")).scalar()


def _migrate_target() -> None:
    from alembic.config import Config

    from alembic import command

    ini = Path(__file__).resolve().parents[1] / "alembic.ini"
    cfg = Config(str(ini))
    cfg.set_main_option("script_location", str(ini.parent / "alembic"))
    command.upgrade(cfg, "head")


def _utc(row: dict) -> dict:
    # SQLite stores timestamps without their zone; they were written in UTC.
    return {k: (v.replace(tzinfo=UTC) if isinstance(v, datetime) and v.tzinfo is None else v) for k, v in row.items()}


def copy_database(source_url: str, target_engine, replace: bool = False) -> dict[str, int]:
    import app.models  # noqa: F401  (registers every table on Base.metadata)

    src = create_engine(source_url)
    head = _head_revision()
    rev = _revision(src)
    if rev != head:
        raise SystemExit(f"The source database is at schema {rev or 'unknown'}, not {head}. Start the old app once.")
    tables = Base.metadata.sorted_tables  # parents before children
    with target_engine.begin() as dst:
        users = dst.execute(select(func.count()).select_from(Base.metadata.tables["users"])).scalar()
        if users and not replace:
            raise SystemExit(f"The target database already has {users} user(s). Use --replace to empty it first.")
        if replace:
            for t in reversed(tables):
                dst.execute(delete(t))
        counts: dict[str, int] = {}
        with src.connect() as s:
            for t in tables:
                n = 0
                rows = s.execute(select(t)).mappings()
                while batch := rows.fetchmany(BATCH):
                    dst.execute(t.insert(), [_utc(dict(r)) for r in batch])
                    n += len(batch)
                counts[t.name] = n
        if target_engine.dialect.name == "postgresql":
            # Rows were inserted with their ids: move each id counter past the highest one.
            for t in tables:
                for col in t.primary_key.columns:
                    if isinstance(col.type, Integer) and col.autoincrement in (True, "auto"):
                        dst.execute(
                            text(
                                f"SELECT setval(pg_get_serial_sequence('{t.name}', '{col.name}'), "
                                f"COALESCE((SELECT MAX({col.name}) FROM {t.name}), 1), "
                                f"(SELECT MAX({col.name}) FROM {t.name}) IS NOT NULL)"
                            )
                        )
    with src.connect() as s, target_engine.connect() as d:
        for t in tables:
            a = s.execute(select(func.count()).select_from(t)).scalar()
            b = d.execute(select(func.count()).select_from(t)).scalar()
            if a != b:
                raise SystemExit(f"Row count mismatch in {t.name}: source {a}, target {b}")
    src.dispose()
    return counts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--from", dest="source", help="source database URL, e.g. sqlite:////data/import/app.db")
    g.add_argument("--from-storage", dest="key", help="storage key of an uploaded SQLite file, e.g. migration/app.db")
    ap.add_argument("--replace", action="store_true", help="empty the target database first")
    args = ap.parse_args(argv)

    target = get_settings().database_url
    if args.key:
        from app.storage import get_storage

        tmp = Path(tempfile.mkdtemp()) / "source.db"
        tmp.write_bytes(get_storage().get(args.key))
        source = f"sqlite:///{tmp.as_posix()}"
    else:
        source = args.source
    if source == target:
        print("The source and the target are the same database.", file=sys.stderr)
        return 2
    _migrate_target()
    counts = copy_database(source, get_engine(), replace=args.replace)
    for name, n in counts.items():
        print(f"{name:24} {n:6} row(s)")
    print(f"copied {sum(counts.values())} row(s) into {get_engine().dialect.name}; every table's count matches")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
