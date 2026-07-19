# Script Organization Design

## Goal

Place the repository's Docker helper scripts in one `scripts/` directory while
preserving their existing behavior.

## Approach

Move `build-and-push.sh`, `docker-entrypoint.sh`, and `start.sh` from the
repository root into `scripts/` without renaming them. Update `Dockerfile` and
any documentation that refers to those files. Preserve executable permissions
so the image entrypoint and local helper commands continue to work.

## Error Handling

The scripts retain their existing `set -euo pipefail` behavior. No runtime
logic changes are part of this cleanup.

## Verification

Run shell syntax checks for each script and the full Python unit-test suite.
