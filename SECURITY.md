# Security

Fallout 4 Save Tracker reads untrusted binary files (saves, plugins, archives). It's designed to
fail safely on malformed input: every read is bounds-checked, and unusual data produces a warning
or a clear error rather than undefined behaviour. It never writes to saves or game files, runs no
external programs, and makes no network requests.

If you find a file that makes the tool hang, use excessive memory or crash with a traceback,
please open an issue. If you think a problem could harm users beyond that (for example, writing
outside the tool's own cache and config folders), please report it privately through GitHub's
**Security ▸ Report a vulnerability** on the repository instead of opening a public issue.
