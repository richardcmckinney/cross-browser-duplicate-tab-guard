#!/usr/bin/env bash
# One fixed trusted repository, VSH only. Never execute PR code with publishing credentials.
set -euo pipefail
umask 077
release_state="${CWS_RELEASE_STATE:-$HOME/.local/state/duplicate-tab-guard-release}"
mkdir -p "$release_state"
exec 9>"$release_state/runner.lock"
flock -n 9 || exit 0
alert_failure() {
  result=$?
  if (( result != 0 )); then
    venturi-alert --id duplicate-tab-guard-release --severity error \
      --summary 'Extension release blocked' \
      --impact 'A reviewed extension update may not be submitted to the Chrome Web Store' \
      --action 'Inspect the VSH release journal and release receipt; preserve active store reviews' \
      --evidence "journalctl --user -u duplicate-tab-guard-release.service; $release_state/artifacts/receipt.json" || true
  fi
  exit "$result"
}
trap alert_failure EXIT
source_dir="$release_state/source"
if [[ ! -e "$source_dir" ]]; then
  gh repo clone richardcmckinney/cross-browser-duplicate-tab-guard "$source_dir" -- --single-branch --branch main
fi
cd "$source_dir"
[[ "$(git remote get-url origin)" == 'https://github.com/richardcmckinney/cross-browser-duplicate-tab-guard.git' ]]
[[ -z "$(git status --porcelain --untracked-files=normal)" ]]
git fetch --quiet origin main
git checkout --quiet --detach origin/main
reviewed_commit="$(git rev-parse HEAD)"
# This gate is part of the installed trusted runner, before executing freshly fetched code.
set +e
python3 - "$reviewed_commit" <<'PY_GATE'
import json,subprocess,sys
repo="richardcmckinney/cross-browser-duplicate-tab-guard"
try:
 def api(path):
  r=subprocess.run(["gh","api",path],capture_output=True,text=True,timeout=60)
  if r.returncode:raise RuntimeError("GitHub check lookup failed")
  return json.loads(r.stdout)
 checks=api(f"repos/{repo}/commits/{sys.argv[1]}/check-runs?per_page=100")
 statuses=api(f"repos/{repo}/commits/{sys.argv[1]}/status?per_page=100")
 runs={}
 for check in sorted(checks.get("check_runs",[]),key=lambda x:x.get("id",0),reverse=True):runs.setdefault(check.get("name"),check)
 required={"tests","gitleaks"}
 if checks.get("total_count",0)>len(checks.get("check_runs",[])):raise RuntimeError("Incomplete CI evidence")
 if not required<=runs.keys() or any(runs[n].get("status")!="completed" for n in required):raise SystemExit(2)
 if any(runs[n].get("conclusion")!="success" for n in required):raise RuntimeError("Required CI failed")
 if any(c.get("status")!="completed" for c in runs.values()):raise SystemExit(2)
 if any(c.get("conclusion") not in {"success","neutral","skipped"} for c in runs.values()):raise RuntimeError("CI failed")
 if statuses.get("total_count",0) and statuses.get("state")=="pending":raise SystemExit(2)
 if statuses.get("total_count",0) and statuses.get("state")!="success":raise RuntimeError("Commit status failed")
except (RuntimeError,ValueError,subprocess.TimeoutExpired):
 print("Release blocked: could not confirm successful CI for current main",file=sys.stderr);raise SystemExit(1)
PY_GATE
gate_result=$?
set -e
if (( gate_result == 2 )); then exit 0; fi
if (( gate_result != 0 )); then exit "$gate_result"; fi
# Test dependencies have no publishing credentials. npm lifecycle scripts are disabled at installation.
if python3 - "$release_state/artifacts/receipt.json" "$reviewed_commit" <<'PY_CHECK'
import json,sys
try:
 receipt=json.load(open(sys.argv[1]))
 raise SystemExit(0 if receipt.get("commit")==sys.argv[2] and receipt.get("store") in {"published","pending_propagation","pending_review","staged","partial_rollout","existing_published_unverified","existing_pending_review_unverified","existing_staged_unverified","existing_partial_rollout_unverified"} else 1)
except (OSError,ValueError):raise SystemExit(1)
PY_CHECK
then
  set +e
  python3 -B scripts/release_extension.py --status-only --output-dir "$release_state/artifacts"
  status_result=$?
  set -e
  if (( status_result != 3 )); then exit "$status_result"; fi
fi
lock_digest="$(sha256sum package-lock.json | cut -d' ' -f1)"
if [[ ! -f "$release_state/dependency-lock.sha256" ]] || [[ "$(cat "$release_state/dependency-lock.sha256")" != "$lock_digest" ]]; then
  env -u CWS_ACCESS_TOKEN -u CWS_CLIENT_ID -u CWS_CLIENT_SECRET -u CWS_REFRESH_TOKEN -u CWS_SERVICE_ACCOUNT_KEY -u CWS_SERVICE_ACCOUNT_EMAIL -u GH_TOKEN -u GITHUB_TOKEN npm ci --ignore-scripts
  env -u CWS_ACCESS_TOKEN -u CWS_CLIENT_ID -u CWS_CLIENT_SECRET -u CWS_REFRESH_TOKEN -u CWS_SERVICE_ACCOUNT_KEY -u CWS_SERVICE_ACCOUNT_EMAIL -u GH_TOKEN -u GITHUB_TOKEN npx --no-install playwright install --only-shell chromium
  printf '%s\n' "$lock_digest" >"$release_state/dependency-lock.sha256"
fi
python3 -B scripts/release_extension.py --expected-commit "$reviewed_commit" \
  --github --submit --output-dir "$release_state/artifacts"
