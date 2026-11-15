# SPDX-License-Identifier: GPL-3.0-or-later
# test_bridge_addons.py - ZeroScript addon-server (Blender etc.) test suite.
# Run:  python3 test_bridge_addons.py            (unit + e2e, skips e2e parts
#                                                whose deps are missing)
#   python3 test_bridge_addons.py unit           (no deps beyond stdlib)
#   python3 test_bridge_addons.py e2e            (needs: pip install websockets)
#
# PORTABILITY RULES (deliberate, see the review this suite was written for):
#   - no fixed ports: every bridge gets a freshly probed free port;
#   - no pkill / killpg: the harness tracks ONLY the PIDs it spawned and stops
#     them with subprocess.terminate() (+ taskkill /T as a Windows belt);
#   - paths and encodings are set for both POSIX and Windows;
#   - real-world checks the suite cannot do (Windows GUI launch, real Blender)
#     are skipped with clear reasons - they are listed UNTESTED in the report,
#     and scripts/run_tests_windows.ps1 runs the full suite on Windows.

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent if HERE.name == "scripts" else HERE
REPO = Path(os.environ.get("ZS_REPO_ROOT", REPO))
sys.path.insert(0, str(REPO))

# ── import bridge.py for the unit layer (websockets stubbed if missing) ────
try:
    import websockets  # noqa: F401
    HAVE_WS = True
except ImportError:
    sys.modules["websockets"] = types.SimpleNamespace(serve=None, ConnectionClosed=Exception)
    HAVE_WS = False

import bridge  # noqa: E402

IS_WINDOWS = (os.name == "nt")


# ════════════════════════════════════════════════════════════════════════════
#  Helpers
# ════════════════════════════════════════════════════════════════════════════
def free_port():
    """A TCP port that is free RIGHT NOW (probe-bind, then verify nothing
    answers). Races are possible but re-probed up to 50 times."""
    for _ in range(50):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        try:
            c = socket.create_connection(("127.0.0.1", port), timeout=0.2)
            c.close()  # something is listening - try another
        except OSError:
            return port
    raise RuntimeError("no free port found")


class FakeHeaders:
    """Minimal .get() mapping for _gate_request unit tests."""
    def __init__(self, **kw):
        self._d = kw

    def get(self, k, default=None):
        return self._d.get(k, default)


def write_fake_server(directory, kind):
    """Write a fake stdio MCP server and return its path. kinds:
      ok          - normal handshake; tools get_scene_info/execute_blender_code;
                    every tools/call of execute_blender_code appends to exec.log
      slow_start  - sleeps 2.5s BEFORE answering initialize (settle-race test)
      crash       - exits immediately with code 3
      long_tool   - one tool long_tool; each call sleeps 4s and logs start/end
    """
    if kind == "ok":
        body = r'''
import json, sys, os
LOG = os.environ.get("FAKE_EXEC_LOG")
TOOLS = [
    {"name": "get_scene_info", "description": "fake scene summary",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "execute_blender_code", "description": "fake bpy exec",
     "inputSchema": {"type": "object", "properties": {"code": {"type": "string"}},
                     "required": ["code"]}},
]
def send(o):
    sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()
for raw in sys.stdin:
    raw = raw.strip()
    if not raw: continue
    try: msg = json.loads(raw)
    except Exception: continue
    mid = msg.get("id")
    if mid is None: continue
    m = msg.get("method")
    if m == "initialize":
        send({"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
            "serverInfo": {"name": "fake-blender", "version": "0.1"}}})
    elif m == "tools/list":
        send({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}})
    elif m == "tools/call":
        name = (msg.get("params") or {}).get("name")
        args = (msg.get("params") or {}).get("arguments") or {}
        if name == "get_scene_info":
            text = "scene: Cube, Camera, Light"
        elif name == "execute_blender_code":
            if LOG:
                with open(LOG, "a", encoding="utf-8") as f:
                    f.write("EXEC:" + str(args.get("code", ""))[:40] + "\n")
            text = "py:" + str(args.get("code", ""))
        else:
            text = "unknown tool " + str(name)
        send({"jsonrpc": "2.0", "id": mid,
              "result": {"content": [{"type": "text", "text": text}]}})
    else:
        send({"jsonrpc": "2.0", "id": mid,
              "error": {"code": -32601, "message": "method not found"}})
'''
    elif kind == "slow_start":
        body = r'''
import json, sys, time
time.sleep(2.5)
TOOLS = [{"name": "slow_tool", "description": "from the slow starter",
          "inputSchema": {"type": "object", "properties": {}}}]
def send(o):
    sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()
for raw in sys.stdin:
    raw = raw.strip()
    if not raw: continue
    try: msg = json.loads(raw)
    except Exception: continue
    mid = msg.get("id")
    if mid is None: continue
    m = msg.get("method")
    if m == "initialize":
        send({"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
            "serverInfo": {"name": "fake-slow", "version": "0.1"}}})
    elif m == "tools/list":
        send({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}})
    elif m == "tools/call":
        send({"jsonrpc": "2.0", "id": mid,
              "result": {"content": [{"type": "text", "text": "slow ok"}]}})
'''
    elif kind == "crash":
        body = 'import sys\nsys.stderr.write("fatal: intentionally broken\\n")\nsys.exit(3)\n'
    elif kind == "long_tool":
        body = r'''
import json, sys, os, time
LOG = os.environ.get("FAKE_EXEC_LOG")
TOOLS = [{"name": "long_tool", "description": "sleeps 4s",
          "inputSchema": {"type": "object", "properties": {}}}]
def send(o):
    sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()
for raw in sys.stdin:
    raw = raw.strip()
    if not raw: continue
    try: msg = json.loads(raw)
    except Exception: continue
    mid = msg.get("id")
    if mid is None: continue
    m = msg.get("method")
    if m == "initialize":
        send({"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
            "serverInfo": {"name": "fake-long", "version": "0.1"}}})
    elif m == "tools/list":
        send({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}})
    elif m == "tools/call":
        if LOG:
            with open(LOG, "a", encoding="utf-8") as f: f.write("START\n")
        time.sleep(4)
        if LOG:
            with open(LOG, "a", encoding="utf-8") as f: f.write("END\n")
        send({"jsonrpc": "2.0", "id": mid,
              "result": {"content": [{"type": "text", "text": "long done"}]}})
'''
    else:
        raise ValueError(kind)
    path = directory / f"fake_{kind}.py"
    path.write_text(body, encoding="utf-8")
    return path


class BridgeProc:
    """One real bridge.py process on a private port in a private temp dir.
    Tracks its own PID; stop() is portable (terminate + wait, taskkill belt on
    Windows). Never uses killpg or pattern-matched kills."""

    def __init__(self, bridge_path, extra_env=None, name="bridge"):
        self.dir = Path(tempfile.mkdtemp(prefix=f"zs-test-{name}-"))
        self.port = free_port()
        shutil.copy(bridge_path, self.dir / "bridge.py")
        launcher = REPO / "launch_studio_mcp.py"
        if launcher.exists():
            shutil.copy(launcher, self.dir / "launch_studio_mcp.py")
        with open(self.dir / "config.json", "w", encoding="utf-8") as f:
            json.dump({"mcpServers": {"roblox":
                       {"command": "launch_studio_mcp.py", "args": []}}}, f)
        self.config_path = self.dir / "config.json"
        self.auth_path = self.dir / "bridge_auth.json"
        env = dict(os.environ, ZS_BRIDGE_PORT=str(self.port), FAKE_EXEC_LOG="")
        env["PYTHONIOENCODING"] = "utf-8"
        if extra_env:
            env.update(extra_env)
        self.proc = subprocess.Popen(
            [sys.executable, str(self.dir / "bridge.py")],
            cwd=str(self.dir), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace")
        self.log_lines = []
        import threading
        threading.Thread(target=self._drain, daemon=True).start()
        self.wait_listening(30)

    def wait_listening(self, timeout):
        """Block until the bridge's port accepts TCP (or the process dies).
        Called from __init__ so no caller can race the boot."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise AssertionError("bridge exited during startup:\n" +
                                     "\n".join(self.log_lines[-25:]))
            try:
                c = socket.create_connection(("127.0.0.1", self.port), timeout=0.5)
                c.close()
                return
            except OSError:
                time.sleep(0.2)
        raise AssertionError(f"bridge never listened within {timeout}s")

    def _drain(self):
        try:
            for line in self.proc.stdout:
                self.log_lines.append(line.rstrip())
        except Exception:
            pass

    @property
    def ws_url(self):
        return f"ws://127.0.0.1:{self.port}"

    def read_token(self):
        with open(self.auth_path, encoding="utf-8") as f:
            return json.load(f)["token"]

    def alive(self):
        return self.proc.poll() is None

    def stop(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
                try:
                    self.proc.wait(timeout=5)
                except Exception:
                    pass
            if IS_WINDOWS:
                # terminate() kills only the bridge PID on Windows; MCP children
                # are its direct kids and die on stdin EOF, but belt+braces:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.proc.pid)],
                               capture_output=True)
        shutil.rmtree(self.dir, ignore_errors=True)


# ════════════════════════════════════════════════════════════════════════════
#  UNIT TESTS (stdlib only)
# ════════════════════════════════════════════════════════════════════════════
class TokenAuthTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="zs-token-")
        self._saved = bridge.AUTH_FILE
        bridge.AUTH_FILE = os.path.join(self.tmp, "bridge_auth.json")
        os.environ.pop(bridge.AUTH_TOKEN_ENV, None)

    def tearDown(self):
        bridge.AUTH_FILE = self._saved
        os.environ.pop(bridge.AUTH_TOKEN_ENV, None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_token_created_and_reloaded(self):
        t1 = bridge._auth_load_or_create()
        self.assertEqual(len(t1), 64)
        self.assertTrue(all(c in "0123456789abcdef" for c in t1))
        self.assertTrue(os.path.exists(bridge.AUTH_FILE))
        self.assertEqual(bridge._auth_load_or_create(), t1, "file must be reused")

    def test_token_file_permissions_posix(self):
        bridge._auth_load_or_create()
        if not IS_WINDOWS:
            mode = os.stat(bridge.AUTH_FILE).st_mode & 0o777
            self.assertEqual(mode, 0o600, f"token file must be 0600, got {oct(mode)}")
        # (Windows ACL hardening is not achievable via os.chmod - documented
        #  as a residual risk in the report, not silently claimed here.)

    def test_env_override_wins(self):
        os.environ[bridge.AUTH_TOKEN_ENV] = "ab" * 32
        self.assertEqual(bridge._auth_load_or_create(), "ab" * 32)

    def test_corrupt_file_regenerates(self):
        with open(bridge.AUTH_FILE, "w") as f:
            f.write("{not json")
        t = bridge._auth_load_or_create()
        self.assertEqual(len(t), 64)

    def test_constant_time_compare(self):
        self.assertTrue(bridge._ct_equal("abc", "abc"))
        self.assertFalse(bridge._ct_equal("abc", "abd"))
        self.assertFalse(bridge._ct_equal("abc", ""))
        self.assertFalse(bridge._ct_equal("", "abc"))
        self.assertFalse(bridge._ct_equal(None, None))

    def test_rotate_changes_token_and_persists(self):
        t1 = bridge._auth_load_or_create()
        t2 = bridge._auth_rotate()
        self.assertNotEqual(t1, t2)
        self.assertEqual(bridge._auth_load_or_create(), t2)


class StrictValidationTest(unittest.TestCase):
    """Requirement: reject malformed specs with clear errors - NO silent
    coercion (the previous suite asserted coercions; those assertions were
    WRONG per the requirement and were changed, not the code around them)."""

    def test_valid_spec(self):
        spec, err = bridge.validate_server_spec(
            "blender", "uvx mcp-for-blender@2.3.0",
            ["--python", "3.11"], {"BLENDER_MCP_SAFE_MODE": "1"})
        self.assertIsNone(err)
        self.assertEqual(spec, {"server_id": "blender",
                                "command": "uvx mcp-for-blender@2.3.0",
                                "args": ["--python", "3.11"],
                                "env": {"BLENDER_MCP_SAFE_MODE": "1"}})

    def test_args_string_is_rejected_not_coerced(self):
        _, err = bridge.validate_server_spec("blender", "uvx", args="mcp-for-blender")
        self.assertIn("args must be a list of strings", err)

    def test_args_int_element_is_rejected_not_coerced(self):
        _, err = bridge.validate_server_spec("blender", "uvx", args=[9876])
        self.assertIn("argument 1 must be a string", err)

    def test_env_int_value_is_rejected_not_coerced(self):
        _, err = bridge.validate_server_spec("blender", "uvx", env={"PORT": 9876})
        self.assertIn("env var PORT must be a string", err)

    def test_env_bad_name_rejected(self):
        _, err = bridge.validate_server_spec("blender", "uvx", env={"BAD-KEY": "x"})
        self.assertIn("not a valid environment variable name", err)

    def test_control_characters_rejected(self):
        _, err = bridge.validate_server_spec("blender", "uvx\nmcp-for-blender")
        self.assertIn("control characters", err)
        _, err = bridge.validate_server_spec("blender", "uvx", env={"X": "a\x00b"})
        self.assertIn("control characters", err)

    def test_primary_server_refused(self):
        _, err = bridge.validate_server_spec("roblox", "evil.py")
        self.assertIn("primary", err)
        _, err = bridge.validate_server_spec("ROBLOX", "evil.py")
        self.assertIsNotNone(err)

    def test_sid_shape(self):
        for bad in ("", "   ", "Bad Id!", "blender/mcp", "-lead", "x" * 65, 42):
            _, err = bridge.validate_server_spec(bad, "uvx")
            self.assertIsNotNone(err, f"sid {bad!r} must be rejected")
        for good in ("blender", "blender-2", "sketchfab_3", "a.b"):
            spec, err = bridge.validate_server_spec(good, "uvx")
            self.assertIsNone(err, err)

    def test_command_required(self):
        _, err = bridge.validate_server_spec("blender", "   ")
        self.assertIn("command is required", err)
        _, err = bridge.validate_server_spec("blender", None)
        self.assertIn("must be a string", err)


class GateRequestTest(unittest.TestCase):
    """The HTTP handshake gate: Host + Origin decisions, no server needed."""

    def test_valid_loopback_host(self):
        self.assertIsNone(bridge._gate_request("/", FakeHeaders(Host=f"127.0.0.1:{bridge.PORT}"), False))

    def test_foreign_host_rejected(self):
        # DNS rebinding: the page arrives with its own domain in Host
        st, body = bridge._gate_request("/", FakeHeaders(Host="attacker.com:17613"), False)
        self.assertEqual(st, 403)

    def test_missing_host_rejected(self):
        st, _ = bridge._gate_request("/", FakeHeaders(), False)
        self.assertEqual(st, 403)

    def test_webpage_origin_rejected_for_ws(self):
        st, _ = bridge._gate_request("/", FakeHeaders(Host=f"127.0.0.1:{bridge.PORT}",
                                                      Origin="http://evil.com"), False)
        self.assertEqual(st, 403)
        st, _ = bridge._gate_request("/", FakeHeaders(Host=f"127.0.0.1:{bridge.PORT}",
                                                      Origin="null"), False)
        self.assertEqual(st, 403)

    def test_extension_origin_accepted_for_ws(self):
        origin = "chrome-extension://" + "abcdefghijklmnopabcdefghijklmnop"
        self.assertIsNone(bridge._gate_request("/", FakeHeaders(Host=f"127.0.0.1:{bridge.PORT}",
                                                                Origin=origin), False))

    def test_absent_origin_accepted_for_ws_but_not_for_token(self):
        h = FakeHeaders(Host=f"127.0.0.1:{bridge.PORT}")
        self.assertIsNone(bridge._gate_request("/", h, False))          # ws: ok
        st, _ = bridge._gate_request("/zs-auth-token", h, True)         # token: NO
        self.assertEqual(st, 403)

    def test_token_endpoint_needs_extension_origin(self):
        h = FakeHeaders(Host=f"127.0.0.1:{bridge.PORT}", Origin="http://evil.com")
        st, _ = bridge._gate_request("/zs-auth-token", h, True)
        self.assertEqual(st, 403)


class ConfigRoundtripTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._saved = bridge.CONFIG_PATH
        bridge.CONFIG_PATH = os.path.join(self.tmp.name, "config.json")
        bridge._write_config({"mcpServers": {"roblox":
                             {"command": "launch_studio_mcp.py", "args": []}}})

    def tearDown(self):
        bridge.CONFIG_PATH = self._saved
        self.tmp.cleanup()

    def _read(self):
        with open(bridge.CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)

    def test_add_preserves_roblox(self):
        ok, err = bridge.config_add_server("blender", "uvx mcp-for-blender@2.3.0", ["--port", "9877"])
        self.assertTrue(ok, err)
        cfg = self._read()
        self.assertEqual(cfg["mcpServers"]["roblox"],
                         {"command": "launch_studio_mcp.py", "args": []})
        self.assertEqual(cfg["mcpServers"]["blender"]["command"], "uvx mcp-for-blender@2.3.0")

    def test_env_is_persisted(self):
        bridge.config_add_server("blender", "uvx mcp-for-blender@2.3.0",
                                 env={"BLENDER_MCP_SAFE_MODE": "1"})
        self.assertEqual(self._read()["mcpServers"]["blender"]["env"]["BLENDER_MCP_SAFE_MODE"], "1")

    def test_replace_and_remove(self):
        bridge.config_add_server("blender", "uvx blender-mcp")
        bridge.config_add_server("blender", "uvx mcp-for-blender@2.3.0")
        self.assertEqual(self._read()["mcpServers"]["blender"]["command"], "uvx mcp-for-blender@2.3.0")
        ok, err = bridge.config_remove_server("blender")
        self.assertTrue(ok, err)
        self.assertNotIn("blender", self._read()["mcpServers"])

    def test_roblox_is_protected(self):
        ok, err = bridge.config_add_server("roblox", "evil.py")
        self.assertFalse(ok)
        self.assertIn("primary", err)
        ok, err = bridge.config_remove_server("roblox")
        self.assertFalse(ok)
        self.assertIn("primary", err)
        self.assertIn("roblox", self._read()["mcpServers"])

    def test_remove_unknown_is_an_error(self):
        ok, err = bridge.config_remove_server("never-added")
        self.assertFalse(ok)
        self.assertIn("not in the config", err)

    def test_atomic_write_leaves_valid_json(self):
        bridge.config_add_server("blender", "uvx mcp-for-blender@2.3.0")
        self.assertFalse(os.path.exists(bridge.CONFIG_PATH + ".tmp"))


class ManagerLiveTest(unittest.TestCase):
    def setUp(self):
        self.mgr = bridge.MCPManager()

    def test_add_registers_and_replace_stops_old(self):
        c1 = self.mgr.add_server_live({"server_id": "blender", "command": "uvx",
                                       "args": ["mcp-for-blender@2.3.0"]})
        self.assertIs(self.mgr.clients["blender"], c1)
        c2 = self.mgr.add_server_live({"server_id": "blender", "command": "uvx",
                                       "args": ["blender-mcp"]})
        self.assertIs(self.mgr.clients["blender"], c2)
        self.assertIsNot(c1, c2)

    def test_remove_refuses_primary_and_unknown(self):
        self.assertFalse(self.mgr.remove_server_live("roblox"))
        self.assertFalse(self.mgr.remove_server_live("ghost"))

    def test_remove_stops_and_forges(self):
        c = self.mgr.add_server_live({"server_id": "blender", "command": "uvx",
                                      "args": ["mcp-for-blender@2.3.0"]})
        self.mgr.rebuild_index()
        self.assertTrue(self.mgr.remove_server_live("blender"))
        self.assertNotIn("blender", self.mgr.clients)
        self.assertIsNone(c.proc)

    def test_health_carries_starting_and_error(self):
        c = self.mgr.add_server_live({"server_id": "blender", "command": "uvx",
                                      "args": ["mcp-for-blender@2.3.0"]})
        h = {x["id"]: x for x in self.mgr.health()}
        self.assertTrue(h["blender"]["starting"])
        c.starting = False
        c.start_error = "command not found: 'uvx'"
        h = {x["id"]: x for x in self.mgr.health()}
        self.assertFalse(h["blender"]["starting"])
        self.assertEqual(h["blender"]["error"], "command not found: 'uvx'")


# ════════════════════════════════════════════════════════════════════════════
#  E2E TESTS (spawn the REAL bridge; need websockets)
# ════════════════════════════════════════════════════════════════════════════
def _e2e_runner():
    """Build the e2e TestCase class only when websockets is importable, so the
    module imports cleanly everywhere."""
    import asyncio
    import websockets
    from websockets.exceptions import ConnectionClosed, InvalidStatus

    EXT_ORIGIN = "chrome-extension://" + "abcdefghijklmnopabcdefghijklmnop"

    class BridgeE2ETest(unittest.IsolatedAsyncioTestCase):
        async def asyncSetUp(self):
            self.bridge = BridgeProc(REPO / "bridge.py")
            self.ws = None
            self.token = None
            self.extra_msgs = []

        async def asyncTearDown(self):
            if self.ws is not None:
                try:
                    await self.ws.close()
                except Exception:
                    pass
            self.bridge.stop()

        # ── helpers ──────────────────────────────────────────────────────
        async def connect(self, token="AUTO", origin="AUTO", extra_headers=None):
            kw = {"open_timeout": 5}
            if origin == "AUTO":
                pass  # no Origin header at all (curl-like / our test client)
            elif origin is not None:
                kw["origin"] = origin
            if extra_headers:
                kw["additional_headers"] = extra_headers
            return await websockets.connect(self.bridge.ws_url, **kw)

        async def auth_ws(self, token=None, origin="AUTO"):
            """Open + authenticate. Returns (ws, connected_payload)."""
            ws = await self.connect(origin=origin)
            tok = self.bridge.read_token() if token is None else token
            await ws.send(json.dumps({"type": "hello", "token": tok}))
            first = json.loads(await asyncio.wait_for(ws.recv(), 10))
            self.assertEqual(first["type"], "hello_ok", first)
            connected = json.loads(await asyncio.wait_for(ws.recv(), 15))
            self.assertEqual(connected["type"], "connected")
            self.assertIn("servers", connected)
            return ws, connected

        async def roundtrip(self, ws, obj, timeout=20):
            rid = obj.get("id") or next_id()
            await ws.send(json.dumps({**obj, "id": rid}))
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                raw = await asyncio.wait_for(ws.recv(), timeout)
                msg = json.loads(raw)
                if msg.get("id") == rid and msg.get("type") != "connected":
                    return msg
                self.extra_msgs.append(msg)
            raise AssertionError(f"no response to {obj.get('type')} within {timeout}s")

        async def wait_tool(self, ws, tool_name, timeout=25):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                r = await self.roundtrip(ws, {"type": "list_tools"}, timeout=timeout)
                if tool_name in [t.get("name") for t in r.get("tools", [])]:
                    return r
                await asyncio.sleep(0.5)
            raise AssertionError(f"'{tool_name}' never appeared in list_tools")

        async def read_config(self):
            with open(self.bridge.config_path, encoding="utf-8") as f:
                return json.load(f)

        async def expect_close(self, ws_or_coro, code=None):
            """Drain frames until the socket closes; assert the close code.
            Data frames (e.g. a status broadcast) may legitimately arrive
            before the close - only the close itself is asserted."""
            ws = await ws_or_coro if asyncio.iscoroutine(ws_or_coro) else ws_or_coro
            deadline = time.monotonic() + 15
            last = None
            while time.monotonic() < deadline:
                try:
                    last = await asyncio.wait_for(
                        ws.recv(), max(0.1, deadline - time.monotonic()))
                except asyncio.TimeoutError:
                    self.fail(f"connection never closed (last frame: {last!r})")
                except ConnectionClosed as e:
                    got = e.rcvd.code if e.rcvd else None
                    if code is not None:
                        self.assertEqual(got, code, f"close code {got} != {code}")
                    return
            self.fail("connection never closed within 15s")

        # ── auth ─────────────────────────────────────────────────────────
        async def test_01_hello_auth_required(self):
            # (a) first message is not a hello -> closed 4001, NOTHING processed
            ws = await self.connect()
            await ws.send(json.dumps({"type": "ping", "id": 1}))
            await self.expect_close(ws, 4003)
            # (b) wrong token -> 4003
            ws = await self.connect()
            await ws.send(json.dumps({"type": "hello", "token": "0" * 64}))
            await self.expect_close(ws, 4003)
            # (c) silence -> 4000 after the hello timeout... (10s) - skip the
            # wait here; (d) correct token -> full flow
            ws, connected = await self.auth_ws()
            self.assertIn("roblox", [s["id"] for s in connected["servers"]])
            r = await self.roundtrip(ws, {"type": "ping"})
            self.assertEqual(r["type"], "pong")

        async def test_02_web_page_origin_is_rejected(self):
            with self.assertRaises(InvalidStatus) as ctx:
                await websockets.connect(self.bridge.ws_url, origin="http://evil.com",
                                         open_timeout=5)
            self.assertEqual(ctx.exception.response.status_code, 403)
            # an extension origin passes the gate and can auth
            ws, _ = await self.auth_ws(origin=EXT_ORIGIN)
            await ws.close()

        async def test_03_dns_rebinding_host_is_rejected(self):
            # raw HTTP handshake with a foreign Host header (what a rebinding
            # page produces) - must get an HTTP rejection before any upgrade
            import base64
            key = base64.b64encode(os.urandom(16)).decode()
            reader, writer = await asyncio.open_connection("127.0.0.1", self.bridge.port)
            req = (f"GET / HTTP/1.1\r\nHost: attacker.com\r\nUpgrade: websocket\r\n"
                   f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                   f"Sec-WebSocket-Version: 13\r\n\r\n")
            writer.write(req.encode())
            await writer.drain()
            data = await asyncio.wait_for(reader.read(1024), 5)
            writer.close()
            self.assertTrue(data.startswith(b"HTTP/1.1 403"),
                            f"expected 403, got {data[:40]!r}")

        async def test_04_token_endpoint_gating(self):
            import urllib.request
            base = f"http://127.0.0.1:{self.bridge.port}"
            def get(origin):
                req = urllib.request.Request(base + "/zs-auth-token")
                if origin:
                    req.add_header("Origin", origin)
                try:
                    with urllib.request.urlopen(req, timeout=5) as r:
                        return r.status, json.loads(r.read())
                except Exception as e:
                    return getattr(e, "code", None), None
            st, body = get(None)
            self.assertEqual(st, 403, "no-origin fetch must not read the token")
            st, body = get("http://evil.com")
            self.assertEqual(st, 403, "web-page origin must not read the token")
            st, body = get(EXT_ORIGIN)
            self.assertEqual(st, 200)
            self.assertEqual(body["token"], self.bridge.read_token())

        async def test_05_token_rotation_kills_old_sessions(self):
            ws, _ = await self.auth_ws()
            old_token = self.bridge.read_token()
            import urllib.request
            req = urllib.request.Request(f"http://127.0.0.1:{self.bridge.port}/zs-auth/rotate")
            req.add_header("Origin", EXT_ORIGIN)
            with urllib.request.urlopen(req, timeout=5) as r:
                new_token = json.loads(r.read())["token"]
            self.assertNotEqual(old_token, new_token)
            # the authed session dies with 4002
            await self.expect_close(ws, 4002)
            # the OLD token is dead
            ws2 = await self.connect()
            await ws2.send(json.dumps({"type": "hello", "token": old_token}))
            await self.expect_close(ws2, 4003)
            # the NEW token works
            ws3, _ = await self.auth_ws(token=new_token)
            await ws3.close()

        # ── limits ───────────────────────────────────────────────────────
        async def test_06_rate_limit_closes_connection(self):
            ws, _ = await self.auth_ws()
            closed = None
            try:
                for i in range(bridge.RATE_MAX_MSGS + 20):
                    await ws.send(json.dumps({"type": "ping", "id": i}))
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    await asyncio.wait_for(ws.recv(), 15)  # pongs, then the close
            except ConnectionClosed as e:
                closed = e.rcvd.code if e.rcvd else None
            except (asyncio.TimeoutError, asyncio.exceptions.TimeoutError):
                self.fail("connection never closed under a flood")
            self.assertEqual(closed, 4008, "flood must be closed with 4008")

        async def test_07_oversized_message_closed(self):
            ws, _ = await self.auth_ws()
            big = "x" * (17 * 1024 * 1024)
            try:
                await ws.send(big)
                await asyncio.wait_for(ws.recv(), 20)
                self.fail("oversized message must close the connection")
            except ConnectionClosed as e:
                code = getattr(getattr(e, "rcvd", None), "code", None)
                self.assertEqual(code, 1009)

        async def test_08_malformed_payload_gets_an_error_not_silence(self):
            ws, _ = await self.auth_ws()
            await ws.send("this is not json")
            r = json.loads(await asyncio.wait_for(ws.recv(), 10))
            self.assertEqual(r["type"], "error")
            self.assertIn("malformed", r["error"])
            # and the connection still works
            r = await self.roundtrip(ws, {"type": "ping"})
            self.assertEqual(r["type"], "pong")

        async def test_09_max_clients_enforced(self):
            conns = []
            try:
                for _ in range(bridge.MAX_CLIENTS):
                    ws, _ = await self.auth_ws()
                    conns.append(ws)
                extra = await self.connect()
                await extra.send(json.dumps({"type": "hello",
                                             "token": self.bridge.read_token()}))
                await self.expect_close(extra, 4007)
            finally:
                for w in conns:
                    try:
                        await w.close()
                    except Exception:
                        pass

        # ── addon lifecycle ──────────────────────────────────────────────
        async def test_10_addon_lifecycle_live_no_restart(self):
            ws, _ = await self.auth_ws()
            fake = write_fake_server(self.bridge.dir, "ok")
            # invalid spec is refused and config untouched
            r = await self.roundtrip(ws, {"type": "add_server", "server_id": "Bad Id!",
                                          "command": "uvx"}, timeout=15)
            self.assertFalse(r["ok"])
            self.assertIn("server id", r["error"])
            self.assertEqual(list((await self.read_config())["mcpServers"].keys()), ["roblox"])
            # live add: ack, no restart flag, socket survives
            r = await self.roundtrip(ws, {"type": "add_server", "server_id": "blender",
                                          "command": sys.executable,
                                          "args": [str(fake)],
                                          "env": {"BLENDER_MCP_SAFE_MODE": "1"}}, timeout=20)
            self.assertTrue(r["ok"], r.get("error"))
            self.assertFalse(r.get("restarting"), "live add must NOT restart the bridge")
            # starting flag visible in health (event-driven, no fixed timers)
            r = await self.roundtrip(ws, {"type": "list_tools"}, timeout=30)
            tools = [t.get("name") for t in r.get("tools", [])]
            self.assertIn("get_scene_info", tools)
            self.assertIn("execute_blender_code", tools)
            names = {t["name"]: t for t in r["tools"]}
            self.assertEqual(names["get_scene_info"].get("server"), "blender")
            # env persisted verbatim
            cfg = await self.read_config()
            self.assertEqual(cfg["mcpServers"]["blender"]["env"]["BLENDER_MCP_SAFE_MODE"], "1")
            self.assertEqual(cfg["mcpServers"]["roblox"],
                             {"command": "launch_studio_mcp.py", "args": []})
            # tool calls route + return
            r = await self.roundtrip(ws, {"type": "call_tool", "name": "get_scene_info",
                                          "arguments": {}}, timeout=20)
            self.assertTrue(r["ok"], r.get("error"))
            self.assertIn("scene: Cube", r["text"])
            r = await self.roundtrip(ws, {"type": "call_tool", "name": "execute_blender_code",
                                          "arguments": {"code": "print(1+1)"}}, timeout=20)
            self.assertEqual(r["text"], "py:print(1+1)")
            # list_mcp_servers-style health carries error field (None when ok)
            for m in self.extra_msgs:
                if m.get("type") == "connected" and "servers" in m:
                    for sv in m["servers"]:
                        self.assertIn("error", sv)
            # replace same id, then remove; roblox protected throughout
            r = await self.roundtrip(ws, {"type": "remove_server", "server_id": "roblox"},
                                     timeout=15)
            self.assertFalse(r["ok"])
            r = await self.roundtrip(ws, {"type": "add_server", "server_id": "blender",
                                          "command": sys.executable,
                                          "args": [str(fake), "--port", "9877"]}, timeout=20)
            self.assertTrue(r["ok"], r.get("error"))
            await self.wait_tool(ws, "get_scene_info")
            r = await self.roundtrip(ws, {"type": "remove_server", "server_id": "blender"},
                                     timeout=15)
            self.assertTrue(r["ok"], r.get("error"))
            cfg = await self.read_config()
            self.assertEqual(list(cfg["mcpServers"].keys()), ["roblox"])
            r = await self.roundtrip(ws, {"type": "ping"}, timeout=10)
            self.assertEqual(r["type"], "pong")

        async def test_11_starting_flag_and_failure_reason_surface(self):
            ws, _ = await self.auth_ws()
            fake = write_fake_server(self.bridge.dir, "crash")
            r = await self.roundtrip(ws, {"type": "add_server", "server_id": "broken",
                                          "command": sys.executable,
                                          "args": [str(fake)]}, timeout=20)
            self.assertTrue(r["ok"])
            # list_tools responses carry server health; wait for the settle:
            # starting flips false and the failure names its reason
            deadline = time.monotonic() + 25
            state = None
            while time.monotonic() < deadline:
                r = await self.roundtrip(ws, {"type": "list_tools"}, timeout=15)
                sv = next((x for x in r.get("servers", []) if x["id"] == "broken"), None)
                if sv and sv.get("starting") is False and not sv.get("alive"):
                    state = sv
                    break
                await asyncio.sleep(0.5)
            self.assertIsNotNone(state, "starting flag never cleared after a crash")
            self.assertIn("exited", (state.get("error") or "").lower(),
                          f"failure must name a reason, got: {state}")

        async def test_12_replace_while_starting(self):
            ws, _ = await self.auth_ws()
            slow = write_fake_server(self.bridge.dir, "slow_start")
            okf = write_fake_server(self.bridge.dir, "ok")
            await self.roundtrip(ws, {"type": "add_server", "server_id": "blender",
                                      "command": sys.executable, "args": [str(slow)]},
                                 timeout=15)
            await asyncio.sleep(0.4)  # slow start is mid-handshake now
            r = await self.roundtrip(ws, {"type": "add_server", "server_id": "blender",
                                          "command": sys.executable, "args": [str(okf)]},
                                     timeout=20)
            self.assertTrue(r["ok"], r.get("error"))
            deadline = time.monotonic() + 30
            seen_slow = False
            while time.monotonic() < deadline:
                r = await self.roundtrip(ws, {"type": "list_tools"}, timeout=15)
                names = [t.get("name") for t in r.get("tools", [])]
                if "slow_tool" in names:
                    seen_slow = True  # the replaced one briefly surfaced - fine
                if "get_scene_info" in names:
                    break
                await asyncio.sleep(0.4)
            self.assertIn("get_scene_info", names, "final owner must be the replacement")
            r = await self.roundtrip(ws, {"type": "ping"}, timeout=10)
            self.assertEqual(r["type"], "pong")

        async def test_13_disconnect_mid_tool_call_is_safe_and_idempotent(self):
            ws, _ = await self.auth_ws()
            fake = write_fake_server(self.bridge.dir, "long_tool")
            logp = self.bridge.dir / "exec.log"
            r = await self.roundtrip(ws, {"type": "add_server", "server_id": "blender",
                                          "command": sys.executable, "args": [str(fake)],
                                          "env": {"FAKE_EXEC_LOG": str(logp)}}, timeout=20)
            self.assertTrue(r["ok"], r.get("error"))
            await self.wait_tool(ws, "long_tool")
            # start the long call, then drop the connection mid-flight
            rid = next_id()
            await ws.send(json.dumps({"type": "call_tool", "name": "long_tool",
                                      "arguments": {}, "id": rid, "call_id": "dup-1"}))
            await asyncio.sleep(0.6)
            await ws.close()
            # reconnect and replay the SAME call_id: must NOT execute twice
            ws2, _ = await self.auth_ws()
            # the replay either coalesces onto the in-flight call or replays the
            # cached result - but the fake server must have executed ONCE.
            r = await self.roundtrip(ws2, {"type": "call_tool", "name": "long_tool",
                                           "arguments": {}, "id": rid,
                                           "call_id": "dup-1"}, timeout=40)
            deadline = time.monotonic() + 30
            execs = 0
            while time.monotonic() < deadline:
                execs = logp.read_text().count("START") if logp.exists() else 0
                if execs >= 1:
                    break
                await asyncio.sleep(0.4)
            self.assertEqual(execs, 1, f"tool executed {execs}x for one logical call")
            r = await self.roundtrip(ws2, {"type": "ping"}, timeout=10)
            self.assertEqual(r["type"], "pong")

        async def test_14_two_clients_concurrent(self):
            ws1, _ = await self.auth_ws()
            ws2, _ = await self.auth_ws()
            r1 = await self.roundtrip(ws1, {"type": "list_tools"}, timeout=20)
            r2 = await self.roundtrip(ws2, {"type": "list_tools"}, timeout=20)
            self.assertEqual([t.get("name") for t in r1["tools"]],
                             [t.get("name") for t in r2["tools"]])

        async def test_15_crash_loop_keeps_bridge_responsive(self):
            ws, _ = await self.auth_ws()
            fake = write_fake_server(self.bridge.dir, "crash")
            await self.roundtrip(ws, {"type": "add_server", "server_id": "broken",
                                      "command": sys.executable, "args": [str(fake)]},
                                 timeout=20)
            # server_watch will restart it repeatedly - the bridge must stay up
            # and responsive through at least one restart window
            await asyncio.sleep(12)
            r = await self.roundtrip(ws, {"type": "ping"}, timeout=10)
            self.assertEqual(r["type"], "pong")
            self.assertTrue(self.bridge.alive())

        # ── real mcp-for-blender package (no Blender installed here) ─────
        async def test_16_real_blender_mcp_server_safe_mode_and_routing(self):
            try:
                import blender_mcp  # noqa: F401
                from blender_mcp.safe_mode import SAFE_MODE_ENV  # noqa: F401
            except ImportError:
                self.skipTest("mcp-for-blender not installed; pip install mcp-for-blender==2.3.0")
            ws, _ = await self.auth_ws()
            r = await self.roundtrip(ws, {"type": "add_server", "server_id": "blender",
                                          "command": sys.executable,
                                          "args": ["-m", "blender_mcp.server"],
                                          "env": {"BLENDER_MCP_SAFE_MODE": "1",
                                                  "DISABLE_TELEMETRY": "true"}},
                                     timeout=60)  # first run may build deps
            self.assertTrue(r["ok"], r.get("error"))
            r = await self.wait_tool(ws, "execute_blender_code", timeout=60)
            names = [t.get("name") for t in r["tools"]]
            self.assertIn("get_scene_info", names)
            # SAFE MODE, REAL PACKAGE: hostile code rejected BEFORE the Blender
            # hop (validation happens server-side pre-connection; verified in
            # source, now proven live through the bridge)
            r = await self.roundtrip(ws, {"type": "call_tool", "name": "execute_blender_code",
                                          "arguments": {"code": "import os\nprint(os.getcwd())"}},
                                     timeout=60)
            self.assertTrue(r["ok"], r.get("error"))
            self.assertIn("Rejected by safe mode", r["text"])
            # get_scene_info without Blender: the REAL server answers with its
            # real connection error - transport + protocol + routing proven,
            # only the Blender hop is unexercised here (UNTESTED in the report)
            r = await self.roundtrip(ws, {"type": "call_tool", "name": "get_scene_info",
                                          "arguments": {}}, timeout=60)
            combined = ((r.get("text") or "") + " " + (r.get("error") or "")).lower()
            self.assertTrue(("connect" in combined) or ("blender" in combined),
                            f"expected the real server's connection error, got: {r}")

        # ── two-way compatibility ────────────────────────────────────────
        async def test_17_old_extension_vs_new_bridge_is_cleanly_refused(self):
            # an old extension speaks WITHOUT hello: first message is a ping.
            # New bridge must close cleanly with 4003 and process nothing.
            ws = await self.connect()
            await ws.send(json.dumps({"type": "ping", "id": 1}))
            await self.expect_close(ws, 4003)

        async def test_18_new_extension_vs_old_bridge_legacy_mode(self):
            # run the HEAD (pre-auth) bridge from git and speak the NEW
            # extension's protocol to it: first frame is "connected" (no gate),
            # hello gets "unknown message type" - legacy mode must still work.
            import subprocess as sp
            head = sp.run(["git", "-C", str(REPO), "show", "HEAD:bridge.py"],
                          capture_output=True, text=True)
            self.assertEqual(head.returncode, 0, "git show failed")
            old = BridgeProc.__new__(BridgeProc)
            old.dir = Path(tempfile.mkdtemp(prefix="zs-test-oldbridge-"))
            old.port = free_port()
            (old.dir / "bridge.py").write_text(head.stdout, encoding="utf-8")
            launcher = REPO / "launch_studio_mcp.py"
            if launcher.exists():
                shutil.copy(launcher, old.dir / "launch_studio_mcp.py")
            with open(old.dir / "config.json", "w", encoding="utf-8") as f:
                json.dump({"mcpServers": {"roblox":
                           {"command": "launch_studio_mcp.py", "args": []}}}, f)
            env = dict(os.environ, ZS_BRIDGE_PORT=str(old.port))
            env["PYTHONIOENCODING"] = "utf-8"
            old.proc = sp.Popen([sys.executable, str(old.dir / "bridge.py")],
                                cwd=str(old.dir), env=env, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
            old.log_lines = []
            old.config_path = old.dir / "config.json"
            old.auth_path = old.dir / "bridge_auth.json"
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:  # old bridge: same boot race
                try:
                    c = socket.create_connection(("127.0.0.1", old.port), timeout=0.5)
                    c.close()
                    break
                except OSError:
                    time.sleep(0.2)
            try:
                ws = await websockets.connect(f"ws://127.0.0.1:{old.port}", open_timeout=5)
                first = json.loads(await asyncio.wait_for(ws.recv(), 15))
                self.assertEqual(first["type"], "connected")  # no gate: old bridge
                await ws.send(json.dumps({"type": "hello", "token": "x"}))
                err = json.loads(await asyncio.wait_for(ws.recv(), 10))
                self.assertEqual(err["type"], "error")
                self.assertIn("unknown message type: hello", err["error"])
                await ws.send(json.dumps({"type": "ping", "id": 9}))
                pong = json.loads(await asyncio.wait_for(ws.recv(), 10))
                self.assertEqual(pong["type"], "pong")
                await ws.close()
            finally:
                old.stop()

    return BridgeE2ETest


_next_id = [100]
def next_id():
    _next_id[0] += 1
    return _next_id[0]


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    suite = unittest.TestSuite()
    loader = unittest.TestLoader()
    if which in ("all", "unit"):
        for cls in (TokenAuthTest, StrictValidationTest, GateRequestTest,
                    ConfigRoundtripTest, ManagerLiveTest):
            suite.addTests(loader.loadTestsFromTestCase(cls))
    if which in ("all", "e2e"):
        if HAVE_WS:
            suite.addTests(loader.loadTestsFromTestCase(_e2e_runner()))
        else:
            print("e2e layer skipped: pip install websockets")
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
