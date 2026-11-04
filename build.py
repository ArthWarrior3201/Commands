#!/usr/bin/env python3
"""
Builds the single-file downloads for a GitHub release:

    dist/commands.cmd   Windows        (batch launcher + Python in one file)
    dist/commands.sh    macOS / Linux  (sh launcher + Python in one file)
    dist/commands.py    any system     (python commands.py)

Each file carries the whole of Commands (the commands package, the SDKs,
the API docs) as a zip inside it. The first run unpacks it into the
Commands settings folder (%APPDATA%\\Commands\\app or ~/.config/commands/app)
and later runs just start it, so people download one file.

Run:  python build.py   then upload all three files from dist/ to a release
tagged v<VERSION> (VERSION is in commands/__init__.py).
"""
import base64
import hashlib
import io
import os
import re
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(ROOT, "dist")
INCLUDE = ["commands", "sdk", "docs", "run.py", "commands.cmd", "commands.sh", "README.md", "LICENSE"]

STUB = r'''"""Commands {version} - a command prompt with the CMD feel, Odus built in,
and the Commands API. https://github.com/ArthWarrior3201/Commands

This one file holds all of Commands. The first run unpacks it into your
Commands settings folder; after that it starts straight away.
Needs Python 3.9 or newer.
"""
import base64, io, os, shutil, sys, zipfile

VERSION = "{version}"
BUILD = "{build}"
PAYLOAD = (
{payload}
)


def _home():
    if os.name == "nt":
        base = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "Commands")
    else:
        base = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "commands")
    return os.path.join(base, "app")


def _unpack():
    home = _home()
    target = os.path.join(home, VERSION + "-" + BUILD)
    if os.path.isfile(os.path.join(target, "run.py")):
        return target
    tmp = target + ".unpacking-" + str(os.getpid())
    with zipfile.ZipFile(io.BytesIO(base64.b64decode("".join(PAYLOAD)))) as z:
        z.extractall(tmp)
    try:
        os.replace(tmp, target)
    except OSError:  # another window unpacked it at the same moment
        shutil.rmtree(tmp, ignore_errors=True)
    for old in os.listdir(home):  # tidy older versions
        p = os.path.join(home, old)
        if p != target and os.path.isdir(p) and ".unpacking-" not in old:
            shutil.rmtree(p, ignore_errors=True)
    return target


def _main():
    if sys.version_info < (3, 9):
        sys.stderr.write("Commands needs Python 3.9 or newer.\n")
        return 1
    app = _unpack()
    os.environ["COMMANDS_LAUNCHER"] = os.path.abspath(sys.argv[0])
    sys.path.insert(0, app)
    from commands.__main__ import main
    return main(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(_main())
'''

CMD_HEAD = "@echo off & goto :commands_batch\n"
CMD_TAIL = r"""
r'''
:commands_batch
setlocal
where py >nul 2>nul && goto :commands_py
python -x "%~f0" %*
goto :commands_end
:commands_py
py -3 -x "%~f0" %*
:commands_end
exit /b %errorlevel%
'''
"""
SH_HEAD = ("#!/bin/sh\n"
           "''''command -v python3 >/dev/null 2>&1 && exec python3 \"$0\" \"$@\"; exec python \"$0\" \"$@\" #'''\n")


def payload():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for item in INCLUDE:
            path = os.path.join(ROOT, item)
            if os.path.isfile(path):
                z.write(path, item)
                continue
            for dirpath, dirs, files in os.walk(path):
                dirs[:] = sorted(d for d in dirs if d != "__pycache__")
                for f in sorted(files):
                    if f.endswith((".pyc", ".pyo")):
                        continue
                    full = os.path.join(dirpath, f)
                    z.write(full, os.path.relpath(full, ROOT).replace(os.sep, "/"))
    return buf.getvalue()


def main():
    src = open(os.path.join(ROOT, "commands", "__init__.py"), encoding="utf-8").read()
    version = re.search(r'^VERSION = "([^"]+)"', src, re.M).group(1)
    data = payload()
    b64 = base64.b64encode(data).decode()
    lines = "\n".join(f'    "{b64[i:i + 100]}"' for i in range(0, len(b64), 100))
    body = STUB.format(version=version, build=hashlib.sha256(data).hexdigest()[:10], payload=lines)
    os.makedirs(DIST, exist_ok=True)

    with open(os.path.join(DIST, "commands.py"), "w", encoding="utf-8", newline="\n") as f:
        f.write("#!/usr/bin/env python3\n" + body)
    with open(os.path.join(DIST, "commands.cmd"), "w", encoding="utf-8", newline="\r\n") as f:
        f.write(CMD_HEAD + body.rstrip("\n") + "\n" + CMD_TAIL)  # cmd needs Windows line endings for labels
    # line 2 of the .sh is the docstring to Python, so the real docstring becomes comments
    m = re.match(r'"""(.*?)"""\n', body, re.S)
    doc = "".join(f"# {l}".rstrip() + "\n" for l in m.group(1).strip("\n").split("\n"))
    sh_path = os.path.join(DIST, "commands.sh")
    with open(sh_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(SH_HEAD + doc + body[m.end():])
    os.chmod(sh_path, 0o755)

    print(f"Built Commands v{version}:")
    for name in ("commands.cmd", "commands.sh", "commands.py"):
        print(f"  dist/{name}  ({os.path.getsize(os.path.join(DIST, name)) // 1024} KB)")
    print(f"Upload all three to a GitHub release tagged v{version}.")


if __name__ == "__main__":
    main()
