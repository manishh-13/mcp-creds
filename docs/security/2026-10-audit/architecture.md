# MCP Creds: architecture summary (run-1, commit b2de4f7)

## Product, principals, protected resources
MCP Creds is a single-user, local-only API key store for AI desktop apps. One Python file (mcpcreds.py, stdlib only) plus one HTML page (ui.html). Protected resources: (1) secret values in ~/.mcpcreds/store.json (folder 0700, file 0600); (2) the integrity of the user's AI app config files that MCP Creds rewrites (Claude Desktop claude_desktop_config.json, Claude Code ~/.claude.json, Cursor ~/.cursor/mcp.json, Windsurf, VS Code user mcp.json) and backups in ~/.mcpcreds/backups; (3) what program a rewritten config launches. Principals: the local user (full authority by design); arbitrary websites open in the user's browser (lower trust, must not read or change keys or configs); other local OS users (lower trust); AI apps the user connected (intended to read keys through MCP); an LLM inside those apps (influenced by untrusted content, e.g. prompt injection); MCP servers started through `run` (receive only the keys named for them); repository contributors / GitHub (CI and release supply chain).

## Comparable baseline
Like ~/.aws/credentials or a .env file: plaintext protected by file permissions; any process running as the user can read it. This is documented and accepted. Same-user processes reading the store, and connected AI apps requesting keys, are by design (see README Security and SECURITY.md scope).

## Stack and deployment paths
Python 3.9+ stdlib. Modes: `ui` (ThreadingHTTPServer on 127.0.0.1:7878, falls back to a random port; optional pywebview native window with js_api copy/export_json), `mcp` (stdio JSON-RPC MCP server), `run` (exec child with injected env), `connect` (rewrite AI app configs), CLI set/get/list/rm/export/config. Packaged with PyInstaller (packaging/build.sh) into a macOS .app (ad-hoc signed), Windows windowed exe, Linux binary; CI in .github/workflows/ci.yml and release.yml (tag v* builds, smoke tests, publishes via softprops/action-gh-release). docs/ is a static GitHub Pages landing page.

## Entry surfaces and important paths
1. HTTP API (mcpcreds.py make_server Handler): GET / (HTML with per-session token injected), GET /api/items (all secrets), POST /api/items, POST /api/import, DELETE /api/items/<name>, GET /api/apps, POST /api/apps/connect, POST /api/apps/undo. Controls: Host header must be 127.0.0.1 or localhost (_host_ok); X-Token header compared with secrets.compare_digest (_authed); custom header forces CORS preflight which the server never answers; X-Frame-Options DENY and CSP frame-ancestors 'none'.
2. UI JavaScript (ui.html): renders names, notes, values, app status, imported .env/JSON/MCP-config content (parseBulk, openImport, render, renderApps, highlight). Uses text nodes via h(); innerHTML only for constant SVG and highlight() of the generated config snippet.
3. MCP stdio (serve_mcp, handle_rpc, call_tool): tools list_credentials (names, notes, part counts) and get_credential (returns value; case-insensitive fallback).
4. run (run_with_keys, value_part, augmented_path, _run_windows, _cmd_quote): parses -e VAR[=KEY], --all, `--`; sets env from store; augments PATH with common bin dirs (appended); POSIX os.execve; Windows Popen with explicit std handles, CREATE_NO_WINDOW, job object; .cmd/.bat via cmd.exe /d /s /c with custom quoting.
5. Config rewriting (apps_catalog, _read_cfg, _write_cfg, _inline_secrets, connect_app, undo_app, _unwrap, _free_name, _backup): reads JSON (refuses JSONC), mtime/size fingerprint check before os.replace, resolves symlinks, moves env secrets into store and wraps servers as `<us> run -e ... -- <orig cmd> <orig args>`, targeted disconnect, backups ~/.mcpcreds/backups/<app>-original.json (never overwritten) and -previous.json (0600).
6. Store and settings I/O (load, save, locked, set_item, import_items, delete_item): atomic temp+replace, cross-process O_EXCL lock file with stale-lock removal after 10s.
7. Supply chain: .github/workflows/*.yml, packaging/build.sh, packaging/make_icon.py, docs/index.html (fetches api.github.com for a star count).

## Trust boundaries and strongest controls
Web origin -> local API: Host check + unguessable token + no CORS. Other local users -> store/backups: 0700/0600 permissions. LLM/AI app -> store: by design full read through MCP (no per-key policy). Config file content -> process execution: wrapping preserves original command/args; run uses argv lists (no shell) on POSIX. Imported/pasted text -> DOM: text-node rendering.

## Starting paths
mcpcreds.py, ui.html, packaging/build.sh, .github/workflows/ci.yml, .github/workflows/release.yml, docs/index.html, test_mcpcreds.py (tests).

## Prior coverage
No prior runs. Two earlier informal code reviews fixed many issues before this commit; they are not coverage.

## Companion selection
WEB-PROTOCOL-AND-AUTH (local HTTP API, token/Host auth), CLIENT-SIDE (DOM rendering of untrusted pasted content, framing, cross-origin), AI-AND-LLM (MCP server exposing secrets to model-driven tools), DESKTOP-MOBILE-AND-LOCAL-IPC (local daemon on loopback, webview js_api, local files, process launch), DATA-ISOLATION-AND-LIFECYCLE (backups, restore, deletion), SUPPLY-CHAIN-AND-RELEASE (CI, release, build). Excluded: CLOUD-AND-DEPLOYMENT (no cloud), MEMORY-SAFETY-AND-BINARY (pure Python), PROTOCOLS-RPC-AND-MESSAGING (only JSON-RPC over stdio, covered under AI-AND-LLM MCP), RESOURCE-EXHAUSTION (single-user local tool; no shared resource).

## Execution policy for this run
macOS sandbox-exec profile denies outbound network (including DNS) except loopback and denies writes outside the agent scratch dir; CPU, file-size and process limits via ulimit; wall clock via perl alarm. macOS cannot enforce a memory limit (setrlimit RLIMIT_AS fails), so per the skill's universal execution safety, target code is NOT executed in this run: hunters and verifiers use source analysis only, and runtime-decisive facts become needs_validation.
