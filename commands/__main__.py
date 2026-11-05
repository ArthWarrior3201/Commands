"""Start Commands.

    commands                      open the Commands prompt
    commands <command line>       run one line and exit   (commands path add C:\\Tools)
    commands -c "<line>"          same, as one string
    commands script.cmds          run a file of Commands lines
    commands api <method> k=v     call the Commands API
Options: --cd <folder>  --plain (no colors)  --pause (wait for a key at the end)  --version
"""
import os
import sys


def write_launcher() -> str:
    """Writes a small `commands` launcher (commands.cmd on Windows) into the settings folder
    and returns its path. It starts whatever this copy of Commands is: a downloaded
    single file (COMMANDS_LAUNCHER) or the source folder's run.py."""
    from . import ui
    from .backends import python_exe
    folder = os.path.join(ui.CONFIG_DIR, "launcher")
    single = os.environ.get("COMMANDS_LAUNCHER")
    if single and os.path.isfile(single):
        target = single
    else:
        target = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "run.py")
    if os.name == "nt":
        path = os.path.join(folder, "commands.cmd")
        if target.lower().endswith((".bat", ".cmd")):
            body = f'@echo off\r\ncall "{target}" %*\r\nexit /b %errorlevel%\r\n'
        else:
            body = f'@echo off\r\n"{python_exe()}" "{target}" %*\r\nexit /b %errorlevel%\r\n'
    else:
        path = os.path.join(folder, "commands")
        if target.endswith((".sh", ".command")):  # a standalone download runs by itself
            body = f'#!/bin/sh\nexec "{target}" "$@"\n'
        else:
            body = f'#!/bin/sh\nexec "{python_exe()}" "{target}" "$@"\n'
    try:
        with open(path, encoding="utf-8", newline="") as f:
            same = f.read() == body
    except OSError:
        same = False
    if not same:
        os.makedirs(folder, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(body)
        if os.name != "nt":
            os.chmod(path, 0o755)
    return path


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        sys.stdout.reconfigure(errors="replace")
        sys.stderr.reconfigure(errors="replace")
    except Exception:
        pass
    from . import VERSION, ui, term
    pause = False
    line = None
    while argv and argv[0].startswith("--"):
        a = argv.pop(0)
        if a == "--plain":
            os.environ["NO_COLOR"] = "1"
        elif a == "--pause":
            pause = True
        elif a == "--cd" and argv:
            try:
                os.chdir(os.path.expanduser(argv.pop(0).strip('"')))
            except OSError:
                pass
        elif a == "--version":
            print(f"Commands {VERSION}")
            return 0
        elif a == "--help":
            print(__doc__)
            return 0
        else:
            argv.insert(0, a)
            break
    keep = False
    if argv and argv[0] in ("-c", "/c", "-k", "/k"):
        keep = argv[0] in ("-k", "/k")  # like cmd /k: run it, then stay open
        line = " ".join(argv[1:]) if len(argv) > 2 else (argv[1] if len(argv) > 1 else "")
        argv = []
    term.enable_vt()
    # put a `commands` launcher on this session's PATH so programs inside can run `commands api ...`
    try:
        shim_dir = os.path.dirname(write_launcher())
        path = os.environ.get("PATH", "")
        if shim_dir.lower() not in [p.lower().rstrip("\\/") for p in path.split(os.pathsep)]:
            os.environ["PATH"] = path + os.pathsep + shim_dir
    except OSError:
        pass
    if argv and argv[0] == "api":
        from . import api
        return api.cli(argv[1:])

    from .shell import Shell, ExitShell
    if line is not None and not keep:
        sh = Shell(interactive=False)
        try:
            rc = sh.run_line(line)
        except ExitShell as e:
            rc = e.code
        if pause and term.is_tty():
            term.write(ui.dim("\nPress any key to close."))
            with term.raw_mode():
                term.read_key()
        return rc
    if argv:
        first = argv[0]
        sh = Shell(interactive=False)
        try:
            if first.lower().endswith(".cmds") and os.path.isfile(first):
                rc = 0
                with open(first, encoding="utf-8") as f:
                    for l in f:
                        rc = sh.run_line(l.rstrip("\n"))
                return rc
            from .lexer import join
            return sh.run_line(join(argv))
        except ExitShell as e:
            return e.code
    sh = Shell(interactive=True)
    try:
        return sh.repl(first_line=line if keep else None)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
