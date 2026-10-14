# Google Drive sync and lab runner

EasyRead can keep a library in a dedicated Google Drive folder and queue remote Docker experiments for an always-on runner. Both are optional; local-only use remains available.

## Configure Google Drive

1. In Google Cloud Console, create a Desktop app OAuth client, enable the Google Drive API, and configure the OAuth consent screen for `https://www.googleapis.com/auth/drive.file`.
2. Download the OAuth client JSON and place it at `google-oauth-client.json` in EasyRead's data directory. The default data directory is the source directory when running from a checkout, or `~/EasyRead` for an installed package. `EASYREAD_GOOGLE_OAUTH_FILE` can point to another path.
3. Open **Settings → Cloud library → Google Drive → Connect Google Drive** and approve access. EasyRead stores the refresh token in `google-drive.json` in its data directory and attempts to restrict that file to owner-only access.
4. The first **Sync now** uploads the current library into the app-created `EasyRead` Drive folder. Later syncs compare per-file checksums in both directions. If both copies changed, EasyRead keeps the local version and writes the Drive version beside it with a `.conflict-<timestamp>` suffix.

The app requests only the `drive.file` scope and reads or writes files it created. It does not scan unrelated Drive files. Google requires an OAuth consent screen and may show an unverified-app warning while an OAuth client is in testing. See Google's [Drive scope guidance](https://developers.google.com/workspace/drive/api/guides/api-specific-auth) and [Desktop OAuth guidance](https://developers.google.com/identity/protocols/oauth2/native-app).

## Install a runner on the lab host

Install EasyRead on an always-on lab host. Configure its ordinary OpenSSH `~/.ssh/config`, private key and `known_hosts` so `ssh <alias>` works non-interactively. Install Docker on the SSH target and pre-pull the images used by profiles.

Authorize Drive from the EasyRead app first, then securely copy `google-oauth-client.json` and `google-drive.json` to the runner's EasyRead data directory. These files contain OAuth credentials and tokens; do not put them in the Drive folder or source control. Keep model API keys and SSH private keys only on their respective hosts.

Start polling the Drive task queue with:

```sh
easyread runner
```

Use `easyread runner --once` to process one polling cycle. The runner writes a durable local task ledger before starting SSH, records success/failure and exit code in the Drive task manifest, and uploads `run.log` plus retrieved output files. A task already claimed by a runner is not automatically rerun after a crash; an interrupted task is marked failed for manual review.

## Experiment profile

Create or edit profiles in **Experiments → Profile JSON**. `command_template` is an argument array, not a shell string. Parameters are inserted into individual arguments; for example:

```json
{
  "id": "baseline",
  "name": "Baseline",
  "ssh_alias": "lab-gpu",
  "docker_image": "research/image:latest",
  "command_template": ["python", "train.py", "--seed", "{seed}"],
  "params": {"seed": {"type": "integer", "default": 1}},
  "mounts": [],
  "output_path": "/workspace/output",
  "host_output_path": "/var/tmp/easyread-runs",
  "network": "none"
}
```

Mount entries use absolute paths on the SSH target, for example `{"host":"/datasets/project","container":"/data"}`. Docker network mode accepts `none`, `bridge` or `host`; `none` is the default. Queue an experiment from the profile panel; the paper ID is recorded as run context. Profiles and run history stay in the EasyRead data directory, while queue manifests and runner outputs live in Drive.

The first sync is deliberately an explicit action. Review the local/Drive library counts before using the runner from another device.
