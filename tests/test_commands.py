"""Tests for Commands. Run:  python -m unittest discover tests

The PATH tests use a fake registry, so your real PATH is never touched."""
import io
import os
import sys
import json
import shutil
import socket
import tempfile
import unittest
import contextlib
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
TMP = tempfile.mkdtemp(prefix="commands-tests-")

from commands import ui  # noqa: E402
ui.CONFIG_DIR = os.path.join(TMP, "config")
from commands import lexer, envtools, api  # noqa: E402
envtools.BACKUP_DIR = os.path.join(TMP, "config", "backups")
from commands.shell import Shell  # noqa: E402

IS_WIN = os.name == "nt"


def run(line):
    """Runs a line through a fresh shell and returns (exit code, output)."""
    sh = Shell(interactive=False)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = sh.run_line(line)
    return rc, buf.getvalue()


class Lexer(unittest.TestCase):
    def test_chain(self):
        self.assertEqual(lexer.split_chain('a && b || c & d'),
                         [(None, "a"), ("&&", "b"), ("||", "c"), ("&", "d")])

    def test_chain_respects_quotes_and_escapes(self):
        self.assertEqual(lexer.split_chain('echo "a && b" ^& c'), [(None, 'echo "a && b" ^& c')])

    def test_redirect_2_to_1_is_not_a_chain(self):
        self.assertEqual(len(lexer.split_chain("dir 2>&1")), 1)

    def test_pipe(self):
        self.assertEqual(lexer.split_pipe('dir | grep "a|b" | count'), ["dir", 'grep "a|b"', "count"])

    def test_tokens_and_redirects(self):
        args, red = lexer.take_redirects(lexer.tokenize('echo "hi there" >> out.txt 2>&1'))
        self.assertEqual(args, ["echo", "hi there"])
        self.assertEqual(red["out"], ("out.txt", "a"))
        self.assertTrue(red["err_to_out"])

    def test_expand(self):
        os.environ["COMMANDS_T"] = "abcdef"
        self.assertEqual(lexer.expand("%COMMANDS_T%-%commands_t:~1,3%-$env:COMMANDS_T"), "abcdef-bcd-abcdef")
        self.assertEqual(lexer.expand("%NOPE_NOT_SET%"), "%NOPE_NOT_SET%")
        self.assertEqual(lexer.expand("%COMMANDS_T:cd=XY%"), "abXYef")


class FakeRegistry:
    """Stands in for HKCU/HKLM so PATH code can be tested safely."""

    def __init__(self, user, machine):
        self.data = {"user": {"Path": (user, 2)}, "machine": {"Path": (machine, 2)}}

    def get(self, scope, name):
        return self.data[scope].get(name, (None, None))

    def set(self, scope, name, value, kind=None):
        if value is None:
            self.data[scope].pop(name, None)
        else:
            self.data[scope][name] = (value, kind or 2)


class PathEngine(unittest.TestCase):
    def setUp(self):
        self.dirs = [tempfile.mkdtemp(dir=TMP) for _ in range(3)]
        self.reg = FakeRegistry(";".join(self.dirs[:2]), r"C:\Windows")
        self._get, self._set, self._all = envtools.win_get, envtools.win_set, envtools.win_all
        self._posix = envtools._posix_state, envtools._posix_apply
        state = {"path_front": [], "path_end": list(self.dirs[:2]), "vars": {}}
        envtools.win_get = self.reg.get
        envtools.win_set = self.reg.set
        envtools.win_all = lambda s: {k: v for k, (v, _) in self.reg.data[s].items()}
        envtools._posix_state = lambda: state
        envtools._posix_apply = lambda s: None
        self.saved_path = os.environ["PATH"]

    def tearDown(self):
        envtools.win_get, envtools.win_set, envtools.win_all = self._get, self._set, self._all
        envtools._posix_state, envtools._posix_apply = self._posix
        os.environ["PATH"] = self.saved_path

    def test_add_is_appended_and_kept(self):
        r = envtools.add_path("user", self.dirs[2])
        self.assertTrue(r["changed"])
        self.assertEqual(envtools.get_path("user"), self.dirs)
        self.assertTrue(os.path.exists(r["backup"]))
        self.assertIn(self.dirs[2], os.environ["PATH"])  # this session too

    def test_add_twice_does_nothing(self):
        envtools.add_path("user", self.dirs[0])
        self.assertEqual(envtools.get_path("user"), self.dirs[:2])

    def test_front(self):
        envtools.add_path("user", self.dirs[2], front=True)
        self.assertEqual(envtools.get_path("user")[0], self.dirs[2])

    def test_long_path_is_not_cut(self):
        long_dirs = [os.path.join(TMP, "x" * 60 + str(i)) for i in range(40)]  # ~2700 characters
        for d in long_dirs:
            envtools.add_path("user", d)
        value = ";".join(envtools.get_path("user"))
        self.assertGreater(len(value), 2000)
        self.assertEqual(envtools.get_path("user")[-1], long_dirs[-1])

    def test_refuses_to_wipe(self):
        with self.assertRaises(envtools.EnvError):
            envtools.set_path("user", [])
        with self.assertRaises(envtools.EnvError):
            envtools.set_path("user", [self.dirs[0]])  # would drop one without saying so
        self.assertEqual(envtools.get_path("user"), self.dirs[:2])

    def test_remove(self):
        r = envtools.remove_path("user", self.dirs[0].upper() if IS_WIN else self.dirs[0])
        self.assertTrue(r["changed"])
        self.assertEqual(envtools.get_path("user"), [self.dirs[1]])

    def test_remove_by_number(self):
        envtools.remove_path("user", "2")
        self.assertEqual(envtools.get_path("user"), [self.dirs[0]])

    def test_clean(self):
        envtools.add_path("user", os.path.join(TMP, "does-not-exist"))
        self.reg.data["user"]["Path"] = (self.reg.data["user"]["Path"][0] + ";" + self.dirs[0], 2)
        if not IS_WIN:
            envtools._posix_state()["path_end"].append(self.dirs[0])
        r = envtools.clean_path("user")
        self.assertEqual(len(r["removed"]), 2)
        self.assertEqual(envtools.get_path("user"), self.dirs[:2])

    def test_restore(self):
        r = envtools.add_path("user", self.dirs[2])
        envtools.restore_path("user", r["backup"])
        self.assertEqual(envtools.get_path("user"), self.dirs[:2])

    def test_setx_path_adds_instead_of_replacing(self):
        rc, out = run(f'setx PATH "{self.dirs[2]}"')
        self.assertEqual(rc, 0)
        self.assertEqual(envtools.get_path("user"), self.dirs)


@unittest.skipUnless(IS_WIN, "Windows registry")
class RealRegistry(unittest.TestCase):
    """One round trip through the real HKCU\\Environment with a throwaway variable."""
    NAME = "COMMANDS_SELFTEST_DELETE_ME"

    def tearDown(self):
        envtools.win_set("user", self.NAME, None)
        os.environ.pop(self.NAME, None)

    def test_long_value_round_trip(self):
        value = "x" * 3000  # setx would cut this at 1024
        envtools.set_var("user", self.NAME, value)
        self.assertEqual(envtools.win_get("user", self.NAME)[0], value)
        self.assertEqual(os.environ[self.NAME], value)
        envtools.set_var("user", self.NAME, None)
        self.assertIsNone(envtools.win_get("user", self.NAME)[0])


class ShellBehaviour(unittest.TestCase):
    def test_echo_and_chain(self):
        rc, out = run("echo one && echo two")
        self.assertEqual(out.split(), ["one", "two"])

    def test_or_runs_after_failure(self):
        rc, out = run("cd Z:\\nope\\nope || echo fallback")
        self.assertIn("fallback", out)

    def test_set_and_expand(self):
        rc, out = run("set CMDS_X=42 & echo %CMDS_X%")
        self.assertEqual(out.strip(), "42")

    def test_set_arith(self):
        rc, out = run("set /a 6*7")
        self.assertEqual(out.strip(), "42")

    def test_builtin_pipeline(self):
        rc, out = run("echo apple banana | count")
        self.assertIn("2 words", out)

    def test_redirect_builtin_to_file(self):
        f = os.path.join(TMP, "out.txt")
        run(f'echo saved > "{f}"')
        with open(f, encoding="utf-8") as fh:
            self.assertEqual(fh.read().strip(), "saved")

    def test_calc(self):
        self.assertIn("1024", run("calc 2^10")[1])

    def test_alias(self):
        sh = Shell(interactive=False)
        sh.config["aliases"]["greet"] = "echo hi $1!"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            sh.run_line("greet Sam")
        self.assertEqual(buf.getvalue().strip(), "hi Sam!")

    def test_cd_back(self):
        sh = Shell(interactive=False)
        start = os.getcwd()
        with contextlib.redirect_stdout(io.StringIO()):
            sh.run_line(f'cd "{TMP}"')
            self.assertEqual(os.path.normcase(os.getcwd()), os.path.normcase(TMP))
            sh.run_line("cd -")
        self.assertEqual(os.getcwd(), start)

    def test_unknown_command(self):
        rc, out = run("definitelynotacommand123")
        self.assertEqual(rc, 9009)

    @unittest.skipUnless(IS_WIN, "CMD")
    def test_cmd_passthrough(self):
        rc, out = run("ver")
        self.assertEqual(rc, 0)

    @unittest.skipUnless(IS_WIN, "batch files")
    def test_batch_file_variables_stick(self):
        bat = os.path.join(TMP, "setit.cmd")
        with open(bat, "w") as f:
            f.write("@echo off\nset FROM_BATCH=yes\ncd /d %TEMP%\n")
        sh = Shell(interactive=False)
        start = os.getcwd()
        try:
            sh.run_line(f'call "{bat}"')
            self.assertEqual(os.environ.get("FROM_BATCH"), "yes")
            self.assertEqual(os.path.normcase(os.path.realpath(os.getcwd())),
                             os.path.normcase(os.path.realpath(os.environ["TEMP"])))
        finally:
            os.chdir(start)
            os.environ.pop("FROM_BATCH", None)

    @unittest.skipUnless(shutil.which("powershell") or shutil.which("pwsh"), "PowerShell")
    def test_powershell_variables_stick(self):
        sh = Shell(interactive=False)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                sh.run_line("$env:FROM_PS = 'hello'")
            self.assertEqual(os.environ.get("FROM_PS"), "hello")
        finally:
            os.environ.pop("FROM_PS", None)

    def test_routes(self):
        sh = Shell(interactive=False)
        self.assertEqual(sh.kind_of("path"), "builtin")
        self.assertEqual(sh.kind_of("install"), "odus")
        self.assertEqual(sh.kind_of("Get-ChildItem"), "cmdlet")
        if IS_WIN:
            self.assertEqual(sh.kind_of("dir"), "cmd")
            self.assertEqual(sh.kind_of("D:"), "drive")


class Api(unittest.TestCase):
    def test_round_trip_and_session_cd(self):
        sh = Shell(interactive=True)
        srv = api.Server(sh)
        env = dict(os.environ, **srv.child_env(), PYTHONPATH=os.path.join(ROOT, "sdk", "python"))
        code = ("from commands_api import Commands\n"
                "cx = Commands()\n"
                "print(cx.call('api.version')['api'])\n"
                f"cx.cd({TMP!r})\n"
                "cx.setenv('FROM_APP', 'ok')\n"
                "cx.run_after('echo queued')\n")
        start = os.getcwd()
        try:
            out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(out.stdout.strip(), "1", out.stderr)
            self.assertEqual(os.path.normcase(os.getcwd()), os.path.normcase(TMP))
            self.assertEqual(os.environ.get("FROM_APP"), "ok")
            self.assertEqual(sh.pending, ["echo queued"])
        finally:
            os.chdir(start)
            os.environ.pop("FROM_APP", None)

    def test_bad_token_is_refused(self):
        sh = Shell(interactive=True)
        srv = api.Server(sh)
        s = socket.create_connection(("127.0.0.1", srv.port))
        s.sendall(b'{"token":"wrong","id":7,"method":"session.cd","params":{"path":"C:/"}}\n')
        resp = json.loads(s.makefile().readline())
        s.close()
        self.assertEqual(resp["error"]["code"], "bad_token")

    def test_session_methods_need_a_window(self):
        with self.assertRaises(api.ApiError):
            api.call(None, "session.cd", {"path": "."})

    def test_cli_without_session(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("COMMANDS_API")}
        out = subprocess.run([sys.executable, os.path.join(ROOT, "run.py"), "api", "api.version"],
                             env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(json.loads(out.stdout)["api"], 1, out.stderr)


if __name__ == "__main__":
    unittest.main()


class LineEditorKeys(unittest.TestCase):
    """Feeds keys to the line editor as if they were typed."""

    def type_keys(self, keys, history=(), sh=None):
        from commands import term, lineedit
        sh = sh or Shell(interactive=False)
        sh.history[:] = list(history)  # the prompt shares one history list with the shell
        feed = list(keys)
        saved = term.read_key, term.key_ready, term.raw_mode, term.write, term.size
        out = []
        term.read_key = lambda: feed.pop(0)
        term.key_ready = lambda: False
        term.raw_mode = contextlib.nullcontext
        term.write = out.append
        term.size = lambda: (80, 24)
        try:
            ed = lineedit.LineEditor(sh.history, sh.highlight, sh.complete, sh.suggest)
            return ed.read("> "), "".join(out)
        finally:
            term.read_key, term.key_ready, term.raw_mode, term.write, term.size = saved

    def test_typing_and_editing(self):
        line, _ = self.type_keys(list("echo hellp") + ["backspace", "o", "home", "delete", "E", "enter"])
        self.assertEqual(line, "Echo hello")

    def test_word_delete(self):
        line, _ = self.type_keys(list("git push origin") + ["ctrl-w", "enter"])
        self.assertEqual(line, "git push ")

    def test_history_by_prefix(self):
        line, _ = self.type_keys(list("git") + ["up", "enter"], history=["git status", "dir", "ls"])
        self.assertEqual(line, "git status")

    def test_suggestion_taken_with_right_arrow(self):
        line, out = self.type_keys(list("ser") + ["right", "enter"], history=["serve --local"])
        self.assertEqual(line, "serve --local")

    def test_escape_clears(self):
        line, _ = self.type_keys(list("oops") + ["esc"] + list("ok") + ["enter"])
        self.assertEqual(line, "ok")

    def test_ctrl_c_gives_empty_line(self):
        line, _ = self.type_keys(list("abc") + ["ctrl-c"])
        self.assertEqual(line, "")

    def test_tab_completes_a_builtin(self):
        line, _ = self.type_keys(list("sysin") + ["tab", "enter"])
        self.assertEqual(line, "sysinfo ")

    def test_tab_completes_a_folder(self):
        d = tempfile.mkdtemp(dir=TMP)
        os.makedirs(os.path.join(d, "projects-xyz"))
        start = os.getcwd()
        os.chdir(d)
        try:
            line, _ = self.type_keys(list("cd proj") + ["tab", "enter"])
        finally:
            os.chdir(start)
        self.assertEqual(line, "cd projects-xyz" + os.sep)

    def test_long_line_wraps_without_crashing(self):
        line, _ = self.type_keys(list("echo " + "x" * 200) + ["home", "end", "enter"])
        self.assertEqual(len(line), 205)
