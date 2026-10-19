import io
import json
import subprocess
from pathlib import Path
import os
import stat
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
import urllib.error

import mcpcreds as mc


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["MCPCREDS_HOME"] = self.tmp.name

    def tearDown(self):
        os.environ.pop("MCPCREDS_HOME", None)
        self.tmp.cleanup()


class StoreTests(Base):
    def test_set_get_delete(self):
        mc.set_item("GITHUB_TOKEN", "ghp_abc", "gh mcp")
        self.assertEqual(mc.load()["GITHUB_TOKEN"]["value"], "ghp_abc")
        self.assertEqual(mc.load()["GITHUB_TOKEN"]["note"], "gh mcp")
        self.assertTrue(mc.delete_item("GITHUB_TOKEN"))
        self.assertFalse(mc.delete_item("GITHUB_TOKEN"))
        self.assertEqual(mc.load(), {})

    def test_overwrite_keeps_created(self):
        a = mc.set_item("K", "1")
        b = mc.set_item("K", "2")
        self.assertEqual(a["created"], b["created"])
        self.assertEqual(mc.load()["K"]["value"], "2")

    def test_bad_names_and_empty_value(self):
        for bad in ["", "has space", "a/b", "x" * 129]:
            with self.assertRaises(ValueError):
                mc.set_item(bad, "v")
        with self.assertRaises(ValueError):
            mc.set_item("OK", "")
        with self.assertRaises(ValueError):
            mc.set_item("OK", 123)

    @unittest.skipIf(sys.platform == "win32", "POSIX permissions only")
    def test_file_permissions(self):
        mc.set_item("K", "v")
        mode = stat.S_IMODE(os.stat(mc.store_path()).st_mode)
        self.assertEqual(mode, 0o600)


class McpTests(Base):
    def rpc(self, method, params=None, mid=1):
        return mc.handle_rpc({"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}})

    def test_initialize_and_list(self):
        r = self.rpc("initialize", {"protocolVersion": "2025-06-18"})
        self.assertEqual(r["result"]["serverInfo"]["name"], "mcp-creds")
        names = [t["name"] for t in self.rpc("tools/list")["result"]["tools"]]
        self.assertEqual(names, ["list_credentials", "get_credential"])

    def test_notification_no_reply(self):
        self.assertIsNone(mc.handle_rpc({"jsonrpc": "2.0", "method": "notifications/initialized"}))

    def test_tools(self):
        mc.set_item("OPENAI_KEY", "sk-1", "openai")
        listed = self.rpc("tools/call", {"name": "list_credentials"})["result"]
        self.assertIn("OPENAI_KEY: openai", listed["content"][0]["text"])
        mc.set_item("AWS_CREDENTIALS", "AWS_ACCESS_KEY_ID=A\nAWS_SECRET_ACCESS_KEY=B")
        self.assertIn("AWS_CREDENTIALS (2 parts)", self.rpc("tools/call", {"name": "list_credentials"})["result"]["content"][0]["text"])
        self.assertNotIn("sk-1", listed["content"][0]["text"])
        got = self.rpc("tools/call", {"name": "get_credential", "arguments": {"name": "openai_key"}})["result"]
        self.assertEqual(got["content"][0]["text"], "sk-1")
        miss = self.rpc("tools/call", {"name": "get_credential", "arguments": {"name": "NOPE"}})["result"]
        self.assertTrue(miss["isError"])
        self.assertIn("OPENAI_KEY", miss["content"][0]["text"])

    def test_unknown(self):
        self.assertEqual(self.rpc("nope")["error"]["code"], -32601)
        self.assertEqual(self.rpc("tools/call", {"name": "x"})["error"]["code"], -32602)

    def test_bad_input_never_crashes(self):
        lines = "\n".join(["[1,2]", json.dumps({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                            "params": {"name": "get_credential", "arguments": {"name": 5}}}),
                            json.dumps({"jsonrpc": "2.0", "id": 8, "method": "tools/call",
                            "params": {"name": "get_credential", "arguments": "oops"}}),
                            json.dumps({"jsonrpc": "2.0", "id": 9, "method": "ping"})]) + "\n"
        out = io.StringIO()
        mc.serve_mcp(io.StringIO(lines), out)
        resps = [json.loads(l) for l in out.getvalue().splitlines()]
        self.assertEqual(resps[0]["error"]["code"], -32600)
        self.assertTrue(resps[1]["result"]["isError"])
        self.assertTrue(resps[2]["result"]["isError"])
        self.assertEqual(resps[3], {"jsonrpc": "2.0", "id": 9, "result": {}})

    def test_stdio_loop(self):
        lines = "\n".join([
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            "not json",
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
        ]) + "\n"
        out = io.StringIO()
        mc.serve_mcp(io.StringIO(lines), out)
        resps = [json.loads(l) for l in out.getvalue().splitlines()]
        self.assertEqual([r.get("id") for r in resps], [1, None, 2])
        self.assertEqual(resps[1]["error"]["code"], -32700)


class HttpTests(Base):
    def setUp(self):
        super().setUp()
        self.srv, self.token = mc.make_server(0, token="t0k")
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        super().tearDown()

    def req(self, method, path, body=None, token="t0k", host=None):
        r = urllib.request.Request(self.base + path, method=method,
                                   data=json.dumps(body).encode() if body is not None else None)
        r.add_header("Content-Type", "application/json")
        if token:
            r.add_header("X-Token", token)
        if host:
            r.add_header("Host", host)
        try:
            with urllib.request.urlopen(r) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            with e:
                return e.code, e.read()

    def test_anti_framing_headers(self):
        with urllib.request.urlopen(self.base + "/") as r:
            self.assertEqual(r.headers["X-Frame-Options"], "DENY")

    def test_concurrent_writes_not_lost(self):
        def w(i):
            mc.set_item(f"K{i}", str(i))
        ts = [threading.Thread(target=w, args=(i,)) for i in range(25)]
        [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(len(mc.load()), 25)

    def test_page_served_with_token(self):
        code, body = self.req("GET", "/", token=None)
        self.assertEqual(code, 200)
        self.assertIn(b'const T = "t0k"', body)
        self.assertNotIn(b"__STORE__", body)

    def test_auth_required(self):
        self.assertEqual(self.req("GET", "/api/items", token=None)[0], 401)
        self.assertEqual(self.req("GET", "/api/items", token="wrong")[0], 401)

    def test_dns_rebinding_blocked(self):
        self.assertEqual(self.req("GET", "/", host="evil.example")[0], 403)
        self.assertEqual(self.req("GET", "/api/items", host="evil.example")[0], 403)

    def test_bulk_import(self):
        code, body = self.req("POST", "/api/import", {"items": [
            {"name": "A", "value": "1", "note": "x"}, {"name": "B", "value": "2"}]})
        self.assertEqual((code, json.loads(body)["count"]), (200, 2))
        self.assertEqual(sorted(mc.load()), ["A", "B"])
        code, _ = self.req("POST", "/api/import", {"items": [{"name": "C", "value": "3"}, {"name": "bad name", "value": "4"}]})
        self.assertEqual(code, 400)
        self.assertNotIn("C", mc.load())

    def test_page_placeholders_filled(self):
        _, body = self.req("GET", "/", token=None)
        for ph in (b"__STORE__", b"__CONFIG__", b"__VERSION__", b"__TOKEN__"):
            self.assertNotIn(ph, body)
        self.assertIn(b'"mcp-creds"', body)

    def test_crud(self):
        self.assertEqual(self.req("POST", "/api/items", {"name": "A.B-c_1", "value": "v", "note": "n"})[0], 200)
        code, body = self.req("GET", "/api/items")
        self.assertEqual(json.loads(body)["items"]["A.B-c_1"]["value"], "v")
        self.assertEqual(self.req("POST", "/api/items", {"name": "bad name", "value": "v"})[0], 400)
        self.assertEqual(self.req("DELETE", "/api/items/A.B-c_1")[0], 200)
        self.assertEqual(self.req("DELETE", "/api/items/A.B-c_1")[0], 404)


class ConnectTests(Base):
    def setUp(self):
        super().setUp()
        self.apps = tempfile.TemporaryDirectory()
        os.environ["MCPCREDS_APPS_HOME"] = self.apps.name
        self.cat = {a["id"]: a for a in mc.apps_catalog()}

    def tearDown(self):
        os.environ.pop("MCPCREDS_APPS_HOME", None)
        self.apps.cleanup()
        super().tearDown()

    def write(self, app_id, data):
        p = self.cat[app_id]["path"]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data))
        return p

    def status(self, app_id):
        return next(a for a in mc.apps_overview()["apps"] if a["id"] == app_id)

    def test_detects_only_installed(self):
        self.cat["cursor"]["marker"].mkdir(parents=True)
        st = {a["id"]: a["installed"] for a in mc.apps_overview()["apps"]}
        self.assertTrue(st["cursor"])
        self.assertFalse(st["claude"])

    def test_connect_adds_entry_keeps_others_and_backs_up(self):
        p = self.write("claude", {"mcpServers": {"fs": {"command": "npx", "args": ["fs"]}}, "other": 1})
        mc.connect_app("claude")
        data = json.loads(p.read_text())
        self.assertEqual(data["other"], 1)
        self.assertIn("fs", data["mcpServers"])
        self.assertEqual(data["mcpServers"]["mcp-creds"]["args"][-1], "mcp")
        self.assertTrue(self.status("claude")["connected"])
        self.assertFalse(Path(str(p) + ".mcpcreds-backup").exists())  # never next to the config
        orig = mc.backups_dir() / "claude-original.json"
        self.assertEqual(json.loads(orig.read_text())["other"], 1)
        self.assertEqual(stat.S_IMODE(os.stat(orig).st_mode), 0o600) if os.name != "nt" else None
        self.assertTrue(mc.undo_app("claude"))
        self.assertNotIn("mcp-creds", json.loads(p.read_text())["mcpServers"])

    def test_creates_config_when_app_installed_but_no_file(self):
        self.cat["cursor"]["marker"].mkdir(parents=True)
        mc.connect_app("cursor")
        self.assertIn("mcp-creds", json.loads(self.cat["cursor"]["path"].read_text())["mcpServers"])
        self.assertTrue(mc.undo_app("cursor"))
        self.assertEqual(json.loads(self.cat["cursor"]["path"].read_text())["mcpServers"], {})

    def test_vscode_format(self):
        self.write("vscode", {"servers": {}})
        mc.connect_app("vscode")
        e = json.loads(self.cat["vscode"]["path"].read_text())["servers"]["mcp-creds"]
        self.assertEqual(e["type"], "stdio")

    def test_secure_moves_tokens_and_wraps(self):
        p = self.write("claude", {"mcpServers": {
            "github": {"command": "npx", "args": ["-y", "gh-mcp"],
                       "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_live123", "LOG_LEVEL": "debug"}},
            "brave": {"command": "npx", "env": {"BRAVE_API_KEY": "<your-key>"}},
            "remote": {"url": "https://x.example/mcp"}}})
        st = self.status("claude")
        self.assertEqual([x["env"] for x in st["secrets"]], ["GITHUB_PERSONAL_ACCESS_TOKEN"])
        r = mc.connect_app("claude", secure=True)
        self.assertEqual(r["moved"], 1)
        gh = json.loads(p.read_text())["mcpServers"]["github"]
        self.assertEqual(gh["env"], {"LOG_LEVEL": "debug"})
        self.assertEqual(gh["command"], mc.cmd_prefix()[0])
        self.assertEqual(gh["args"][-5:], ["GITHUB_PERSONAL_ACCESS_TOKEN", "--", "npx", "-y", "gh-mcp"])
        self.assertEqual(mc.load()["GITHUB_PERSONAL_ACCESS_TOKEN"]["value"], "ghp_live123")
        self.assertEqual(self.status("claude")["secrets"], [])
        self.assertEqual(mc.connect_app("claude", secure=True)["moved"], 0)  # idempotent

    def test_secure_name_conflict_keeps_both(self):
        mc.set_item("API_KEY", "existing")
        p = self.write("cursor", {"mcpServers": {"my srv": {"command": "uvx", "env": {"API_KEY": "different"}}}})
        mc.connect_app("cursor", secure=True)
        self.assertEqual(mc.load()["API_KEY"]["value"], "existing")
        self.assertEqual(mc.load()["API_KEY__MY_SRV"]["value"], "different")
        self.assertIn("API_KEY=API_KEY__MY_SRV", json.loads(p.read_text())["mcpServers"]["my srv"]["args"])

    def test_three_way_name_collision_loses_nothing(self):
        mc.set_item("GITHUB_TOKEN", "A")
        self.write("claude", {"mcpServers": {"github": {"command": "npx", "env": {"GITHUB_TOKEN": "B"}}}})
        self.write("cursor", {"mcpServers": {"github": {"command": "npx", "env": {"GITHUB_TOKEN": "C"}}}})
        mc.connect_app("claude", secure=True)
        mc.connect_app("cursor", secure=True)
        self.assertEqual(sorted(v["value"] for v in mc.load().values()), ["A", "B", "C"])
        for app_id, want in (("claude", "B"), ("cursor", "C")):
            args = json.loads(self.cat[app_id]["path"].read_text())["mcpServers"]["github"]["args"]
            key = args[args.index("-e") + 1].partition("=")[2] or "GITHUB_TOKEN"
            self.assertEqual(mc.load()[key]["value"], want)

    def test_original_backup_never_overwritten(self):
        self.write("claude", {"mcpServers": {"gh": {"command": "npx", "env": {"GH_TOKEN": "ghp_orig"}}}})
        mc.connect_app("claude", secure=True)
        mc.connect_app("claude")
        mc.connect_app("claude")
        orig = json.loads((mc.backups_dir() / "claude-original.json").read_text())
        self.assertEqual(orig["mcpServers"]["gh"]["env"]["GH_TOKEN"], "ghp_orig")

    def test_disconnect_is_targeted_and_restores_tokens(self):
        p = self.write("claude", {"mcpServers": {"gh": {"command": "npx", "args": ["-y", "gh"], "cwd": "/w",
                                                        "env": {"GH_TOKEN": "ghp_x", "LOG": "1"}}}, "keep": True})
        mc.connect_app("claude", secure=True)
        data = json.loads(p.read_text())
        data["mcpServers"]["added_later"] = {"command": "uvx", "args": ["thing"]}
        data["later"] = 2
        p.write_text(json.dumps(data))
        self.assertTrue(mc.undo_app("claude"))
        after = json.loads(p.read_text())
        self.assertEqual(after["mcpServers"]["gh"], {"command": "npx", "args": ["-y", "gh"], "cwd": "/w",
                                                     "env": {"LOG": "1", "GH_TOKEN": "ghp_x"}})
        self.assertIn("added_later", after["mcpServers"])
        self.assertNotIn("mcp-creds", after["mcpServers"])
        self.assertEqual((after["keep"], after["later"]), (True, 2))
        self.assertFalse(mc.undo_app("claude"))

    def test_refuses_to_write_if_app_changed_file(self):
        p = self.write("claude", {"mcpServers": {}})
        data, fp = mc._read_cfg(p)
        time.sleep(0.01); p.write_text(json.dumps({"mcpServers": {"new": {"command": "x"}}, "pad": "changed"}))
        with self.assertRaises(mc.ConfigChanged):
            mc._write_cfg("claude", p, data, fp)
        self.assertIn("new", json.loads(p.read_text())["mcpServers"])

    def test_skips_variable_references(self):
        self.write("claude", {"mcpServers": {"a": {"command": "x", "env": {"API_KEY": "Bearer ${input:k}", "T_TOKEN": "{{t}}"}}}})
        self.assertEqual(self.status("claude")["secrets"], [])

    def test_odd_shapes_do_not_crash_or_wipe(self):
        p = self.write("claude", {"mcpServers": ["not", "a", "dict"]})
        self.assertTrue(self.status("claude")["error"])
        with self.assertRaises(ValueError):
            mc.connect_app("claude")
        self.assertEqual(json.loads(p.read_text())["mcpServers"], ["not", "a", "dict"])

    @unittest.skipIf(os.name == "nt", "symlinks need admin on Windows")
    def test_symlinked_config_stays_a_symlink(self):
        real = Path(self.apps.name) / "dotfiles" / "claude.json"
        real.parent.mkdir(); real.write_text(json.dumps({"mcpServers": {}}))
        link = self.cat["claude"]["path"]; link.parent.mkdir(parents=True); link.symlink_to(real)
        mc.connect_app("claude")
        self.assertTrue(link.is_symlink())
        self.assertIn("mcp-creds", json.loads(real.read_text())["mcpServers"])

    def test_refuses_jsonc(self):
        p = self.cat["vscode"]["path"]; p.parent.mkdir(parents=True)
        p.write_text('{ // comment\n "servers": {} }')
        self.assertIn("comments", self.status("vscode")["error"])
        with self.assertRaises(ValueError):
            mc.connect_app("vscode")
        self.assertIn("// comment", p.read_text())

    def test_http_endpoints(self):
        self.cat["cursor"]["marker"].mkdir(parents=True)
        srv, _ = mc.make_server(0, token="t")
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        def call(path, body=None):
            r = urllib.request.Request(base + path, method="POST" if body is not None else "GET",
                                       data=json.dumps(body).encode() if body is not None else None,
                                       headers={"X-Token": "t", "Content-Type": "application/json"})
            with urllib.request.urlopen(r) as resp:
                return json.loads(resp.read())
        try:
            self.assertIn("apps", call("/api/apps"))
            res = call("/api/apps/connect", {"ids": ["cursor", "nope"]})
            self.assertEqual([x["ok"] for x in res["results"]], [True, False])
            self.assertTrue(call("/api/apps/undo", {"id": "cursor"})["ok"])
        finally:
            srv.shutdown(); srv.server_close()


class RunTests(Base):
    def run_cli(self, *args):
        script = str(Path(mc.__file__).resolve())
        return subprocess.run([sys.executable, script, "run", *args], capture_output=True, text=True,
                              env={**os.environ})

    def test_injects_mapped_key(self):
        mc.set_item("GITHUB_TOKEN", "ghp_secret")
        r = self.run_cli("-e", "GH_PAT=GITHUB_TOKEN", "--", sys.executable, "-c",
                         "import os,sys; sys.stdout.write(os.environ['GH_PAT'])")
        self.assertEqual((r.returncode, r.stdout), (0, "ghp_secret"))

    def test_stdio_passes_through(self):
        mc.set_item("K", "v")
        script = str(Path(mc.__file__).resolve())
        r = subprocess.run([sys.executable, script, "run", "-e", "K", "--", sys.executable, "-c",
                            "import sys,os; sys.stdout.write(sys.stdin.read().upper()+os.environ['K'])"],
                           input="hello", capture_output=True, text=True)
        self.assertEqual(r.stdout, "HELLOv")

    def test_multi_part_key_feeds_each_var(self):
        mc.set_item("AWS_CREDENTIALS", "[default]\naws_access_key_id = AKIAEXAMPLE\naws_secret_access_key = s3cr3t/abc\n")
        r = self.run_cli("-e", "AWS_ACCESS_KEY_ID=AWS_CREDENTIALS", "-e", "AWS_SECRET_ACCESS_KEY=AWS_CREDENTIALS", "--",
                         sys.executable, "-c", "import os,sys; sys.stdout.write(os.environ['AWS_ACCESS_KEY_ID']+'|'+os.environ['AWS_SECRET_ACCESS_KEY'])")
        self.assertEqual(r.stdout, "AKIAEXAMPLE|s3cr3t/abc")
        self.assertEqual(mc.value_part("single", "X"), "single")

    def test_missing_key_explains(self):
        r = self.run_cli("-e", "NOPE", "--", sys.executable, "-c", "pass")
        self.assertEqual(r.returncode, 1)
        self.assertIn("no saved key named NOPE", r.stderr)

    def test_exit_code_propagates(self):
        r = self.run_cli("--", sys.executable, "-c", "raise SystemExit(3)")
        self.assertEqual(r.returncode, 3)


class CliTests(Base):
    def test_cli(self):
        self.assertEqual(mc.main(["set", "K", "val", "--note", "hi"]), 0)
        self.assertEqual(mc.load()["K"]["note"], "hi")
        self.assertEqual(mc.main(["get", "MISSING"]), 1)
        self.assertEqual(mc.main(["rm", "K"]), 0)


if __name__ == "__main__":
    unittest.main()
