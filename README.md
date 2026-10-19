# MCP Creds

One local place for every API key, token and parameter your AI desktop apps need.

You use Claude Desktop, Cursor, VS Code, Windsurf... and every new MCP server wants its own token pasted into its own config. MCP Creds ends that: save each key once in a clean little app, connect MCP Creds to your AI apps once, and the AI fetches what it needs from here.

- **Local only.** No cloud, no account, no login, no telemetry. One JSON file on your machine.
- **Zero dependencies.** A single Python file (3.9+) plus one HTML page. Download and run.
- **Works with any MCP client.** Two tools: `list_credentials` (names only) and `get_credential`.

## Get it

**Download the app** (no Python needed): grab the file for your OS from the [Releases](../../releases) page.

| OS | File | First launch |
|---|---|---|
| macOS (Apple silicon) | `MCP-Creds-macOS.zip` | Unzip, drag to Applications. The app is not notarized yet, so right-click it and choose **Open** the first time. |
| Windows | `MCP-Creds-Windows.zip` | Unzip, run `MCP Creds.exe`. If SmartScreen warns, choose **More info, Run anyway**. |
| Linux | `MCP-Creds-Linux.tar.gz` | Extract, run `./MCP Creds`. Opens in your browser. |

**Or run from source** (any OS with Python 3.9+, zero dependencies):

```bash
git clone https://github.com/manishh-13/mcp-creds && cd mcp-creds
python3 mcpcreds.py ui
```

It opens in your browser. Install `pywebview` (`pip install pywebview`) and it opens in its own native window instead.

## What it does

- **Paste anything.** Copy a token, press Cmd/Ctrl+V anywhere on the page. It recognises 20+ services by the key's prefix (GitHub, OpenAI, Anthropic, Slack, Notion, AWS, Stripe, Hugging Face...) and names the key for you.
- **Bring your existing setup.** Paste or drop a `.env`, a JSON file, or your `claude_desktop_config.json`: it pulls every `env` value out of your `mcpServers` and lets you pick which ones to import. Placeholders like `<your-key>` are skipped.
- **Credentials with several parts** (an AWS access key, secret and session token): paste them all into one key, one per line. The card shows how many parts it holds, the AI is told to read every line, and `run -e AWS_SECRET_ACCESS_KEY=AWS_CREDENTIALS` hands each server just the part it needs. Or split them into separate keys with one click.
- **Copy, reveal, edit, delete,** search by name, note or service, export everything as JSON.
- **Keyboard first:** `N` new key, `Cmd/Ctrl+K` search, `Enter` save, `Esc` close.

## Connect your AI apps

Click **Connect AI**. MCP Creds finds the AI apps on your computer (Claude Desktop, Claude Code, Cursor, Windsurf, VS Code) and connects them with one click. It saves a backup of each config first, and every app has an **Undo**.

It works with any model: Claude, GPT, Gemini, a local model. All it needs is an app that supports MCP.

You never have to name a key. When a task needs one, the AI checks MCP Creds on its own through two tools: `list_credentials` (names only) and `get_credential`.

### Stop pasting tokens into other MCP servers

Most MCP servers (GitHub, Slack, Notion, Brave Search...) read their token from an environment variable when they start, so today the token sits in plain text in your app's config. MCP Creds spots these and offers to **move them**. The config then starts the server through MCP Creds:

```json
"github": {
  "command": "/Applications/MCP Creds.app/Contents/MacOS/MCP Creds",
  "args": ["run", "-e", "GITHUB_PERSONAL_ACCESS_TOKEN", "--", "npx", "-y", "@modelcontextprotocol/server-github"]
}
```

`run` puts the saved key into that server's environment and starts it. The config holds no secret, the AI never sees the token, and when a token changes you update it once in MCP Creds. Use `-e VAR=KEY` when the server's variable name differs from your saved key's name.

On macOS, move the app into **Applications** before connecting: AI apps remember where MCP Creds lives.

Prefer to do it by hand? The app shows the exact snippet for each app, or run `python3 mcpcreds.py config`.

## CLI

```bash
python3 mcpcreds.py set GITHUB_TOKEN ghp_xxx --note "GitHub MCP"
python3 mcpcreds.py get GITHUB_TOKEN      # prints the value, great for $(...)
python3 mcpcreds.py list
python3 mcpcreds.py rm GITHUB_TOKEN
python3 mcpcreds.py export > backup.json
python3 mcpcreds.py connect --secure      # connect every detected AI app and move their tokens
python3 mcpcreds.py run -e GH=GITHUB_TOKEN -- some-mcp-server
```

Use it inside other MCP configs or scripts: `export GITHUB_TOKEN=$(python3 mcpcreds.py get GITHUB_TOKEN)`.

## Where your data lives

`~/.mcpcreds/store.json` (override with `MCPCREDS_HOME`). The folder is `0700` and the file `0600`, so only your user can read it. Writes are atomic.

**Be honest with yourself about the threat model:** values are stored as plain JSON, protected by file permissions, the same as `~/.aws/credentials` or a `.env` file. Anything running as your user can read them, and any AI app you connect can read them through the MCP tools. Only connect apps you trust.

The web UI listens on `127.0.0.1` only, rejects requests whose `Host` is not localhost (blocks DNS rebinding), and requires a random per-session token on every API call (blocks other websites from reading your keys).

## Tests and builds

```bash
python3 -m unittest -v          # 31 tests, stdlib only
./packaging/build.sh            # builds the desktop app for your OS into dist/ (needs uv)
```

Pushing a `v*` tag builds macOS, Windows and Linux apps in GitHub Actions and attaches them to the release.

## License

MIT
