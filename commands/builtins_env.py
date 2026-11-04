"""path and env: permanent PATH and variables without setx's 1024-character
limit and without ever wiping what's there."""
import os
import sys

from . import ui, envtools, lexer
from .registry import builtin, Usage

IS_WIN = os.name == "nt"
# set by Windows for every login/process, not stored with the saved variables
WINDOWS_GIVEN = {"ALLUSERSPROFILE", "APPDATA", "LOCALAPPDATA", "COMMONPROGRAMFILES", "COMMONPROGRAMFILES(X86)",
                 "COMMONPROGRAMW6432", "COMPUTERNAME", "HOMEDRIVE", "HOMEPATH", "LOGONSERVER", "PROGRAMDATA",
                 "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432", "PUBLIC", "SESSIONNAME", "SYSTEMDRIVE",
                 "SYSTEMROOT", "USERDOMAIN", "USERDOMAIN_ROAMINGPROFILE", "USERNAME", "USERPROFILE", "PROMPT",
                 "ONEDRIVE", "ONEDRIVECONSUMER", "ONEDRIVECOMMERCIAL", "CLIENTNAME", "PSMODULEPATH"}
SUBS = ["list", "add", "remove", "clean", "move", "find", "doctor", "backups", "restore", "export", "import", "refresh"]


def _scope(o, default="user"):
    if o.get("machine") or o.get("system") or o.get("m"):
        return "machine"
    if o.get("session") or o.get("temp"):
        return "session"
    if o.get("user"):
        return "user"
    return default


def _admin_hint(e, line):
    ui.say(ui.err(str(e)))
    if IS_WIN:
        ui.say(ui.dim("Run it as administrator: ") + ui.b("sudo " + line))
    return 1


def _show(scope, entries, highlight=None):
    p = envtools.problems(entries)
    for i, e in enumerate(entries):
        mark = ui.ok("✓") if i not in p["missing"] else ui.err("✗")
        notes = []
        if i in p["missing"]:
            notes.append(ui.err("missing"))
        if i in p["duplicates"]:
            notes.append(ui.warn("duplicate"))
        if "%" in e:
            notes.append(ui.dim("→ " + os.path.expandvars(e)))
        line = f"{ui.dim(f'{i + 1:>3}')} {mark} {ui.paint(e, 'path') if highlight is not None and i == highlight else e}"
        ui.say(line + ("  " + "  ".join(notes) if notes else ""))
    return p


def _complete_path(sh, args, word):
    if not args:
        return SUBS
    if args[0] == "remove":
        return envtools.get_path("user") + (envtools.get_path("machine") if IS_WIN else [])
    return ["--user", "--machine", "--session", "--front"]


@builtin("path", cat="PATH", complete=_complete_path,
         summary="See and change PATH for good - no 1024-character limit, backups, never wiped.",
         usage="path                          this window's PATH, numbered, with problems marked\n"
               "path list [--user|--machine]  what's saved for you / for everyone\n"
               "path add <folder> [--front] [--machine] [--session]\n"
               "path remove <folder or number> [--machine]\n"
               "path clean [--machine] [--dry]    drop missing and duplicate folders\n"
               "path move <folder or number> <position>\n"
               "path find <program>           every copy on PATH, and which one wins\n"
               "path doctor                   check everything\n"
               "path backups | path restore [file]\n"
               "path export <file> | path import <file>\n"
               "path refresh                  pick up changes made by installers\n"
               "path C:\\a;C:\\b              (CMD style: set this window's PATH only)")
def cmd_path(ctx):
    o, rest = ctx.opts({"user": bool, "machine": bool, "system": bool, "session": bool, "temp": bool,
                        "front": bool, "start": bool, "before": str, "dry": bool, "y|yes": bool, "m": bool})
    line = "path " + lexer.join(ctx.args)
    if not rest:
        if any(o[k] for k in ("user", "machine", "system")):
            rest = ["list"]
        else:
            ui.say(ui.dim("PATH of this window (path list --user / --machine for what's saved):"))
            _show("session", envtools.get_path("session"))
            return 0
    sub = rest[0].lower()
    args = rest[1:]
    try:
        if sub not in SUBS:  # CMD compatibility: path X;Y sets this window's PATH
            raw = " ".join(ctx.args)
            if raw.strip() == ";":
                os.environ["PATH"] = ""
                return 0
            os.environ["PATH"] = raw.replace("%PATH%", os.environ.get("PATH", ""))
            return 0
        if sub == "list":
            scope = _scope(o, "user")
            entries = envtools.get_path(scope)
            total = len(envtools.join(entries))
            ui.say(ui.b(f"{scope.capitalize()} PATH") + ui.dim(f"  {len(entries)} folders · {total} characters"))
            _show(scope, entries)
            return 0
        if sub == "add":
            if not args:
                raise Usage("path add needs a folder")
            scope = _scope(o)
            rc = 0
            for folder in args:
                folder = os.path.abspath(os.path.expanduser(folder)) if not folder.startswith("%") else folder
                if not os.path.isdir(os.path.expandvars(folder)) and not o["yes"]:
                    if not ui.confirm(ui.warn(f"{folder} doesn't exist. Add it anyway?")):
                        rc = 1
                        continue
                res = envtools.add_path(scope, folder, front=o["front"] or o["start"], before=o["before"])
                if res.get("already"):
                    ui.say(ui.dim(f"Already in the {scope} PATH: ") + folder)
                else:
                    where = "start" if (o["front"] or o["start"]) else "end"
                    ui.say(ui.ok(f"Added to the {where} of the {scope} PATH: ") + folder +
                           (ui.dim(f"\n  backup: {res['backup']}") if res.get("backup") else ""))
                    if scope != "session":
                        ui.say(ui.dim("  This window has it already. Other open terminals: restart them (or use 'path refresh')."))
            return rc
        if sub == "remove":
            if not args:
                raise Usage("path remove needs a folder or a number")
            rc = 0
            for what in args:
                if what.isdigit() and not (o["user"] or o["machine"] or o["system"] or o["session"]):
                    scope = "user"
                elif o["user"] or o["machine"] or o["system"] or o["session"]:
                    scope = _scope(o)
                else:  # find where it is
                    scope = None
                    for s in ("user", "machine"):
                        if envtools.find_entry(envtools.get_path(s), what) >= 0:
                            scope = s
                            break
                    if scope is None:
                        scope = "session"
                res = envtools.remove_path(scope, what)
                if res.get("missing"):
                    ui.say(ui.err(f"Not in the {scope} PATH: {what}"))
                    rc = 1
                else:
                    ui.say(ui.ok(f"Removed from the {scope} PATH: ") + res["entry"] +
                           (ui.dim(f"\n  backup: {res['backup']}") if res.get("backup") else ""))
            return rc
        if sub == "clean":
            scope = _scope(o)
            res = envtools.clean_path(scope, dry=True)
            if not res["removed"]:
                ui.say(ui.ok(f"The {scope} PATH is clean."))
                return 0
            ui.say(ui.b(f"These would be removed from the {scope} PATH:"))
            for e in res["removed"]:
                ui.say("  " + ui.err("−") + " " + e)
            if o["dry"]:
                return 0
            if not o["yes"] and not ui.confirm(f"Remove {len(res['removed'])} entries?"):
                ui.say(ui.dim("Nothing changed."))
                return 1
            res = envtools.clean_path(scope)
            ui.say(ui.ok(f"Cleaned. ") + ui.dim(f"backup: {res['backup']}"))
            return 0
        if sub == "move":
            if len(args) < 2 or not args[1].isdigit():
                raise Usage("path move <folder or number> <position>")
            scope = _scope(o)
            res = envtools.move_path(scope, args[0], int(args[1]) - 1)
            ui.say(ui.ok(f"Moved {res['entry']} to position {res['position']}."))
            return 0
        if sub == "find":
            if not args:
                raise Usage("path find <program>")
            hits = envtools.which_all(args[0])
            if not hits:
                ui.say(ui.err(f"{args[0]} isn't on PATH."))
                return 1
            for i, h in enumerate(hits):
                ui.say((ui.ok("runs   ") if i == 0 else ui.dim("hidden ")) + h)
            return 0
        if sub == "doctor":
            return _doctor()
        if sub == "backups":
            files = envtools.list_backups("PATH")
            if not files:
                ui.say(ui.dim("No PATH backups yet (one is made before every change)."))
            for f in files[:30]:
                n = len(envtools.split(open(f, encoding="utf-8").read().strip()))
                ui.say(f"{ui.dim(os.path.basename(f))}  {n} folders")
            return 0
        if sub == "restore":
            scope = _scope(o)
            files = [f for f in envtools.list_backups("PATH") if f"-{scope}-PATH" in os.path.basename(f)]
            target = args[0] if args else (ui.choose([os.path.basename(f) for f in files], ui.b("Restore which backup?"))
                                           if files else None)
            if not target:
                ui.say(ui.dim("Nothing restored."))
                return 1
            if not os.path.isabs(target):
                target = os.path.join(envtools.BACKUP_DIR, target)
            entries = envtools.split(open(target, encoding="utf-8").read().strip())
            ui.say(f"That backup has {len(entries)} folders; the {scope} PATH has {len(envtools.get_path(scope))} now.")
            if not o["yes"] and not ui.confirm(f"Replace the {scope} PATH with it?"):
                return 1
            bk = envtools.restore_path(scope, target)
            ui.say(ui.ok("Restored.") + ui.dim(f" (the PATH before this was saved too: {bk})"))
            return 0
        if sub == "export":
            if not args:
                raise Usage("path export <file>")
            scope = _scope(o)
            with open(args[0], "w", encoding="utf-8") as f:
                f.write("\n".join(envtools.get_path(scope)) + "\n")
            ui.say(ui.ok(f"Saved the {scope} PATH to {args[0]}."))
            return 0
        if sub == "import":
            if not args:
                raise Usage("path import <file>")
            scope = _scope(o)
            added = 0
            for l in open(args[0], encoding="utf-8").read().splitlines():
                for e in envtools.split(l):
                    if envtools.find_entry(envtools.get_path(scope), e) < 0:
                        envtools.add_path(scope, e)
                        added += 1
            ui.say(ui.ok(f"Added {added} folders to the {scope} PATH (existing ones were kept)."))
            return 0
        if sub == "refresh":
            return _refresh()
    except envtools.NeedsAdmin as e:
        return _admin_hint(e, line)
    return 0


def _refresh():
    if not IS_WIN:
        ui.say(ui.dim("On macOS/Linux new settings load in new terminals; this window already has Commands' changes."))
        return 0
    fresh = envtools.fresh_from_system()
    sess = envtools.split(os.environ.get("PATH", ""))
    have = {envtools.norm(p) for p in sess}
    added = [p for p in envtools.split(fresh.get("Path", "")) if envtools.norm(p) not in have]
    sess += added
    os.environ["PATH"] = envtools.join(sess)
    changed = []
    for k, v in fresh.items():
        if k.lower() != "path" and os.environ.get(k) != v:
            os.environ[k] = v
            changed.append(k)
    envtools.live_refresh(initial=True)
    if not added and not changed:
        ui.say(ui.ok("Already up to date."))
    for a in added:
        ui.say(ui.ok("+ ") + a)
    for c in changed:
        ui.say(ui.ok("~ ") + c)
    return 0


def _doctor():
    bad = 0
    sess = envtools.get_path("session")
    scopes = [("user", envtools.get_path("user"))]
    if IS_WIN:
        scopes.append(("machine", envtools.get_path("machine")))
    for scope, entries in scopes:
        p = envtools.problems(entries)
        size = len(envtools.join(entries))
        ui.say(ui.b(f"{scope.capitalize()} PATH") + ui.dim(f"  {len(entries)} folders, {size} characters"))
        if size > 1024:
            ui.say(ui.dim("  over 1024 characters: setx would cut it off; Commands handles it fine"))
        for i in p["missing"]:
            ui.say("  " + ui.err("missing   ") + entries[i])
            bad += 1
        for i in p["duplicates"]:
            ui.say("  " + ui.warn("duplicate ") + entries[i])
            bad += 1
    total = len(os.environ.get("PATH", ""))
    if IS_WIN and total > 8000:
        ui.say(ui.warn(f"This window's PATH is {total} characters; some old programs break past 8191."))
    # programs hidden by an earlier copy
    seen, shadows = {}, []
    exts = (".exe", ".cmd", ".bat") if IS_WIN else None
    for d in sess:
        try:
            names = os.listdir(os.path.expandvars(d))
        except OSError:
            continue
        for n in names:
            low = n.lower() if IS_WIN else n
            if exts and not low.endswith(exts):
                continue
            base = os.path.splitext(low)[0] if IS_WIN else low
            if base in seen and envtools.norm(seen[base]) != envtools.norm(d):
                shadows.append((base, seen[base], d))
            else:
                seen.setdefault(base, d)
    interesting = [s for s in shadows if s[0] in ("python", "python3", "node", "git", "java", "pip", "npm",
                                                  "code", "dotnet", "go", "cargo", "ruby", "perl", "php", "odus")]
    if interesting:
        ui.say(ui.b("Programs found in more than one place") + ui.dim(" (the first wins)"))
        for name, first, other in interesting[:15]:
            ui.say(f"  {ui.acc(name)}: {first} {ui.dim('beats')} {other}")
    if not bad:
        ui.say(ui.ok("No missing or duplicate folders."))
    else:
        ui.say(ui.dim(f"\nFix with: path clean   (or path clean --machine as admin). A backup is made first."))
    return 0


@builtin("env", "envvar", cat="Variables",
         complete=lambda sh, a, w: ["list", "get", "set", "unset", "diff", "refresh", "backups", "history"] if not a else [],
         summary="Permanent variables done right (setx without the 1024 limit, with backups).",
         usage="env                           this window's variables\n"
               "env list --user | --machine   saved ones\n"
               "env get NAME [--user]\n"
               "env set NAME value [--machine] [--session]   saved for good + this window\n"
               "env unset NAME [--machine]\n"
               "env diff                      what this window has that new windows won't, and back\n"
               "env refresh                   load changes made by installers into this window\n"
               "env history                   every change Commands made, with backups")
def cmd_env(ctx):
    o, rest = ctx.opts({"user": bool, "machine": bool, "system": bool, "session": bool, "temp": bool,
                        "m": bool, "y|yes": bool})
    line = "env " + lexer.join(ctx.args)
    sub = rest[0].lower() if rest else "list"
    args = rest[1:]
    try:
        if sub == "list":
            scope = _scope(o, "session") if rest else "session"
            vars_ = envtools.list_vars(scope)
            ui.say(ui.b(f"{scope.capitalize()} variables") + ui.dim(f"  {len(vars_)}"))
            for k in sorted(vars_, key=str.lower):
                v = str(vars_[k])
                ui.say(f"{ui.paint(k, 'var')}={v if len(v) < 300 else v[:300] + ui.dim('…')}")
            return 0
        if sub == "get":
            if not args:
                raise Usage()
            v = envtools.get_var(_scope(o, "session"), args[0])
            if v is None:
                ui.say(ui.err(f"{args[0]} isn't set."))
                return 1
            ui.say(v)
            return 0
        if sub == "set":
            if not args:
                raise Usage("env set NAME value")
            name = args[0]
            value = " ".join(args[1:])
            if "=" in name and not value:
                name, _, value = name.partition("=")
            scope = _scope(o, "user")
            bk = envtools.set_var(scope, name, value)
            ui.say(ui.ok(f"{name} saved for the {scope}" if scope != "session" else f"{name} set for this window") +
                   ui.dim(f" ({len(value)} characters)") + (ui.dim(f"\n  backup: {bk}") if bk else ""))
            return 0
        if sub in ("unset", "remove", "delete"):
            if not args:
                raise Usage()
            scope = _scope(o, "user")
            if scope != "session" and envtools.get_var(scope, args[0]) is None:
                ui.say(ui.err(f"{args[0]} isn't saved for the {scope}."))
                return 1
            if args[0].lower() == "path":
                raise envtools.EnvError("Refusing to delete PATH. Use 'path remove' for single folders.")
            bk = envtools.set_var(scope, args[0], None)
            ui.say(ui.ok(f"Removed {args[0]} ({scope}).") + (ui.dim(f"  backup: {bk}") if bk else ""))
            return 0
        if sub == "diff":
            if not IS_WIN:
                ui.say(ui.dim("env diff compares with the registry, so it's Windows-only."))
                return 0
            fresh = envtools.fresh_from_system()
            fk = {k.upper(): (k, v) for k, v in fresh.items()}
            for k, v in sorted(os.environ.items()):
                if k.upper() in ("PATH",) or k.upper().startswith(("COMMANDS", "=")) or k.upper() in WINDOWS_GIVEN:
                    continue
                if k.upper() not in fk:
                    ui.say(ui.paint("only here  ", "warn") + f"{k}={v[:120]}")
                elif fk[k.upper()][1] != v:
                    ui.say(ui.paint("different  ", "accent") + f"{k}: {v[:60]} {ui.dim('vs saved')} {fk[k.upper()][1][:60]}")
            for K, (k, v) in sorted(fk.items()):
                if K != "PATH" and K not in {x.upper() for x in os.environ}:
                    ui.say(ui.paint("not loaded ", "glyph") + f"{k}={v[:120]}" + ui.dim("  (env refresh)"))
            sess = {envtools.norm(p) for p in envtools.split(os.environ.get("PATH", ""))}
            saved = envtools.split(fresh.get("Path", ""))
            for p in saved:
                if envtools.norm(p) not in sess:
                    ui.say(ui.paint("not loaded ", "glyph") + f"PATH {p}" + ui.dim("  (path refresh)"))
            sv = {envtools.norm(p) for p in saved}
            for p in envtools.split(os.environ.get("PATH", "")):
                if envtools.norm(p) not in sv:
                    sv.add(envtools.norm(p))
                    ui.say(ui.paint("only here  ", "warn") + f"PATH {p}")
            return 0
        if sub == "refresh":
            return _refresh()
        if sub == "backups":
            for f in envtools.list_backups()[:40]:
                ui.say(ui.dim(os.path.basename(f)))
            return 0
        if sub == "history":
            for h in ui.load_json("env-changes.json", [])[-40:]:
                ui.say(f"{ui.dim(h['time'])} {ui.acc(h['scope'])} {h['what']} {ui.b(h['name'])}")
            return 0
        raise Usage(f"unknown: env {sub}")
    except envtools.NeedsAdmin as e:
        return _admin_hint(e, line)
