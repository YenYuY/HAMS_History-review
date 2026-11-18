"""Read HAMS events from Access into a local, rebuildable query index."""

import csv
import errno
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
from collections import Counter, defaultdict
from contextlib import closing, contextmanager
from datetime import date, datetime, time
from functools import lru_cache
from io import TextIOWrapper
from operator import itemgetter
from pathlib import Path
from threading import Lock, Thread
from time import monotonic, sleep

if os.name == "nt":
    import msvcrt
else:
    import fcntl


INDEX_VERSION = 1
YEAR_FILENAME = re.compile(r"HAMS_([0-9]{4})\.mdb", re.IGNORECASE)
SNAPSHOT_DIRNAME = ".hams-snapshots"
SNAPSHOT_MANIFEST = re.compile(r"HAMS_([0-9]{4})\.json", re.IGNORECASE)
FACETS = {
    "department": "departments",
    "device": "devices",
    "door": "doors",
    "event": "events",
}
MDB_COLUMNS = (
    "eventDate", "eventTime", "deviceName", "doorName", "deptName",
    "personName", "personID", "eventCard", "eventName", "NewEventCode_Name",
)

SCHEMA = """
CREATE TABLE records (
    id INTEGER PRIMARY KEY,
    datetime TEXT NOT NULL,
    device TEXT NOT NULL,
    door TEXT NOT NULL,
    department TEXT NOT NULL,
    actor INTEGER NOT NULL,
    event TEXT NOT NULL
);
CREATE TABLE actors (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    employee_id TEXT NOT NULL,
    card TEXT NOT NULL,
    search_name TEXT NOT NULL,
    search_card TEXT NOT NULL
);
CREATE TABLE source_info (
    fingerprint TEXT NOT NULL,
    skipped INTEGER NOT NULL
);
CREATE TABLE year_counts (
    year INTEGER PRIMARY KEY,
    count INTEGER NOT NULL
);
CREATE TABLE options (
    year INTEGER NOT NULL,
    facet TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (year, facet, value)
) WITHOUT ROWID;
"""

RECORD_COLUMNS = """
    r.device, r.door, r.department, a.name, a.employee_id AS employeeId,
    a.card, r.datetime, r.event
"""


class DataError(Exception):
    """An error that can be displayed in the local interface."""


@lru_cache(maxsize=512)
def _event_date(value):
    value = value.strip()
    if len(value) == 8 and value.isascii() and value.isdigit():
        value = f"{value[:4]}-{value[4:6]}-{value[6:]}"
    try:
        return date.fromisoformat(value.replace("/", "-")).isoformat()
    except ValueError:
        return None


@lru_cache(maxsize=32768)
def _event_time(value):
    value = value.strip()
    if len(value) not in (5, 8):
        return None
    try:
        parsed = time.fromisoformat(value)
        return parsed.isoformat(timespec="seconds") if parsed.tzinfo is None else None
    except ValueError:
        return None


def _mdb_export():
    installed = shutil.which("mdb-export")
    if installed:
        return installed
    for location in ("/opt/homebrew/bin/mdb-export", "/usr/local/bin/mdb-export"):
        if Path(location).is_file():
            return location
    raise DataError("找不到 MDB Tools。請先執行 brew install mdbtools，再重開查詢網頁。")


@contextmanager
def _build_lock(path):
    with path.open("a+b") as file:
        if os.name != "nt":
            fcntl.flock(file, fcntl.LOCK_EX)
            yield
            return

        # Byte locks are released automatically if a process exits during an import.
        file.seek(0)
        while True:
            try:
                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError as error:
                if error.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise
                sleep(0.2)
        try:
            yield
        finally:
            file.seek(0)
            msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)


class _AccessFile:
    """Import a single Access file and answer queries against its local index."""

    def __init__(self, source, index, reader=None, year=None, snapshot=None):
        self.source = source
        self.index = index
        self.reader = reader
        self.year = year
        self.snapshot = snapshot
        if snapshot is None:
            self.mark_loading()
        else:
            self._update("ready", "資料庫已就緒", skipped=snapshot["skipped"])

    def mark_loading(self):
        self._update("loading", "正在準備門禁記錄…")

    def _update(self, state, message, **extra):
        self._status = {
            "state": state,
            "message": message,
            "source": self.source.name if self.source else None,
            **extra,
        }

    def status(self):
        return self._status.copy()

    def _fingerprint(self):
        info = self.source.stat()
        location = self.source.name if self.year is None else os.path.normcase(str(self.source))
        return repr((INDEX_VERSION, location, info.st_dev, info.st_ino,
                     info.st_size, info.st_mtime_ns, info.st_ctime_ns))

    def _open_index(self):
        connection = sqlite3.connect(f"{self.index.as_uri()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        return connection

    def _cached_skipped(self, fingerprint):
        if not self.index.is_file():
            return None
        try:
            with closing(self._open_index()) as connection:
                found = connection.execute(
                    "SELECT skipped FROM source_info WHERE fingerprint = ?", (fingerprint,)
                ).fetchone()
                return found["skipped"] if found else None
        except sqlite3.DatabaseError:
            return None

    def prepare(self):
        try:
            self.index.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            lock = self.index.with_suffix(".lock")
            with _build_lock(lock):
                fingerprint = self._fingerprint()
                skipped = self._cached_skipped(fingerprint)
                if skipped is None:
                    skipped = self._build(fingerprint)
            self._update("ready", "資料庫已就緒", skipped=skipped)
        except DataError as error:
            self._update("error", str(error))
        except FileNotFoundError:
            self._update("error", "找不到 MDB 檔案或資料庫讀取程式。請確認檔案位置後重新整理。")
        except OSError:
            self._update("error", "無法讀取 MDB 或建立本機索引。請確認檔案權限和可用空間後重新整理。")
        except sqlite3.Error:
            self._update("error", "無法建立查詢索引。請確認可用磁碟空間後重開。")

    def _build(self, fingerprint):
        reader = self.reader if self.reader else (_mdb_export(),)
        command = [*reader, str(self.source), "PubEvent"]
        with tempfile.TemporaryDirectory(prefix="build-", dir=self.index.parent) as work:
            temp_index = Path(work) / self.index.name
            temp_index.touch(mode=0o600)
            with closing(sqlite3.connect(temp_index)) as connection:
                connection.executescript("PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;" + SCHEMA)
                skipped = self._read_mdb(command, connection)
                self._update("loading", "正在建立查詢索引…")
                connection.execute("INSERT INTO source_info VALUES (?, ?)", (fingerprint, skipped))
                connection.commit()
                connection.execute("CREATE INDEX by_datetime ON records (datetime)")
                for column in ("actor", *FACETS):
                    connection.execute(f"CREATE INDEX by_{column} ON records ({column}, datetime)")
                connection.execute("ANALYZE")
                connection.commit()
            if self._fingerprint() != fingerprint:
                raise DataError("建立索引期間 MDB 已更新，請重開查詢網頁以重新讀取。")
            with temp_index.open("r+b") as index_file:
                os.fsync(index_file.fileno())
            try:
                temp_index.replace(self.index)
            except PermissionError as error:
                raise DataError("無法更新本機索引。請等待查詢或匯出完成，確認索引資料夾可寫入後重新整理。") from error
        return skipped

    def _read_mdb(self, command, connection):
        with subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ) as process:
            try:
                with TextIOWrapper(process.stdout, encoding="utf-8", newline="") as output:
                    skipped = self._import_rows(csv.reader(output, strict=True), connection)
                result = process.wait()
            except (UnicodeError, csv.Error) as error:
                raise DataError("無法讀取 MDB 的文字資料。請確認檔案完整。") from error
            finally:
                if process.poll() is None:
                    process.kill()
            if result:
                raise DataError("無法讀取 MDB 的 PubEvent 資料表。請確認檔案完整。")
        return skipped

    def _import_rows(self, reader, connection):
        header = next(reader, [])
        try:
            select = itemgetter(*(header.index(column) for column in MDB_COLUMNS))
        except ValueError as error:
            raise DataError("MDB 缺少門禁事件資料表或必要欄位。") from error

        actors = {}
        pending_actors = []
        pending_records = []
        year_counts = Counter()
        options = defaultdict(set)
        skipped = 0
        for position, raw in enumerate(reader, 1):
            if position % 50_000 == 0:
                self._update("loading", f"正在整理門禁記錄（已讀取 {position:,} 筆）…")
            if len(raw) != len(header):
                raise DataError("MDB 含有欄位數不完整的記錄，無法建立索引。")
            event_date, event_time, device, door, department, name, employee, card, event, fallback = select(raw)
            day, hour = _event_date(event_date), _event_time(event_time)
            if day is None or hour is None:
                skipped += 1
                continue
            year = int(day[:4])
            year_counts[year] += 1
            device, door, department = device.strip(), door.strip(), department.strip()
            event = event.strip() or fallback.strip()
            for facet, value in (("device", device), ("door", door), ("department", department), ("event", event)):
                if value:
                    options[year, facet].add(value)

            name, employee, card = name.strip(), employee.strip(), card.strip()
            actor_key = name, employee, card
            actor = actors.get(actor_key)
            if actor is None:
                actor = actors[actor_key] = len(actors) + 1
                pending_actors.append((actor, name, employee, card, name.casefold(), card.casefold()))
            pending_records.append((f"{day}T{hour}", device, door, department, actor, event))
            if len(pending_records) >= 10_000:
                self._flush(connection, pending_actors, pending_records)

        self._flush(connection, pending_actors, pending_records)
        connection.executemany("INSERT INTO year_counts VALUES (?, ?)", year_counts.items())
        connection.executemany(
            "INSERT INTO options VALUES (?, ?, ?)",
            ((year, facet, value) for (year, facet), values in options.items() for value in values),
        )
        return skipped

    @staticmethod
    def _flush(connection, actors, records):
        connection.executemany("INSERT INTO actors VALUES (?, ?, ?, ?, ?, ?)", actors)
        connection.executemany(
            "INSERT INTO records (datetime, device, door, department, actor, event) VALUES (?, ?, ?, ?, ?, ?)",
            records,
        )
        actors.clear()
        records.clear()

    def options(self, year=None):
        with closing(self._open_index()) as connection:
            years = [row[0] for row in connection.execute("SELECT year FROM year_counts ORDER BY year DESC")]
            year = int(year) if year is not None else (years[0] if years else None)
            facets = {key: [] for key in FACETS.values()}
            for row in connection.execute(
                "SELECT facet, value FROM options WHERE year = ? ORDER BY value COLLATE NOCASE, value",
                (year,),
            ):
                facets[FACETS[row["facet"]]].append(row["value"])
        return {"source": self.source.name, "years": years, "year": year, **facets}

    @staticmethod
    def _where(connection, filters):
        year = filters.get("year")
        if not year:
            year = connection.execute("SELECT MAX(year) FROM year_counts").fetchone()[0]
        year_prefix = f"{int(year):04d}" if year else None
        conditions, params = [], []
        if year_prefix:
            conditions.extend(["r.datetime >= ?", "r.datetime <= ?"])
            params.extend([f"{year_prefix}-01-01T00:00:00", f"{year_prefix}-12-31T23:59:59"])
        for field, operator in (("start", ">="), ("end", "<=")):
            if filters.get(field):
                conditions.append(f"r.datetime {operator} ?")
                params.append(filters[field])
        for facet in FACETS:
            if filters.get(facet):
                conditions.append(f"r.{facet} = ?")
                params.append(filters[facet])
        actor_conditions = []
        for field in ("name", "card"):
            if filters.get(field):
                actor_conditions.append(f"instr(search_{field}, ?) > 0")
                params.append(filters[field].casefold())
        if actor_conditions:
            conditions.append(f"r.actor IN (SELECT id FROM actors WHERE {' AND '.join(actor_conditions)})")

        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        return where, params, year

    def search(self, filters, page, page_size):
        with closing(self._open_index()) as connection:
            where, params, year = self._where(connection, filters)
            if any(value for key, value in filters.items() if key != "year"):
                total = connection.execute(f"SELECT COUNT(*) FROM records r{where}", params).fetchone()[0]
            else:
                count = connection.execute("SELECT count FROM year_counts WHERE year = ?", (year,)).fetchone()
                total = count[0] if count else 0
            rows = connection.execute(
                f"SELECT {RECORD_COLUMNS} FROM records r JOIN actors a ON a.id = r.actor"
                f"{where} ORDER BY r.datetime DESC, r.id DESC LIMIT ? OFFSET ?",
                (*params, page_size, (page - 1) * page_size),
            )
            return {"rows": [dict(row) for row in rows], "total": total, "page": page, "pageSize": page_size}

    def iter_rows(self, filters):
        with closing(self._open_index()) as connection:
            where, params, _ = self._where(connection, filters)
            rows = connection.execute(
                f"SELECT {RECORD_COLUMNS} FROM records r JOIN actors a ON a.id = r.actor"
                f"{where} ORDER BY r.datetime DESC, r.id DESC",
                params,
            )
            yield from (dict(row) for row in rows)


class AccessData:
    """Serve published snapshots and coordinate lazy imports for local MDB files."""

    def __init__(self, project, source_dir=None, cache_dir=None, reader=None):
        project = Path(project).resolve()
        self.source_dir = Path(source_dir).absolute() if source_dir is not None else None
        self.cache_dir = Path(cache_dir).absolute() if cache_dir is not None else project / ".cache"
        if self.source_dir is not None:
            namespace = os.path.normcase(str(self.source_dir)).encode("utf-8", errors="surrogatepass")
            self.cache_dir /= hashlib.sha256(namespace).hexdigest()[:16]
        self.reader = (os.fspath(reader),) if isinstance(reader, (str, os.PathLike)) else tuple(reader or ())
        self._local = None
        self._state_lock = Lock()
        self._catalog_job = None
        self._scan_finished = float("-inf")
        self._revision = 0
        self._manual_sources = {}
        self._stores = {}
        self._requests = {}
        self._catalog = self._catalog_state("loading", "正在搜尋年度 MDB 檔案…")
        if self.source_dir is None:
            self._local = self._find_local_file(project)

    def _find_local_file(self, project):
        preferred = project / "HAMS_2026.mdb"
        if preferred.is_file():
            return _AccessFile(preferred, self.cache_dir / "access.sqlite3", self.reader)
        try:
            candidates = [p for p in project.iterdir() if p.suffix.lower() == ".mdb" and p.is_file()]
        except OSError:
            self._catalog = self._catalog_state("error", "無法讀取專案資料夾。請確認檔案權限。")
            return None
        if len(candidates) == 1:
            return _AccessFile(candidates[0], self.cache_dir / "access.sqlite3", self.reader)
        message = "專案資料夾內找不到 MDB 檔案。"
        if candidates:
            message = "專案資料夾內有多個 MDB，請將要查詢的檔案命名為 HAMS_2026.mdb。"
        self._catalog = self._catalog_state("error", message)
        return None

    def _catalog_state(self, state, message):
        years = sorted(self._stores, reverse=True)
        year = years[0] if years else None
        catalog = {
            "state": state,
            "message": message,
            "source": self._stores[year].source.name if years else None,
            "years": years,
            "year": year,
        }
        if self.source_dir is not None:
            catalog["snapshots"] = {key: store.snapshot for key, store in self._stores.items() if store.snapshot}
        return catalog

    def status(self, year=None):
        if self._local is not None:
            return self._local.status()
        with self._state_lock:
            if year is not None and self._catalog["state"] == "ready":
                return self._selected_store(year).status()
            return {**self._catalog, "years": self._catalog["years"][:]}

    def refresh(self, wait=False, force=False):
        """Scan the source directory without loading any MDB files."""
        if self.source_dir is None:
            return self.status()
        with self._state_lock:
            job = self._catalog_job
            if job is None or not job.is_alive():
                if not force and monotonic() - self._scan_finished < 2:
                    return {**self._catalog, "years": self._catalog["years"][:]}
                if self._catalog["state"] != "ready":
                    self._catalog = self._catalog_state("loading", "正在搜尋年度 MDB 檔案…")
                job = self._catalog_job = Thread(
                    target=self._scan_sources, args=(force,), name="mdb-catalog", daemon=True
                )
                job.start()
        if wait:
            job.join()
        return self.status()

    def _scan_snapshots(self):
        folder = self.source_dir / SNAPSHOT_DIRNAME
        snapshots = {}
        try:
            manifests = list(folder.iterdir())
        except OSError:
            return snapshots
        for manifest in manifests:
            match = SNAPSHOT_MANIFEST.fullmatch(manifest.name)
            if not match or not int(match[1]):
                continue
            try:
                payload = json.loads(manifest.read_text(encoding="utf-8"))
                if not isinstance(payload, dict):
                    continue
                filename = payload.get("file")
                synced_at = payload.get("synced_at")
                skipped = payload.get("skipped")
                if not isinstance(filename, str) or not re.fullmatch(
                    rf"HAMS_{match[1]}-[a-f0-9]{{32}}\.sqlite3", filename
                ):
                    continue
                if not isinstance(synced_at, str) or len(synced_at) > 40:
                    continue
                if datetime.fromisoformat(synced_at.replace("Z", "+00:00")).tzinfo is None:
                    continue
                if type(skipped) is not int or skipped < 0:
                    continue
                index = folder / filename
                if index.is_file():
                    snapshots[int(match[1])] = (
                        index, {"id": index.stem, "syncedAt": synced_at, "skipped": skipped}
                    )
            except (OSError, ValueError):
                continue
        return snapshots

    def _scan_sources(self, force):
        sources = {}
        manual_sources = {}
        message = ""
        try:
            with os.scandir(self.source_dir) as directory:
                for entry in directory:
                    match = YEAR_FILENAME.fullmatch(entry.name)
                    if match and int(match[1]):
                        try:
                            if not entry.is_file():
                                continue
                            info = entry.stat()
                        except OSError:
                            continue
                        year = int(match[1])
                        sources[year] = Path(entry.path)
                        manual_sources[year] = (
                            sources[year], info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns
                        )
        except OSError:
            message = f"無法讀取資料夾 {self.source_dir}。請確認資料夾存在，且有讀取權限。"
        snapshots = self._scan_snapshots()
        with self._state_lock:
            current = {}
            for year, source in sources.items():
                store = self._stores.get(year)
                if store is None or store.source != source or store.snapshot:
                    store = _AccessFile(source, self.cache_dir / f"access-{year}.sqlite3", self.reader, year)
                current[year] = store
            for year, store in self._stores.items():
                if store.snapshot and year not in snapshots:
                    try:
                        if store.index.is_file():
                            current[year] = store
                    except OSError:
                        current[year] = store
            for year, (index, snapshot) in snapshots.items():
                store = self._stores.get(year)
                if store is None or store.index != index:
                    source = sources.get(year, self.source_dir / f"HAMS_{year:04d}.mdb")
                    store = _AccessFile(source, index, year=year, snapshot=snapshot)
                current[year] = store
            self._stores = current
            stores = set(current.values())
            self._requests = {store: job for store, job in self._requests.items() if store in stores}
            if force or manual_sources != self._manual_sources:
                self._revision += 1
            self._manual_sources = manual_sources
            self._scan_finished = monotonic()
            if current:
                self._catalog = self._catalog_state("ready", "已找到年度資料檔")
            else:
                message = message or f"在 {self.source_dir} 找不到年度資料。請先同步或放入 HAMS_年份.mdb。"
                self._catalog = self._catalog_state("error", message)

    def _selected_store(self, year):
        if self._catalog["state"] != "ready":
            raise DataError(self._catalog["message"])
        selected = int(year) if year is not None else self._catalog["year"]
        if selected not in self._stores:
            raise DataError(f"找不到 {selected} 年的 MDB 檔案。請重新整理網頁。")
        return self._stores[selected]

    def _year_job(self, year):
        with self._state_lock:
            store = self._selected_store(year)
            if store.snapshot:
                return store, None
            job = self._requests.get(store)
            if job is None or (job[0] != self._revision and not job[1].is_alive()):
                store.mark_loading()
                worker = Thread(target=store.prepare, name=f"mdb-index-{store.year}", daemon=True)
                self._requests[store] = (self._revision, worker)
                worker.start()
            else:
                worker = job[1]
            return store, worker

    def request_year(self, year=None):
        if self.source_dir is None:
            return self.status()
        store, _ = self._year_job(year)
        return store.status()

    def prepare(self, year=None):
        if self.source_dir is None:
            if self._local is not None:
                self._local.prepare()
            return
        if year is None or self.status()["state"] != "ready":
            self.refresh(wait=True)
        try:
            _, worker = self._year_job(year)
        except DataError:
            return
        if worker is not None:
            worker.join()

    def _ready_store(self, year):
        if self.source_dir is None:
            store = self._local
        else:
            store, worker = self._year_job(year)
            if worker is not None:
                worker.join()
        status = store.status() if store is not None else self.status()
        if status["state"] != "ready":
            raise DataError(status["message"])
        return store

    def options(self, year=None):
        store = self._ready_store(year)
        result = store.options(store.year if store.year is not None else year)
        if self.source_dir is not None:
            result["years"] = self.status()["years"]
        if store.snapshot:
            result["snapshot"] = store.snapshot
        return result

    def search(self, filters, page, page_size):
        store = self._ready_store(filters.get("year"))
        if store.year is not None:
            filters = {**filters, "year": store.year}
        result = store.search(filters, page, page_size)
        if store.snapshot:
            result["snapshot"] = store.snapshot
        return result

    def iter_rows(self, filters):
        store = self._ready_store(filters.get("year"))
        if store.year is not None:
            filters = {**filters, "year": store.year}
        yield from store.iter_rows(filters)
