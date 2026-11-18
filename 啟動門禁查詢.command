#!/bin/sh

set -eu
cd -- "$(dirname -- "$0")"

# Finder 啟動的終端機不一定包含 Homebrew 的安裝路徑。
export PATH="${PATH:+$PATH:}/opt/homebrew/bin:/usr/local/bin"

if ! command -v python3 >/dev/null 2>&1 ||
   ! python3 -c 'import sqlite3, sys; sys.exit(sys.version_info < (3, 9))' >/dev/null 2>&1; then
    printf '%s\n' '請先安裝 Python 3.9 或更新版本，再重新開啟。' >&2
    exit 1
fi

if ! command -v mdb-export >/dev/null 2>&1; then
    printf '%s\n' '找不到 MDB Tools。請在終端機執行 brew install mdbtools，安裝後再重新開啟。' >&2
    exit 1
fi

exec python3 server.py --open
