import io
import json
import os
import stat
import tempfile
import threading
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
        self.assertNotIn("sk-1", listed["content"][0]["text"])
        got = self.rpc("tools/call", {"name": "get_credential", "arguments": {"name": "openai_key"}})["result"]
        self.assertEqual(got["content"][0]["text"], "sk-1")
        miss = self.rpc("tools/call", {"name": "get_credential", "arguments": {"name": "NOPE"}})["result"]
        self.assertTrue(miss["isError"])
        self.assertIn("OPENAI_KEY", miss["content"][0]["text"])

    def test_unknown(self):
        self.assertEqual(self.rpc("nope")["error"]["code"], -32601)
        self.assertEqual(self.rpc("tools/call", {"name": "x"})["error"]["code"], -32602)

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

    def test_crud(self):
        self.assertEqual(self.req("POST", "/api/items", {"name": "A.B-c_1", "value": "v", "note": "n"})[0], 200)
        code, body = self.req("GET", "/api/items")
        self.assertEqual(json.loads(body)["items"]["A.B-c_1"]["value"], "v")
        self.assertEqual(self.req("POST", "/api/items", {"name": "bad name", "value": "v"})[0], 400)
        self.assertEqual(self.req("DELETE", "/api/items/A.B-c_1")[0], 200)
        self.assertEqual(self.req("DELETE", "/api/items/A.B-c_1")[0], 404)


class CliTests(Base):
    def test_cli(self):
        self.assertEqual(mc.main(["set", "K", "val", "--note", "hi"]), 0)
        self.assertEqual(mc.load()["K"]["note"], "hi")
        self.assertEqual(mc.main(["get", "MISSING"]), 1)
        self.assertEqual(mc.main(["rm", "K"]), 0)


if __name__ == "__main__":
    unittest.main()
