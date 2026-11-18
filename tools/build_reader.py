"""Build the portable MDB reader used by the Windows package (JDK 11+ required)."""

import hashlib
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


JACKCESS = "https://repo.maven.apache.org/maven2/com/healthmarketscience/jackcess/jackcess/5.0.4/jackcess-5.0.4.jar"
JACKCESS_SHA256 = "19a7494a56f439a57fda85c059b019b665802085cec5ae8186d1e028cc1bdfbc"
LICENSE = "https://www.apache.org/licenses/LICENSE-2.0.txt"
MANIFEST = "Manifest-Version: 1.0\r\nMain-Class: MdbReader\r\nClass-Path: jackcess.jar\r\n\r\n"


def download(url):
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read()


def build(output):
    javac = shutil.which("javac")
    if javac is None:
        raise SystemExit("A JDK with javac is required to build the MDB reader.")
    source = Path(__file__).with_name("MdbReader.java")
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temp:
        directory = Path(temp)
        dependency = directory / "jackcess.jar"
        payload = download(JACKCESS)
        if hashlib.sha256(payload).hexdigest() != JACKCESS_SHA256:
            raise SystemExit("Jackcess SHA-256 did not match the pinned release.")
        dependency.write_bytes(payload)
        subprocess.run(
            [javac, "--release", "11", "-cp", str(dependency), "-d", temp, str(source)],
            check=True,
        )
        with ZipFile(directory / "mdb-reader.jar", "w", compression=ZIP_DEFLATED) as jar:
            jar.writestr("META-INF/MANIFEST.MF", MANIFEST)
            jar.write(directory / "MdbReader.class", "MdbReader.class")
        for name in ("jackcess.jar", "mdb-reader.jar"):
            shutil.copyfile(directory / name, output / name)
        license_text = download(LICENSE)
        (output / "jackcess-LICENSE.txt").write_bytes(license_text)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python3 tools/build_reader.py <output-directory>")
    build(Path(sys.argv[1]))
