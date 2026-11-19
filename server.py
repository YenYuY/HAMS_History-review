import argparse
import hashlib
import http.client
import json
import os
import re
import shutil
import socket
import threading
import urllib.parse
import webbrowser
from datetime import date, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryFile

from data import AccessData, DataError
from excel_export import write_workbook


ROOT = Path(__file__).resolve().parent
LOOPBACK = "127.0.0.1"
DEFAULT_PAGE_SIZE = 15
ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/favicon.svg": ("favicon.svg", "image/svg+xml"),
}
FILTER_FIELDS = ("name", "department", "card", "device", "door", "event")
OPTION_FIELDS = ("departments", "devices", "doors", "events")
API_PATHS = {"/api/options", "/api/records", "/api/export"}
DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")
DATETIME_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?")
INVALID_FILENAME = re.compile(r'[\x00-\x1f\x7f<>:"/\\|?*]')
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)


def positive_int(value, label, upper):
    if not re.fullmatch(r"[0-9]{1,9}", value) or not 1 <= int(value) <= upper:
        raise ValueError(f"{label}格式不正確。")
    return int(value)


def local_timestamp(value, is_end):
    if not value:
        return ""
    try:
        if DATE_PATTERN.fullmatch(value):
            return date.fromisoformat(value).isoformat() + ("T23:59:59" if is_end else "T00:00:00")
        if DATETIME_PATTERN.fullmatch(value):
            parsed = datetime.fromisoformat(value)
            if is_end and len(value) == 16:
                parsed = parsed.replace(second=59)
            return parsed.isoformat(timespec="seconds")
    except ValueError:
        pass
    raise ValueError("日期時間格式不正確。")


def requested_year(params):
    value = params.get("year", "")
    return positive_int(value, "年份", 9999) if value else None


def read_filters(params):
    if any(len(params.get(key, "")) > 1024 for key in FILTER_FIELDS):
        raise ValueError("查詢條件過長。")
    filters = {key: params.get(key, "") for key in FILTER_FIELDS}
    for key in ("name", "card"):
        filters[key] = filters[key].strip()
    filters["year"] = requested_year(params)
    filters["start"] = local_timestamp(params.get("start", ""), is_end=False)
    filters["end"] = local_timestamp(params.get("end", ""), is_end=True)
    if filters["start"] and filters["end"] and filters["start"] > filters["end"]:
        raise ValueError("開始日期時間必須早於或等於結束日期時間。")
    return filters


class RecordServer(ThreadingHTTPServer):
    allow_reuse_address = os.name != "nt"

    def __init__(self, port, data, app_id, multi_year=False):
        self.data = data
        self.app_id = app_id
        self.multi_year = multi_year
        super().__init__((LOOPBACK, port), Handler)

    def server_bind(self):
        if os.name == "nt":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Access logs would contain staff names and card numbers in query strings.
        pass

    def headers_for(self, status, content_type, size, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self.end_headers()

    def send_json(self, payload, status=HTTPStatus.OK, extra=None):
        encoded = json.dumps(payload, ensure_ascii=False).encode()
        self.headers_for(status, "application/json; charset=utf-8", len(encoded), extra)
        self.wfile.write(encoded)

    def do_GET(self):
        path = ""
        try:
            port = self.server.server_port
            if self.headers.get("Host") not in (f"{LOOPBACK}:{port}", f"localhost:{port}"):
                self.send_json({"message": "請從本機網址開啟門禁紀錄查詢。"}, HTTPStatus.FORBIDDEN)
                return
            parts = urllib.parse.urlsplit(self.path)
            path = parts.path
            if path.startswith("/api/"):
                if self.headers.get("Sec-Fetch-Site") in ("cross-site", "same-site"):
                    self.send_json({"message": "查詢請從門禁紀錄頁面發出。"}, HTTPStatus.FORBIDDEN)
                    return
                if len(parts.query) > 16384:
                    raise ValueError("查詢條件過長。")
                params = dict(urllib.parse.parse_qsl(parts.query, max_num_fields=24))
                self.handle_api(path, params)
            elif path in ASSETS:
                name, content_type = ASSETS[path]
                content = (ROOT / "web" / name).read_bytes()
                self.headers_for(HTTPStatus.OK, content_type, len(content))
                self.wfile.write(content)
            else:
                self.send_json({"message": "找不到頁面。"}, HTTPStatus.NOT_FOUND)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except DataError as exc:
            self.send_json({"message": str(exc)}, HTTPStatus.UNPROCESSABLE_ENTITY)
        except (KeyError, ValueError) as exc:
            message = str(exc) if isinstance(exc, ValueError) and str(exc).endswith("。") else "查詢條件不正確。"
            self.send_json({"message": message}, HTTPStatus.BAD_REQUEST)
        except FileNotFoundError:
            status = self.server.data.status()
            self.send_json({"message": status.get("message") if status.get("state") != "ready" else "找不到要求的檔案。"},
                           HTTPStatus.NOT_FOUND)
        except Exception:
            print(f"處理 {path if path in ASSETS or path in API_PATHS else '請求'} 時發生錯誤。", flush=True)
            self.send_json({"message": "暫時無法完成，請再試一次。"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def handle_api(self, path, params):
        data = self.server.data
        if path == "/api/status":
            refresh = params.get("refresh") == "1"
            if self.server.multi_year and (refresh or params.get("live") == "1"):
                data.refresh(wait=True, force=refresh)
            self.send_json(data.status(), extra={"X-Access-Query-App": self.server.app_id})
            return
        if path not in API_PATHS:
            self.send_json({"message": "找不到查詢功能。"}, HTTPStatus.NOT_FOUND)
            return

        status = data.refresh(wait=True) if self.server.multi_year else data.status()
        if status["state"] != "ready":
            self.send_json(status, HTTPStatus.SERVICE_UNAVAILABLE)
            return

        year = requested_year(params)
        if self.server.multi_year:
            year = year or status.get("year")
            year_status = data.request_year(year)
            if year_status["state"] != "ready":
                code = (HTTPStatus.SERVICE_UNAVAILABLE if year_status["state"] == "loading"
                        else HTTPStatus.UNPROCESSABLE_ENTITY)
                self.send_json(year_status, code)
                return

        if path == "/api/options":
            result = data.options(year)
            for field in OPTION_FIELDS:
                result[field] = [
                    {"value": value, "label": value} if isinstance(value, str) else value
                    for value in result[field]
                ]
            self.send_json(result)
        elif path == "/api/records":
            page = positive_int(params.get("page", "1"), "頁碼", 1_000_000)
            size = positive_int(params.get("pageSize", str(DEFAULT_PAGE_SIZE)), "每頁筆數", 300)
            filters = read_filters(params)
            filters["year"] = year
            self.send_json(data.search(filters, page, size))
        elif path == "/api/export":
            filters = read_filters(params)
            filters["year"] = year
            self.export(data, filters, params.get("title", ""))

    def export(self, data, filters, title):
        year = filters["year"]
        suffix = f"_{year}" if year else ""
        label = INVALID_FILENAME.sub("_", title[:60]).strip(" .") or "歷史資料一覽表"
        filename = urllib.parse.quote(f"{label}{suffix}.xlsx")
        with TemporaryFile(mode="w+b") as output:
            write_workbook(output, data.iter_rows(filters))
            length = output.tell()
            output.seek(0)
            self.headers_for(
                HTTPStatus.OK, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", length,
                {"Content-Disposition": f"attachment; filename=access-records{suffix}.xlsx; filename*=UTF-8''{filename}"},
            )
            shutil.copyfileobj(output, self.wfile, length=262144)


def is_this_app(port, app_id):
    connection = http.client.HTTPConnection(LOOPBACK, port, timeout=0.5)
    try:
        connection.request("GET", "/api/status")
        return connection.getresponse().getheader("X-Access-Query-App") == app_id
    except (OSError, http.client.HTTPException):
        return False
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description="啟動本機門禁紀錄查詢網頁。")
    parser.add_argument("--port", type=int, default=8765, help="指定埠號，使用 0 可自動選擇。")
    parser.add_argument("--open", action="store_true", help="使用預設瀏覽器開啟網頁。")
    parser.add_argument("--source-dir", type=Path, help="年度 MDB 檔所在的資料夾。")
    parser.add_argument("--cache-dir", type=Path, help="本機查詢索引的資料夾。")
    parser.add_argument("--java", default="java", help="執行 Java 的程式路徑。")
    parser.add_argument("--reader-jar", type=Path, help="可攜式 MDB 讀取器的 JAR 路徑。")
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("埠號必須在 0 到 65535 之間。")

    reader = None
    if args.reader_jar:
        reader = [args.java, "-Xmx256m", "-jar", str(args.reader_jar.resolve())]
    data = AccessData(ROOT, source_dir=args.source_dir, cache_dir=args.cache_dir, reader=reader)
    identity = str((ROOT, args.source_dir, args.cache_dir, reader)).encode()
    app_id = hashlib.sha256(identity).hexdigest()[:20]
    ports = [args.port] if args.port == 0 else range(args.port, min(args.port + 10, 65536))
    for port in ports:
        try:
            server = RecordServer(port, data, app_id, multi_year=args.source_dir is not None)
            break
        except OSError:
            if port and is_this_app(port, app_id):
                url = f"http://{LOOPBACK}:{port}/"
                print(f"門禁紀錄查詢已啟動 {url}", flush=True)
                if args.open:
                    webbrowser.open(url)
                return
    else:
        parser.exit(1, "找不到可用的本機埠號，請使用 --port 0 重試。\n")

    url = f"http://{LOOPBACK}:{server.server_port}/"
    print(f"門禁紀錄查詢 {url}\n按 Ctrl+C 結束。", flush=True)
    threading.Thread(target=data.prepare, name="mdb-index", daemon=True).start()
    if args.open:
        webbrowser.open(url)
    with server:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\n已停止門禁紀錄查詢。")


if __name__ == "__main__":
    main()
