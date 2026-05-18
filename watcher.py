"""
watcher.py — Lennox filesystem monitor
On-demand scanning only. No continuous inotifywait.
"""

import os
import json
import fcntl
import datetime

HOME         = os.path.expanduser("~")
MEMORY_DIR   = os.path.join(os.path.dirname(__file__), "memory")
FS_LOG       = os.path.join(MEMORY_DIR, "filesystem_log.json")

IGNORE_DIRS = {
    ".cache", ".config", ".local", ".mozilla", ".chrome",
    ".thumbnails", ".gvfs", ".dbus", ".Xauthority",
    "__pycache__", ".git", ".npm", ".conda", "snap",
    "proc", "sys", "dev", "run", "boot", "tmp"
}

IGNORE_EXTENSIONS = {
    ".log", ".tmp", ".temp", ".lock", ".pid",
    ".swp", ".swo", ".part", ".crdownload",
}


def atomic_json_write(path: str, data: dict):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    os.replace(tmp_path, path)


def load_fs_log() -> dict:
    try:
        with open(FS_LOG) as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_SH)
            data = json.load(f)
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            return data
    except (FileNotFoundError, json.JSONDecodeError):
        return {
            "_note": "This file is in JSON format. Ask Lennox to convert it to plain English if you don't understand it.",
            "last_scan": "",
            "scan_paths": [HOME],
            "snapshot": {},
            "changes": []
        }


def should_ignore(path: str) -> bool:
    parts = path.split(os.sep)
    for part in parts:
        if part in IGNORE_DIRS:
            return True
    _, ext = os.path.splitext(path)
    if ext.lower() in IGNORE_EXTENSIONS:
        return True
    filename = os.path.basename(path)
    rel      = os.path.relpath(path, HOME)
    depth    = len(rel.split(os.sep))
    if filename.startswith(".") and depth > 1:
        return True
    return False


def build_snapshot() -> dict:
    snapshot = {}
    for root, dirs, files in os.walk(HOME):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS and not d.startswith(".")]
        rel_root = os.path.relpath(root, HOME)
        meaningful_files = [f for f in files if not should_ignore(os.path.join(root, f))]
        if meaningful_files or rel_root == ".":
            try:
                mtime = datetime.datetime.fromtimestamp(os.path.getmtime(root)).strftime("%Y-%m-%d %H:%M:%S")
            except OSError:
                mtime = ""
            snapshot[rel_root] = {"files": meaningful_files, "last_modified": mtime}
    return snapshot


def run_scan() -> dict:
    """Run a fresh filesystem scan, compare with previous snapshot, log changes."""
    old_data = load_fs_log()
    old_snapshot = old_data.get("snapshot", {})
    new_snapshot = build_snapshot()

    changes = []
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for path, info in new_snapshot.items():
        if path not in old_snapshot:
            changes.append({"timestamp": now_str, "type": "new_dir", "path": path, "alerted": False})
        else:
            old_files = set(old_snapshot[path].get("files", []))
            new_files = set(info.get("files", []))
            for f in new_files - old_files:
                changes.append({
                    "timestamp": now_str, "type": "new_file",
                    "path": os.path.join(path, f), "alerted": False
                })
            for f in old_files - new_files:
                changes.append({
                    "timestamp": now_str, "type": "deleted_file",
                    "path": os.path.join(path, f), "alerted": False
                })

    for path in old_snapshot:
        if path not in new_snapshot:
            changes.append({"timestamp": now_str, "type": "removed_dir", "path": path, "alerted": False})

    all_changes = old_data.get("changes", []) + changes
    all_changes = all_changes[-500:]

    result = {
        "_note": old_data.get("_note", ""),
        "last_scan": now_str,
        "scan_paths": [HOME],
        "snapshot": new_snapshot,
        "changes": all_changes
    }
    atomic_json_write(FS_LOG, result)
    return result


if __name__ == "__main__":
    print("[Lennox] Running on-demand filesystem scan...")
    result = run_scan()
    print(f"[Lennox] Scan complete — {len(result['snapshot'])} directories tracked.")
