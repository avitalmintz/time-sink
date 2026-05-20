#!/bin/bash
# Install sleepwatcher hooks for time-sink.
#
# What this does:
#   1. Verifies sleepwatcher is installed (Homebrew: `brew install sleepwatcher`)
#   2. Symlinks ~/.sleep -> sleephooks/on_sleep.sh
#   3. Symlinks ~/.wakeup -> sleephooks/on_wake.sh
#   4. Starts the sleepwatcher service via brew services
#
# To uninstall:  rm ~/.sleep ~/.wakeup && brew services stop sleepwatcher

set -e

REPO="$(cd "$(dirname "$0")/.." && pwd)"

if ! command -v sleepwatcher >/dev/null 2>&1; then
  echo "sleepwatcher not installed. Run:"
  echo "  brew install sleepwatcher"
  echo "Then re-run this script."
  exit 1
fi

chmod +x "$REPO/sleephooks/on_sleep.sh" "$REPO/sleephooks/on_wake.sh"

# Back up any existing hooks first
for f in ~/.sleep ~/.wakeup; do
  if [ -e "$f" ] && [ ! -L "$f" ]; then
    echo "Backing up existing $f to $f.before-time-sink"
    mv "$f" "$f.before-time-sink"
  fi
done

ln -sfn "$REPO/sleephooks/on_sleep.sh" ~/.sleep
ln -sfn "$REPO/sleephooks/on_wake.sh" ~/.wakeup

# Start the service. Brew installs sleepwatcher as a "service" via launchd.
brew services restart sleepwatcher

echo "Installed. Logs will land in $REPO/data/sleephook.log"
echo "Test by closing the lid for ~10 seconds and reopening."
