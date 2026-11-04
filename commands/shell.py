"""The Commands shell: reads lines, decides who runs each command, keeps state.

Who runs a command, in this order:
  1. a Commands built-in (cd, set, path, env, ls, ports, ...)
  2. a Commands app (from the apps folder, gets the Commands API)
  3. Odus, for app/media/download words (install, update, compress, ...)
  4. a script by its file type (.bat/.cmd keep their variables, .ps1, .py, .sh)
  5. PowerShell, for cmdlets and PowerShell syntax ($x, Get-Thing, | Where-Object)
  6. CMD, for everything else (dir, copy, winget, git, any program)
Prefixes force one: odus: cmd: ps: sh:
"""
import io
import os
import re
import sys
import time
import shutil
import difflib
import contextlib

from . import VERSION, ui, term, lexer, backends, envtools
from .registry import BUILTINS, Ctx, Usage
from .lineedit import LineEditor

IS_WIN = os.name == "nt"
CMD_INTERNAL = set("""assoc break call cd chdir cls color copy date del dir dpath echo endlocal erase exit
for ftype goto if keys md mkdir mklink move path pause popd prompt pushd rd rem ren rename rmdir set
setlocal shift start time title type ver verify vol""".split())
CMD_RUN_BY_CMD = {"assoc", "break", "copy", "date", "del", "dir", "dpath", "erase", "for", "ftype", "goto",
                  "if", "keys", "md", "mkdir", "mklink", "move", "pause", "rd", "ren", "rename", "rmdir",
                  "shift", "start", "time", "type", "ver", "verify", "vol", "endlocal", "setlocal"}
POSIX_KEEP = {"Shell", "Folders", "Variables", "PATH", "Look", "Apps & API", "Odus"}
APPS_DIR = os.path.join(ui.CONFIG_DIR, "apps")
APP_EXT = (".py", ".js", ".mjs", ".ps1", ".cmd", ".bat", ".exe", ".sh", ".com")

DEFAULT_CONFIG = {"theme": "commands", "aliases": {}, "prompt": None, "bookmarks": {},
                  "live_refresh": True, "odus_fuzzy": True, "banner": True, "suggestions": True,
                  "notify_after": 0}


class ExitShell(Exception):
    def __init__(self, code=0):
        self.code = code


class Shell:
    def __init__(self, interactive=True):
        self.interactive = interactive
        self.last_rc = 0
        self.prev_dir = None
        self.dirstack = []
        self.drive_dirs = {}
        self.pending = []
        self.jobs = []
        self.title = None
        self.started = time.time()
        self.last_duration = 0.0
        cfg = dict(DEFAULT_CONFIG)
        cfg.update(ui.load_json("config.json", {}))
        self.config = cfg
        ui.set_theme(cfg.get("theme") or "commands")
        self.history = self._load_history()
        self._exe_cache = {}
        self._exe_names = None
        self._path_seen = os.environ.get("PATH", "")
        self.api = None
        self.notices = []
        os.environ["COMMANDS"] = VERSION
        os.environ["COMMANDS_SESSION"] = str(os.getpid())
        if IS_WIN:
            envtools.live_refresh(initial=True)
        from . import builtins_core, builtins_files, builtins_sys, builtins_env, builtins_api  # noqa: F401

    # ------------------------------------------------------------ config
    def save_config(self):
        ui.save_json("config.json", self.config)

    def _load_history(self):
        try:
            with open(os.path.join(ui.CONFIG_DIR, "history.txt"), encoding="utf-8") as f:
                return [l.rstrip("\n") for l in f.readlines()[-5000:] if l.strip()]
        except OSError:
            return []

    def add_history(self, line):
        if not line.strip() or line.startswith(" "):
            return
        if self.history and self.history[-1] == line:
            return
        self.history.append(line)
        try:
            os.makedirs(ui.CONFIG_DIR, exist_ok=True)
            with open(os.path.join(ui.CONFIG_DIR, "history.txt"), "a", encoding="utf-8") as f:
                f.write(line.replace("\n", " ") + "\n")
        except OSError:
            pass

    # ------------------------------------------------------ lookups
    def which(self, name):
        if not name:
            return None
        path = os.environ.get("PATH", "")
        if path != self._path_seen:
            self._exe_cache.clear()
            self._exe_names = None
            self._path_seen = path
        key = name.lower() if IS_WIN else name
        if key not in self._exe_cache:
            p = None
            if any(s in name for s in "\\/"):
                cand = os.path.abspath(name)
                if os.path.isfile(cand):
                    p = cand
                elif IS_WIN:
                    for ext in os.environ.get("PATHEXT", ".EXE;.CMD;.BAT").split(";"):
                        if os.path.isfile(cand + ext):
                            p = cand + ext
                            break
            else:
                p = shutil.which(name)
            self._exe_cache[key] = p
        return self._exe_cache[key]

    def exe_names(self):
        if self._exe_names is None:
            names = set()
            exts = {e.lower() for e in os.environ.get("PATHEXT", "").split(";") if e} if IS_WIN else None
            for d in envtools.split(os.environ.get("PATH", "")):
                try:
                    for n in os.listdir(os.path.expandvars(d)):
                        base, ext = os.path.splitext(n)
                        if IS_WIN:
                            if ext.lower() in exts:
                                names.add(base.lower())
                        else:
                            names.add(n)
                except OSError:
                    pass
            self._exe_names = sorted(names)
        return self._exe_names

    def apps(self):
        out = {}
        try:
            for n in os.listdir(APPS_DIR):
                base, ext = os.path.splitext(n)
                if ext.lower() in APP_EXT:
                    out.setdefault(base.lower(), os.path.join(APPS_DIR, n))
                elif os.path.isdir(os.path.join(APPS_DIR, n)):
                    for main in ("main.py", "main.js", "app.py", "index.js"):
                        p = os.path.join(APPS_DIR, n, main)
                        if os.path.isfile(p):
                            out.setdefault(n.lower(), p)
                            break
        except OSError:
            pass
        for name, entry in ui.load_json("apps.json", {}).items():
            out.setdefault(name.lower(), entry)
        return out

    def kind_of(self, name: str) -> str:
        """builtin | app | odus | alias | cmd | exe | script | cmdlet | drive | unknown"""
        if not name:
            return "unknown"
        low = name.lower()
        if re.fullmatch(r"[A-Za-z]:", name) and IS_WIN:
            return "drive"
        if low in self.config["aliases"]:
            return "alias"
        if low in BUILTINS:
            # on macOS/Linux the real tools (ls, grep, kill, open...) win over Commands' versions
            if IS_WIN or BUILTINS[low].cat in POSIX_KEEP or not self.which(name):
                return "builtin"
        if low == "odus" or low in backends.ODUS_ALWAYS:
            return "odus-off" if self.config.get("odus_on_path") == "no" else "odus"
        if low.endswith(":") and low[:-1] in ("ps", "cmd", "sh", "odus", "pwsh"):
            return "builtin"
        if low in self.apps():
            return "app"
        ext = os.path.splitext(low)[1]
        if ext in (".ps1", ".py", ".sh", ".bat", ".cmd") and os.path.isfile(name):
            return "script"
        if IS_WIN and low in CMD_INTERNAL:
            return "cmd"
        if self.which(name):
            return "exe"
        m = backends._CMDLET.match(name)
        if m and m.group(1).capitalize() in backends.PS_VERB_SET:
            return "cmdlet"
        return "unknown"

    # ------------------------------------------------------- running
    def run_line(self, line: str, record=False) -> int:
        line = line.strip()
        if record:
            self.add_history(line)
        if not line:
            return self.last_rc
        if line.startswith("@"):
            line = line[1:].lstrip()
        low = line.lower()
        if low.startswith(("::", "rem ", "#")) or low == "rem":
            return self.last_rc
        t0 = time.time()
        try:
            rc = self._dispatch(line)
        except ExitShell:
            raise
        except KeyboardInterrupt:
            ui.say(ui.paint("^C", "dim"))
            rc = 130
        except Usage as e:
            ui.say(ui.err(str(e)))
            rc = 2
        self.last_rc = rc if isinstance(rc, int) else 0
        self.last_duration = time.time() - t0
        self.after_command()
        return self.last_rc

    def after_command(self):
        term.enable_vt()
        if IS_WIN and self.config.get("live_refresh", True):
            notes = envtools.live_refresh()
            if notes and self.interactive:
                ui.say(ui.dim("live refresh: " + "; ".join(notes)))
        while self.pending:
            line = self.pending.pop(0)
            self.run_line(line)

    def _dispatch(self, line: str) -> int:
        # forced backends
        m = re.match(r"^(ps|pwsh|powershell|cmd|sh|bash|odus):\s*(.*)$", line, re.I | re.S)
        if m:
            which, rest = m.group(1).lower(), m.group(2)
            if which in ("ps", "pwsh", "powershell"):
                return backends.ps_line(rest)[0]
            if which == "cmd":
                return backends.cmd_line(rest)[0]
            if which in ("sh", "bash"):
                return backends.sh_line(rest)[0]
            if self.config.get("odus_on_path") == "no":
                from .odus_setup import declined_message
                ui.say(declined_message())
                return 9009
            return backends.run_odus(lexer.words(rest))[0]
        first = (lexer.words(line) or [""])[0]
        line = self._expand_alias(line, first)
        first = (lexer.words(line) or [""])[0]
        kind = self.kind_of(first)
        if kind in ("cmdlet", "unknown") and backends.looks_like_powershell(line, first) or \
                (kind == "unknown" and line.lstrip().startswith(("$", "[", "@{", "@("))):
            return backends.ps_line(line)[0]
        rc = self.last_rc
        for op, seg in lexer.split_chain(line):
            if op == "&&" and rc != 0:
                continue
            if op == "||" and rc == 0:
                continue
            rc = self.run_segment(seg)
            self.last_rc = rc
        return rc

    def _expand_alias(self, line, first, depth=0):
        body = self.config["aliases"].get(first.lower())
        if body is None or depth > 5:
            return line
        rest = line.lstrip()[len(first):].strip() if line.lstrip().lower().startswith(first.lower()) else ""
        args = lexer.words(rest)
        if "$*" in body or re.search(r"\$[1-9]", body):
            out = body.replace("$*", rest)
            for i in range(1, 10):
                out = out.replace(f"${i}", lexer.quote(args[i - 1]) if i <= len(args) else "")
        else:
            out = body + (" " + rest if rest else "")
        nf = (lexer.words(out) or [""])[0]
        if nf.lower() != first.lower():
            return self._expand_alias(out, nf, depth + 1)
        return out

    def run_segment(self, seg: str) -> int:
        stages = lexer.split_pipe(seg)
        if len(stages) == 1:
            rc, _ = self.run_stage(stages[0])
            return rc
        kinds = [self.kind_of((lexer.words(lexer.expand(s, self.last_rc)) or [""])[0]) for s in stages]
        if all(k in ("exe", "cmd", "unknown", "cmdlet") for k in kinds):
            return backends.cmd_line(seg)[0]
        text = None
        rc = 0
        for i, st in enumerate(stages):
            last = i == len(stages) - 1
            rc, text = self.run_stage(st, stdin=text, capture=not last)
            if text is None and not last:
                text = ""
        return rc

    def run_stage(self, text: str, stdin=None, capture=False):
        """Runs one command. Returns (rc, captured output or None)."""
        expanded = lexer.expand(text, self.last_rc)
        try:
            toks = lexer.tokenize(expanded)
            args, red = lexer.take_redirects(toks)
        except ValueError as e:
            ui.say(ui.err(str(e)))
            return 1, None
        if not args:
            return 0, None
        name = args[0]
        kind = self.kind_of(name)

        if kind == "drive":
            return self.change_drive(name), None
        if kind == "builtin":
            m = re.match(r'\s*("[^"]*"|\S+)\s?', expanded)
            return self.run_builtin(name, args[1:], red, stdin, capture, raw=expanded[m.end():] if m else "")
        if kind == "app":
            return self.run_app(name, args[1:], stdin, capture)
        if kind == "odus-off":
            from .odus_setup import declined_message
            ui.say(declined_message())
            return 9009, None
        if kind == "odus":
            argv = args[1:] if name.lower() == "odus" else args
            if name.lower() == "odus" and argv[:1] in (["get"], ["setup-odus"]):
                return self.run_builtin("odus-get", [], red, stdin, capture)
            return backends.run_odus(argv, stdin, capture)
        if kind == "script":
            return self.run_script(name, args[1:], stdin, capture)
        if kind in ("cmd", "exe"):
            p = self.which(name) or ""
            if p.lower().endswith((".bat", ".cmd")) and any(s in name for s in "\\/.") and not capture and \
                    not red and stdin is None:
                return backends.run_batch(p, [lexer.quote(a) for a in args[1:]])
            if p.lower().endswith(".ps1"):
                return backends.ps_line("", stdin, capture, file=p, args=args[1:])
            if not IS_WIN:
                return backends.sh_line(expanded, stdin, capture)
            return backends.cmd_line(text, stdin, capture)
        if kind == "cmdlet":
            return backends.ps_line(text, stdin, capture)
        # not found anywhere: maybe Odus understands it ("isntall vlc", "fast update all my apps")
        if self.config.get("odus_fuzzy", True) and self.config.get("odus_on_path") != "no" and                 backends.odus_understands(args):
            ui.say(ui.dim(f"'{name}' isn't a command here; handing it to Odus."))
            return backends.run_odus(args, stdin, capture)
        self.not_found(name)
        return 9009, None

    def not_found(self, name):
        pool = set(BUILTINS) | set(self.config["aliases"]) | set(self.apps()) | backends.ODUS_ALWAYS
        if IS_WIN:
            pool |= CMD_INTERNAL
        close = difflib.get_close_matches(name.lower(), list(pool), 3, 0.7)
        if not close:
            close = difflib.get_close_matches(name.lower(), self.exe_names(), 3, 0.8)
        msg = ui.err(f"'{name}' isn't a command, program or script that Commands can find.")
        if close:
            msg += "\n" + ui.dim("Did you mean: ") + ", ".join(ui.b(c) for c in close) + ui.dim("?")
        else:
            msg += "\n" + ui.dim("Not installed? Try: ") + ui.b(f"install {name}")
        ui.say(msg)

    @contextlib.contextmanager
    def _redirect(self, red, capture):
        """Points stdout/stderr of a built-in at a file or a buffer."""
        buf = io.StringIO() if capture else None
        files = []
        old_out, old_err = sys.stdout, sys.stderr
        try:
            if "out" in red:
                f = open(os.path.expanduser(red["out"][0]), red["out"][1], encoding="utf-8")
                files.append(f)
                sys.stdout = f
            elif buf is not None:
                sys.stdout = buf
            if "err" in red:
                f = open(red["err"][0], red["err"][1], encoding="utf-8")
                files.append(f)
                sys.stderr = f
            elif red.get("err_to_out"):
                sys.stderr = sys.stdout
            yield buf
        finally:
            sys.stdout, sys.stderr = old_out, old_err
            for f in files:
                f.close()

    def run_builtin(self, name, args, red=None, stdin=None, capture=False, raw=None):
        red = red or {}
        low = name.lower()
        b = BUILTINS.get(low)
        if b is None:
            return 9009, None
        if "in" in red:
            try:
                stdin = open(red["in"], encoding="utf-8", errors="replace").read()
            except OSError as e:
                ui.say(ui.err(f"Can't read {red['in']}: {e.strerror}"))
                return 1, None
        if args and args[0] in ("/?", "--help", "-?") and low not in ("help",):
            self.print_usage(b)
            return 0, None
        try:
            with self._redirect(red, capture) as buf:
                try:
                    rc = b.func(Ctx(self, low, args, stdin, raw))
                except Usage as e:
                    if str(e):
                        ui.say(ui.err(str(e)))
                    self.print_usage(b)
                    rc = 2
                except (OSError, envtools.EnvError) as e:
                    ui.say(ui.err(str(e) if isinstance(e, envtools.EnvError) else
                                  f"{getattr(e, 'filename', '') or ''} {e.strerror or e}".strip()))
                    rc = 1
                except BrokenPipeError:
                    rc = 0
        except OSError as e:
            ui.say(ui.err(f"Can't write there: {e}"))
            return 1, None
        return (rc or 0), (buf.getvalue() if buf is not None else None)

    def print_usage(self, b):
        ui.say(ui.b(b.name) + ui.dim(" - ") + b.summary)
        if b.usage:
            for l in b.usage.strip("\n").splitlines():
                ui.say("  " + l)
        if len(b.names) > 1:
            ui.say(ui.dim("  also: " + ", ".join(b.names[1:])))

    def api_env(self):
        env = dict(os.environ)
        if self.api:
            env.update(self.api.child_env())
        return env

    def run_app(self, name, args, stdin=None, capture=False):
        entry = self.apps()[name.lower()]
        if isinstance(entry, dict):  # registered through the API: {"run": "...", "help": "..."}
            line = entry.get("run", "")
            return self._with_api_env(lambda: (self.run_segment(line + " " + lexer.join(args)), None))
        return self._with_api_env(lambda: self.run_script(entry, args, stdin, capture))

    def _with_api_env(self, fn):
        added = self.api.child_env() if self.api else {}
        os.environ.update(added)
        try:
            return fn()
        finally:
            if self.api and not self.api.global_env:
                for k in added:
                    os.environ.pop(k, None)

    def run_script(self, path, args, stdin=None, capture=False):
        ext = os.path.splitext(path)[1].lower()
        if ext in (".bat", ".cmd"):
            if capture or stdin is not None:
                return backends.cmd_line(lexer.join([path] + args), stdin, capture)
            return backends.run_batch(path, [lexer.quote(a) for a in args])
        if ext == ".ps1":
            return backends.ps_line("", stdin, capture, file=os.path.abspath(path), args=args)
        if ext == ".py":
            return backends.run_process([backends.python_exe(), path] + args, stdin, capture)
        if ext in (".js", ".mjs"):
            node = shutil.which("node")
            if not node:
                ui.say(ui.err("This app needs Node.js.") + " Try: install nodejs")
                return 9009, None
            return backends.run_process([node, path] + args, stdin, capture)
        if ext == ".sh":
            return backends.sh_line(lexer.join([path] + args), stdin, capture)
        return backends.run_process([path] + args, stdin, capture)

    def change_drive(self, d) -> int:
        d = d.upper()
        cur = os.getcwd()
        self.drive_dirs[cur[:2].upper()] = cur
        target = self.drive_dirs.get(d, d + "\\")
        try:
            os.chdir(target)
        except OSError:
            ui.say(ui.err("The system cannot find the drive specified."))
            return 1
        return 0

    def chdir(self, path) -> bool:
        old = os.getcwd()
        os.chdir(path)
        if os.getcwd() != old:
            self.prev_dir = old
            from .builtins_core import record_visit
            record_visit(os.getcwd())
        return True

    # ------------------------------------------------------------ prompt
    def git_branch(self):
        d = os.getcwd()
        for _ in range(40):
            head = os.path.join(d, ".git", "HEAD")
            if os.path.isfile(head):
                try:
                    s = open(head, encoding="utf-8").read().strip()
                except OSError:
                    return None
                return s.split("/", 2)[-1] if s.startswith("ref:") else s[:7]
            nd = os.path.dirname(d)
            if nd == d:
                return None
            d = nd
        return None

    def prompt(self) -> str:
        tpl = self.config.get("prompt")
        if tpl:
            return self._cmd_prompt(tpl)
        cwd = os.getcwd()
        home = os.path.expanduser("~")
        shown = cwd
        p = ui.paint(shown, "path")
        br = self.git_branch()
        if br:
            p += ui.paint(f" ({br})", "git")
        if self.last_rc not in (0, None):
            p += ui.paint(f" [{self.last_rc}]", "err")
        if self.last_duration >= 5:
            p += ui.paint(f" {self.last_duration:.0f}s", "dim")
        if envtools.is_admin():
            p += ui.paint(" admin", "warn")
        glyph = ui.paint("›", "err" if self.last_rc not in (0, None) else "glyph", bold=True)
        return f"{p} {glyph} "

    def _cmd_prompt(self, tpl):
        cwd = os.getcwd()
        codes = {"P": cwd, "G": ">", "L": "<", "B": "|", "Q": "=", "S": " ", "$": "$", "_": "\n",
                 "N": cwd[:1], "T": time.strftime("%H:%M:%S"), "D": time.strftime("%a %m/%d/%Y"),
                 "V": f"Commands {VERSION}", "H": "\b", "E": "\x1b", "A": "&", "C": "(", "F": ")",
                 "+": "+" * len(self.dirstack), "R": self.git_branch() or "", "X": str(self.last_rc)}
        return re.sub(r"\$(.)", lambda m: codes.get(m.group(1).upper(), m.group(0)), tpl)

    def set_title(self):
        t = self.title or f"Commands - {os.getcwd()}"
        if ui.color_on(sys.__stdout__):
            term.write(f"\x1b]0;{t}\x07")

    # ------------------------------------------------------- editing help
    def highlight(self, text: str) -> str:
        if not ui.color_on(sys.__stdout__):
            return text
        out, last = [], 0
        roles = {"builtin": "builtin", "alias": "builtin", "app": "odus", "odus": "odus", "odus-off": "unknown", "cmd": "exe",
                 "exe": "exe", "script": "exe", "cmdlet": "cmdlet", "drive": "path", "unknown": "unknown"}
        ps = False
        for s, e, kind in lexer.segments_for_highlight(text):
            out.append(text[last:s])
            word = text[s:e]
            if kind == "cmd":
                w = word.strip('"')
                k = self.kind_of(w)
                if k == "unknown" and (backends.looks_like_powershell(text, w) or w.startswith("$")):
                    k = "cmdlet"
                    ps = True
                if re.match(r"^(ps|pwsh|cmd|sh|odus):", w, re.I):
                    k = "builtin"
                role = roles[k]
                out.append(ui.fg(role) + (ui.BOLD if k in ("builtin", "odus", "alias") else "") + word + ui.RESET)
            elif kind == "string":
                out.append(ui.fg("string") + word + ui.RESET)
            elif kind == "flag":
                out.append(ui.fg("flag") + word + ui.RESET)
            elif kind == "op":
                out.append(ui.fg("op") + word + ui.RESET)
            else:
                out.append(re.sub(r"(%[^%\s]+%|\$env:\w+|\$\w+)", lambda m: ui.fg("var") + m.group(0) +
                                  ui.RESET + ui.fg("text"), ui.fg("text") + word) + ui.RESET)
            last = e
        out.append(text[last:])
        return "".join(out)

    def suggest(self, text: str):
        if not self.config.get("suggestions", True) or len(text) < 2:
            return ""
        for h in reversed(self.history):
            if h.startswith(text) and len(h) > len(text):
                return h[len(text):]
        return ""

    def complete(self, text: str, cur: int):
        before = text[:cur]
        # find where the current word starts
        q, start = False, 0
        for i, ch in enumerate(before):
            if ch == '"':
                q = not q
                if q:
                    start = i
            elif not q and ch in " \t&|<>":
                start = i + 1
        word = before[start:]
        prior = before[:start].rstrip()
        is_cmd = not prior or prior.endswith(("&", "|"))
        cands = []
        if is_cmd and not any(s in word for s in "\\/:"):
            names = set(BUILTINS) | set(self.config["aliases"]) | set(self.apps()) | backends.ODUS_ALWAYS | {"odus"}
            if IS_WIN:
                names |= CMD_INTERNAL
            lw = word.lower()
            pool = sorted(n for n in names if n.startswith(lw))
            pool += [n for n in self.exe_names() if n.lower().startswith(lw) and n not in names]
            cands = [(n, n) for n in pool[:400]]
            if cands:
                return start, cands
        # sub-command completion for built-ins
        seg_words = lexer.words(re.split(r"&&|\|\||[&|]", prior)[-1]) if prior else []
        if seg_words:
            b = BUILTINS.get(seg_words[0].lower())
            if b and b.complete:
                try:
                    opts = b.complete(self, seg_words[1:], word)
                except Exception:
                    opts = []
                hits = [(o, o) for o in opts if o.lower().startswith(word.lower())]
                if hits:
                    return start, hits
        return start, self.complete_path(word, dirs_only=bool(seg_words) and seg_words[0].lower() in
                                         ("cd", "chdir", "pushd", "mkcd", "j", "ls"))

    def complete_path(self, word, dirs_only=False):
        raw = word.strip('"')
        expanded = os.path.expanduser(lexer.expand(raw, self.last_rc))
        d, prefix = os.path.split(expanded)
        typed_dir = raw[:len(raw) - len(prefix)] if prefix and raw.endswith(prefix) else (raw if not prefix else "")
        look = d or "."
        try:
            names = sorted(os.listdir(look), key=str.lower)
        except OSError:
            return []
        sep = "\\" if IS_WIN else "/"
        out = []
        for n in names:
            if not n.lower().startswith(prefix.lower()) if IS_WIN else not n.startswith(prefix):
                continue
            if n.startswith(".") and not prefix.startswith("."):
                continue
            full = os.path.join(look, n)
            isdir = os.path.isdir(full)
            if dirs_only and not isdir:
                continue
            ins = typed_dir + n + (sep if isdir else "")
            if any(c in ins for c in " &()^=;,"):
                ins = '"' + ins + ('"' if not isdir else "")
            out.append((n + (sep if isdir else ""), ins))
        return out

    # ------------------------------------------------------------ banner
    def banner(self):
        if not self.config.get("banner", True):
            return
        ui.say(ui.paint("▌", "accent") + ui.b("Commands") + ui.dim(f" [Version {VERSION}]"))
        ui.say()

    # -------------------------------------------------------------- loop
    def repl(self, first_line=None) -> int:
        term.enable_vt()
        self.banner()
        from .odus_setup import first_run
        first_run(self)
        from . import api
        from .builtins_sys import check_jobs
        self.api = api.start_server(self)
        if first_line:
            try:
                self.run_line(first_line)
            except ExitShell as e:
                return e.code
        ed = LineEditor(self.history, self.highlight, self.complete, self.suggest)
        tty = term.is_tty()
        while True:
            self.set_title()
            check_jobs(self)
            while self.notices:
                ui.say(self.notices.pop(0))
            try:
                if tty:
                    line = ed.read(self.prompt())
                else:
                    try:
                        line = input(ui.strip_ansi(self.prompt()))
                    except EOFError:
                        line = None
            except KeyboardInterrupt:
                ui.say()
                continue
            if line is None:
                return self.last_rc
            try:
                t0 = time.time()
                self.run_line(line, record=True)
                took = time.time() - t0
                after = self.config.get("notify_after") or 0
                if tty and after and took >= after and line.strip():
                    from .builtins_sys import desktop_notify
                    desktop_notify("Commands", f"Finished: {line.strip()[:60]} ({took:.0f}s)", quiet=True)
            except ExitShell as e:
                return e.code
