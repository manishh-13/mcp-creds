#!/usr/bin/env python3
"""MCP Creds: one local place for the API keys and parameters your AI apps need.

No cloud, no login, no dependencies. Everything stays in ~/.mcpcreds/store.json.

  python3 mcpcreds.py ui              open the app (native window if pywebview is installed,
                                      else your browser; --browser forces the browser)
  python3 mcpcreds.py mcp             run as an MCP server (stdio) for AI apps
  python3 mcpcreds.py set NAME VALUE [--note TEXT]
  python3 mcpcreds.py get NAME        print one value (handy in scripts)
  python3 mcpcreds.py list            list names
  python3 mcpcreds.py rm NAME
  python3 mcpcreds.py export          print everything as JSON
"""
import datetime
import html as htmlmod
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

VERSION = "0.1.0"
NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,128}$")
FROZEN = bool(getattr(sys, "frozen", False))
HERE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


# ---------------------------------------------------------------- store

def home() -> Path:
    return Path(os.environ.get("MCPCREDS_HOME") or (Path.home() / ".mcpcreds"))


def store_path() -> Path:
    return home() / "store.json"


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def load() -> dict:
    p = store_path()
    if not p.exists():
        return {}
    text = p.read_text("utf-8").strip()
    return json.loads(text).get("items", {}) if text else {}


def save(items: dict) -> None:
    h = home()
    h.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(h, 0o700)
    except OSError:
        pass
    fd, tmp = tempfile.mkstemp(dir=h, prefix=".store-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "items": items}, f, indent=2, sort_keys=True)
    os.chmod(tmp, 0o600)
    for attempt in range(20):
        try:
            os.replace(tmp, store_path())
            return
        except PermissionError:  # Windows: another process has the file open
            if attempt == 19:
                os.unlink(tmp)
                raise
            time.sleep(0.05)


@contextmanager
def locked():
    """Cross-process lock so the UI and MCP processes never lose each other's writes."""
    h = home()
    h.mkdir(parents=True, exist_ok=True)
    lock = h / ".lock"
    deadline = time.time() + 5
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > 10:  # stale lock from a crash
                    lock.unlink()
                    continue
            except OSError:
                pass
            if time.time() > deadline:
                raise OSError("store is busy, try again")
            time.sleep(0.02)
    try:
        yield
    finally:
        os.close(fd)
        try:
            lock.unlink()
        except OSError:
            pass


def check_name(name: str) -> str:
    name = (name or "").strip()
    if not NAME_RE.match(name):
        raise ValueError("Name must be 1 to 128 characters: letters, digits, _ . -")
    return name


def set_item(name: str, value: str, note: str = "") -> dict:
    name = check_name(name)
    if not isinstance(value, str) or value == "":
        raise ValueError("Value must be non-empty text")
    with locked():
        items = load()
        old = items.get(name, {})
        items[name] = {
            "value": value,
            "note": str(note or "").strip(),
            "created": old.get("created", _now()),
            "updated": _now(),
        }
        save(items)
    return items[name]


def import_items(new_items: list) -> int:
    """Validate everything first, then write once. Returns how many were saved."""
    clean = []
    if not isinstance(new_items, list):
        raise ValueError("items must be a list")
    for it in new_items:
        if not isinstance(it, dict):
            raise ValueError("each item must be an object")
        name = check_name(it.get("name", ""))
        value = it.get("value", "")
        if not isinstance(value, str) or value == "":
            raise ValueError(f"Value for {name} is empty")
        clean.append((name, value, str(it.get("note") or "").strip()))
    if not clean:
        return 0
    with locked():
        items = load()
        for name, value, note in clean:
            old = items.get(name, {})
            items[name] = {"value": value, "note": note or old.get("note", ""),
                           "created": old.get("created", _now()), "updated": _now()}
        save(items)
    return len(clean)


def delete_item(name: str) -> bool:
    with locked():
        items = load()
        if name not in items:
            return False
        del items[name]
        save(items)
    return True


# ---------------------------------------------------------------- MCP (stdio JSON-RPC)

TOOLS = [
    {
        "name": "list_credentials",
        "description": (
            "List the credentials the user has saved in MCP Creds (names and notes only, "
            "no values). Call this first whenever you need an API key, token, URL or other "
            "parameter for a service, then fetch the one you need with get_credential."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_credential",
        "description": "Get the value of one saved credential by its exact name, for example GITHUB_TOKEN.",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Exact credential name"}},
            "required": ["name"],
        },
    },
]


def _text(s: str, error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": s}], "isError": error}


def call_tool(name: str, args: dict) -> dict:
    items = load()
    if name == "list_credentials":
        if not items:
            return _text("No credentials saved yet. Ask the user to add them in the MCP Creds app.")
        lines = [f"- {k}" + (f": {v.get('note')}" if v.get("note") else "") for k, v in sorted(items.items())]
        return _text("Saved credentials:\n" + "\n".join(lines))
    if name == "get_credential":
        key = str((args if isinstance(args, dict) else {}).get("name", "") or "")
        if key in items:
            return _text(items[key]["value"])
        lower = {k.lower(): k for k in items}
        if key.lower() in lower:
            return _text(items[lower[key.lower()]]["value"])
        avail = ", ".join(sorted(items)) or "none"
        return _text(f"No credential named '{key}'. Available: {avail}", error=True)
    raise KeyError(name)


def handle_rpc(msg: dict):
    """Return a JSON-RPC response dict, or None for notifications."""
    if not isinstance(msg, dict):
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid request"}}
    mid = msg.get("id")
    method = msg.get("method", "")
    if mid is None:
        return None

    def ok(result):
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def err(code, message):
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}

    params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
    if method == "initialize":
        return ok({
            "protocolVersion": params.get("protocolVersion", "2025-06-18"),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "mcp-creds", "version": VERSION},
            "instructions": (
                "MCP Creds is the user's local store of API keys and parameters. When a task "
                "needs a token or key, call list_credentials, then get_credential. Never echo "
                "secret values back to the user unless they ask."
            ),
        })
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": TOOLS})
    if method == "tools/call":
        try:
            return ok(call_tool(params.get("name", ""), params.get("arguments") or {}))
        except KeyError:
            return err(-32602, f"Unknown tool: {params.get('name')}")
    return err(-32601, f"Method not found: {method}")


def serve_mcp(stdin=None, stdout=None) -> None:
    if stdin is None:
        stdin = sys.stdin
        if hasattr(stdin, "reconfigure"):
            stdin.reconfigure(encoding="utf-8", errors="replace")
    if stdout is None:
        stdout = sys.stdout
        if hasattr(stdout, "reconfigure"):
            stdout.reconfigure(encoding="utf-8")
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        else:
            try:
                resp = handle_rpc(msg)
            except Exception as e:  # never let one bad request kill the server
                mid = msg.get("id") if isinstance(msg, dict) else None
                resp = {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": f"Internal error: {e}"}}
        if resp is not None:
            stdout.write(json.dumps(resp) + "\n")
            stdout.flush()


# ---------------------------------------------------------------- local web UI

def mcp_config_snippet() -> dict:
    if FROZEN:
        entry = {"command": sys.executable, "args": ["mcp"]}
    else:
        entry = {"command": sys.executable or "python3", "args": [str(Path(__file__).resolve()), "mcp"]}
    return {"mcpServers": {"mcp-creds": entry}}


def make_server(port: int = 7878, token: str = None):
    token = token or secrets.token_urlsafe(24)
    page = (HERE / "ui.html").read_text("utf-8")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(data)

        def _host_ok(self):
            host = (self.headers.get("Host") or "").split(":")[0]
            return host in ("127.0.0.1", "localhost")

        def _authed(self):
            if not self._host_ok():
                self._send(403, {"error": "bad host"})
                return False
            if not secrets.compare_digest(self.headers.get("X-Token", ""), token):
                self._send(401, {"error": "bad token"})
                return False
            return True

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}") if n else {}

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                if not self._host_ok():
                    return self._send(403, {"error": "bad host"})
                cfg = json.dumps(mcp_config_snippet()).replace("</", "<\\/")
                html = (page.replace("__TOKEN__", token)
                        .replace("__STORE__", htmlmod.escape(str(store_path())))
                        .replace("__VERSION__", VERSION)
                        .replace("__CONFIG__", cfg))
                return self._send(200, html.encode(), "text/html; charset=utf-8")
            if self.path == "/api/items":
                if self._authed():
                    try:
                        self._send(200, {"items": load()})
                    except (OSError, ValueError) as e:
                        self._send(500, {"error": f"Could not read store: {e}"})
                return
            self._send(404, {"error": "not found"})

        def do_POST(self):
            if self.path not in ("/api/items", "/api/import"):
                return self._send(404, {"error": "not found"})
            if not self._authed():
                return
            try:
                b = self._body()
                if self.path == "/api/import":
                    n = import_items(b.get("items", []))
                    return self._send(200, {"ok": True, "count": n})
                item = set_item(b.get("name", ""), b.get("value", ""), b.get("note", ""))
                self._send(200, {"ok": True, "item": item})
            except (ValueError, AttributeError, json.JSONDecodeError) as e:
                self._send(400, {"error": str(e)})
            except OSError as e:
                self._send(500, {"error": str(e)})

        def do_DELETE(self):
            if not self.path.startswith("/api/items/"):
                return self._send(404, {"error": "not found"})
            if self._authed():
                name = unquote(self.path[len("/api/items/"):])
                try:
                    gone = delete_item(name)
                except OSError as e:
                    return self._send(500, {"error": str(e)})
                if gone:
                    self._send(200, {"ok": True})
                else:
                    self._send(404, {"error": "not found"})

    try:
        srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError:
        srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    return srv, token


def copy_to_clipboard(text: str) -> bool:
    """Native clipboard, used by the desktop window (webviews can block navigator.clipboard)."""
    for cmd in (["pbcopy"], ["clip"], ["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "-ib"]):
        if shutil.which(cmd[0]):
            win = cmd[0] == "clip"
            data = ("\ufeff" + text).encode("utf-16le") if win else text.encode("utf-8")
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if win else 0
            try:
                subprocess.run(cmd, input=data, check=True, creationflags=flags)
                return True
            except (OSError, subprocess.CalledProcessError):
                continue
    return False


class DesktopApi:
    """Functions the page can call inside the native window (window.pywebview.api.*)."""

    def __init__(self):
        self.window = None

    def copy(self, text):
        return copy_to_clipboard(text)

    def export_json(self, data):
        import webview
        dialog = getattr(getattr(webview, "FileDialog", None), "SAVE", None) or webview.SAVE_DIALOG
        path = self.window.create_file_dialog(dialog, save_filename="mcp-creds-export.json")
        if not path:
            return ""
        path = path[0] if isinstance(path, (list, tuple)) else path
        Path(path).write_text(data, encoding="utf-8")
        return str(path)


def run_ui(port: int = 7878, open_browser: bool = True, native: bool = True) -> None:
    srv, _ = make_server(port)
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    if native:
        try:
            import webview  # optional: pip install pywebview, bundled in the desktop app
        except ImportError:
            native = False
    if native:
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        api = DesktopApi()
        api.window = webview.create_window("MCP Creds", url, js_api=api, width=1120, height=820,
                                           min_size=(420, 560), background_color="#07080c")
        webview.start()
        srv.shutdown()
        return
    print(f"MCP Creds running at {url}  (Ctrl+C to stop)")
    print(f"Store: {store_path()}")
    if open_browser:
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


# ---------------------------------------------------------------- CLI

def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    argv = [a for a in argv if not a.startswith("-psn_")]  # macOS Finder launch arg
    cmd = argv.pop(0) if argv else "ui"
    try:
        if cmd == "ui":
            port = int(argv[argv.index("--port") + 1]) if "--port" in argv else 7878
            run_ui(port, open_browser="--no-open" not in argv,
                   native="--browser" not in argv and "--no-open" not in argv)
        elif cmd == "mcp":
            serve_mcp()
        elif cmd == "set":
            note = ""
            if "--note" in argv:
                i = argv.index("--note")
                note = argv[i + 1] if i + 1 < len(argv) else ""
                del argv[i:i + 2]
            if len(argv) < 2:
                print("usage: set NAME VALUE [--note TEXT]", file=sys.stderr)
                return 2
            set_item(argv[0], argv[1], note)
            print(f"Saved {argv[0]}")
        elif cmd == "get":
            items = load()
            if not argv or argv[0] not in items:
                print(f"Not found: {argv[0] if argv else ''}", file=sys.stderr)
                return 1
            print(items[argv[0]]["value"])
        elif cmd in ("list", "ls"):
            for k, v in sorted(load().items()):
                print(k + (f"  ({v['note']})" if v.get("note") else ""))
        elif cmd in ("rm", "delete"):
            if not argv or not delete_item(argv[0]):
                print("Not found", file=sys.stderr)
                return 1
            print(f"Deleted {argv[0]}")
        elif cmd == "export":
            print(json.dumps(load(), indent=2, sort_keys=True))
        elif cmd == "config":
            print(json.dumps(mcp_config_snippet(), indent=2))
        elif cmd in ("-v", "--version"):
            print(VERSION)
        else:
            print(__doc__)
            return 0 if cmd in ("-h", "--help", "help") else 2
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
