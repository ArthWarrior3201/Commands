"""Live Commands API tests: a real session server, called by real child programs
(a Python app, a Node app, a .cmd batch file and a .ps1 script). Key presses for
menus and prompts are fed in as if the user typed them.

Run:  python -m unittest tests.test_api_live -v
"""
import os
import sys
import json
import time
import shutil
import socket
import tempfile
import threading
import unittest
import contextlib
import subprocess
import http.server
import functools

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
TMP = tempfile.mkdtemp(prefix="commands-api-live-")

from commands import ui, term  # noqa: E402
ui.CONFIG_DIR = os.path.join(TMP, "config")
from commands import envtools, api  # noqa: E402
envtools.BACKUP_DIR = os.path.join(TMP, "config", "backups")
from commands.shell import Shell  # noqa: E402

IS_WIN = os.name == "nt"
NODE = shutil.which("node")
PS = shutil.which("powershell") or shutil.which("pwsh")


class FakeUser:
    """Answers menus and prompts in the Commands window."""

    def __init__(self):
        self.keys = []
        self.screen = []
        self.lock = threading.Lock()

    def press(self, *keys):
        with self.lock:
            self.keys.extend(keys)

    def read_key(self):
        for _ in range(400):
            with self.lock:
                if self.keys:
                    return self.keys.pop(0)
            time.sleep(0.01)
        return "esc"  # nobody answered: cancel instead of hanging

    def text(self):
        return ui.strip_ansi("".join(self.screen))


class LiveApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.user = FakeUser()
        cls.saved = (term.read_key, term.key_ready, term.raw_mode, term.write, term.size, term.is_tty)
        term.read_key = cls.user.read_key
        term.key_ready = lambda: False
        term.raw_mode = contextlib.nullcontext
        term.write = cls.user.screen.append
        term.size = lambda: (100, 30)
        term.is_tty = lambda: True
        cls.sh = Shell(interactive=True)
        cls.srv = api.Server(cls.sh)
        cls.env = dict(os.environ, **cls.srv.child_env())
        from commands.__main__ import write_launcher  # the `commands` command scripts call
        cls.env["PATH"] = os.path.dirname(write_launcher()) + os.pathsep + cls.env.get("PATH", "")
        cls.env["PYTHONPATH"] = os.path.join(ROOT, "sdk", "python")
        cls.start_dir = os.getcwd()
        # a tiny local web server for net.download
        cls.web_dir = tempfile.mkdtemp(dir=TMP)
        with open(os.path.join(cls.web_dir, "file.bin"), "wb") as f:
            f.write(os.urandom(300_000))
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=cls.web_dir)
        handler.log_message = lambda *a: None
        cls.web = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=cls.web.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        (term.read_key, term.key_ready, term.raw_mode, term.write, term.size, term.is_tty) = cls.saved
        cls.web.shutdown()
        os.chdir(cls.start_dir)

    def setUp(self):
        os.chdir(self.start_dir)
        self.user.screen.clear()
        self.user.keys.clear()

    def py(self, code, timeout=60):
        r = subprocess.run([sys.executable, "-c", code], env=self.env, capture_output=True, text=True,
                           timeout=timeout, cwd=TMP)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def call(self, method, **params):
        """A raw socket call, like any language would make."""
        host, _, port = self.env["COMMANDS_API"].rpartition(":")
        with socket.create_connection((host, int(port))) as s:
            s.sendall((json.dumps({"token": self.env["COMMANDS_API_TOKEN"], "id": 1, "method": method,
                                   "params": params}) + "\n").encode())
            return json.loads(s.makefile().readline())

    # ------------------------------------------------------------ window
    def test_window_state_from_a_python_app(self):
        target = tempfile.mkdtemp(dir=TMP)
        out = self.py(f"""
from commands_api import Commands
cx = Commands()
assert cx.available
cx.cd({target!r})
cx.setenv("APP_SET", "from python")
cx.alias("hey", "echo hey $1")
cx.title("Built by the API")
cx.run_after("echo after the app")
print(cx.getenv("APP_SET"))
print(cx.info()["cwd"])
""")
        self.assertEqual(os.path.normcase(os.getcwd()), os.path.normcase(target))
        self.assertEqual(os.environ["APP_SET"], "from python")
        self.assertEqual(self.sh.config["aliases"]["hey"], "echo hey $1")
        self.assertEqual(self.sh.title, "Built by the API")
        self.assertIn("echo after the app", self.sh.pending)
        self.sh.pending.clear()
        lines = out.split("\n")
        self.assertEqual(lines[0], "from python")
        self.assertEqual(os.path.normcase(lines[1]), os.path.normcase(target))
        os.environ.pop("APP_SET")
        self.sh.config["aliases"].pop("hey")

    def test_shell_run_capture(self):
        r = self.call("shell.run", line="echo captured text", capture=True)
        self.assertEqual(r["result"]["output"].strip(), "captured text")
        self.assertEqual(r["result"]["exit_code"], 0)
        r = self.call("shell.run", line="calc 6*7", capture=True)
        self.assertTrue(r["result"]["output"].startswith("42"))

    def test_history(self):
        self.sh.history.append("dir /b")
        self.assertIn("dir /b", self.call("session.history", limit=5)["result"])

    # ------------------------------------------------------------ prompts
    def test_menus_and_questions(self):
        self.user.press("down", "enter",                 # choose -> second item
                        "y",                             # confirm -> yes
                        *"Ann", "enter",                 # ask
                        *"s3cret", "enter",              # secret ask
                        " ", "down", "down", " ", "enter",  # multi choose -> first and third
                        "esc")                           # choose cancelled
        out = self.py("""
import json
from commands_api import Commands
cx = Commands()
r = [cx.choose(["alpha", "beta", "gamma"], title="Pick"),
     cx.confirm("Sure?"),
     cx.ask("Name?"),
     cx.ask("Password?", secret=True),
     cx.choose(["a", "b", "c"], multi=True),
     cx.choose(["x", "y"])]
print(json.dumps(r))
""")
        self.assertEqual(json.loads(out), ["beta", True, "Ann", "s3cret", ["a", "c"], None])
        screen = self.user.text()
        self.assertIn("Pick", screen)
        self.assertNotIn("s3cret", screen)  # the password was hidden
        self.assertIn("••••••", screen)

    def test_choose_filters_by_typing(self):
        self.user.press(*"gam", "enter")
        out = self.py("from commands_api import Commands; print(Commands().choose(['alpha','beta','gamma']))")
        self.assertEqual(out.strip(), "gamma")

    def test_print_progress_table(self):
        self.py("""
from commands_api import Commands
cx = Commands()
cx.print("[ok]all good[/] and [b]bold[/]")
for i in range(0, 101, 25):
    cx.progress("Copying", i, 100)
cx.progress("Copying", done=True)
cx.table([["one", 1], ["two", 22]], header=["name", "n"])
""")
        screen = self.user.text()
        self.assertIn("all good and bold", screen)
        self.assertIn("100.0%", screen)
        self.assertIn("✓ Copying", screen)

    # ------------------------------------------------------------ PATH / env (session scope: nothing saved)
    def test_path_and_env_in_session_scope(self):
        d = tempfile.mkdtemp(dir=TMP)
        saved = os.environ["PATH"]
        try:
            self.assertTrue(self.call("path.add", folder=d, scope="session")["result"]["changed"])
            self.assertIn(d, self.call("path.get", scope="session")["result"])
            self.assertTrue(self.call("path.remove", folder=d, scope="session")["result"]["changed"])
            self.assertNotIn(d, os.environ["PATH"])
            self.call("env.set", name="API_SESSION_VAR", value="v1", scope="session")
            self.assertEqual(self.call("env.get", name="API_SESSION_VAR")["result"], "v1")
            self.assertTrue(self.call("path.which", name="python")["result"])
        finally:
            os.environ["PATH"] = saved
            os.environ.pop("API_SESSION_VAR", None)

    def test_path_refuses_bad_scope(self):
        self.assertEqual(self.call("path.get", scope="everywhere")["error"]["code"], "bad_params")

    # ------------------------------------------------------------ system
    def test_system_methods(self):
        info = self.call("sys.info")["result"]
        self.assertGreater(info["memory_total"], 0)
        procs = self.call("sys.processes", filter="python")["result"]
        self.assertTrue(any(p["pid"] == os.getpid() for p in procs))
        ports = self.call("sys.ports")["result"]
        self.assertTrue(any(p["port"] == self.srv.port for p in ports))
        free = self.call("net.free_port")["result"]
        self.assertIsInstance(free, int)
        self.assertGreaterEqual(self.call("net.free_port", start=self.srv.port)["result"], self.srv.port + 1)

    def test_kill(self):
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        self.assertTrue(self.call("sys.kill", pid=p.pid)["result"])
        self.assertIsNotNone(p.wait(timeout=10))

    def test_clipboard_round_trip(self):
        try:
            before = self.call("clipboard.get")["result"]
        except Exception:
            before = None
        try:
            self.call("clipboard.set", text="Commands ✓ ünïcode")
            self.assertEqual(self.call("clipboard.get")["result"], "Commands ✓ ünïcode")
        finally:
            if isinstance(before, str):
                self.call("clipboard.set", text=before)

    def test_files(self):
        f = os.path.join(TMP, "hash-me.txt")
        with open(f, "w") as fh:
            fh.write("hello")
        self.assertEqual(self.call("fs.hash", path=f)["result"],
                         "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824")
        self.assertEqual(self.call("fs.hash", path=f, algo="md5")["result"], "5d41402abc4b2a76b9719d911017c592")
        self.assertEqual(self.call("fs.size", path=TMP)["result"]["files"] >= 1, True)
        self.assertEqual(self.call("fs.hash", path=f, algo="crc")["error"]["code"], "bad_params")

    def test_trash(self):
        f = os.path.join(TMP, "commands-api-test-delete-me.txt")
        open(f, "w").close()
        r = self.call("fs.trash", paths=[f])["result"]
        self.assertEqual(r["trashed"], [f])
        self.assertFalse(os.path.exists(f))

    def test_watch(self):
        d = tempfile.mkdtemp(dir=TMP)
        threading.Timer(0.8, lambda: open(os.path.join(d, "new.txt"), "w").close()).start()
        r = self.call("fs.watch", path=d, timeout=10)["result"]
        self.assertTrue(r["changed"])
        self.assertEqual([os.path.basename(x) for x in r["added"]], ["new.txt"])
        self.assertFalse(self.call("fs.watch", path=d, timeout=0.5)["result"]["changed"])

    def test_download_with_progress(self):
        url = f"http://127.0.0.1:{self.web.server_port}/file.bin"
        r = self.call("net.download", url=url, dest=TMP)["result"]
        self.assertEqual(r["bytes"], 300_000)
        with open(r["path"], "rb") as a, open(os.path.join(self.web_dir, "file.bin"), "rb") as b:
            self.assertEqual(a.read(), b.read())
        self.assertIn("file.bin", self.user.text())
        self.assertEqual(self.call("net.download", url=url + "-missing", dest=TMP)["error"]["code"], "failed")

    @unittest.skipUnless(IS_WIN, "Windows shortcut")
    def test_shortcut(self):
        r = self.call("shortcut.create", path=os.path.join(TMP, "Notepad"), target=r"C:\Windows\notepad.exe")
        self.assertTrue(os.path.exists(r["result"]["path"]))

    def test_startup_add_list_remove(self):
        name = "CommandsApiSelfTest"
        try:
            self.assertTrue(self.call("startup.add", name=name, command="echo test")["result"])
            listed = self.call("startup.list")["result"]
            self.assertTrue(any(name in k for k in listed))
        finally:
            self.assertTrue(self.call("startup.remove", name=name)["result"])
        self.assertFalse(any(name in k for k in self.call("startup.list")["result"]))

    def test_jobs(self):
        r = self.call("jobs.start", line="echo from a job")["result"]
        for _ in range(100):
            jobs = self.call("jobs.list")["result"]
            if not jobs[-1]["running"]:
                break
            time.sleep(0.2)
        self.assertEqual(jobs[-1]["exit_code"], 0)
        with open(r["log"], encoding="utf-8", errors="replace") as f:
            self.assertIn("from a job", f.read())

    def test_commands_register_and_store(self):
        self.assertTrue(self.call("commands.register", name="zz-site-deploy", run="echo deploying", help="x")["result"])
        self.assertEqual(self.sh.kind_of("zz-site-deploy"), "app")
        self.assertTrue(self.call("commands.unregister", name="zz-site-deploy")["result"])
        self.assertEqual(self.sh.kind_of("zz-site-deploy"), "unknown")
        self.call("store.set", app="myapp", key="count", value={"n": 3})
        self.assertEqual(self.call("store.get", app="myapp", key="count")["result"], {"n": 3})

    def test_odus(self):
        st = self.call("odus.status")["result"]
        if st["file"]:
            self.assertEqual(self.call("odus.run", args=["version"])["result"]["exit_code"], 0)

    # ------------------------------------------------------------ errors and safety
    def test_errors(self):
        self.assertEqual(self.call("nope.nothing")["error"]["code"], "unknown_method")
        self.assertEqual(self.call("session.cd")["error"]["code"], "bad_params")
        self.assertEqual(self.call("session.cd", path=os.path.join(TMP, "missing"))["error"]["code"], "not_found")
        host, _, port = self.env["COMMANDS_API"].rpartition(":")
        with socket.create_connection((host, int(port))) as s:
            s.sendall(b"not json\n")
            self.assertEqual(json.loads(s.makefile().readline())["error"]["code"], "bad_request")
        with socket.create_connection((host, int(port))) as s:
            s.sendall(b'{"token":"guess","id":1,"method":"api.version"}\n')
            self.assertEqual(json.loads(s.makefile().readline())["error"]["code"], "bad_token")

    def test_many_calls_on_one_connection(self):
        out = self.py("""
from commands_api import Commands
cx = Commands()
print(sum(cx.call("net.free_port") > 0 for _ in range(200)))
""")
        self.assertEqual(out.strip(), "200")

    # ------------------------------------------------------------ other languages
    @unittest.skipUnless(IS_WIN, "batch file")
    def test_batch_file_uses_the_api(self):
        target = tempfile.mkdtemp(dir=TMP)
        bat = os.path.join(TMP, "use-api.cmd")
        with open(bat, "w", newline="\r\n") as f:
            f.write("@echo off\n"
                    f'call commands api session.cd path="{target}" >nul\n'
                    "call commands api session.setenv name=FROM_BATCH value=yes >nul\n"
                    'call commands api ui.choose items="[\\"C:\\One\\",\\"C:\\Two\\"]" --raw > "%TEMP%\\cx-pick.txt"\n'
                    'set /p PICK=<"%TEMP%\\cx-pick.txt"\n'
                    "echo picked %PICK%\n")
        self.user.press("down", "enter")
        r = subprocess.run(["cmd", "/d", "/c", bat], env=self.env, capture_output=True, text=True, timeout=120)
        self.assertIn(r"picked C:\Two", r.stdout, r.stderr)
        self.assertEqual(os.path.normcase(os.getcwd()), os.path.normcase(target))
        self.assertEqual(os.environ.pop("FROM_BATCH", None), "yes")

    @unittest.skipUnless(PS, "PowerShell")
    def test_powershell_script_uses_the_api(self):
        ps1 = os.path.join(TMP, "use-api.ps1")
        with open(ps1, "w", encoding="utf-8-sig") as f:
            f.write("$info = commands api sys.info | ConvertFrom-Json\n"
                    "commands api session.setenv name=FROM_PS1 value=$($info.threads) | Out-Null\n"
                    "$ok = commands api ui.confirm question='Continue?' --raw\n"
                    "Write-Output \"confirm=$ok\"\n")
        self.user.press("n")
        r = subprocess.run([PS, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps1], env=self.env,
                           capture_output=True, text=True, timeout=120)
        self.assertIn("confirm=false", r.stdout, r.stderr)
        self.assertEqual(os.environ.pop("FROM_PS1", None), str(os.cpu_count()))

    @unittest.skipUnless(NODE, "Node.js")
    def test_node_app(self):
        sdk = os.path.join(ROOT, "sdk", "node", "commands-api.js").replace("\\", "\\\\")
        target = tempfile.mkdtemp(dir=TMP).replace("\\", "\\\\")
        js = (f'const {{ Commands }} = require("{sdk}");\n'
              "(async () => {\n"
              "  const cx = new Commands();\n"
              "  const v = await cx.call('api.version');\n"
              "  const pick = await cx.choose(['red', 'green'], { title: 'Color' });\n"
              f"  await cx.cd('{target}');\n"
              "  let err = null; try { await cx.call('nope'); } catch (e) { err = e.code; }\n"
              "  console.log(JSON.stringify([v.api, pick, err]));\n"
              "  cx.close();\n"
              "})();\n")
        path = os.path.join(TMP, "app.js")
        with open(path, "w") as f:
            f.write(js)
        self.user.press("down", "enter")
        r = subprocess.run([NODE, path], env=self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual(json.loads(r.stdout), [1, "green", "unknown_method"], r.stderr)
        self.assertEqual(os.path.normcase(os.getcwd()), os.path.normcase(target.replace("\\\\", "\\")))

    def test_cli_outside_a_window(self):
        env = {k: v for k, v in self.env.items() if not k.startswith("COMMANDS_API")}
        r = subprocess.run([sys.executable, os.path.join(ROOT, "run.py"), "api", "session.cd", "path=."],
                           env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(json.loads(r.stderr)["code"], "needs_session")
        r = subprocess.run([sys.executable, os.path.join(ROOT, "run.py"), "api", "fs.size", f"path={ROOT}"],
                           env=env, capture_output=True, text=True, timeout=60)
        self.assertGreater(json.loads(r.stdout)["files"], 5)


class OdusQuestion(unittest.TestCase):
    """The first-start question about putting Odus on PATH."""

    def setUp(self):
        from commands import odus_setup
        self.o = odus_setup
        self.saved = (odus_setup.odus_file, odus_setup.on_path, odus_setup.add, ui.can_interact, ui.confirm)
        self.added = []
        odus_setup.odus_file = lambda: os.path.join(TMP, "Odus", "odus.py")
        odus_setup.on_path = lambda: False
        odus_setup.add = lambda: self.added.append(1) or ""
        ui.can_interact = lambda: True

    def tearDown(self):
        (self.o.odus_file, self.o.on_path, self.o.add, ui.can_interact, ui.confirm) = self.saved

    def ask(self, answer):
        asked = []
        ui.confirm = lambda q, default=False: asked.append(q) or answer
        sh = Shell(interactive=False)
        sh.config.pop("odus_on_path", None)
        with contextlib.redirect_stdout(open(os.devnull, "w", encoding="utf-8")):
            self.o.first_run(sh)
        return sh, asked

    def test_yes_adds_it(self):
        sh, asked = self.ask(True)
        self.assertEqual(len(asked), 1)
        self.assertEqual(self.added, [1])
        self.assertEqual(sh.config["odus_on_path"], "yes")
        self.assertEqual(sh.kind_of("install"), "odus")

    def test_no_is_remembered_and_odus_is_not_run(self):
        sh, asked = self.ask(False)
        self.assertEqual(self.added, [])
        self.assertEqual(sh.config["odus_on_path"], "no")
        self.assertEqual(sh.kind_of("install"), "odus-off")
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = sh.run_line("install vlc")
        self.assertEqual(rc, 9009)
        self.assertIn("Odus isn't on your PATH", buf.getvalue())
        # not asked again next time
        asked2 = []
        ui.confirm = lambda q, default=False: asked2.append(q) or True
        self.o.first_run(sh)
        self.assertEqual(asked2, [])

    def test_not_asked_when_already_on_path(self):
        self.o.on_path = lambda: True
        sh, asked = self.ask(True)
        self.assertEqual(asked, [])
        self.assertEqual(sh.config["odus_on_path"], "yes")


if __name__ == "__main__":
    unittest.main()
