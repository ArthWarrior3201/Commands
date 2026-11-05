#!/usr/bin/env python3
"""
Builds the downloads for a GitHub release:

    dist/commands.bat             Windows  - standalone, Python is inside, nothing to install
    dist/commands-macos.command   macOS    - standalone (Intel and Apple Silicon); double-click opens Terminal
    dist/commands-linux.sh        Linux    - standalone (x86_64 and ARM64)
    dist/commands-python.zip   any system with Python 3.9+ - the source files, run with: python run.py

The .bat, .command and .sh files are a small launcher followed by a packed Python and
Commands. The first run unpacks them once into the user's folder
(%LOCALAPPDATA%\\Commands\\runtime or ~/.local/share/commands/runtime);
after that Commands starts straight away.

Python comes from:
  Windows        python.org's official embeddable package
  macOS, Linux   python-build-standalone (github.com/astral-sh/python-build-standalone)
Downloads are kept in build-cache/ so the next build is quick.

Run:  python build.py   then upload the four files from dist/ to a release
tagged v<VERSION> (VERSION is in commands/__init__.py).
"""
import base64
import hashlib
import io
import os
import re
import sys
import tarfile
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(ROOT, "dist")
CACHE = os.path.join(ROOT, "build-cache")
APP_FILES = ["commands", "sdk", "docs", "run.py", "README.md", "LICENSE", "PYTHON-REQUIRED.txt"]

WIN_PYTHON = "3.13.12"  # newest 3.13 with an official embeddable package
WIN_URL = f"https://www.python.org/ftp/python/{WIN_PYTHON}/python-{WIN_PYTHON}-embed-amd64.zip"
PBS_TAG, PBS_PYTHON = "20261026", "3.13.18"
PBS_BASE = f"https://github.com/astral-sh/python-build-standalone/releases/download/{PBS_TAG}/"
PBS_TARGETS = {
    "macos": [("x86_64", "x86_64-apple-darwin"), ("aarch64", "aarch64-apple-darwin")],
    "linux": [("x86_64", "x86_64-unknown-linux-gnu"), ("aarch64", "aarch64-unknown-linux-gnu")],
}


def pbs_name(triple):
    return f"cpython-{PBS_PYTHON}+{PBS_TAG}-{triple}-install_only_stripped.tar.gz"


# ------------------------------------------------------------ downloads
def fetch(url, name=None):
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, name or url.rsplit("/", 1)[-1])
    if not os.path.exists(path):
        print(f"  downloading {url.rsplit('/', 1)[-1]} ...")
        req = urllib.request.Request(url, headers={"User-Agent": "Commands-build"})
        with urllib.request.urlopen(req, timeout=300) as r, open(path + ".part", "wb") as f:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
        os.replace(path + ".part", path)
    return path


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def pbs_runtime(triple):
    """A python-build-standalone build, checked against the release's SHA256SUMS."""
    sums = open(fetch(PBS_BASE + "SHA256SUMS", f"SHA256SUMS-{PBS_TAG}"), encoding="utf-8").read()
    name = pbs_name(triple)
    m = re.search(r"^([0-9a-f]{64})\s+" + re.escape(name) + r"$", sums, re.M)
    if not m:
        sys.exit(f"{name} isn't in python-build-standalone {PBS_TAG}")
    path = fetch(PBS_BASE + name)
    if sha256(path) != m.group(1):
        os.remove(path)
        sys.exit(f"{name}: checksum doesn't match, deleted it. Run build.py again.")
    return path


# --------------------------------------------------------------- packing
def app_entries():
    """(path inside the package, file on disk) for every source file."""
    for item in APP_FILES:
        path = os.path.join(ROOT, item)
        if os.path.isfile(path):
            yield item, path
            continue
        for dirpath, dirs, files in os.walk(path):
            dirs[:] = sorted(d for d in dirs if d != "__pycache__")
            for f in sorted(files):
                if not f.endswith((".pyc", ".pyo")):
                    full = os.path.join(dirpath, f)
                    yield os.path.relpath(full, ROOT).replace(os.sep, "/"), full


def app_tar_gz():
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", compresslevel=9) as t:
        for arc, full in app_entries():
            info = t.gettarinfo(full, "app/" + arc)
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mode = 0o644
            with open(full, "rb") as f:
                t.addfile(info, f)
    return buf.getvalue()


def windows_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        with zipfile.ZipFile(fetch(WIN_URL)) as py:
            if "python.exe" not in py.namelist():
                sys.exit("the Windows Python download doesn't look right")
            for n in py.namelist():
                z.writestr("python/" + n, py.read(n))
        for arc, full in app_entries():
            z.write(full, "app/" + arc)
    return buf.getvalue()


def build_id(*parts):
    h = hashlib.sha256()
    for p in parts:
        h.update(p)
    return h.hexdigest()[:10]


# --------------------------------------------------------------- Windows
CMD_HEAD = """@echo off
rem Commands {version} for Windows (commands.bat) - https://github.com/ArthWarrior3201/Commands
rem Standalone: Python is packed inside this file, nothing else to install.
rem The first run unpacks it once into %LOCALAPPDATA%\\Commands\\runtime.
setlocal
set "COMMANDS_LAUNCHER=%~f0"
set "CMDS_RT=%LOCALAPPDATA%\\Commands\\runtime\\{build}"
if not exist "%CMDS_RT%\\python\\python.exe" (
  echo Setting up Commands for the first time...
  powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand {ps}
  if errorlevel 1 (
    echo Commands couldn't unpack itself. Try downloading it again.
    exit /b 1
  )
)
"%CMDS_RT%\\python\\python.exe" "%CMDS_RT%\\app\\run.py" %*
exit /b %errorlevel%
"""

PS_UNPACK = r"""
$ErrorActionPreference = 'Stop'
trap { [Console]::Error.WriteLine('Commands: ' + $_.Exception.Message); exit 1 }
$self = $env:COMMANDS_LAUNCHER; $rt = $env:CMDS_RT
$parent = Split-Path $rt; New-Item -ItemType Directory -Force $parent | Out-Null
$tmp = "$rt.part$PID"; $zip = "$tmp.zip"
$in = [IO.File]::OpenRead($self)
try {
  [void]$in.Seek(OFFSET, 'Begin')
  $out = [IO.File]::Create($zip)
  try {
    $buf = New-Object byte[] 1048576; [long]$left = LENGTH
    while ($left -gt 0) {
      $n = $in.Read($buf, 0, [int][Math]::Min($buf.Length, $left))
      if ($n -le 0) { throw 'this file is incomplete' }
      $out.Write($buf, 0, $n); $left -= $n
    }
  } finally { $out.Close() }
} finally { $in.Close() }
try {
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  [IO.Compression.ZipFile]::ExtractToDirectory($zip, $tmp)
} catch {
  if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp }
  New-Item -ItemType Directory $tmp | Out-Null
  & "$env:SystemRoot\System32\tar.exe" -xf $zip -C $tmp
  if ($LASTEXITCODE) { throw "couldn't unpack ($($_.Exception.Message))" }
}
Remove-Item $zip
if (Test-Path "$rt\python\python.exe") { Remove-Item -Recurse -Force $tmp } else { Move-Item $tmp $rt }
Get-ChildItem $parent -Directory | Where-Object { $_.FullName -ne $rt -and $_.Name -notlike '*.part*' } |
  Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
"""


def build_cmd(version, payload):
    bid = build_id(payload)

    def head(offset):
        ps = PS_UNPACK.replace("OFFSET", str(offset)).replace("LENGTH", str(len(payload)))
        enc = base64.b64encode(ps.encode("utf-16-le")).decode()
        text = CMD_HEAD.format(version=version, build=bid, ps=enc) + "rem ---- packed Python and Commands below ----\n"
        return text.replace("\n", "\r\n").encode("ascii")

    offset = len(head(0))
    for _ in range(5):  # the offset is written inside the header, so settle its length
        h = head(offset)
        if len(h) == offset:
            break
        offset = len(h)
    with open(os.path.join(DIST, "commands.bat"), "wb") as f:
        f.write(h + payload)


# ---------------------------------------------------------- macOS / Linux
SH_HEAD = """#!/bin/sh
# Commands {version} for {osname} - https://github.com/ArthWarrior3201/Commands
# Standalone: Python is packed inside this file, nothing else to install.
# The first run unpacks it once into ~/.local/share/commands/runtime.
set -e
case "$(uname -m)" in
  x86_64|amd64) arch=x86_64; off={x_off}; len={x_len} ;;
  arm64|aarch64) arch=aarch64; off={a_off}; len={a_len} ;;
  *) echo "Commands: $(uname -m) computers aren't supported yet." >&2; exit 1 ;;
esac
self="$0"
case "$self" in /*) ;; *) self="$(pwd)/$self" ;; esac
data="${{XDG_DATA_HOME:-$HOME/.local/share}}/commands/runtime/{build}-$arch"
if [ ! -x "$data/python/bin/python3" ]; then
  echo "Setting up Commands for the first time..." >&2
  tmp="$data.part$$"
  rm -rf "$tmp"
  mkdir -p "$tmp"
  tail -c +$((off + 1)) "$self" | head -c "$len" | tar -xzf - -C "$tmp"
  tail -c +$(({app_off} + 1)) "$self" | head -c {app_len} | tar -xzf - -C "$tmp"
  if [ -x "$data/python/bin/python3" ]; then rm -rf "$tmp"; else rm -rf "$data"; mv "$tmp" "$data"; fi
  for old in "$(dirname "$data")"/*; do
    case "$old" in "$data"|*.part*) ;; *-"$arch") rm -rf "$old" ;; esac
  done
fi
COMMANDS_LAUNCHER="$self" exec "$data/python/bin/python3" "$data/app/run.py" "$@"
exit 1
# ---- packed Python and Commands below ----
"""


def build_sh(version, osname, app):
    pythons = {arch: open(pbs_runtime(triple), "rb").read() for arch, triple in PBS_TARGETS[osname]}
    bid = build_id(app, *pythons.values())

    def head(start):
        x_off = start
        a_off = x_off + len(pythons["x86_64"])
        app_off = a_off + len(pythons["aarch64"])
        return SH_HEAD.format(version=version, osname="macOS" if osname == "macos" else "Linux", build=bid,
                              x_off=x_off, x_len=len(pythons["x86_64"]), a_off=a_off,
                              a_len=len(pythons["aarch64"]), app_off=app_off, app_len=len(app)).encode("ascii")

    start = len(head(0))
    for _ in range(5):
        h = head(start)
        if len(h) == start:
            break
        start = len(h)
    path = os.path.join(DIST, f"commands-{osname}" + (".command" if osname == "macos" else ".sh"))
    with open(path, "wb") as f:
        f.write(h + pythons["x86_64"] + pythons["aarch64"] + app)
    os.chmod(path, 0o755)


# ---------------------------------------------------------------- Python
def build_python_zip(version):
    with zipfile.ZipFile(os.path.join(DIST, "commands-python.zip"), "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for arc, full in app_entries():
            z.write(full, f"Commands-{version}/{arc}")


def main():
    src = open(os.path.join(ROOT, "commands", "__init__.py"), encoding="utf-8").read()
    version = re.search(r'^VERSION = "([^"]+)"', src, re.M).group(1)
    os.makedirs(DIST, exist_ok=True)
    for old in ("commands.py", "commands.sh", "commands.cmd", "commands-macos.sh"):  # names used by older builds
        p = os.path.join(DIST, old)
        if os.path.exists(p):
            os.remove(p)
    print(f"Building Commands v{version}")
    print("Windows (.bat)")
    build_cmd(version, windows_zip())
    app = app_tar_gz()
    for osname in ("macos", "linux"):
        print("macOS (.command)" if osname == "macos" else "Linux (.sh)")
        build_sh(version, osname, app)
    print("Python (.zip)")
    build_python_zip(version)
    print(f"\nBuilt Commands v{version}:")
    for name in ("commands.bat", "commands-macos.command", "commands-linux.sh", "commands-python.zip"):
        size = os.path.getsize(os.path.join(DIST, name))
        print(f"  dist/{name}  ({size / 1e6:.1f} MB)")
    print(f"Upload all four to a GitHub release tagged v{version}.")


if __name__ == "__main__":
    main()
