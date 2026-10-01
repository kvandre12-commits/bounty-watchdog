#!/data/data/com.termux/files/usr/bin/bash
# Cron-friendly entrypoint for termux-job-scheduler. One poll pass, then exit.
cd "$(dirname "$0")/.." || exit 1
exec python -m bounty_watchdog watch
