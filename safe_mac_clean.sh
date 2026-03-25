#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

# safe_mac_clean.sh
# Conservative macOS cleanup helper.
# Default mode is DRY RUN. Use --apply to move matched files to ~/.Trash.

SCRIPT_NAME="$(basename "$0")"
MODE="dry-run"
DAYS=30
VERBOSE=0
INCLUDE_CONTAINER_CACHES=0
INCLUDE_XCODE_DERIVED_DATA=0
ASSUME_YES=0
PROGRESS_EVERY=5000
MAX_CANDIDATES=0

TRASH_DIR="$HOME/.Trash"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
SESSION_TAG="safe-clean-$TIMESTAMP"
LOG_FILE="$HOME/${SESSION_TAG}.log"
LIST_FILE=""

usage() {
  cat <<USAGE
Usage: $SCRIPT_NAME [options]

Safe macOS cleaner (default: dry-run)

Options:
  --apply                     Actually move files to ~/.Trash (default: preview only)
  --days N                    Only clean files older than N days (default: 30)
  --include-container-caches  Include app container caches under ~/Library/Containers/*/Data/Library/Caches
  --include-xcode             Include Xcode DerivedData older than --days
  --progress-every N          Show scan progress every N files (default: 5000)
  --max-candidates N          Stop scanning after N matched files (default: 0 = no limit)
  -y, --yes                   Skip confirmation prompt in --apply mode
  -v, --verbose               Print per-file actions
  -h, --help                  Show this help

Examples:
  $SCRIPT_NAME
  $SCRIPT_NAME --apply --days 14
  $SCRIPT_NAME --apply --include-container-caches --include-xcode
  $SCRIPT_NAME --days 30 --progress-every 1000 --max-candidates 50000
USAGE
}

err() { echo "[ERROR] $*" >&2; }
info() { echo "[INFO]  $*"; }
warn() { echo "[WARN]  $*"; }

is_number() {
  [[ "$1" =~ ^[0-9]+$ ]]
}

human_size() {
  local bytes="$1"
  awk -v b="$bytes" 'function human(x){
    s="B KB MB GB TB";
    split(s,a," ");
    for(i=1;x>=1024 && i<5;i++) x/=1024;
    return sprintf("%.2f %s", x, a[i]);
  } BEGIN{print human(b)}'
}

log_line() {
  local line="$1"
  echo "$line" | tee -a "$LOG_FILE" >/dev/null
}

declare -a ALLOWED_ROOTS=()
add_allowed_root() {
  local p="$1"
  [[ -d "$p" ]] || return 0
  ALLOWED_ROOTS+=("$(cd "$p" && pwd -P)")
}

refresh_allowed_roots() {
  ALLOWED_ROOTS=()
  add_allowed_root "$HOME/Library/Caches"
  add_allowed_root "$HOME/Library/Logs"
  add_allowed_root "$HOME/Library/Application Support/CrashReporter"
  add_allowed_root "$HOME/Library/Developer/CoreSimulator/Caches"
  add_allowed_root "$HOME/.Trash"

  if (( INCLUDE_CONTAINER_CACHES == 1 )); then
    add_allowed_root "$HOME/Library/Containers"
  fi
  if (( INCLUDE_XCODE_DERIVED_DATA == 1 )); then
    add_allowed_root "$HOME/Library/Developer/Xcode/DerivedData"
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply)
      MODE="apply"
      shift
      ;;
    --days)
      [[ $# -ge 2 ]] || { err "--days requires a number"; exit 2; }
      is_number "$2" || { err "--days must be a non-negative integer"; exit 2; }
      DAYS="$2"
      shift 2
      ;;
    --include-container-caches)
      INCLUDE_CONTAINER_CACHES=1
      shift
      ;;
    --include-xcode)
      INCLUDE_XCODE_DERIVED_DATA=1
      shift
      ;;
    --progress-every)
      [[ $# -ge 2 ]] || { err "--progress-every requires a number"; exit 2; }
      is_number "$2" || { err "--progress-every must be a non-negative integer"; exit 2; }
      PROGRESS_EVERY="$2"
      shift 2
      ;;
    --max-candidates)
      [[ $# -ge 2 ]] || { err "--max-candidates requires a number"; exit 2; }
      is_number "$2" || { err "--max-candidates must be a non-negative integer"; exit 2; }
      MAX_CANDIDATES="$2"
      shift 2
      ;;
    -y|--yes)
      ASSUME_YES=1
      shift
      ;;
    -v|--verbose)
      VERBOSE=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      err "Unknown option: $1"
      usage
      exit 2
      ;;
  esac
done

refresh_allowed_roots

# Build scan roots (subset of allowlist).
declare -a SCAN_ROOTS=()
for root in "${ALLOWED_ROOTS[@]}"; do
  [[ "$root" == "$HOME/.Trash" ]] && continue
  SCAN_ROOTS+=("$root")
done

if [[ ${#SCAN_ROOTS[@]} -eq 0 ]]; then
  err "No scan roots found."
  exit 1
fi

NEED_LIST=0
if [[ "$MODE" == "apply" ]] || (( VERBOSE == 1 )); then
  NEED_LIST=1
  LIST_FILE="$(mktemp -t safe-clean-list.XXXXXX)"
  trap '[[ -n "$LIST_FILE" && -f "$LIST_FILE" ]] && rm -f "$LIST_FILE"' EXIT
fi

TOTAL_FILES=0
TOTAL_BYTES=0
ROOT_FILES=0
ROOT_BYTES=0
STOP_EARLY=0

info "Mode: $MODE"
info "Older-than days: $DAYS"
info "Log: $LOG_FILE"
info "Scan roots:"
for r in "${SCAN_ROOTS[@]}"; do
  echo "  - $r"
done

for r in "${SCAN_ROOTS[@]}"; do
  [[ -d "$r" ]] || continue

  ROOT_FILES=0
  ROOT_BYTES=0
  info "Scanning: $r"

  while IFS= read -r -d '' f; do
    sz=$(stat -f%z "$f" 2>/dev/null || echo 0)

    ((TOTAL_FILES+=1))
    ((TOTAL_BYTES+=sz))
    ((ROOT_FILES+=1))
    ((ROOT_BYTES+=sz))

    if (( NEED_LIST == 1 )); then
      printf '%s\0' "$f" >>"$LIST_FILE"
    fi

    if (( PROGRESS_EVERY > 0 && TOTAL_FILES % PROGRESS_EVERY == 0 )); then
      info "Progress: matched $TOTAL_FILES files, size $(human_size "$TOTAL_BYTES")"
    fi

    if (( MAX_CANDIDATES > 0 && TOTAL_FILES >= MAX_CANDIDATES )); then
      warn "Reached --max-candidates=$MAX_CANDIDATES, stopping scan early."
      STOP_EARLY=1
      break
    fi
  done < <(find "$r" -xdev -mindepth 1 -mtime +"$DAYS" -type f -print0 2>/dev/null)

  info "Finished: $r (matched $ROOT_FILES files, $(human_size "$ROOT_BYTES"))"

  if (( STOP_EARLY == 1 )); then
    break
  fi
done

if (( TOTAL_FILES == 0 )); then
  info "No candidate files found."
  exit 0
fi

info "Candidate files (validated by root allowlist): $TOTAL_FILES"
info "Estimated space: $(human_size "$TOTAL_BYTES")"

if [[ "$MODE" == "dry-run" ]]; then
  info "Dry-run only. No files were moved."

  if (( VERBOSE == 1 )); then
    if [[ -n "$LIST_FILE" && -f "$LIST_FILE" ]]; then
      while IFS= read -r -d '' f; do
        echo "[DRY] $f"
      done <"$LIST_FILE"
    fi
  fi

  exit 0
fi

if (( ASSUME_YES == 0 )); then
  echo
  read -r -p "Move $TOTAL_FILES files ($(human_size "$TOTAL_BYTES")) to Trash? [y/N] " ans
  case "$ans" in
    y|Y|yes|YES)
      ;;
    *)
      info "Cancelled by user."
      exit 0
      ;;
  esac
fi

MOVED=0
MOVE_FAIL=0

if [[ -z "$LIST_FILE" || ! -f "$LIST_FILE" ]]; then
  err "Internal error: candidate list is missing in apply mode."
  exit 1
fi

while IFS= read -r -d '' f; do
  # Extra guard: only process files that still exist under HOME.
  [[ -f "$f" ]] || continue
  [[ "$f" == "$HOME"/* ]] || { warn "Skipped outside HOME: $f"; continue; }

  rel="${f#"$HOME"/}"
  target="$TRASH_DIR/$SESSION_TAG/$rel"
  target_dir="$(dirname "$target")"
  mkdir -p "$target_dir"

  if mv "$f" "$target" 2>/dev/null; then
    ((MOVED+=1))
    (( VERBOSE == 1 )) && echo "[MOVED] $f -> $target"
  else
    ((MOVE_FAIL+=1))
    warn "Move failed: $f"
  fi
done <"$LIST_FILE"

log_line "session=$SESSION_TAG mode=$MODE days=$DAYS moved=$MOVED failed=$MOVE_FAIL estimated_bytes=$TOTAL_BYTES"
info "Done. Moved: $MOVED, Failed: $MOVE_FAIL"
info "Moved items are inside: $TRASH_DIR/$SESSION_TAG"
info "Review in Trash before permanent deletion."
