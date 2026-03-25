# Safe Clean Mac

A safety-first macOS cleanup project for removing disposable cache and log files without risking personal data.

This repository provides two ways to use the cleaner:

- `safe_mac_clean.sh`: a conservative command-line cleaner
- `safe_clean_web.py`: a local web UI for scan, review, and cleanup

## Why This Project Exists

Many Mac cleanup tools feel too aggressive: they scan too broadly, delete too much, or hide what they are doing.

This project takes the opposite approach:

- Preview first
- Restrict cleanup to known-safe cache and log locations
- Move files to Trash instead of permanently deleting them
- Require explicit confirmation before cleanup
- Keep cleanup auditable with logs

## Safety Principles

- Dry-run by default
- Strict allowlist of scan roots
- No permanent delete
- Human confirmation before apply
- Easy rollback from `~/.Trash`

By default, the project targets common disposable locations such as:

- `~/Library/Caches`
- `~/Library/Logs`
- `~/Library/Application Support/CrashReporter`
- `~/Library/Developer/CoreSimulator/Caches`

Optional support is available for:

- app container caches
- Xcode `DerivedData`

It does not target user folders like Desktop, Documents, Downloads, Photos, or other personal content directories.

## Project Structure

```text
safe_mac_clean.sh          Conservative CLI cleaner
safe_clean_web.py          Local web UI
README_SAFE_MAC_CLEAN.md   Detailed CLI guide
README_SAFE_MAC_CLEAN_WEB.md
                           Detailed web guide
```

## Quick Start

### CLI

Preview only:

```bash
./safe_mac_clean.sh
```

Apply cleanup by moving matched files to Trash:

```bash
./safe_mac_clean.sh --apply
```

More conservative example:

```bash
./safe_mac_clean.sh --apply --days 60
```

Large-cache example with visible progress:

```bash
./safe_mac_clean.sh --days 30 --progress-every 1000 --max-candidates 50000
```

### Web UI

Start the local server:

```bash
python3 safe_clean_web.py
```

Then open:

```text
http://127.0.0.1:8765
```

The web UI lets you:

- start a background scan
- watch progress
- review summary and large files
- confirm cleanup with `MOVE_TO_TRASH`
- move matched files into a session folder inside `~/.Trash`

## Documentation

- CLI guide: [README_SAFE_MAC_CLEAN.md](README_SAFE_MAC_CLEAN.md)
- Web guide: [README_SAFE_MAC_CLEAN_WEB.md](README_SAFE_MAC_CLEAN_WEB.md)

## Current Notes

- The web app uses Python standard library only, so it has no third-party dependency requirement.
- Web task history is currently stored in memory and clears on restart.
- Cleanup sessions are logged in the user's home directory for auditability.

## License

No license has been added yet. If you want this repository to be open for reuse, add a license such as MIT.
