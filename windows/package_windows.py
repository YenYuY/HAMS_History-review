"""Build the Windows x64 ZIP using only explicitly listed application files."""

import hashlib
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
BUNDLE = PurePosixPath("HAMSAccessQuery")
OUTPUT = ROOT / "dist" / "門禁紀錄查詢-Windows-x64.zip"
DOWNLOADS = ROOT / ".cache" / "windows-runtime"
PYTHON_URL = "https://www.python.org/ftp/python/3.13.17/python-3.13.17-embed-amd64.zip"
PYTHON_SHA256 = "9e35319bffedab8b36ad44d8f377cb4f1733bcec257a0b6d04d94f01486ddb38"
JAVA_RELEASE = "https://github.com/adoptium/temurin21-binaries/releases/download/jdk-21.0.13%2B9/"
JAVA_URL = JAVA_RELEASE + "OpenJDK21U-jre_x64_windows_hotspot_21.0.13_9.zip"
JAVA_SOURCE = JAVA_RELEASE + "OpenJDK21U-jdk-sources_21.0.13_9.tar.gz"
JAVA_SHA256 = "2332c0b4cff6bed722da3567c91d5e47568bcba863fa4945d0a8d6fc76158628"
WEB_ASSETS = ("index.html", "styles.css", "app.js", "favicon.svg")
APP_FILES = ("server.py", "data.py", "excel_export.py", "sync_mdb.py", *(f"web/{asset}" for asset in WEB_ASSETS))
WINDOWS_SCRIPTS = ("Start.cmd", "Install-SyncTask.cmd", "Install-SyncTask.ps1", "Sync-Now.cmd")
BRIDGE_FILES = ("mdb-reader.jar", "jackcess.jar", "jackcess-LICENSE.txt")


def sha256(path):
    result = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def download(url, expected):
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    destination = DOWNLOADS / url.rsplit("/", 1)[-1]
    if destination.is_file() and sha256(destination) == expected:
        return destination
    with tempfile.TemporaryDirectory(dir=DOWNLOADS) as folder:
        staging = Path(folder) / destination.name
        with urllib.request.urlopen(url, timeout=60) as response, staging.open("wb") as output:
            shutil.copyfileobj(response, output)
        if sha256(staging) != expected:
            raise ValueError(f"Checksum mismatch: {destination.name}")
        staging.replace(destination)
    return destination


def add_runtime(archive, source, destination, strip_prefix=False):
    with zipfile.ZipFile(source) as contents:
        for entry in contents.infolist():
            if entry.is_dir():
                continue
            relative = PurePosixPath(entry.filename)
            if relative.is_absolute() or ".." in relative.parts or "\\" in entry.filename:
                raise ValueError(f"Unexpected runtime archive path: {entry.filename}")
            if strip_prefix:
                relative = PurePosixPath(*relative.parts[1:])
            target = (BUNDLE / "runtime" / destination / relative).as_posix()
            with contents.open(entry) as member:
                if relative == PurePosixPath("python313._pth"):
                    # Embedded Python searches paths relative to the ._pth location.
                    archive.writestr(target, member.read().rstrip() + b"\r\n..\\..\r\n")
                else:
                    with archive.open(target, mode="w") as output:
                        shutil.copyfileobj(member, output)


def write_zip(target, python, java, bridge):
    notices = (
        "CPython 3.13.17 (Python Software Foundation License)\r\n"
        "License: runtime/python/LICENSE.txt\r\n"
        "Source: https://www.python.org/downloads/release/python-31317/\r\n\r\n"
        "Eclipse Adoptium Temurin JRE 21.0.13+9 (GPLv2 with Classpath Exception)\r\n"
        "Licenses: runtime/jre/legal/\r\n"
        f"Corresponding source: {JAVA_SOURCE}\r\n\r\n"
        "Jackcess 5.0.4 (Apache License 2.0)\r\n"
        "License: runtime/jackcess-LICENSE.txt\r\n"
        "Source: https://github.com/jahlborn/jackcess\r\n"
    )
    with zipfile.ZipFile(target, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for filename in APP_FILES:
            archive.write(ROOT / filename, (BUNDLE / filename).as_posix())
        for filename in BRIDGE_FILES:
            archive.write(bridge / filename, (BUNDLE / "runtime" / filename).as_posix())
        add_runtime(archive, python, "python")
        add_runtime(archive, java, "jre", strip_prefix=True)
        for filename in WINDOWS_SCRIPTS:
            script = (ROOT / "windows" / filename).read_text(encoding="utf-8")
            encoding = "utf-8-sig" if filename.endswith(".ps1") else "ascii"
            archive.writestr((BUNDLE / filename).as_posix(), script.replace("\n", "\r\n").encode(encoding))
        instructions = (ROOT / "windows" / "README-Windows.txt").read_text(encoding="utf-8")
        archive.writestr((BUNDLE / "README.txt").as_posix(), instructions.replace("\n", "\r\n").encode("utf-8-sig"))
        archive.writestr((BUNDLE / "THIRD-PARTY-NOTICES.txt").as_posix(), notices)


def main():
    python = download(PYTHON_URL, PYTHON_SHA256)
    java = download(JAVA_URL, JAVA_SHA256)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=OUTPUT.parent) as temporary:
        folder = Path(temporary)
        bridge = folder / "bridge"
        subprocess.run([sys.executable, str(ROOT / "tools" / "build_reader.py"), str(bridge)], check=True)
        partial = folder / OUTPUT.name
        write_zip(partial, python, java, bridge)
        partial.replace(OUTPUT)
    print(OUTPUT)


if __name__ == "__main__":
    main()
