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
  python3 mcpcreds.py connect [APP...] [--secure]
                                      add MCP Creds to your AI apps (all detected if none given);
                                      --secure also moves tokens out of their configs
  python3 mcpcreds.py run -e VAR[=KEY] ... -- command args
                                      start another MCP server with saved keys as env vars
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
        except (FileExistsError, PermissionError):  # Windows says PermissionError while the lock is being deleted
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


def load_settings() -> dict:
    p = home() / "settings.json"
    try:
        data = json.loads(p.read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(changes: dict) -> dict:
    allowed = {"theme": ("light", "dark")}
    data = load_settings()
    for k, v in changes.items():
        if k not in allowed or v not in allowed[k]:
            raise ValueError(f"Unsupported setting {k}={v}")
        data[k] = v
    h = home()
    h.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=h, prefix=".settings-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, h / "settings.json")
    return data


def current_theme() -> str:
    return "dark" if load_settings().get("theme") == "dark" else "light"


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
        "description": (
            "Get the value of one saved credential by its exact name, for example GITHUB_TOKEN. "
            "Some credentials have several parts saved together as labelled lines (for example "
            "aws_access_key_id, aws_secret_access_key and aws_session_token in one AWS_CREDENTIALS "
            "entry). Read each line and use every part the task needs."
        ),
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
        def parts(v):
            n = len([x for x in v.get("value", "").splitlines() if x.strip() and not re.match(r"^\s*(\[.*\]|#.*)$", x)])
            return f" ({n} parts)" if n > 1 else ""
        lines = [f"- {k}{parts(v)}" + (f": {v.get('note')}" if v.get("note") else "") for k, v in sorted(items.items())]
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


# ---------------------------------------------------------------- run: hand keys to other MCP servers

ENV_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
EXTRA_BIN_DIRS = ["/opt/homebrew/bin", "/usr/local/bin", "~/.volta/bin", "~/.bun/bin", "~/.deno/bin",
                  "~/.local/bin", "~/.cargo/bin", "~/.npm-global/bin", "~/Library/pnpm"]


def augmented_path(path: str) -> str:
    """GUI apps launch MCP servers with a tiny PATH, so npx/uvx/node often vanish. Add the usual places."""
    parts = [x for x in (path or "").split(os.pathsep) if x]
    extra = [os.path.expanduser(d) for d in EXTRA_BIN_DIRS]
    nvm = Path.home() / ".nvm" / "versions" / "node"
    if nvm.is_dir():
        extra += [str(v / "bin") for v in sorted(nvm.iterdir(), reverse=True)]
    for d in extra:
        if d not in parts and os.path.isdir(d):
            parts.append(d)
    return os.pathsep.join(parts)


PART_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z0-9_.\-]+)\s*[=:]\s*(.*?)\s*$")


def value_part(value: str, var: str) -> str:
    """A multi-part credential (several NAME=value lines saved as one key) can feed one env var:
    pick the line whose name matches `var`, ignoring case. Single values pass through unchanged."""
    if "\n" not in value.strip():
        return value
    for line in value.splitlines():
        m = PART_RE.match(line)
        if m and m.group(1).lower() == var.lower():
            return m.group(2).strip("\"'")
    return value


def run_with_keys(argv: list) -> int:
    """mcpcreds run -e VAR[=KEY] ... [--all] -- command args...

    Starts `command` with the requested stored keys set as environment variables. MCP servers
    read their tokens from env at startup, so their config no longer has to contain any secret.
    """
    pairs, use_all, i = [], False, 0
    while i < len(argv):
        a = argv[i]
        if a == "--":
            i += 1
            break
        if a in ("-e", "--env") and i + 1 < len(argv):
            var, _, key = argv[i + 1].partition("=")
            pairs.append((var, key or var))
            i += 2
            continue
        if a == "--all":
            use_all, i = True, i + 1
            continue
        break
    cmd = argv[i:]
    if not cmd:
        print("usage: run -e VAR[=KEY] [-e ...] [--all] -- command [args...]", file=sys.stderr)
        return 2
    items = load()
    if use_all:
        pairs = [(k, k) for k in items if ENV_RE.match(k)] + pairs
    missing = sorted({key for _, key in pairs if key not in items})
    if missing:
        print(f"MCP Creds: no saved key named {', '.join(missing)}. Add it in the MCP Creds app, "
              "then restart this AI app.", file=sys.stderr)
        return 1
    env = os.environ.copy()
    if FROZEN:  # undo PyInstaller's library path so child processes (node, python) load their own libs
        for var in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"):
            orig = env.pop(var + "_ORIG", None)
            if orig is not None:
                env[var] = orig
            else:
                env.pop(var, None)
    for var, key in pairs:
        env[var] = value_part(items[key]["value"], var)
    env["PATH"] = augmented_path(env.get("PATH", ""))
    exe = shutil.which(cmd[0], path=env["PATH"]) or cmd[0]
    if os.name == "posix":
        try:
            os.execve(exe, cmd, env)  # replace this process: stdio flows straight through
        except OSError as e:
            print(f"MCP Creds: could not start {cmd[0]}: {e}", file=sys.stderr)
            return 127
    return _run_windows(exe, cmd, env)


def _cmd_quote(a: str) -> str:
    if a and not any(c in a for c in ' \t&|<>^%"(),;='):
        return a
    return '"' + a.replace('"', '""') + '"'


def _run_windows(exe: str, cmd: list, env: dict) -> int:
    """Windows has no exec: start the child, and tie its life to ours with a kill-on-close job
    so it never lingers after the AI app stops us. .cmd/.bat shims (npx.cmd) need cmd.exe quoting."""
    if exe.lower().endswith((".cmd", ".bat")):
        line = " ".join([_cmd_quote(exe)] + [_cmd_quote(a) for a in cmd[1:]])
        target = 'cmd.exe /d /s /c "' + line + '"'
    else:
        target = [exe] + cmd[1:]
    # A windowed (GUI) exe does not pass its stdio pipes on by default: hand them over explicitly,
    # and keep console children from opening a window of their own.
    std = []
    for fd in (0, 1, 2):
        try:
            os.fstat(fd)
            std.append(fd)
        except OSError:
            std.append(None)
    try:
        proc = subprocess.Popen(target, env=env, stdin=std[0], stdout=std[1], stderr=std[2],
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError as e:
        print(f"MCP Creds: could not start {cmd[0]}: {e}", file=sys.stderr)
        return 127
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        job = k32.CreateJobObjectW(None, None)

        class LIMITS(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class IO(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in ("r", "w", "o", "rb", "wb", "ob")]

        class EXT(ctypes.Structure):
            _fields_ = [("Basic", LIMITS), ("Io", IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        info = EXT()
        info.Basic.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
        k32.AssignProcessToJobObject(job, wintypes.HANDLE(int(proc._handle)))
    except Exception:
        pass  # best effort: the server still runs, it just may outlive us
    try:
        return proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        return 130


# ---------------------------------------------------------------- one-click connect for AI apps

SECRET_NAME = re.compile(r"KEY|TOKEN|SECRET|PASSW|(^|_)PAT($|_)|AUTH|CREDENTIAL|COOKIE|DSN|DATABASE_URL|CONN_?STR", re.I)
SECRET_PREFIX = re.compile(r"^(sk-|sk_|rk_|ghp_|gho_|ghu_|ghs_|github_pat_|glpat-|xox[bpae]-|ntn_|secret_|AKIA|ASIA|AIza|hf_|gsk_|"
                           r"pplx-|lin_api_|figd_|sbp_|ATATT|BSA|postgres(ql)?://|mysql://|mongodb(\+srv)?://|rediss?://)")
PLACEHOLDER_RE = re.compile(r"^(<.*>|\$\{.*\}|\{\{.*\}\}|your[_ -].*|xxx+|changeme|\.\.\.|)$", re.I)


def cmd_prefix() -> list:
    """How AI apps should launch this program."""
    if FROZEN:
        return [sys.executable]
    return [sys.executable or "python3", str(Path(__file__).resolve())]


def location_warning() -> str:
    if FROZEN and sys.platform == "darwin" and not sys.executable.startswith("/Applications/"):
        return "Move MCP Creds into your Applications folder first. AI apps remember where it lives, so moving it later breaks them."
    where = " ".join(cmd_prefix()).replace("\\", "/")
    if any(t in where for t in ("/.cache/", "/tmp/", "/Temp/", "/private/var/folders/", "/Downloads/", "/archive-v0/")):
        return "MCP Creds is running from a temporary or Downloads folder. Move it somewhere permanent before connecting, or your AI apps will lose it."
    return ""


def apps_catalog() -> list:
    base = Path(os.environ.get("MCPCREDS_APPS_HOME") or Path.home())
    if sys.platform == "darwin":
        sup = base / "Library" / "Application Support"
    elif os.name == "nt":
        sup = Path(os.environ["APPDATA"]) if os.environ.get("APPDATA") and not os.environ.get("MCPCREDS_APPS_HOME") \
            else base / "AppData" / "Roaming"
    else:
        sup = base / ".config"
    return [
        {"id": "claude", "label": "Claude Desktop", "path": sup / "Claude" / "claude_desktop_config.json",
         "marker": sup / "Claude", "key": "mcpServers"},
        {"id": "claude-code", "label": "Claude Code", "path": base / ".claude.json",
         "marker": base / ".claude", "key": "mcpServers"},
        {"id": "cursor", "label": "Cursor", "path": base / ".cursor" / "mcp.json",
         "marker": base / ".cursor", "key": "mcpServers"},
        {"id": "windsurf", "label": "Windsurf", "path": base / ".codeium" / "windsurf" / "mcp_config.json",
         "marker": base / ".codeium" / "windsurf", "key": "mcpServers"},
        {"id": "vscode", "label": "VS Code", "path": sup / "Code" / "User" / "mcp.json",
         "marker": sup / "Code" / "User", "key": "servers", "vscode": True},
    ]


def backups_dir() -> Path:
    return home() / "backups"


def _backup(app_id: str, p: Path) -> None:
    """Keep the untouched original forever plus the version before the latest write.
    They can hold tokens, so they live in ~/.mcpcreds (0700), never next to the config."""
    if not p.exists():
        return
    d = backups_dir()
    d.mkdir(parents=True, exist_ok=True)
    for path in (d, home()):
        try:
            os.chmod(path, 0o700)
        except OSError:
            pass
    targets = [d / f"{app_id}-previous.json"]
    if not (d / f"{app_id}-original.json").exists():
        targets.append(d / f"{app_id}-original.json")
    for t in targets:
        shutil.copyfile(p, t)
        os.chmod(t, 0o600)


def _read_cfg(p: Path):
    """Return (data, fingerprint). The fingerprint lets us refuse to write if the app changed the file meanwhile."""
    p = p.resolve() if p.is_symlink() else p
    if not p.exists():
        return {}, None
    st = os.stat(p)
    text = p.read_text("utf-8").strip()
    fp = (st.st_mtime_ns, st.st_size)
    if not text:
        return {}, fp
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        raise ValueError("This config has comments, so MCP Creds leaves it alone. Use the manual setup below.")
    if not isinstance(data, dict):
        raise ValueError("Unexpected config format, so MCP Creds leaves it alone.")
    return data, fp


class ConfigChanged(Exception):
    pass


def _write_cfg(app_id: str, p: Path, data: dict, fp) -> None:
    p = p.resolve() if p.is_symlink() else p  # keep dotfile symlinks intact
    p.parent.mkdir(parents=True, exist_ok=True)
    now = (os.stat(p).st_mtime_ns, os.stat(p).st_size) if p.exists() else None
    if now != fp:
        raise ConfigChanged()
    _backup(app_id, p)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix="." + p.name + ".", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    if p.exists():
        try:
            os.chmod(tmp, os.stat(p).st_mode & 0o777)
        except OSError:
            pass
    os.replace(tmp, p)


def _servers(data: dict, app: dict) -> dict:
    servers = data.get(app["key"])
    if servers is None:
        return {}
    if not isinstance(servers, dict):
        raise ValueError(f"'{app['key']}' in this config is not an object, so MCP Creds leaves it alone.")
    return servers


def _our_entry(app: dict) -> dict:
    pre = cmd_prefix()
    e = {"command": pre[0], "args": pre[1:] + ["mcp"]}
    return {"type": "stdio", **e} if app.get("vscode") else e


def _points_to_us(cfg: dict) -> bool:
    cmd, args = str(cfg.get("command") or ""), [str(a) for a in (cfg.get("args") or [])]
    return cmd == cmd_prefix()[0] or "MCP Creds" in cmd or any(a.endswith("mcpcreds.py") for a in args)


def _is_wrapped(cfg: dict) -> bool:
    args = cfg.get("args") or []
    return isinstance(cfg, dict) and "run" in args and "--" in args and _points_to_us(cfg)


def _inline_secrets(servers: dict) -> list:
    found = []
    for srv, cfg in (servers or {}).items():
        if srv == "mcp-creds" or not isinstance(cfg, dict) or not cfg.get("command") or _is_wrapped(cfg):
            continue
        env = cfg.get("env")
        if not isinstance(env, dict):
            continue
        for k, v in env.items():
            if not isinstance(v, str) or "${" in v or "{{" in v or PLACEHOLDER_RE.match(v.strip()) or not ENV_RE.match(k):
                continue
            if SECRET_NAME.search(k) or SECRET_PREFIX.match(v):
                found.append({"server": srv, "env": k, "value": v})
    return found


def app_status(app: dict) -> dict:
    p = app["path"]
    out = {"id": app["id"], "label": app["label"], "path": str(p),
           "installed": app["marker"].exists() or p.exists(), "connected": False, "stale": False,
           "secrets": [], "wrapped": 0, "error": "", "undo": False}
    if not out["installed"]:
        return out
    try:
        servers = _servers(_read_cfg(p)[0], app)
        mine = servers.get("mcp-creds")
        if isinstance(mine, dict):
            want = _our_entry(app)
            out["connected"] = mine.get("command") == want["command"] and mine.get("args") == want["args"]
            out["stale"] = not out["connected"]
        out["wrapped"] = sum(1 for c in servers.values() if isinstance(c, dict) and _is_wrapped(c))
        out["secrets"] = [{"server": x["server"], "env": x["env"]} for x in _inline_secrets(servers)]
        out["undo"] = bool(mine) or out["wrapped"] > 0
    except Exception as e:  # one odd config must never break the whole list
        out["error"] = str(e) or e.__class__.__name__
    return out


def apps_overview() -> dict:
    return {"apps": [app_status(a) for a in apps_catalog()], "warning": location_warning()}


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_").upper()[:40] or "SERVER"


def _free_name(items: dict, base: str, value: str, hints: list) -> str:
    """A store name for `value`: reuse one that already holds it, else find one that is free."""
    cands = [base] + [f"{base[:80]}__{h}" for h in hints]
    for c in cands:
        if c in items and items[c]["value"] == value:
            return c
    for c in cands:
        if c not in items:
            return c
    n = 2
    while f"{cands[-1][:120]}_{n}" in items and items[f"{cands[-1][:120]}_{n}"]["value"] != value:
        n += 1
    return f"{cands[-1][:120]}_{n}"


def _find_app(app_id: str) -> dict:
    app = next((a for a in apps_catalog() if a["id"] == app_id), None)
    if not app:
        raise ValueError(f"Unknown app {app_id}")
    return app


def _with_retry(fn):
    for attempt in range(3):
        try:
            return fn()
        except ConfigChanged:
            time.sleep(0.2)
    raise ValueError("The app kept changing its config file. Close it and try again.")


def connect_app(app_id: str, secure: bool = False) -> dict:
    """Add MCP Creds to one app. With secure=True also move inline tokens into the store and wrap
    each server with `run`, so its config holds no secrets."""
    app = _find_app(app_id)

    def attempt():
        data, fp = _read_cfg(app["path"])
        servers = _servers(data, app)
        servers["mcp-creds"] = _our_entry(app)
        moved = 0
        if secure:
            pre = cmd_prefix()
            found = _inline_secrets(servers)
            with locked():
                items = load()
                new_items = dict(items)
                for srv in sorted({f["server"] for f in found}):
                    cfg = servers[srv]
                    env = dict(cfg.get("env") or {})
                    flags = []
                    for f in (x for x in found if x["server"] == srv):
                        k, v = f["env"], f["value"]
                        key = _free_name(new_items, k, v, [_slug(srv), f"{_slug(app_id)}_{_slug(srv)}"])
                        if key not in new_items:
                            new_items[key] = {"value": v, "note": f"{app['label']}: {srv}",
                                              "created": _now(), "updated": _now()}
                        flags += ["-e", k if key == k else f"{k}={key}"]
                        env.pop(k, None)
                        moved += 1
                    new = {kk: vv for kk, vv in cfg.items() if kk not in ("command", "args", "env")}
                    new["command"] = pre[0]
                    new["args"] = pre[1:] + ["run"] + flags + ["--", cfg["command"]] + list(cfg.get("args") or [])
                    if env:
                        new["env"] = env
                    servers[srv] = new
                if new_items != items:
                    save(new_items)  # tokens are safe in the store before the config loses them
        data[app["key"]] = servers
        _write_cfg(app_id, app["path"], data, fp)
        return {"id": app_id, "ok": True, "moved": moved}

    return _with_retry(attempt)


def _unwrap(cfg: dict, items: dict) -> dict:
    args = [str(a) for a in cfg.get("args") or []]
    i = args.index("run")
    j = args.index("--", i)
    flags, orig = args[i + 1:j], args[j + 1:]
    env = dict(cfg.get("env") or {})
    k = 0
    while k < len(flags):
        if flags[k] in ("-e", "--env") and k + 1 < len(flags):
            var, _, key = flags[k + 1].partition("=")
            key = key or var
            if key in items:
                env[var] = items[key]["value"]
            k += 2
        else:
            k += 1
    new = {kk: vv for kk, vv in cfg.items() if kk not in ("command", "args", "env")}
    new["command"] = orig[0] if orig else cfg.get("command")
    if orig[1:]:
        new["args"] = orig[1:]
    if env:
        new["env"] = env
    return new


def undo_app(app_id: str) -> bool:
    """Disconnect: remove only what MCP Creds added. Wrapped servers get their original command
    back with their tokens restored from the store. Everything else in the file is untouched."""
    app = _find_app(app_id)
    if not app["path"].exists():
        return False

    def attempt():
        data, fp = _read_cfg(app["path"])
        servers = _servers(data, app)
        changed = servers.pop("mcp-creds", None) is not None
        items = load()
        for name, cfg in list(servers.items()):
            if isinstance(cfg, dict) and _is_wrapped(cfg):
                servers[name] = _unwrap(cfg, items)
                changed = True
        if not changed:
            return False
        data[app["key"]] = servers
        _write_cfg(app_id, app["path"], data, fp)
        return True

    return _with_retry(attempt)


# ---------------------------------------------------------------- local web UI

def mcp_config_snippet() -> dict:
    pre = cmd_prefix()
    return {"mcpServers": {"mcp-creds": {"command": pre[0], "args": pre[1:] + ["mcp"]}}}


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
                html = (page.replace("__THEME__", current_theme()).replace("__TOKEN__", token)
                        .replace("__STORE__", htmlmod.escape(str(store_path())))
                        .replace("__VERSION__", VERSION)
                        .replace("__CONFIG__", cfg))
                return self._send(200, html.encode(), "text/html; charset=utf-8")
            if self.path == "/api/apps":
                if self._authed():
                    self._send(200, apps_overview())
                return
            if self.path == "/api/items":
                if self._authed():
                    try:
                        self._send(200, {"items": load()})
                    except (OSError, ValueError) as e:
                        self._send(500, {"error": f"Could not read store: {e}"})
                return
            self._send(404, {"error": "not found"})

        def do_POST(self):
            if self.path not in ("/api/items", "/api/import", "/api/apps/connect", "/api/apps/undo", "/api/settings"):
                return self._send(404, {"error": "not found"})
            if not self._authed():
                return
            try:
                b = self._body()
                if self.path == "/api/settings":
                    if not isinstance(b, dict):
                        raise ValueError("settings must be an object")
                    return self._send(200, {"ok": True, "settings": save_settings(b)})
                if self.path == "/api/apps/connect":
                    results = []
                    for app_id in b.get("ids") or []:
                        try:
                            results.append(connect_app(str(app_id), bool(b.get("secure"))))
                        except (ValueError, OSError) as e:
                            results.append({"id": app_id, "ok": False, "error": str(e)})
                    return self._send(200, {"results": results, **apps_overview()})
                if self.path == "/api/apps/undo":
                    ok = undo_app(str(b.get("id", "")))
                    return self._send(200 if ok else 404, {"ok": ok, **apps_overview()})
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
                                           min_size=(420, 560),
                                           background_color="#07080c" if current_theme() == "dark" else "#f5f6fa")
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
        elif cmd == "run":
            return run_with_keys(argv)
        elif cmd == "connect":
            secure = "--secure" in argv
            ids = [a for a in argv if not a.startswith("--")] or [a["id"] for a in apps_overview()["apps"] if a["installed"]]
            for app_id in ids:
                try:
                    r = connect_app(app_id, secure)
                    print(f"Connected {app_id}" + (f", moved {r['moved']} keys" if r["moved"] else ""))
                except (ValueError, OSError) as e:
                    print(f"Skipped {app_id}: {e}", file=sys.stderr)
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
