# Resolution (v0.1.1)

The audit ran on v0.1.0 (commit b2de4f7) with Cloudflare's [security-audit skill](https://github.com/cloudflare/security-audit-skill), quick profile. Every item below was fixed in v0.1.1 and has a regression test in `test_mcpcreds.py`.

## Needs validation: other local OS users could get the API token
**Status: fixed, and runtime-validated after the fix.**
Before, `GET /` returned the page (with the API token) to anyone who could reach `127.0.0.1`, including other accounts on the same computer.
Now each launch creates a one-time code in the URL MCP Creds opens. The first request exchanges it for an `HttpOnly; SameSite=Strict` session cookie, and the code is then dead. The page and every API call need that cookie, and the API also still needs the per-session token.
Validated in Chrome and in the macOS WebKit window: your window works (also after reload); a browser without the cookie gets "expired", reusing the link fails, and a stolen API token alone returns 401.
Tests: `test_other_local_users_cannot_get_the_page_or_token`, `test_launch_code_is_single_use_and_cookie_is_httponly`, `test_anti_framing_headers`.

## Rejected (not a security issue), fixed anyway: Disconnect restored whole multi-part values
Disconnect now restores only the matching part of a multi-part credential. Test: `test_disconnect_restores_only_the_matching_part`.

## Deferred coverage, addressed
- Export: the desktop export file is now written `0600`, and both export paths warn that the file holds keys in plain text.
- Deletion and backups: documented in the README Security section (backups in `~/.mcpcreds/backups` keep the original configs, including any tokens they held).
- The command MCP Creds writes into configs: covered by the existing Applications/temporary-folder location warning.

## Hardening notes, applied
- Negative or oversized `Content-Length` is rejected (`test_negative_content_length_rejected`).
- `get_credential` refuses names that match several keys differing only in case (`test_ambiguous_case_is_refused`).
- Internal MCP errors return a generic message; details go to stderr (`test_internal_error_is_generic`).
- `run`: invalid `-e` names are refused (`test_invalid_env_name_refused`); `-psn_` is only stripped as the first argument (`test_psn_only_stripped_before_command`); on Windows, commands are never resolved from the current directory, and `%`/`!`/newlines are refused for `.cmd`/`.bat` shims.
- Config rewriting: `run` must come before `--` to count as wrapped (`test_wrapped_requires_run_before_separator`); backups never follow a planted symlink (`test_backup_does_not_follow_planted_symlink`).
- Release: workflows default to read-only, only the publish job can write, checkout does not persist the token, build tools are pinned, and releases ship a `SHA256SUMS` file.

## Not changed (by design or low value)
- Plaintext store protected by file permissions, and connected AI apps reading keys: documented threat model.
- Lock-file and config fingerprint races between processes: same-user only; content hashing noted for a future version.
- Unpinned GitHub Action major tags: noted for a future version (pin to commit SHAs).
