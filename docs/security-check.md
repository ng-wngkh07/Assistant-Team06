# Security checks

Follow [SECURITY.md](../SECURITY.md) for reporting and data boundaries. Before publishing a change, review the exact diff and new files, run the available secret scanner with redacted output, and confirm that ignored private artifacts remain excluded.

Record scanner version, revision, scope and limitations in controlled evidence. Do not publish sensitive findings or infer that no findings means complete security. Repeat checks for the revision being released.
