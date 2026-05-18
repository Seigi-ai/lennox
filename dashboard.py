#!/usr/bin/env python3
"""dashboard.py — Lennox Live Dashboard
Shows current execution status, recent actions, and heartbeat.
Run in a separate terminal or tmux window while Lennox is active.
"""

import os
import json
import time
import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR  = os.path.join(BASE_DIR, "logs")


def clear_screen():
    os.system("clear")


def format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        return f"{int(seconds/60)}m {int(seconds%60)}s"
    else:
        return f"{int(seconds/3600)}h {int((seconds%3600)/60)}m"


def show_dashboard():
    clear_screen()
    print("╔══════════════════════════════════════════╗")
    print("║         Lennox Live Dashboard            ║")
    print("╚══════════════════════════════════════════╝")

    # Heartbeat
    heartbeat_path = os.path.join(LOG_DIR, ".heartbeat")
    if os.path.exists(heartbeat_path):
        try:
            with open(heartbeat_path) as f:
                last_beat = float(f.read().strip())
            ago = time.time() - last_beat
            status = "✓ ALIVE" if ago < 60 else f"⚠ STALE ({format_duration(ago)} ago)"
        except Exception:
            status = "? UNKNOWN"
    else:
        status = "✗ NO HEARTBEAT"

    print(f"\n  Process Status : {status}")

    # Current execution status
    status_path = os.path.join(LOG_DIR, "status.json")
    if os.path.exists(status_path):
        try:
            with open(status_path) as f:
                st = json.load(f)

            state = st.get("state", "unknown").upper()
            state_icon = {"IDLE": "○", "RUNNING": "▶", "ERROR": "✗"}.get(state, "?")
            print(f"  Current State  : {state_icon} {state}")

            ts = st.get("timestamp", "")
            if ts:
                try:
                    dt = datetime.datetime.fromisoformat(ts)
                    ago = (datetime.datetime.now() - dt).total_seconds()
                    print(f"  Last Update    : {format_duration(ago)} ago")
                except Exception:
                    pass

            cmd = st.get("command", [])
            if cmd:
                print(f"\n  Active Command:")
                print(f"    $ {' '.join(str(c) for c in cmd)}")

            out = st.get("output_tail", "")
            if out:
                print(f"\n  Output (last 300 chars):")
                print(f"  {'─'*54}")
                for line in out.splitlines()[-8:]:
                    print(f"  {line[:54]}")
        except Exception as e:
            print(f"\n  [Error reading status: {e}]")
    else:
        print("\n  No status file found. Is Lennox running?")

    # Recent actions
    activity_path = os.path.join(LOG_DIR, "activity.json")
    if os.path.exists(activity_path):
        try:
            with open(activity_path) as f:
                data = json.load(f)

            actions = data.get("actions", [])[-5:]
            if actions:
                print(f"\n  Recent Actions:")
                print(f"  {'─'*54}")
                for a in reversed(actions):
                    icon = "✓" if a.get("success") else "✗"
                    desc = a.get("description", "?")[:40]
                    ts   = a.get("timestamp", "?")[11:]  # HH:MM:SS only
                    print(f"  {icon} [{ts}] {desc}")

            overrides = data.get("overrides", [])[-3:]
            if overrides:
                print(f"\n  Recent Overrides (undo):")
                print(f"  {'─'*54}")
                for o in reversed(overrides):
                    desc = o.get("description", "?")[:40]
                    ts   = o.get("timestamp", "?")[11:]
                    print(f"  ↩ [{ts}] {desc}")
        except Exception:
            pass

    print(f"\n  {'─'*54}")
    print("  Press Ctrl+C to exit. Refreshing every 2 seconds...")


if __name__ == "__main__":
    try:
        while True:
            show_dashboard()
            time.sleep(2)
    except KeyboardInterrupt:
        print("\nDashboard closed.")
