# MCP Creds

One local place for every API key, token and parameter your AI desktop apps need.

You use Claude Desktop, Cursor, VS Code, Windsurf... and every new MCP server wants its own token pasted into its own config. MCP Creds ends that: save each key once in a clean little app, connect MCP Creds to your AI apps once, and the AI fetches what it needs from here.

- **Local only.** No cloud, no account, no login, no telemetry. One JSON file on your machine.
- **Zero dependencies.** A single Python file (3.8+) plus one HTML page. Download and run.
- **Works with any MCP client.** Two tools: `list_credentials` (names only) and `get_credential`.

## Quick start

```bash
git clone https://github.com/<you>/mcp-creds && cd mcp-creds
python3 mcpcreds.py ui
```

Your browser opens the app. Add a key (tip: paste `NAME=value` into the name box and it splits itself), copy it, edit it, export everything as JSON.

## Connect your AI apps

Run `python3 mcpcreds.py config` (or open "Connect your AI apps" in the UI) and paste the result into your app's MCP config:

```json
{
  "mcpServers": {
    "mcp-creds": {
      "command": "python3",
      "args": ["/full/path/to/mcpcreds.py", "mcp"]
    }
  }
}
```

| App | Config file |
|---|---|
| Claude Desktop (macOS) | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Claude Desktop (Windows) | `%APPDATA%\Claude\claude_desktop_config.json` |
| Claude Code | `claude mcp add mcp-creds -- python3 /full/path/to/mcpcreds.py mcp` |
| Cursor | `~/.cursor/mcp.json` |

Then just ask: *"Use my GitHub token from MCP Creds to list my open PRs."* The AI calls `list_credentials`, sees `GITHUB_TOKEN`, and calls `get_credential`.

## CLI

```bash
python3 mcpcreds.py set GITHUB_TOKEN ghp_xxx --note "GitHub MCP"
python3 mcpcreds.py get GITHUB_TOKEN      # prints the value, great for $(...)
python3 mcpcreds.py list
python3 mcpcreds.py rm GITHUB_TOKEN
python3 mcpcreds.py export > backup.json
```

Use it inside other MCP configs or scripts: `export GITHUB_TOKEN=$(python3 mcpcreds.py get GITHUB_TOKEN)`.

## Where your data lives

`~/.mcpcreds/store.json` (override with `MCPCREDS_HOME`). The folder is `0700` and the file `0600`, so only your user can read it. Writes are atomic.

**Be honest with yourself about the threat model:** values are stored as plain JSON, protected by file permissions, the same as `~/.aws/credentials` or a `.env` file. Anything running as your user can read them, and any AI app you connect can read them through the MCP tools. Only connect apps you trust.

The web UI listens on `127.0.0.1` only, rejects requests whose `Host` is not localhost (blocks DNS rebinding), and requires a random per-session token on every API call (blocks other websites from reading your keys).

## Tests

```bash
python3 -m unittest -v
```

## License

MIT
