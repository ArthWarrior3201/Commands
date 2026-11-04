"""Core shell commands: folders, variables, aliases, history, look, timing, admin."""
import os
import re
import sys
import time
import shutil
import subprocess

from . import VERSION, ui, term, lexer, backends, envtools
from .registry import builtin, BUILTINS, Usage, resolve

IS_WIN = os.name == "nt"


# ------------------------------------------------------------- folders
def record_visit(path):
    data = ui.load_json("jumps.json", {})
    rec = data.get(path, [0, 0])
    data[path] = [rec[0] + 1, time.time()]
    if len(data) > 2000:
        for k in sorted(data, key=lambda k: data[k][1])[:500]:
            data.pop(k)
    ui.save_json("jumps.json", data)


def _complete_dirs(sh, args, word):
    return []


@builtin("cd", "chdir", cat="Folders", summary="Change folder. cd - goes back, cd ~ goes home.",
         usage="cd [/d] <folder>     cd ..     cd -     cd ~     cd (shows where you are)")
def cmd_cd(ctx):
    args = [a for a in ctx.args if a.lower() != "/d"]
    sh = ctx.sh
    if not args:
        ui.say(os.getcwd())
        return 0
    target = " ".join(args)  # CMD allows: cd Program Files
    if target == "-":
        if not sh.prev_dir:
            ui.say(ui.err("No previous folder yet."))
            return 1
        target = sh.prev_dir
    elif target in ("~", ""):
        target = os.path.expanduser("~")
    elif re.fullmatch(r"\.{3,}", target):  # cd ... = up two
        target = os.path.join(*([".."] * (len(target) - 1)))
    target = os.path.expanduser(target.strip('"'))
    if not os.path.isdir(target):
        bm = sh.config["bookmarks"].get(target.lower())
        if bm:
            target = bm
        else:
            ui.say(ui.err("The system cannot find the path specified.") +
                   ui.dim(f"  (try: j {args[0]})" if len(args) == 1 else ""))
            return 1
    sh.chdir(target)
    return 0


@builtin("pushd", cat="Folders", summary="Remember this folder and go to another one.", usage="pushd <folder>")
def cmd_pushd(ctx):
    if not ctx.args:
        for d in reversed(ctx.sh.dirstack):
            ui.say(d)
        return 0
    target = resolve(" ".join(ctx.args))
    if not os.path.isdir(target):
        ui.say(ui.err("The system cannot find the path specified."))
        return 1
    ctx.sh.dirstack.append(os.getcwd())
    ctx.sh.chdir(target)
    return 0


@builtin("popd", cat="Folders", summary="Go back to the folder pushd remembered.")
def cmd_popd(ctx):
    if not ctx.sh.dirstack:
        return 0
    ctx.sh.chdir(ctx.sh.dirstack.pop())
    return 0


@builtin("dirs", cat="Folders", summary="Show the pushd stack.")
def cmd_dirs(ctx):
    for i, d in enumerate([os.getcwd()] + list(reversed(ctx.sh.dirstack))):
        ui.say(ui.dim(f"{i:>2} ") + d)
    return 0


@builtin("up", cat="Folders", summary="Go up N folders (default 1).", usage="up [N]")
def cmd_up(ctx):
    n = int(ctx.args[0]) if ctx.args and ctx.args[0].isdigit() else 1
    ctx.sh.chdir(os.path.join(*([".."] * n)))
    return 0


@builtin("mkcd", cat="Folders", summary="Make a folder (and its parents) and go into it.", usage="mkcd <folder>")
def cmd_mkcd(ctx):
    if not ctx.args:
        raise Usage()
    p = resolve(" ".join(ctx.args))
    os.makedirs(p, exist_ok=True)
    ctx.sh.chdir(p)
    return 0


def _frecency(path, rec):
    count, last = rec
    age = time.time() - last
    w = 4 if age < 3600 else 2 if age < 86400 else 0.5 if age < 604800 else 0.25
    return count * w


def _complete_jump(sh, args, word):
    return list(sh.config["bookmarks"])


@builtin("j", "jump", "z", cat="Folders", complete=_complete_jump,
         summary="Jump to a folder you've visited before by part of its name.",
         usage="j proj        best match for 'proj'\nj proj api    must match both\nj             pick from a menu")
def cmd_jump(ctx):
    sh = ctx.sh
    data = ui.load_json("jumps.json", {})
    words = [w.lower() for w in ctx.args]
    if len(words) == 1 and words[0] in sh.config["bookmarks"]:
        sh.chdir(sh.config["bookmarks"][words[0]])
        return 0
    ranked = sorted(((p, _frecency(p, r)) for p, r in data.items() if os.path.isdir(p)),
                    key=lambda x: -x[1])
    if not words:
        pick = ui.choose([p for p, _ in ranked[:200]], ui.b("Jump to"))
        if pick:
            sh.chdir(pick)
        return 0 if pick else 1
    def fits(p):
        low = p.lower()
        pos = 0
        for w in words:
            i = low.find(w, pos)
            if i < 0:
                return False
            pos = i + len(w)
        return words[-1] in os.path.basename(low.rstrip("\\/")) or len(words) > 1
    hits = [p for p, _ in ranked if fits(p) and os.path.normcase(p) != os.path.normcase(os.getcwd())]
    if not hits:
        ui.say(ui.err(f"No folder you've visited matches '{' '.join(ctx.args)}'.") +
               ui.dim(" Commands learns folders as you cd into them."))
        return 1
    sh.chdir(hits[0])
    ui.say(ui.dim("→ ") + ui.paint(hits[0], "path"))
    return 0


@builtin("mark", "bookmark", cat="Folders", complete=_complete_jump,
         summary="Name this folder so 'j name' or 'cd name' goes there.",
         usage="mark <name>          bookmark this folder\nmark                 list bookmarks\nmark -d <name>       delete one")
def cmd_mark(ctx):
    bm = ctx.sh.config["bookmarks"]
    if not ctx.args:
        if not bm:
            ui.say(ui.dim("No bookmarks yet. In a folder, type: mark <name>"))
        ui.table([[ui.acc(k), v] for k, v in sorted(bm.items())])
        return 0
    if ctx.args[0] in ("-d", "/d", "--delete"):
        for n in ctx.args[1:]:
            bm.pop(n.lower(), None)
    else:
        bm[ctx.args[0].lower()] = os.getcwd()
        ui.say(ui.ok(f"Bookmarked {os.getcwd()} as '{ctx.args[0]}'."))
    ctx.sh.save_config()
    return 0


# ----------------------------------------------------------- variables
_SET_A = re.compile(r"^[\s\d+\-*/%()<>&|^~!xa-fA-F]*$")


def _set_arith(expr):
    """set /a: integer math with variables, like CMD."""
    results = None
    for part in expr.split(","):
        part = part.strip()
        m = re.match(r"^([A-Za-z_]\w*)\s*([+\-*/%&|^]|<<|>>)?=(?!=)\s*(.*)$", part)
        target, op, rhs = (m.group(1), m.group(2), m.group(3)) if m else (None, None, part)
        def var(mm):
            v = lexer.getenv_ci(mm.group(0))
            try:
                return str(int(v, 0)) if v else "0"
            except ValueError:
                return "0"
        py = re.sub(r"\b[A-Za-z_]\w*\b", var, rhs.replace('"', ""))
        py = py.replace("/", "//")
        if not re.fullmatch(r"[\s\d+\-*/%()<>&|^~xX]*", py):
            raise Usage("set /a only does whole-number math")
        val = int(eval(py, {"__builtins__": {}}, {})) if py.strip() else 0
        if target:
            if op:
                cur = int(lexer.getenv_ci(target) or 0)
                val = int(eval(f"{cur} {op.replace('/', '//')} {val}", {"__builtins__": {}}, {}))
            os.environ[target] = str(val)
        results = val
    return results


@builtin("set", cat="Variables", summary="Show or set variables for this window (like CMD).",
         usage='set                 show all\nset PY             show the ones starting with PY\n'
               'set NAME=value     set (empty value removes it)\nset /a X=5*3       whole-number math\n'
               'set /p NAME=Ask?   ask the user\nTo keep a variable forever use: env set NAME value')
def cmd_set(ctx):
    raw = " ".join(ctx.args)
    if not raw:
        for k in sorted(os.environ, key=str.lower):
            ui.say(f"{ui.paint(k, 'var')}={os.environ[k]}")
        return 0
    low = raw.lower()
    if low.startswith("/a"):
        ui.say(str(_set_arith(raw[2:].strip())))
        return 0
    if low.startswith("/p"):
        rest = raw[2:].strip().strip('"')
        name, _, q = rest.partition("=")
        ans = ctx.stdin.splitlines()[0] if ctx.stdin else (ui.ask(q) if ui.can_interact() else input(q))
        if ans is None:
            return 1
        os.environ[name.strip()] = ans
        return 0
    if raw.startswith('"') and raw.endswith('"'):
        raw = raw[1:-1]
    if "=" not in raw:
        hits = [(k, v) for k, v in os.environ.items() if k.lower().startswith(raw.lower())]
        if not hits:
            ui.say(ui.err(f"Environment variable {raw} not defined"))
            return 1
        for k, v in sorted(hits):
            ui.say(f"{ui.paint(k, 'var')}={v}")
        return 0
    name, _, value = raw.partition("=")
    if not name:
        raise Usage("The syntax of the command is incorrect.")
    if value == "":
        os.environ.pop(name, None)
    else:
        os.environ[name] = value
    return 0


@builtin("setx", cat="Variables", summary="Like Windows setx, but with no 1024-character limit and a backup.",
         usage="setx NAME value         saves it for your user\nsetx NAME value /m      for everyone (admin)")
def cmd_setx(ctx):
    args = list(ctx.args)
    machine = any(a.lower() == "/m" for a in args)
    args = [a for a in args if a.lower() != "/m"]
    if len(args) < 2:
        raise Usage()
    scope = "machine" if machine else "user"
    name, value = args[0], " ".join(args[1:])
    if name.lower() == "path":
        ui.say(ui.warn("setx PATH replaces the whole PATH. Commands will add the missing folders instead "
                       "of replacing, so nothing gets wiped."))
        added = 0
        for p in envtools.split(value):
            if envtools.find_entry(envtools.get_path(scope), p) < 0 and \
                    envtools.find_entry(envtools.get_path("machine" if scope == "user" else "user"), p) < 0:
                envtools.add_path(scope, p)
                added += 1
        ui.say(ui.ok(f"SUCCESS: added {added} folder(s) to the {scope} PATH."))
        return 0
    bk = envtools.set_var(scope, name, value)
    ui.say(ui.ok(f"SUCCESS: {name} saved for the {scope} ({len(value)} characters).") + ui.dim(f"  backup: {bk}"))
    return 0


@builtin("echo", cat="Shell", summary="Print text. echo. prints an empty line.", usage="echo <text>")
def cmd_echo(ctx):
    if ctx.name == "echo.":
        ui.say(" ".join(ctx.args))
        return 0
    if not ctx.args:
        ui.say("ECHO is on.")
        return 0
    text = " ".join(ctx.args)
    if ctx.name == "echo" and text.lower() in ("on", "off"):
        return 0
    ui.say(text)
    return 0


BUILTINS["echo."] = BUILTINS["echo"]


@builtin("cls", "clear", cat="Shell", summary="Clear the screen.")
def cmd_cls(ctx):
    term.write("\x1b[2J\x1b[3J\x1b[H")
    return 0


@builtin("exit", "quit", cat="Shell", summary="Close Commands.", usage="exit [code]")
def cmd_exit(ctx):
    from .shell import ExitShell
    code = 0
    args = [a for a in ctx.args if a.lower() != "/b"]
    if args:
        try:
            code = int(args[0])
        except ValueError:
            pass
    raise ExitShell(code)


@builtin("title", cat="Shell", summary="Set the window title.", usage="title <text>")
def cmd_title(ctx):
    ctx.sh.title = " ".join(ctx.args) or None
    ctx.sh.set_title()
    return 0


CMD_COLORS = "0123456789ABCDEF"


@builtin("color", cat="Look", summary="CMD-style colors: 0A = green on black. 'color' alone resets.",
         usage="color 0A     (first = background, second = text; 0 black 1 blue 2 green 3 aqua 4 red\n"
               "              5 purple 6 yellow 7 white 8 gray 9-F bright)")
def cmd_color(ctx):
    if not ctx.args:
        term.write("\x1b[0m")
        return cmd_theme_set(ctx.sh, ctx.sh.config.get("theme") or "commands")
    code = ctx.args[0].upper()
    if len(code) != 2 or any(c not in CMD_COLORS for c in code) or code[0] == code[1]:
        raise Usage()
    sgr = [40, 44, 42, 46, 41, 45, 43, 47, 100, 104, 102, 106, 101, 105, 103, 107]
    fgc = [30, 34, 32, 36, 31, 35, 33, 37, 90, 94, 92, 96, 91, 95, 93, 97]
    term.write(f"\x1b[{sgr[CMD_COLORS.index(code[0])]};{fgc[CMD_COLORS.index(code[1])]}m\x1b[2J\x1b[H")
    ui.set_theme("classic")
    return 0


@builtin("prompt", cat="Look", summary="Change the prompt with CMD codes. 'prompt' alone = the Commands prompt.",
         usage="prompt $P$G      classic CMD\nprompt $T $P$_$G   time, path, new line\n"
               "Codes: $P path $G > $L < $N drive $T time $D date $_ new line $R git branch $X exit code $V version")
def cmd_prompt(ctx):
    ctx.sh.config["prompt"] = " ".join(ctx.args) or None
    ctx.sh.save_config()
    return 0


def cmd_theme_set(sh, name):
    if not ui.set_theme(name):
        ui.say(ui.err(f"No theme called '{name}'."))
        return 1
    sh.config["theme"] = name
    sh.save_config()
    return 0


@builtin("theme", cat="Look", complete=lambda sh, a, w: list(ui.THEMES),
         summary="Pick a color theme.", usage="theme              pick from a menu\ntheme <name>       " +
                                                " | ".join(ui.THEMES))
def cmd_theme(ctx):
    if ctx.args:
        return cmd_theme_set(ctx.sh, ctx.args[0].lower())
    if not ui.can_interact():
        for t in ui.THEMES:
            ui.say(("* " if t == ui.theme_name() else "  ") + t)
        return 0
    names = list(ui.THEMES)
    samples = []
    current = ui.theme_name()
    for n in names:
        ui.set_theme(n)
        samples.append(f"{n:<10} " + ui.paint("C:\\Users", "path") + ui.paint(" › ", "glyph") +
                       ui.paint("install", "odus") + " " + ui.paint('"vlc"', "string") + " " +
                       ui.paint("&&", "op") + " " + ui.paint("git", "exe") + " " + ui.paint("--help", "flag"))
    ui.set_theme(current)
    pick = ui.choose(samples, ui.b("Theme"), start=names.index(current))
    if pick:
        return cmd_theme_set(ctx.sh, pick.split()[0])
    return 0


@builtin("config", cat="Look", complete=lambda sh, a, w: list(sh.config) if not a else [],
         summary="See or change Commands settings.",
         usage="config                       show all\nconfig banner off            no start-up banner\n"
               "config suggestions off       no grey history suggestions\n"
               "config live_refresh off      don't pick up PATH changes from other programs\n"
               "config odus_fuzzy off        don't hand unknown words to Odus\n"
               "config notify_after 30       desktop notification when a command takes 30s+ (0 = off)")
def cmd_config(ctx):
    cfg = ctx.sh.config
    simple = {k: v for k, v in cfg.items() if not isinstance(v, dict)}
    if not ctx.args:
        ui.table([[k, ui.acc(str(v))] for k, v in sorted(simple.items())])
        ui.say(ui.dim(f"\nStored in {os.path.join(ui.CONFIG_DIR, 'config.json')}"))
        return 0
    key = ctx.args[0]
    if len(ctx.args) == 1:
        ui.say(str(cfg.get(key)))
        return 0
    val = " ".join(ctx.args[1:])
    conv = {"on": True, "true": True, "yes": True, "off": False, "false": False, "no": False}
    if val.lower() in conv:
        v = conv[val.lower()]
    elif val.isdigit():
        v = int(val)
    elif val.lower() in ("none", "default", "reset"):
        v = None
    else:
        v = val
    cfg[key] = v
    ctx.sh.save_config()
    if key == "theme" and isinstance(v, str):
        ui.set_theme(v)
    return 0


# ------------------------------------------------------------- aliases
@builtin("alias", "doskey", cat="Shell", summary="Make your own short commands.",
         usage="alias ll=ls -l          ll runs ls -l (extra words go on the end)\n"
               "alias gp=git push $*    $* = all words, $1..$9 = one word\n"
               "alias                   list them\nalias ll=               remove")
def cmd_alias(ctx):
    al = ctx.sh.config["aliases"]
    raw = " ".join(ctx.args)
    if ctx.name == "doskey":
        raw = re.sub(r"^/macros\S*\s*", "", raw, flags=re.I)
    if not raw:
        if not al:
            ui.say(ui.dim("No aliases yet. Example: alias ll=ls -l"))
        for k, v in sorted(al.items()):
            ui.say(f"{ui.paint(k, 'builtin', bold=True)}={v}")
        return 0
    if "=" not in raw:
        ui.say(al.get(raw.lower(), ui.err(f"No alias '{raw}'.")))
        return 0
    name, _, body = raw.partition("=")
    name = name.strip().lower()
    if not re.fullmatch(r"[\w.\-+!]+", name):
        raise Usage("Alias names can only have letters, numbers and . - + !")
    if body.strip():
        al[name] = body.strip()
    else:
        al.pop(name, None)
    ctx.sh.save_config()
    return 0


@builtin("unalias", cat="Shell", summary="Remove an alias.", usage="unalias <name>")
def cmd_unalias(ctx):
    for n in ctx.args:
        ctx.sh.config["aliases"].pop(n.lower(), None)
    ctx.sh.save_config()
    return 0


# ------------------------------------------------------------- history
@builtin("history", cat="Shell", summary="Show, search or clear what you've typed.",
         usage="history            last 30\nhistory 100        last 100\nhistory git        search\n"
               "history -c         clear it\nPress F7 or Ctrl+R for a menu, F3 for the last command.")
def cmd_history(ctx):
    h = ctx.sh.history
    if ctx.args and ctx.args[0] in ("-c", "/c", "--clear"):
        h.clear()
        try:
            os.remove(os.path.join(ui.CONFIG_DIR, "history.txt"))
        except OSError:
            pass
        ui.say(ui.ok("History cleared."))
        return 0
    n = 30
    q = None
    if ctx.args and ctx.args[0].isdigit():
        n = int(ctx.args[0])
    elif ctx.args:
        q = " ".join(ctx.args).lower()
    items = [(i, l) for i, l in enumerate(h, 1) if q is None or q in l.lower()]
    for i, l in items[-n:]:
        ui.say(ui.dim(f"{i:>5}  ") + ctx.sh.highlight(l) if ui.color_on() else f"{i:>5}  {l}")
    return 0


@builtin("!", "again", cat="Shell", summary="Run a command from history again.",
         usage="!             the last one\n! 42          number 42 from 'history'\n! git         the last starting with git")
def cmd_again(ctx):
    h = [l for l in ctx.sh.history if not l.startswith(("!", "again"))]
    if not h:
        return 1
    if not ctx.args:
        line = h[-1]
    elif ctx.args[0].isdigit():
        i = int(ctx.args[0]) - 1
        line = ctx.sh.history[i] if 0 <= i < len(ctx.sh.history) else None
    else:
        pre = " ".join(ctx.args)
        line = next((l for l in reversed(h) if l.startswith(pre)), None)
    if not line:
        ui.say(ui.err("Nothing like that in history."))
        return 1
    ui.say(ui.dim("› ") + line)
    return ctx.sh.run_line(line)


# ---------------------------------------------------------------- help
CAT_ORDER = ["Shell", "Folders", "Files", "Variables", "PATH", "Look", "System", "Network", "Text & data",
             "Apps & API", "Odus"]


@builtin("help", "?", cat="Shell", complete=lambda sh, a, w: sorted(BUILTINS),
         summary="Everything Commands can do. help <command> for details.", usage="help [command]")
def cmd_help(ctx):
    if ctx.args:
        name = ctx.args[0].lower()
        b = BUILTINS.get(name)
        if b:
            ctx.sh.print_usage(b)
            return 0
        if name in backends.ODUS_ALWAYS or name == "odus":
            return backends.run_odus(["help"])[0]
        if IS_WIN:
            return backends.cmd_line(f"help {name}")[0]
        return backends.sh_line(f"{name} --help")[0]
    seen, cats = set(), {}
    for n, b in BUILTINS.items():
        if b.hidden or id(b) in seen:
            continue
        seen.add(id(b))
        cats.setdefault(b.cat, []).append(b)
    cols, _ = term.size()
    ui.say(ui.b(f"Commands {VERSION}") + ui.dim("  - every CMD and PowerShell command works too, plus Odus."))
    ui.say()
    w = 14
    for cat in CAT_ORDER + sorted(set(cats) - set(CAT_ORDER)):
        if cat not in cats:
            continue
        ui.say(ui.acc(cat))
        for b in sorted(cats[cat], key=lambda b: b.name):
            summ = b.summary
            if len(summ) > cols - w - 4:
                summ = summ[:cols - w - 5] + "…"
            ui.say("  " + ui.paint(ui.pad(b.name, w), "builtin", bold=True) + summ)
        ui.say()
    ui.say(ui.acc("Odus") + ui.dim(" (runs by itself for these words)"))
    ui.say("  " + ui.paint(ui.pad("install", w), "odus", bold=True) + "install Google Chrome · uninstall · update · search")
    ui.say("  " + ui.paint(ui.pad("compress", w), "odus", bold=True) + "videos/photos · compress-app · download <link> · clone")
    ui.say("  " + ui.paint(ui.pad("fast / ufa", w), "odus", bold=True) + "presets: fast update · ufa compress *.mp4")
    ui.say()
    ui.say(ui.acc("Choosing who runs it"))
    ui.say("  " + ui.pad("ps: …", w) + "PowerShell    " + ui.pad("cmd: …", 10) + "CMD    " +
           ui.pad("sh: …", 9) + "sh/bash    " + "odus: …  Odus")
    ui.say("  " + ui.dim("Get-Thing and $vars go to PowerShell by themselves. A built-in hides a program with the "
                         "same name; add .exe (grep.exe) to run the program."))
    ui.say()
    ui.say(ui.acc("Keys") + ui.dim("  Tab complete (twice: menu) · → or End take the suggestion · ↑↓ history by prefix"))
    ui.say(ui.dim("      F7/Ctrl+R history menu · F3 last command · Esc clear line · Ctrl+L clear screen"))
    return 0


@builtin("which", "whence", cat="Shell", summary="What runs when you type a name, and every match on PATH.",
         usage="which git          (built-in, alias, Odus, program, cmdlet...)")
def cmd_which(ctx):
    if not ctx.args:
        raise Usage()
    rc = 0
    for name in ctx.args:
        sh = ctx.sh
        k = sh.kind_of(name)
        if k == "alias":
            ui.say(f"{ui.b(name)}: alias for {sh.config['aliases'][name.lower()]}")
        elif k == "builtin":
            ui.say(f"{ui.b(name)}: Commands built-in" + ui.dim(f" - {BUILTINS[name.lower()].summary}"))
        elif k == "odus":
            ui.say(f"{ui.b(name)}: Odus" + ui.dim(f" ({backends.find_odus() or 'not found'})"))
        elif k == "app":
            ui.say(f"{ui.b(name)}: Commands app - {sh.apps()[name.lower()]}")
        elif k == "cmd":
            ui.say(f"{ui.b(name)}: CMD built-in command")
        elif k == "cmdlet":
            ui.say(f"{ui.b(name)}: PowerShell command")
        hits = envtools.which_all(name)
        for i, p in enumerate(hits):
            ui.say(("  " if k not in ("exe",) or i else "") + (ui.ok(p) if i == 0 and k == "exe" else ui.dim(p)) +
                   (ui.dim("   (hidden by the one above)") if i else ""))
        if k == "unknown" and not hits:
            ui.say(ui.err(f"{name}: not found"))
            rc = 1
    return rc


@builtin("version", cat="Shell", summary="Versions of Commands, Odus, Python and the system.")
def cmd_version(ctx):
    import platform
    ui.table([["Commands", VERSION],
              ["Odus", backends.odus_version() or "not found"],
              ["Python", platform.python_version()],
              ["System", f"{platform.system()} {platform.release()} ({platform.version()})"],
              ["PowerShell", backends.powershell_exe() or "not found"]])
    return 0


@builtin("reload", cat="Shell", summary="Re-read settings and look for Odus again.")
def cmd_reload(ctx):
    from .shell import DEFAULT_CONFIG
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(ui.load_json("config.json", {}))
    ctx.sh.config = cfg
    ui.set_theme(cfg.get("theme") or "commands")
    backends.reset_odus()
    ctx.sh._exe_names = None
    ctx.sh._exe_cache.clear()
    ui.say(ui.ok("Reloaded."))
    return 0


@builtin("source", "run-script", cat="Shell",
         summary="Run a file of Commands lines in this window (variables and folder changes stay).",
         usage="source setup.cmds")
def cmd_source(ctx):
    if not ctx.args:
        raise Usage()
    rc = 0
    with open(resolve(ctx.args[0]), encoding="utf-8") as f:
        for line in f:
            rc = ctx.sh.run_line(line.rstrip("\n"))
    return rc


@builtin("call", cat="Shell", summary="Run a .bat/.cmd and keep the variables it sets (like CMD).",
         usage="call vcvars64.bat")
def cmd_call(ctx):
    if not ctx.args:
        raise Usage()
    target = ctx.args[0]
    path = target if os.path.isfile(target) else ctx.sh.which(target)
    if path and path.lower().endswith((".bat", ".cmd")):
        return backends.run_batch(path, [lexer.quote(a) for a in ctx.args[1:]])[0]
    return ctx.sh.run_line(lexer.join(ctx.args))


# ------------------------------------------------------------- timing
@builtin("timeit", "measure", cat="Shell", summary="Run a command and show how long it took.",
         usage="timeit npm run build")
def cmd_timeit(ctx):
    if not ctx.args:
        raise Usage()
    t = time.perf_counter()
    rc = ctx.sh.run_line(lexer.join(ctx.args) if len(ctx.args) > 1 else ctx.args[0])
    dt = time.perf_counter() - t
    shown = f"{dt * 1000:.0f} ms" if dt < 1 else f"{dt:.2f} s" if dt < 60 else f"{int(dt // 60)}m {dt % 60:.1f}s"
    ui.say(ui.dim("took ") + ui.acc(shown) + ui.dim(f" · exit code {rc}"))
    return rc


@builtin("watch", cat="Shell", summary="Run a command again every few seconds until you press a key.",
         usage="watch [-n seconds] <command>      watch -n 1 ports")
def cmd_watch(ctx):
    o, rest = ctx.opts({"n|interval": float})
    if not rest:
        raise Usage()
    every = o["interval"] or 2
    line = lexer.join(rest) if len(rest) > 1 else rest[0]
    try:
        while True:
            term.write("\x1b[2J\x1b[H")
            ui.say(ui.dim(f"every {every:g}s · {time.strftime('%H:%M:%S')} · any key stops · ") + ui.b(line))
            ui.say()
            ctx.sh.run_line(line)
            end = time.time() + every
            while time.time() < end:
                if term.is_tty() and term.key_ready():
                    with term.raw_mode():
                        term.read_key()
                    return 0
                time.sleep(0.05)
    except KeyboardInterrupt:
        return 0


@builtin("repeat", cat="Shell", summary="Run a command N times.", usage="repeat 5 curl https://example.com")
def cmd_repeat(ctx):
    if len(ctx.args) < 2 or not ctx.args[0].isdigit():
        raise Usage()
    rc = 0
    for i in range(int(ctx.args[0])):
        rc = ctx.sh.run_line(lexer.join(ctx.args[1:]))
    return rc


@builtin("sleep", "wait", cat="Shell", summary="Wait a number of seconds (2.5, 1m, 1h work too).",
         usage="sleep 3")
def cmd_sleep(ctx):
    if not ctx.args:
        raise Usage()
    time.sleep(parse_duration(ctx.args[0]))
    return 0


def parse_duration(s: str) -> float:
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(ms|s|m|h|d)?", s.strip().lower())
    if not m:
        raise Usage(f"'{s}' isn't a time like 30, 30s, 5m, 2h")
    n = float(m.group(1))
    return n * {"ms": 0.001, "s": 1, "m": 60, "h": 3600, "d": 86400, None: 1}[m.group(2)]


# --------------------------------------------------------------- admin
def _elevate_windows(line: str | None):
    import ctypes
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    params = f'-m commands --cd "{os.getcwd()}"'
    if line:
        params += " --pause -c " + subprocess.list2cmdline([line])
    py = backends.python_exe()
    env_cd = os.getcwd()
    r = ctypes.windll.shell32.ShellExecuteW(None, "runas", py, params, here, 1)
    return r > 32


@builtin("sudo", "admin", "elevate", cat="System",
         summary="Run a command as administrator (opens an admin Commands window).",
         usage="sudo path add C:\\Tools --machine\nsudo             open an admin Commands window\nsudo !!          last command as admin")
def cmd_sudo(ctx):
    line = lexer.join(ctx.args) if ctx.args else None
    if line in ("!!", "!"):
        line = ctx.sh.history[-2] if len(ctx.sh.history) >= 2 else None
    if IS_WIN:
        if envtools.is_admin():
            return ctx.sh.run_line(line) if line else 0
        if not _elevate_windows(line):
            ui.say(ui.err("Didn't get administrator rights (cancelled?)."))
            return 1
        ui.say(ui.dim("Opened an administrator Commands window."))
        return 0
    sudo = shutil.which("sudo")
    if not sudo:
        ui.say(ui.err("sudo isn't installed."))
        return 1
    if not line:
        return backends.run_process([sudo, "-E", backends.python_exe(), "-m", "commands"])[0]
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ, PYTHONPATH=here)
    return backends.run_process([sudo, "-E", backends.python_exe(), "-m", "commands", "-c", line], env=env)[0]


# ---------------------------------------------------------------- Odus
@builtin("odus-get", cat="Odus", hidden=True, summary="Download Odus.")
def cmd_odus_get(ctx):
    have = backends.find_odus()
    if have:
        ui.say(ui.ok(f"Odus is already here: {have}"))
        return 0
    ui.say("Downloading Odus from github.com/ArthWarrior3201/Odus ...")
    try:
        p = backends.download_odus()
    except Exception as e:
        ui.say(ui.err(f"Couldn't download Odus: {e}"))
        return 1
    ui.say(ui.ok(f"Odus is ready: {p}"))
    return 0


@builtin("apps", cat="Odus", summary="List installed apps (through Odus).")
def cmd_apps(ctx):
    return backends.run_odus(["list"] + ctx.args, ctx.stdin)[0]


@builtin("odus-path", cat="Odus", complete=lambda sh, a, w: ["add", "remove", "status"] if not a else [],
         summary="Put Odus on PATH (registry, never setx) or take it off.",
         usage="odus-path add       add Odus's folder to your PATH (backup first)\n"
               "odus-path remove    take it off again; Commands then won't run Odus\n"
               "odus-path           show where things stand")
def cmd_odus_path(ctx):
    from . import odus_setup as osu
    sub = ctx.args[0].lower() if ctx.args else "status"
    if not osu.odus_file():
        ui.say(ui.err("Odus isn't on this computer.") + " Run " + ui.b("odus get") + " first.")
        return 1
    if sub == "add":
        bk = osu.add()
        ctx.sh.config[osu.KEY] = "yes"
        ctx.sh.save_config()
        ui.say(ui.ok(f"Odus is on your PATH: {osu.odus_dir()}") + (ui.dim(f"\n  old PATH saved: {bk}") if bk else ""))
        return 0
    if sub == "remove":
        osu.remove()
        ctx.sh.config[osu.KEY] = "no"
        ctx.sh.save_config()
        ui.say(ui.ok("Odus is off your PATH.") + ui.dim(" Run it by its file: ") + ui.b(osu.launcher_name()))
        return 0
    ui.say(f"Odus: {ui.b(osu.odus_file())}")
    ui.say("On PATH: " + (ui.ok("yes") if osu.on_path() else ui.warn("no")) +
           ui.dim(f"   (your answer: {ctx.sh.config.get(osu.KEY) or 'not asked yet'})"))
    return 0
