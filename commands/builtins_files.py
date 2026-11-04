"""Files and folders: listing, reading, searching, sizes, duplicates, archives, trash."""
import os
import re
import sys
import stat
import time
import hashlib
import shutil
import fnmatch
import zipfile
import tarfile
import subprocess

from . import ui, term, lexer
from .registry import builtin, Usage, expand_globs, resolve

IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"

EXT_ROLE = {
    "exe": (".exe", ".msi", ".bat", ".cmd", ".ps1", ".sh", ".com", ".appimage"),
    "string": (".txt", ".md", ".log", ".pdf", ".doc", ".docx", ".csv", ".json", ".yaml", ".yml", ".toml", ".ini", ".xml"),
    "odus": (".zip", ".7z", ".rar", ".tar", ".gz", ".tgz", ".xz", ".bz2", ".zst", ".iso"),
    "var": (".py", ".js", ".ts", ".tsx", ".jsx", ".c", ".cpp", ".h", ".rs", ".go", ".java", ".cs", ".html", ".css", ".lua"),
    "warn": (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".ico", ".mp4", ".mkv", ".mov", ".mp3", ".wav", ".flac"),
}


def role_for(name, is_dir):
    if is_dir:
        return "path"
    low = name.lower()
    for role, exts in EXT_ROLE.items():
        if low.endswith(exts):
            return role
    return "text"


def is_hidden(path, name):
    if name.startswith("."):
        return True
    if IS_WIN:
        try:
            return bool(os.stat(path, follow_symlinks=False).st_file_attributes & 2)
        except (OSError, AttributeError):
            return False
    return False


def age(ts):
    d = time.time() - ts
    if d < 60:
        return "just now"
    for n, u in ((60, "min"), (3600, "h"), (86400, "d"), (604800, "wk"), (2629800, "mo"), (31557600, "yr")):
        pass
    if d < 3600:
        return f"{int(d // 60)} min ago"
    if d < 86400:
        return f"{int(d // 3600)} h ago"
    if d < 86400 * 30:
        return f"{int(d // 86400)} d ago"
    return time.strftime("%Y-%m-%d", time.localtime(ts))


@builtin("ls", "ll", "la", cat="Files", summary="Colorful listing. -l details, -a hidden, -t newest, -S biggest, --tree.",
         usage="ls [folder or pattern] [-l] [-a] [-t] [-S] [-r] [--tree] [--depth N]")
def cmd_ls(ctx):
    o, rest = ctx.opts({"l|long": bool, "a|all": bool, "t|time": bool, "S|size": bool, "r|reverse": bool,
                        "tree": bool, "depth": int, "1|one": bool, "d|dirs": bool})
    if ctx.name == "ll":
        o["long"] = True
    if ctx.name == "la":
        o["long"] = o["all"] = True
    targets = rest or ["."]
    if o["tree"]:
        for t in targets:
            _tree(resolve(t), o["depth"] or 3, o["all"])
        return 0
    rc = 0
    for t in targets:
        entries = []
        if any(ch in t for ch in "*?["):
            paths = expand_globs([t])
            entries = [(os.path.basename(p), p) for p in paths if os.path.exists(p)]
        elif os.path.isdir(t):
            try:
                entries = [(n, os.path.join(t, n)) for n in os.listdir(t)]
            except OSError as e:
                ui.say(ui.err(f"{t}: {e.strerror}"))
                rc = 1
                continue
        elif os.path.exists(t):
            entries = [(os.path.basename(t), t)]
        else:
            ui.say(ui.err(f"{t}: not found"))
            rc = 1
            continue
        if len(targets) > 1:
            ui.say(ui.acc(t + ":"))
        items = []
        for n, p in entries:
            if not o["all"] and is_hidden(p, n):
                continue
            try:
                st = os.stat(p)
            except OSError:
                continue
            d = stat.S_ISDIR(st.st_mode)
            if o["dirs"] and not d:
                continue
            items.append((n, p, d, st))
        if o["time"]:
            items.sort(key=lambda x: -x[3].st_mtime)
        elif o["size"]:
            items.sort(key=lambda x: -x[3].st_size)
        else:
            items.sort(key=lambda x: (not x[2], x[0].lower()))
        if o["reverse"]:
            items.reverse()
        sep = "\\" if IS_WIN else "/"
        if o["long"] or not ui.color_on():
            if o["long"]:
                rows = []
                for n, p, d, st in items:
                    rows.append([ui.dim(time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime))),
                                 ui.dim("<DIR>") if d else ui.human(st.st_size),
                                 ui.paint(n + (sep if d else ""), role_for(n, d), bold=d),
                                 ui.dim(age(st.st_mtime))])
                ui.table(rows, aligns="lrll")
                files = [x for x in items if not x[2]]
                ui.say(ui.dim(f"{len(items) - len(files)} folders, {len(files)} files, "
                              f"{ui.human(sum(x[3].st_size for x in files))}"))
            else:
                for n, p, d, st in items:
                    ui.say(n + (sep if d else ""))
            continue
        # grid
        cols, _ = term.size()
        names = [(n + (sep if d else ""), role_for(n, d), d) for n, _, d, _ in items]
        if not names:
            continue
        w = min(max(term.text_width(n) for n, _, _ in names) + 2, cols)
        per = max(1, cols // w)
        nrows = (len(names) + per - 1) // per
        for r in range(nrows):
            line = []
            for c in range(per):
                i = c * nrows + r
                if i < len(names):
                    n, role, d = names[i]
                    line.append(ui.paint(n, role, bold=d) + " " * (w - term.text_width(n)))
            ui.say("".join(line).rstrip())
    return rc


def _tree(root, depth, show_all, prefix="", level=0):
    if level == 0:
        ui.say(ui.paint(root, "path", bold=True))
    if level >= depth:
        return
    try:
        names = sorted(os.listdir(root), key=lambda n: (not os.path.isdir(os.path.join(root, n)), n.lower()))
    except OSError:
        return
    names = [n for n in names if show_all or not is_hidden(os.path.join(root, n), n)]
    for i, n in enumerate(names):
        p = os.path.join(root, n)
        last = i == len(names) - 1
        d = os.path.isdir(p)
        size = "" if d else ui.dim("  " + ui.human(os.path.getsize(p))) if os.path.exists(p) else ""
        ui.say(ui.dim(prefix + ("└─ " if last else "├─ ")) + ui.paint(n, role_for(n, d), bold=d) + size)
        if d and not os.path.islink(p):
            _tree(p, depth, show_all, prefix + ("   " if last else "│  "), level + 1)


@builtin("cat", cat="Files", summary="Show files (or piped text). -n adds line numbers.",
         usage="cat [-n] <file...>")
def cmd_cat(ctx):
    o, rest = ctx.opts({"n|number": bool})
    if not rest and ctx.stdin is None:
        raise Usage()
    files = expand_globs(rest)
    if ctx.stdin is not None and not files:
        texts = [ctx.stdin]
    else:
        texts = []
        for f in files:
            with open(f, "rb") as fh:
                data = fh.read()
            if b"\x00" in data[:8000]:
                ui.say(ui.dim(f"{f}: binary file, {ui.human(len(data))} (try: hash {f}, or open {f})"))
                continue
            for enc in ("utf-8-sig", "utf-16") if data[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-8-sig",):
                try:
                    texts.append(data.decode(enc))
                    break
                except UnicodeDecodeError:
                    continue
            else:
                texts.append(data.decode("latin-1"))
    for t in texts:
        if o["number"]:
            lines = t.splitlines()
            w = len(str(len(lines)))
            for i, l in enumerate(lines, 1):
                ui.say(ui.dim(f"{i:>{w}} │ ") + l)
        else:
            sys.stdout.write(t if t.endswith("\n") or not t else t + "\n")
    sys.stdout.flush()
    return 0


@builtin("head", cat="Text & data", summary="First lines of a file or piped text.", usage="head [-n 10] [file]")
def cmd_head(ctx):
    o, rest = ctx.opts({"n|lines": int})
    for l in ctx.text_in(rest).splitlines()[:o["lines"] or 10]:
        ui.say(l)
    return 0


@builtin("tail", cat="Text & data", summary="Last lines of a file. -f keeps watching it grow.",
         usage="tail [-n 10] [-f] <file>")
def cmd_tail(ctx):
    o, rest = ctx.opts({"n|lines": int, "f|follow": bool})
    n = o["lines"] or 10
    if not o["follow"]:
        for l in ctx.text_in(rest).splitlines()[-n:]:
            ui.say(l)
        return 0
    if not rest:
        raise Usage("tail -f needs a file")
    path = rest[0]
    with open(path, encoding="utf-8", errors="replace") as f:
        for l in f.read().splitlines()[-n:]:
            ui.say(l)
        ui.say(ui.dim(f"── following {path} · Ctrl+C or any key stops ──"))
        try:
            while True:
                line = f.readline()
                if line:
                    sys.stdout.write(line)
                    sys.stdout.flush()
                    continue
                if term.is_tty() and term.key_ready():
                    with term.raw_mode():
                        term.read_key()
                    return 0
                time.sleep(0.2)
        except KeyboardInterrupt:
            return 0


@builtin("grep", "search-text", cat="Text & data",
         summary="Find text in files (searches folders too) or in piped text. Regex, colored.",
         usage="grep <pattern> [files or folders]   -i ignore case  -v not matching  -n line numbers\n"
               "  -l only file names  -c count  -F plain text  -w whole words  --include *.py\n"
               "dir | grep -i readme")
def cmd_grep(ctx):
    o, rest = ctx.opts({"i|ignore-case": bool, "v|invert": bool, "n|line-number": bool, "l|files": bool,
                        "c|count": bool, "F|fixed": bool, "w|word": bool, "include": str, "r|recursive": bool})
    if not rest:
        raise Usage()
    pat = rest[0]
    if o["fixed"]:
        pat = re.escape(pat)
    if o["word"]:
        pat = r"\b" + pat + r"\b"
    try:
        rx = re.compile(pat, re.I if o["ignore-case"] else 0)
    except re.error as e:
        raise Usage(f"bad pattern: {e}")
    targets = rest[1:]
    hl = lambda l: rx.sub(lambda m: ui.paint(m.group(0), "err", bold=True), l) if ui.color_on() else l
    found = 0

    def scan(lines, label):
        nonlocal found
        hits = 0
        for i, l in enumerate(lines, 1):
            m = bool(rx.search(l))
            if m != o["invert"]:
                hits += 1
                if o["files"]:
                    break
                if not o["count"]:
                    pre = (ui.paint(label, "path") + ui.dim(":") if label else "") + \
                          (ui.paint(str(i), "ok") + ui.dim(":") if o["line-number"] or label else "")
                    ui.say(pre + (hl(l) if not o["invert"] else l))
        if hits:
            found += hits
            if o["files"]:
                ui.say(ui.paint(label or "(input)", "path"))
            elif o["count"]:
                ui.say((ui.paint(label, "path") + ui.dim(":") if label else "") + str(hits))

    if not targets:
        if ctx.stdin is None:
            targets = ["."]
        else:
            scan(ctx.stdin.splitlines(), "")
            return 0 if found else 1
    skip_dirs = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".idea", ".vs"}
    for t in expand_globs(targets):
        files = []
        if os.path.isdir(t):
            for root, dirs, fs in os.walk(t):
                dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith(".")]
                for f in fs:
                    if o["include"] and not fnmatch.fnmatch(f, o["include"]):
                        continue
                    files.append(os.path.join(root, f))
        else:
            files = [t]
        for f in files:
            try:
                with open(f, "rb") as fh:
                    data = fh.read(4_000_000)
            except OSError:
                continue
            if b"\x00" in data[:4000]:
                continue
            label = os.path.relpath(f) if len(files) > 1 or os.path.isdir(t) else ""
            scan(data.decode("utf-8", "replace").splitlines(), label)
    return 0 if found else 1


@builtin("count", "wc", cat="Text & data", summary="Count lines, words and characters.", usage="count [file]  ·  dir | count")
def cmd_count(ctx):
    text = ctx.text_in(ctx.args)
    ui.say(f"{len(text.splitlines())} lines · {len(text.split())} words · {len(text)} characters")
    return 0


@builtin("touch", cat="Files", summary="Create empty files or update their time.", usage="touch <file...>")
def cmd_touch(ctx):
    if not ctx.args:
        raise Usage()
    for f in ctx.args:
        with open(f, "a"):
            os.utime(f, None)
    return 0


# ---------------------------------------------------------------- trash
def send_to_trash(path: str):
    path = os.path.abspath(path)
    if not os.path.lexists(path):
        raise FileNotFoundError(2, "not found", path)
    if IS_WIN:
        import ctypes
        from ctypes import wintypes

        class SHFILEOPSTRUCTW(ctypes.Structure):
            _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                        ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                        ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]
        op = SHFILEOPSTRUCTW()
        op.wFunc = 3  # FO_DELETE
        op.pFrom = path + "\0"
        op.fFlags = 0x40 | 0x10 | 0x400 | 0x4  # ALLOWUNDO | NOCONFIRMATION | NOERRORUI | SILENT
        r = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
        if r != 0 or op.fAnyOperationsAborted:
            raise OSError(r, "couldn't move it to the Recycle Bin", path)
        return
    if IS_MAC:
        esc = path.replace("\\", "\\\\").replace('"', '\\"')
        r = subprocess.run(["osascript", "-e", f'tell application "Finder" to delete POSIX file "{esc}"'],
                           capture_output=True)
        if r.returncode:
            raise OSError(r.returncode, "couldn't move it to the Trash", path)
        return
    gio = shutil.which("gio")
    if gio and subprocess.run([gio, "trash", path], capture_output=True).returncode == 0:
        return
    trash = os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "Trash")
    os.makedirs(os.path.join(trash, "files"), exist_ok=True)
    os.makedirs(os.path.join(trash, "info"), exist_ok=True)
    name = os.path.basename(path)
    dest = os.path.join(trash, "files", name)
    n = 1
    while os.path.exists(dest):
        dest = os.path.join(trash, "files", f"{name}.{n}")
        n += 1
    with open(os.path.join(trash, "info", os.path.basename(dest) + ".trashinfo"), "w") as f:
        f.write(f"[Trash Info]\nPath={path}\nDeletionDate={time.strftime('%Y-%m-%dT%H:%M:%S')}\n")
    shutil.move(path, dest)


@builtin("trash", "recycle", cat="Files", summary="Move files to the Recycle Bin / Trash (you can get them back).",
         usage="trash <file or folder...>      (wildcards work: trash *.log)")
def cmd_trash(ctx):
    if not ctx.args:
        raise Usage()
    rc = 0
    for p in expand_globs(ctx.args):
        try:
            send_to_trash(p)
            ui.say(ui.dim("trashed ") + p)
        except OSError as e:
            ui.say(ui.err(f"{p}: {e.strerror or e}"))
            rc = 1
    return rc


@builtin("open", "o", cat="Files", summary="Open a file, folder or link with its usual app. 'open .' = this folder.",
         usage="open <file|folder|url>")
def cmd_open(ctx):
    targets = ctx.args or ["."]
    for t in targets:
        if IS_WIN:
            os.startfile(t if re.match(r"^\w+://", t) else os.path.abspath(t))
        else:
            subprocess.Popen(["open" if IS_MAC else "xdg-open", t], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return 0


# ---------------------------------------------------------------- sizes
def folder_size(path, stop=None):
    total, count = 0, 0
    stack = [path]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        else:
                            total += e.stat(follow_symlinks=False).st_size
                            count += 1
                    except OSError:
                        pass
        except OSError:
            pass
    return total, count


@builtin("du", "sizes", cat="Files", summary="What's taking up space in a folder, biggest first, with bars.",
         usage="du [folder] [-n 20]")
def cmd_du(ctx):
    o, rest = ctx.opts({"n|top": int})
    root = resolve(rest[0]) if rest else os.getcwd()
    rows = []
    with os.scandir(root) as it:
        entries = list(it)
    for i, e in enumerate(entries):
        if ui.color_on():
            term.write(f"\r\x1b[K" + ui.dim(f"measuring {i + 1}/{len(entries)} {e.name[:40]}"))
        try:
            if e.is_dir(follow_symlinks=False):
                size, n = folder_size(e.path)
                rows.append((size, e.name + ("\\" if IS_WIN else "/"), n, True))
            else:
                rows.append((e.stat(follow_symlinks=False).st_size, e.name, 1, False))
        except OSError:
            pass
    if ui.color_on():
        term.write("\r\x1b[K")
    rows.sort(reverse=True)
    total = sum(r[0] for r in rows) or 1
    top = rows[:o["top"] or 25]
    out = []
    for size, name, n, d in top:
        out.append([ui.human(size), ui.paint(ui.bar(size / total, 20), "accent"), f"{size * 100 / total:4.1f}%",
                    ui.paint(name, "path" if d else "text", bold=d), ui.dim(f"{n} files" if d else "")])
    ui.table(out, aligns="rllll")
    ui.say(ui.dim(f"total {ui.human(total)} in {root}" + (f" · {len(rows) - len(top)} smaller items not shown"
                                                          if len(rows) > len(top) else "")))
    return 0


@builtin("bigfiles", "biggest", cat="Files", summary="The biggest files anywhere under a folder.",
         usage="bigfiles [folder] [-n 20] [--min 100MB]")
def cmd_bigfiles(ctx):
    o, rest = ctx.opts({"n|top": int, "min": str})
    root = resolve(rest[0]) if rest else os.getcwd()
    n = o["top"] or 20
    import heapq
    heap = []
    seen = 0
    for dirpath, dirs, files in os.walk(root):
        for f in files:
            p = os.path.join(dirpath, f)
            try:
                s = os.path.getsize(p)
            except OSError:
                continue
            seen += 1
            if len(heap) < n:
                heapq.heappush(heap, (s, p))
            elif s > heap[0][0]:
                heapq.heapreplace(heap, (s, p))
        if ui.color_on() and seen % 500 == 0:
            term.write("\r\x1b[K" + ui.dim(f"looked at {seen} files..."))
    if ui.color_on():
        term.write("\r\x1b[K")
    ui.table([[ui.human(s), ui.paint(os.path.relpath(p, root), role_for(p, False))] for s, p in sorted(heap, reverse=True)],
             aligns="rl")
    ui.say(ui.dim(f"{seen} files checked"))
    return 0


def file_hash(path, algo="sha256"):
    h = hashlib.new(algo)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@builtin("dupes", "duplicates", cat="Files", summary="Find duplicate files (same content) under a folder.",
         usage="dupes [folder] [--min 1MB]")
def cmd_dupes(ctx):
    o, rest = ctx.opts({"min": str})
    root = resolve(rest[0]) if rest else os.getcwd()
    min_size = parse_size(o["min"]) if o["min"] else 1
    by_size = {}
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in (".git", "node_modules")]
        for f in files:
            p = os.path.join(dirpath, f)
            try:
                s = os.path.getsize(p)
            except OSError:
                continue
            if s >= min_size:
                by_size.setdefault(s, []).append(p)
    groups = []
    cands = [ps for ps in by_size.values() if len(ps) > 1]
    for k, ps in enumerate(cands):
        if ui.color_on():
            term.write("\r\x1b[K" + ui.dim(f"comparing {k + 1}/{len(cands)}"))
        by_hash = {}
        for p in ps:
            try:
                by_hash.setdefault(file_hash(p, "sha1"), []).append(p)
            except OSError:
                pass
        groups += [g for g in by_hash.values() if len(g) > 1]
    if ui.color_on():
        term.write("\r\x1b[K")
    if not groups:
        ui.say(ui.ok("No duplicates."))
        return 0
    groups.sort(key=lambda g: -os.path.getsize(g[0]) * (len(g) - 1))
    wasted = 0
    for g in groups:
        s = os.path.getsize(g[0])
        wasted += s * (len(g) - 1)
        ui.say(ui.acc(f"{ui.human(s)} × {len(g)}"))
        for p in g:
            ui.say("   " + os.path.relpath(p, root))
    ui.say(ui.dim(f"{len(groups)} groups · {ui.human(wasted)} could be freed (keep one of each; 'trash' the rest)"))
    return 0


def parse_size(s: str) -> int:
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([KMGT]?B?)\s*", s, re.I)
    if not m:
        raise Usage(f"'{s}' isn't a size like 500KB or 2GB")
    mult = {"": 1, "B": 1, "K": 1024, "KB": 1024, "M": 1024 ** 2, "MB": 1024 ** 2, "G": 1024 ** 3,
            "GB": 1024 ** 3, "T": 1024 ** 4, "TB": 1024 ** 4}[m.group(2).upper()]
    return int(float(m.group(1)) * mult)


@builtin("hash", "checksum", cat="Files", summary="SHA-256 (or md5/sha1/sha512) of files. --check compares.",
         usage="hash <file> [-a md5|sha1|sha256|sha512|all] [--check <expected>]")
def cmd_hash(ctx):
    o, rest = ctx.opts({"a|algo": str, "check": str})
    if not rest and ctx.stdin is None:
        raise Usage()
    algos = ["md5", "sha1", "sha256", "sha512"] if (o["algo"] or "").lower() == "all" else [(o["algo"] or "sha256").lower()]
    rc = 0
    if not rest:
        for a in algos:
            ui.say(f"{ui.dim(a)}  {hashlib.new(a, ctx.stdin.encode()).hexdigest()}")
        return 0
    for f in expand_globs(rest):
        for a in algos:
            h = file_hash(f, a)
            if o["check"]:
                good = h.lower() == o["check"].strip().lower()
                ui.say((ui.ok("MATCH    ") if good else ui.err("DIFFERENT")) + f"  {f}")
                rc |= 0 if good else 1
            else:
                ui.say(f"{h}  {ui.dim(a if len(algos) > 1 else '')}{f}")
    return rc


# -------------------------------------------------------------- archives
@builtin("zip", cat="Files", summary="Pack files and folders into a .zip.", usage="zip <out.zip> <files or folders...>")
def cmd_zip(ctx):
    if len(ctx.args) < 2:
        raise Usage()
    out = ctx.args[0]
    if not out.lower().endswith(".zip"):
        out += ".zip"
    n = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for t in expand_globs(ctx.args[1:]):
            if os.path.isdir(t):
                base = os.path.dirname(os.path.abspath(t))
                for root, _, files in os.walk(t):
                    for f in files:
                        p = os.path.join(root, f)
                        if os.path.abspath(p) == os.path.abspath(out):
                            continue
                        z.write(p, os.path.relpath(p, base))
                        n += 1
            else:
                z.write(t, os.path.basename(t))
                n += 1
    ui.say(ui.ok(f"Packed {n} files into {out} ({ui.human(os.path.getsize(out))})."))
    return 0


@builtin("extract", "unzip", "untar", cat="Files", summary="Unpack .zip, .tar, .tar.gz, .tgz, .tar.xz, .tar.bz2.",
         usage="extract <archive> [folder]")
def cmd_extract(ctx):
    if not ctx.args:
        raise Usage()
    arc = ctx.args[0]
    base = os.path.basename(arc)
    stem = re.sub(r"(\.tar)?\.(zip|tar|gz|tgz|xz|bz2)$", "", base, flags=re.I)
    dest = ctx.args[1] if len(ctx.args) > 1 else stem
    os.makedirs(dest, exist_ok=True)
    real = os.path.realpath(dest)
    if zipfile.is_zipfile(arc):
        with zipfile.ZipFile(arc) as z:
            for m in z.namelist():
                if not os.path.realpath(os.path.join(dest, m)).startswith(real):
                    raise OSError(f"unsafe path inside archive: {m}")
            z.extractall(dest)
            n = len(z.namelist())
    elif tarfile.is_tarfile(arc):
        with tarfile.open(arc) as t:
            members = t.getmembers()
            try:
                t.extractall(dest, filter="data")
            except TypeError:
                for m in members:
                    if not os.path.realpath(os.path.join(dest, m.name)).startswith(real):
                        raise OSError(f"unsafe path inside archive: {m.name}")
                t.extractall(dest)
            n = len(members)
    else:
        seven = shutil.which("7z") or shutil.which("7za")
        if seven:
            return subprocess.call([seven, "x", arc, f"-o{dest}"])
        if IS_WIN and shutil.which("tar"):  # Windows tar.exe reads .7z/.rar via libarchive
            return subprocess.call(["tar", "-xf", arc, "-C", dest])
        ui.say(ui.err("Commands can't open that kind of archive.") + " Try: install 7-zip")
        return 1
    ui.say(ui.ok(f"Unpacked {n} items into {dest}"))
    return 0


@builtin("rename-all", "bulk-rename", cat="Files",
         summary="Rename many files with a pattern. Shows a preview first.",
         usage='rename-all <find> <replace> [files] [--regex]\n'
               'rename-all " " "_" *.jpg            spaces to underscores\n'
               'rename-all "IMG_(\\d+)" "photo-\\1" * --regex')
def cmd_rename_all(ctx):
    o, rest = ctx.opts({"regex": bool, "y|yes": bool})
    if len(rest) < 2:
        raise Usage()
    find, repl = rest[0], rest[1]
    files = expand_globs(rest[2:] or ["*"])
    rx = re.compile(find if o["regex"] else re.escape(find))
    plan = []
    for f in files:
        d, n = os.path.split(f)
        new = rx.sub(repl if o["regex"] else repl.replace("\\", "\\\\"), n)
        if new != n:
            plan.append((f, os.path.join(d, new)))
    if not plan:
        ui.say(ui.dim("Nothing would change."))
        return 0
    targets = [b for _, b in plan]
    if len(set(t.lower() for t in targets)) != len(targets) or any(os.path.exists(b) for _, b in plan
                                                                  if b.lower() not in {a.lower() for a, _ in plan}):
        ui.say(ui.err("Some new names clash with each other or with existing files. Nothing was renamed."))
        return 1
    for a, b_ in plan[:30]:
        ui.say(f"  {os.path.basename(a)} {ui.dim('→')} {ui.acc(os.path.basename(b_))}")
    if len(plan) > 30:
        ui.say(ui.dim(f"  … and {len(plan) - 30} more"))
    if not o["yes"] and not ui.confirm(f"Rename {len(plan)} files?"):
        return 1
    for a, b_ in plan:
        os.rename(a, b_)
    ui.say(ui.ok(f"Renamed {len(plan)} files."))
    return 0


@builtin("copypath", "pwd", cat="Files", summary="Show this folder's path (copypath also copies it).",
         usage="pwd      copypath [file]")
def cmd_pwd(ctx):
    p = os.path.abspath(ctx.args[0]) if ctx.args else os.getcwd()
    ui.say(p)
    if ctx.name == "copypath":
        from .builtins_sys import clipboard_set
        clipboard_set(p)
        ui.say(ui.dim("copied"))
    return 0
