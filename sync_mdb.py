"""Copy the annual HAMS MDB and publish a validated SQLite query snapshot."""

import argparse
import json
import logging
import os
import re
import shutil
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from stat import S_ISDIR
from time import sleep
from uuid import uuid4

from data import SNAPSHOT_DIRNAME, YEAR_FILENAME, DataError, _AccessFile, _build_lock


LOG = logging.getLogger("hams-sync")
ROOT = Path(__file__).resolve().parent


def select_mdb(source):
    if S_ISDIR(source.stat().st_mode):
        candidates = []
        for path in source.iterdir():
            match = YEAR_FILENAME.fullmatch(path.name)
            if match and int(match[1]) and path.is_file():
                candidates.append((int(match[1]), path))
        if not candidates:
            raise DataError(f"在 {source} 找不到 HAMS_年份.mdb。")
        year, path = max(candidates)
        return path, year

    match = YEAR_FILENAME.fullmatch(source.name)
    if not match or not int(match[1]):
        raise DataError("請指定 HAMS_年份.mdb 或包含年度檔的資料夾。")
    return source, int(match[1])


def file_version(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def copy_mdb(source, staged):
    before = source.stat()
    shutil.copyfile(source, staged)
    with staged.open("r+b") as mdb:
        if staged.stat().st_size != before.st_size or file_version(before) != file_version(source.stat()):
            raise DataError("複製期間來源 MDB 已變更，請等來源備份完成後再同步。")
        os.fsync(mdb.fileno())


def replace_file(staged, destination):
    # A scanner or a legacy MDB import can hold a Windows file briefly.
    for attempt in range(6):
        try:
            staged.replace(destination)
            return
        except PermissionError:
            if attempt == 5:
                raise
            sleep(1)


def previous_snapshot(manifest):
    try:
        metadata = json.loads(manifest.read_text(encoding="utf-8"))
        return metadata.get("file") if isinstance(metadata, dict) else None
    except (FileNotFoundError, ValueError):
        return None


def prune_snapshots(folder, stem, keep):
    pattern = re.compile(rf"{stem}-[a-f0-9]{{32}}\.sqlite3")
    try:
        for path in folder.iterdir():
            if pattern.fullmatch(path.name) and path.name not in keep:
                try:
                    path.unlink(missing_ok=True)
                except OSError as error:
                    LOG.warning("稍後再清理舊版 SQLite：%s", error)
    except OSError as error:
        LOG.warning("無法清理舊版 SQLite：%s", error)


def sync_mdb(source, destination, snapshots, reader):
    source, year = select_mdb(source)
    stem = f"HAMS_{year:04d}"
    archive = destination / f"{stem}.mdb"
    if archive.exists() and source.samefile(archive):
        raise DataError("來源 MDB 和本機目的檔案相同，請指定 Y 槽上的來源。")

    manifest = snapshots / f"{stem}.json"
    previous = previous_snapshot(manifest)
    generation = f"{stem}-{uuid4().hex}.sqlite3"
    LOG.info("開始同步 %s → %s", source, archive)
    with tempfile.TemporaryDirectory(prefix="sync-", dir=snapshots) as temporary:
        work = Path(temporary)
        copied_mdb = work / archive.name
        copy_mdb(source, copied_mdb)
        LOG.info("MDB 複製完成，開始轉換 SQLite。")

        staged_db = work / generation
        index = _AccessFile(copied_mdb, staged_db, reader, year)
        index.prepare()
        status = index.status()
        if status["state"] != "ready":
            raise DataError(status["message"])

        with closing(sqlite3.connect(f"{staged_db.as_uri()}?mode=ro", uri=True)) as db:
            if db.execute("PRAGMA quick_check").fetchone() != ("ok",):
                raise DataError("SQLite 完整性檢查失敗。")
            found = db.execute("SELECT count FROM year_counts WHERE year = ?", (year,)).fetchone()
            if not found and (status["skipped"] or db.execute("SELECT 1 FROM year_counts LIMIT 1").fetchone()):
                raise DataError(f"找不到有效的 {year} 年門禁紀錄，請檢查來源 MDB。")

        metadata = {
            "file": generation,
            "synced_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "skipped": status["skipped"],
        }
        staged_manifest = work / manifest.name
        with staged_manifest.open("w", encoding="utf-8") as output:
            json.dump(metadata, output)
            output.flush()
            os.fsync(output.fileno())

        replace_file(copied_mdb, archive)
        replace_file(staged_db, snapshots / generation)
        replace_file(staged_manifest, manifest)

    LOG.info("同步完成：%s 年，%s 筆紀錄，略過 %s 筆。",
             year, f"{found[0] if found else 0:,}", f"{status['skipped']:,}")
    prune_snapshots(snapshots, stem, (generation, previous))


def main():
    parser = argparse.ArgumentParser(description="將年度 HAMS MDB 複製到本機並發布 SQLite 查詢資料。")
    parser.add_argument("--source", type=Path, default=Path("Y:\\"), help="來源 MDB 或資料夾（預設 Y 槽）。")
    parser.add_argument("--destination-dir", type=Path, default=Path(r"C:\Temp"), help="本機目的資料夾。")
    parser.add_argument("--java", default="java")
    parser.add_argument("--reader-jar", type=Path)
    args = parser.parse_args()
    destination = args.destination_dir.absolute()
    snapshots = destination / SNAPSHOT_DIRNAME

    reader_jar = args.reader_jar
    java = args.java
    if reader_jar is None and os.name == "nt":
        reader_jar = ROOT / "runtime" / "mdb-reader.jar"
        java = str(ROOT / "runtime" / "jre" / "bin" / "java.exe")
    reader = (java, "-Xmx256m", "-jar", str(reader_jar.absolute())) if reader_jar is not None else None

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    LOG.addHandler(console)
    LOG.setLevel(logging.INFO)
    try:
        snapshots.mkdir(mode=0o700, parents=True, exist_ok=True)
        with _build_lock(snapshots / "sync.lock"):
            logfile = RotatingFileHandler(snapshots / "sync.log", encoding="utf-8", maxBytes=1024 * 1024, backupCount=1)
            logfile.setFormatter(formatter)
            LOG.addHandler(logfile)
            try:
                sync_mdb(args.source.absolute(), destination, snapshots, reader)
            except (DataError, OSError, sqlite3.Error) as error:
                LOG.error("同步失敗：%s", error)
                return 1
            finally:
                LOG.removeHandler(logfile)
                logfile.close()
    except OSError as error:
        LOG.error("無法建立本機同步資料夾或記錄：%s", error)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
