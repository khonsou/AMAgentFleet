# DeerFlow runtime patches

`apply_deerflow_fixes.py` keeps the Fleet gateway's configuration semantics aligned with ADR-0043:

- `tool_groups: null` restores `inherit all` and clears a stale whitelist.
- `tool_groups: []` remains an explicit zero-tool whitelist.

The script is idempotent and fails closed if the upstream block changes unexpectedly. It is applied by `deploy/Dockerfile.gateway` during the image build so the fix is not limited to the ignored `spike/deer-flow` development snapshot.
