# Security policy

MCP Creds stores API keys, so security reports are very welcome.

## Reporting a vulnerability

Please **do not open a public issue**. Use GitHub's private reporting instead: go to the repository's **Security** tab and choose **Report a vulnerability**. Include what you found, how to reproduce it and the version (`python3 mcpcreds.py --version`).

You will get a reply within a few days. Fixes are released as a new version and credited to you unless you prefer otherwise.

## Scope

In scope: anything that lets another website, another user on the machine, or a process that should not have access read or change saved keys; ways the app could damage or leak an AI app's config file; bugs in `run` that expose a key to the wrong process.

Out of scope, by design (see the README's Security section): programs running as your own user reading `~/.mcpcreds/store.json`, and AI apps you chose to connect asking for a key.
