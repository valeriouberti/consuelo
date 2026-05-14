# Automation

Three patterns for running Consuelo unattended: local cron, GitHub Actions, and a containerised cron job.

## Local cron (macOS / Linux)

The simplest setup. The pipeline is idempotent — safe to run on any cadence.

```cron
# Every morning at 07:00
0 7 * * * cd /Users/me/path/to/consuelo && .venv/bin/consuelo run >> /tmp/sb.log 2>&1
```

**Things to know:**

- macOS users on Sonoma+ must grant cron (or `launchd`) **Full Disk Access** before it can read files inside iCloud-synced folders. Add `/usr/sbin/cron` under System Settings → Privacy & Security → Full Disk Access.
- Use **absolute paths** for both `cd` target and the venv binary. Cron's `PATH` is minimal.
- Capture both `stdout` and `stderr` to a log file. The pipeline writes status to `stderr` (logging) and only the `ask` output goes to `stdout`.
- If you use Ollama, make sure the daemon survives reboots — `ollama serve` is not running by default after restart. On macOS, `brew services start ollama` handles this.

## launchd (macOS-native)

For cleaner macOS integration than cron:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>           <string>com.valeriouberti.secondbrain</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Users/me/path/to/consuelo/.venv/bin/consuelo</string>
        <string>run</string>
    </array>
    <key>WorkingDirectory</key><string>/Users/me/path/to/consuelo</string>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>   <integer>7</integer>
        <key>Minute</key> <integer>0</integer>
    </dict>
    <key>StandardOutPath</key> <string>/tmp/sb.out</string>
    <key>StandardErrorPath</key><string>/tmp/sb.err</string>
</dict>
</plist>
```

Save as `~/Library/LaunchAgents/com.valeriouberti.secondbrain.plist`, then:

```bash
launchctl load ~/Library/LaunchAgents/com.valeriouberti.secondbrain.plist
```

## GitHub Actions

Suitable when:

- you use **cloud mode** (Ollama wouldn't fit on a hosted runner anyway),
- your vault is reachable from CI (git repo, S3 bucket, etc.),
- you're OK with a small monthly Actions cost.

A reference workflow:

```yaml
# .github/workflows/run.yml
name: Consuelo — daily run

on:
  schedule:
    - cron: "0 7 * * *"   # 07:00 UTC
  workflow_dispatch: {}

jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Sync vault from S3
        env:
          AWS_ACCESS_KEY_ID: ${{ secrets.AWS_ACCESS_KEY_ID }}
          AWS_SECRET_ACCESS_KEY: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
        run: aws s3 sync s3://my-vault/ ./vault/

      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }

      - run: pip install -e ".[dev]"

      - name: Run pipeline
        env:
          VAULT_PATH: ./vault
          LLM_MODE: cloud
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
          GDRIVE_CREDENTIALS_JSON: ./credentials/gdrive.json
          GDRIVE_INBOX_PDF_FOLDER_ID: ${{ secrets.GDRIVE_INBOX_ID }}
          GDRIVE_PROCESSED_PDF_FOLDER_ID: ${{ secrets.GDRIVE_PROCESSED_ID }}
        run: |
          mkdir -p credentials
          echo "$GDRIVE_SA_JSON" > credentials/gdrive.json
          consuelo run
        # Note: GDRIVE_SA_JSON is the literal JSON, set as a repo secret.

      - name: Sync vault back to S3
        env:
          AWS_ACCESS_KEY_ID: ${{ secrets.AWS_ACCESS_KEY_ID }}
          AWS_SECRET_ACCESS_KEY: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
        run: aws s3 sync ./vault/ s3://my-vault/ --delete
```

**Caveats:**

- The vault is mutated during the run (new files in `Notes/`, deleted files in `Inbox/`, updated state). You must sync **both** before and after the run.
- State + cache + Chroma also belong in the synced storage if you want true delta runs across days. Otherwise every run will look like a cold start.
- For dual-direction sync (local edits + CI runs), consider `rclone bisync` or a git-based workflow with a separate vault branch.

## Container / Cloud Run / Kubernetes CronJob

For self-hosted or cloud deployments. The pipeline is a standard Python package — there are no platform-specific assumptions beyond the env vars listed in [`configuration.md`](configuration.md).

Sketch of a `Dockerfile`:

```dockerfile
FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY consuelo ./consuelo
COPY prompts ./prompts

RUN pip install --no-cache-dir .

ENTRYPOINT ["consuelo"]
CMD ["run"]
```

Run with the vault mounted as a volume:

```bash
docker run --rm \
  -e LLM_MODE=cloud \
  -e OPENAI_API_KEY=$OPENAI_API_KEY \
  -e VAULT_PATH=/vault \
  -v /Users/me/Library/Mobile\ Documents/iCloud~md~obsidian/Documents/Personal:/vault \
  -v /Users/me/.local/share/consuelo:/state \
  -e CHROMA_PATH=/state/chroma \
  -e STATE_PATH=/state/state \
  -e CACHE_PATH=/state/cache \
  ghcr.io/valeriouberti/consuelo:latest run
```

For Kubernetes, wrap the container in a `CronJob` with a `PersistentVolumeClaim` for `/vault` and `/state`.

## Observability

The pipeline writes structured-ish text to `stderr`. The end-of-run summary is a single line you can grep for:

```
INFO cost: $0.0042 (chat 1240 prompt + 380 completion @ gpt-4o-mini | embed 2100 @ text-embedding-3-small)
```

For cron / launchd, redirect to a log file and rotate it:

```bash
.venv/bin/consuelo run >> /tmp/sb.log 2>&1
```

For GitHub Actions, the run logs are already captured. Failed runs trigger the default email notification.

Stretch goals:

- Send the end-of-run summary to a Telegram bot / Slack webhook.
- Emit Prometheus metrics from the cost tracker.
- Alert when the cost crosses a daily threshold.

None of these are in the project today — the pipeline is intentionally minimal — but the integration surface is small (one log line at the end of `cli.py::run`).
