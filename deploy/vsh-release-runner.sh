#!/usr/bin/env bash
# VSH only: execute the installed reviewed controller; candidate Git blobs are data.
set -euo pipefail
umask 077
export GIT_TERMINAL_PROMPT=0
export GH_PROMPT_DISABLED=1
release_state="${CWS_RELEASE_STATE:-$HOME/.local/state/duplicate-tab-guard-release}"
if [[ "$release_state" != /* || -L "$release_state" ]]; then
  printf 'Release blocked: release state must be an owned private directory\n' >&2
  exit 1
fi
mkdir -p "$release_state"
if [[ ! -d "$release_state" || ! -O "$release_state" ]]; then
  printf 'Release blocked: release state must be an owned private directory\n' >&2
  exit 1
fi
chmod 700 "$release_state"
if [[ -L "$release_state/runner.lock" ]]; then
  printf 'Release blocked: runner lock must not be a symlink\n' >&2
  exit 1
fi
if [[ -e "$release_state/runner.lock" ]] && \
   [[ ! -f "$release_state/runner.lock" || ! -O "$release_state/runner.lock" || "$(stat -c %h "$release_state/runner.lock")" != 1 ]]; then
  printf 'Release blocked: runner lock must be an owned regular file\n' >&2
  exit 1
fi
exec 9>>"$release_state/runner.lock"
chmod 600 "$release_state/runner.lock"
flock -n 9 || exit 0
alert_failure() {
  result=$?
  if (( result != 0 )); then
    failure_evidence="$release_state/last-failure.log"
    failure_stage="$(mktemp "$release_state/.last-failure.XXXXXXXX")"
    {
      printf 'Unit: duplicate-tab-guard-release.service\n'
      printf 'Observed UTC: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
      printf 'Runner exit status: %s\n' "$result"
      printf 'Release receipt: %s/artifacts/receipt.json\n' "$release_state"
      printf 'Inspect: journalctl --user -u duplicate-tab-guard-release.service -n 60 --no-pager\n'
    } >"$failure_stage"
    chmod 600 "$failure_stage"
    mv -fT -- "$failure_stage" "$failure_evidence"
    # Routine sends are durable agent-triage records. Never request human delivery.
    venturi-alert send --id browser.tabguard.release --severity high \
      --summary "Extension release runner exited $result" \
      --plain 'The extension release attempt stopped' \
      --impact 'The current extension release attempt stopped before completion' \
      --action 'journalctl --user -u duplicate-tab-guard-release.service -n 60 --no-pager' \
      --evidence "$failure_evidence" \
      --source duplicate-tab-guard-release.service || true
  fi
  exit "$result"
}
trap alert_failure EXIT
controller="$HOME/.local/libexec/duplicate-tab-guard-release/release_extension.py"
[[ -f "$controller" && ! -L "$controller" ]]

# Monitor an existing rollout even while GitHub is unavailable or newer CI is pending.
status_result=3
if [[ -e "$release_state/artifacts/receipt.json" ]]; then
  set +e
  python3 -I -B "$controller" --status --output-dir "$release_state/artifacts"
  status_result=$?
  set -e
  if (( status_result != 0 && status_result != 3 )); then exit "$status_result"; fi
fi

source_dir="$release_state/source"
if [[ ! -e "$source_dir" ]]; then
  timeout 60s git clone --no-checkout --single-branch --branch main \
    https://github.com/richardcmckinney/cross-browser-duplicate-tab-guard.git "$source_dir"
fi
[[ -d "$source_dir" && ! -L "$source_dir" ]]
cd "$source_dir"
[[ "$(git remote get-url origin)" == 'https://github.com/richardcmckinney/cross-browser-duplicate-tab-guard.git' ]]
timeout 60s git fetch --quiet origin refs/heads/main:refs/remotes/origin/main
reviewed_commit="$(git rev-parse refs/remotes/origin/main)"
[[ "$reviewed_commit" =~ ^[0-9a-f]{40}$ ]]

# An unchanged reviewed commit only needs the status refresh above.
if (( status_result == 0 )) && python3 -I - "$release_state/artifacts/receipt.json" "$reviewed_commit" <<'PY_CURRENT'
import json, sys
try:
    receipt = json.load(open(sys.argv[1]))
    raise SystemExit(0 if receipt.get("commit") == sys.argv[2] else 1)
except (OSError, ValueError):
    raise SystemExit(1)
PY_CURRENT
then
  exit 0
fi

# The pinned controller checks exact workflow/app provenance before release writes.
set +e
python3 -I -B "$controller" --verify-source-only --source-dir "$source_dir" \
  --expected-commit "$reviewed_commit"
gate_result=$?
set -e
if (( gate_result == 2 )); then exit 0; fi
if (( gate_result != 0 )); then exit "$gate_result"; fi

# No candidate npm scripts, dependency installers, Python modules or worktree hooks run.
python3 -I -B "$controller" --source-dir "$source_dir" \
  --expected-commit "$reviewed_commit" --github --submit \
  --output-dir "$release_state/artifacts"
