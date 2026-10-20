# Findings detail

No confirmed findings in this run (see REPORT.md coverage statement). Rejected records:

## Disconnect writes the whole stored credential (all parts) into AI app config env, even for wrappers that never held an inline secret

Fingerprint: `mcpcreds.py:_unwrap:full-store-value-written-to-config-env`

**Rejection reason:** This is not a security boundary crossing. (1) The first trace entry is not a lower-trust entrypoint. Only the authenticated local user can run Disconnect (CLI, or the token-gated POST /api/apps/undo at lines 844 to 845). No website, MCP client, LLM tool or other OS user can reach undo_app. (2) The sink writes into the user's own AI app config, and restoring tokens inline there is the documented intent (docstring at line 739, README line 76, the targeted-restore test at test_mcpcreds.py:294). For wrappers that connect_app created, each key holds exactly the single inline value it took (connect_app and _free_name), so the result matches the file's state before Connect. (3) _write_cfg never broadens permissions (line 559 keeps the existing mode). Any cross-user readability comes from the AI app's own config mode and the home-directory mode, which is the plaintext-under-file-permissions baseline the architecture accepts. (4) The extra-parts case needs the user to edit a key into a multi-part value after Connect, or to hand-write a wrapper and then choose Disconnect themselves. That is the user moving their own secret into their own file. The missing value_part call in _unwrap is a real functional and data-minimization bug: the restored server gets every line instead of the one part it needs. The fix is env[var] = value_part(items[key]["value"], var). It is not an exploitable vulnerability. Source alone settles this; target code was not executed.

