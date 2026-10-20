# Needs validation

## Unauthenticated GET / gives the API session token to any local OS user, who can then read and change every saved key

Fingerprint: `mcpcreds.py/make_server/root-page-discloses-session-token`

While `mcpcreds.py ui` is running, the UI server listens on a TCP socket at 127.0.0.1 (port 7878, or a random port if 7878 is taken). Every account on the machine can reach that socket. For GET / and /index.html the server checks only that the Host header is 127.0.0.1 or localhost. It then returns ui.html with the live per-session API token substituted into `const T = "__TOKEN__"`. A client that is not a browser (curl, or a script run by another OS user) sets the Host header freely. So another local user can fetch the page, take the token, and call GET /api/items, which returns load(), the full store including secret values. With the same token, that user can also POST /api/items or /api/import to overwrite values, DELETE /api/items/<name> to remove keys, and POST /api/apps/connect or /api/apps/undo to rewrite the victim's AI app config files. SECURITY.md line 13 puts 'another user on the machine ... read or change saved keys' in scope. The store's 0700/0600 permissions exist to keep out exactly this principal. The README (line 130) says the token blocks only 'other websites'. It works against cross-origin browser pages, which cannot read the GET / response, but it does not authenticate the local peer. The exposure lasts only while the UI server process runs.

**Claimed root cause:** make_server hands out the bearer token that guards /api/* without any authentication: GET / gives it to any loopback peer that sends a localhost Host header. The server does no OS-level peer authentication. There is no peer UID check, no user-private channel such as a 0600 Unix socket, and no out-of-band or one-time handoff of the token to the window or browser it opens (main passes a plain url to webview.create_window / webbrowser.open). So the only thing separating the victim user from other local users is the Host header, which a non-browser client controls.

**Trace:**
- entrypoint: `mcpcreds.py:872` ThreadingHTTPServer binds 127.0.0.1:port. Any local account can connect to this loopback TCP socket, and there is no peer-credential check.
- propagation: `mcpcreds.py:807` The path / or /index.html is served after only _host_ok(), not _authed().
- propagation: `mcpcreds.py:791` The only gate is Host in ('127.0.0.1','localhost'). The client controls this header, so it is not peer authentication.
- propagation: `mcpcreds.py:811` page.replace('__TOKEN__', token) puts the live session token into the response body.
- propagation: `mcpcreds.py:797` compare_digest accepts the leaked token, so the other user passes API auth.
- sink: `mcpcreds.py:823` Returns {'items': load()}, every saved secret value, to the other local user.

**Blockers:**
- Target code was not executed: this run's macOS sandbox cannot enforce a memory limit, so the cross-account loopback fetch has not been observed locally.
- Whether a second OS account can reach the victim's 127.0.0.1 listener depends on the OS and on any host firewall or per-user network sandbox. Default loopback on macOS, Linux and Windows is shared by all local users, but this has not been observed here.

**Validation plan:**
```json
{
 "local": "On a disposable VM with two OS accounts and only dummy keys: as user A, set MCPCREDS_HOME to a scratch directory, save one dummy key (`python3 mcpcreds.py set DUMMY dummyvalue`), then start `python3 mcpcreds.py ui` and note the port. As user B, run `curl -s -H 'Host: 127.0.0.1' http://127.0.0.1:<port>/ | grep -o 'const T = \"[^\"]*\"'`, then `curl -s -H 'Host: 127.0.0.1' -H 'X-Token: <tok>' http://127.0.0.1:<port>/api/items`. Vulnerable result: user B receives user A's dummy value. Use read-only requests only; do not call connect/undo/delete. Regression test: a GET / without a prior one-time launch nonce (or from a peer with a different UID) must not return a usable token.",
 "deployment": "The owner checks on a multi-user macOS, Windows and Linux host that a connection from another account reaches the UI port. The owner then picks a fix: pass the token out-of-band (a one-time launch nonce in the URL given to webview/webbrowser, exchanged once and then invalidated), or on POSIX use a 0600 Unix socket or a peer-UID check."
}
```

