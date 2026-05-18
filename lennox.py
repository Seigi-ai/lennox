"""
lennox.py — Lennox personal Linux assistant
Plain English in → action out → log everything → update memory
"""

import os
import sys
import json
import time
import signal
import shutil
import socket
import struct
import atexit
import datetime
import subprocess
import threading
import fcntl

# ─── Paths ────────────────────────────────────────────────────────────────────

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
MEMORY_DIR = os.path.join(BASE_DIR, "memory")
LOG_DIR    = os.path.join(BASE_DIR, "logs")

PROFILE_FILE    = os.path.join(MEMORY_DIR, "profile.json")
APP_HISTORY     = os.path.join(MEMORY_DIR, "app_history.json")
ERROR_PATTERNS  = os.path.join(MEMORY_DIR, "error_patterns.json")
FS_LOG          = os.path.join(MEMORY_DIR, "filesystem_log.json")
ACTIVITY_LOG    = os.path.join(LOG_DIR,    "activity.json")

LLAMA_SERVER_PORT = 8080
LLAMA_SERVER_URL  = f"http://localhost:{LLAMA_SERVER_PORT}"
SERVER_PROCESS    = None

LLAMA_BIN_PATHS = [
    "~/llama.cpp/llama-server",
    "~/llama.cpp/build/bin/llama-server",
    "~/llama.cpp/build/llama-server",
    "/usr/local/bin/llama-server",
    "/usr/bin/llama-server",
]

DRY_RUN = "--dry-run" in sys.argv

# ─── Lazy import requests ─────────────────────────────────────────────────────

try:
    import requests as _requests
except ImportError:
    # Install into the venv that's running this script, not system Python
    venv_pip = os.path.join(os.path.dirname(os.path.abspath(__file__)), "venv", "bin", "pip")
    installer = venv_pip if os.path.isfile(venv_pip) else sys.executable + " -m pip"
    subprocess.run([venv_pip, "install", "requests"], check=False)
    import requests as _requests

requests = _requests

# ─── Atomic JSON helpers ──────────────────────────────────────────────────────

def atomic_json_write(path: str, data: dict):
    """Thread-safe, atomic JSON write using fcntl locking."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    os.replace(tmp_path, path)


def load_json_locked(path: str, default: dict) -> dict:
    """Thread-safe JSON read."""
    try:
        with open(path) as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_SH)
            data = json.load(f)
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            return data
    except (FileNotFoundError, json.JSONDecodeError):
        return default


# ─── System prompt ────────────────────────────────────────────────────────────

def build_system_prompt() -> str:
    home = os.path.expanduser("~")
    return f"""You are Lennox, a personal Linux assistant for a non-technical user.

PERSONALITY:
- Speak in plain simple English, never use technical jargon
- Be brief — one or two sentences is enough for most responses
- Sound like a helpful friend, not a manual
- BEFORE executing any destructive action (delete, remove, overwrite, format, rm, apt remove, etc.),
  ALWAYS warn the user clearly in your reply and explain what will happen and why

EXECUTION:
- Execute tasks immediately without asking for confirmation (except destructive ones — warn first)
- After every action write a plain English summary to {ACTIVITY_LOG}
  under the "actions" array with these fields:
    timestamp, description, command_run, success (true/false), output_summary
- If the user says "undo", "reverse", or "override":
    stop immediately, reverse the last logged action in {ACTIVITY_LOG},
    and write the reversal to {ACTIVITY_LOG} under "overrides"
- If DRY_RUN is true, only describe what you would do — do not execute anything

MEMORY — read before acting, update after acting:
- {PROFILE_FILE}
    Read this first for system info, hardware limits, and user preferences
    Update when you learn something new about the user or their system
- {APP_HISTORY}
    Read before installing anything — check if it was tried before and what happened
    Update after every install attempt with path used, success/fail, and any fixes needed
- {ERROR_PATTERNS}
    Read when you hit an error — check if this exact error was seen before and fixed
    Update when you fix a new error so next time is instant
- {FS_LOG}
    Read when user asks about files, folders, or anything on their machine
    A snapshot of the home directory is stored in this file under "snapshot"
    Recent changes are in the "changes" array

FILESYSTEM:
- The user's home directory is {home}
- A full snapshot of the current home directory structure is stored in
  {FS_LOG} under "snapshot" — use this when the user asks about their files

RESPONSE FORMAT:
- Always respond with a JSON object, never plain text
- Schema:
  {{
    "reply": "plain English message to show the user",
    "action": {{
      "type": "apt_install | wine_install | apt_remove | wine_remove | shell | file_op | answer | none",
      "command": ["actual", "shell", "command", "as", "array"],
      "app_name": "app name if installing or removing",
      "description": "one line description of what this action does"
    }},
    "memory_updates": {{
      "profile": {{}},
      "app_history": {{}},
      "error_patterns": []
    }},
    "is_override": false
  }}
- If no action is needed set action.type to "answer" and leave command empty
- memory_updates should only contain fields that actually changed"""


# ─── Memory helpers ───────────────────────────────────────────────────────────

def load_all_memory() -> dict:
    return {
        "profile":        load_json_locked(PROFILE_FILE,   {}),
        "app_history":    load_json_locked(APP_HISTORY,     {"apps": {}}),
        "error_patterns": load_json_locked(ERROR_PATTERNS,  {"patterns": []}),
        "filesystem_log": load_json_locked(FS_LOG,          {"snapshot": {}, "changes": []}),
    }


def apply_memory_updates(updates: dict):
    """Merge model-returned memory updates into the actual memory files."""
    if not updates:
        return

    if updates.get("profile"):
        current = load_json_locked(PROFILE_FILE, {})
        current.update(updates["profile"])
        atomic_json_write(PROFILE_FILE, current)

    if updates.get("app_history"):
        current = load_json_locked(APP_HISTORY, {"apps": {}})
        for app, data in updates["app_history"].items():
            current["apps"][app] = data
        atomic_json_write(APP_HISTORY, current)

    if updates.get("error_patterns"):
        current = load_json_locked(ERROR_PATTERNS, {"patterns": []})
        for pattern in updates["error_patterns"]:
            existing = [p.get("error_signature") for p in current["patterns"]]
            if pattern.get("error_signature") not in existing:
                current["patterns"].append(pattern)
        atomic_json_write(ERROR_PATTERNS, current)


def log_action(description: str, command: list, success: bool, output: str, is_override: bool = False):
    """Append an action or override to the activity log."""
    data = load_json_locked(ACTIVITY_LOG, {"actions": [], "overrides": []})
    entry = {
        "timestamp":      datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "description":    description,
        "command_run":    " ".join(str(c) for c in command) if command else "",
        "success":        success,
        "output_summary": output[:500] if output else ""
    }
    if is_override:
        data["overrides"].append(entry)
    else:
        data["actions"].append(entry)

    data["actions"]   = data["actions"][-1000:]
    data["overrides"] = data["overrides"][-200:]
    atomic_json_write(ACTIVITY_LOG, data)


def get_last_action() -> dict | None:
    data = load_json_locked(ACTIVITY_LOG, {"actions": [], "overrides": []})
    actions = data.get("actions", [])
    return actions[-1] if actions else None


# ─── Live status ───────────────────────────────────────────────────────────────

def write_status(state: str, command: list = None, output: str = ""):
    """Write current execution state for the dashboard to read."""
    status = {
        "timestamp": datetime.datetime.now().isoformat(),
        "state": state,
        "command": command or [],
        "output_tail": output[-300:] if output else "",
        "pid": os.getpid()
    }
    tmp = os.path.join(LOG_DIR, ".status_tmp.json")
    final = os.path.join(LOG_DIR, "status.json")
    with open(tmp, "w") as f:
        json.dump(status, f, indent=2)
    os.replace(tmp, final)


# ─── Heartbeat ────────────────────────────────────────────────────────────────

def start_heartbeat():
    def beat():
        while True:
            try:
                with open(os.path.join(LOG_DIR, ".heartbeat"), "w") as f:
                    f.write(str(time.time()))
            except Exception:
                pass
            time.sleep(30)
    t = threading.Thread(target=beat, daemon=True, name="heartbeat")
    t.start()


# ─── System profile builder ───────────────────────────────────────────────────

def build_profile():
    """Gather system info and write to profile.json on first boot."""
    profile = load_json_locked(PROFILE_FILE, {})

    if profile.get("system", {}).get("os"):
        return

    print("[Lennox] Building system profile...", flush=True)

    def run(cmd: list) -> str:
        try:
            return subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL).strip()
        except Exception:
            return ""

    ram_gb = 0
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if "MemTotal" in line:
                    ram_gb = int(line.split()[1]) / (1024 ** 2)
                    break
    except Exception:
        pass

    disk_gb = 0
    try:
        stat    = os.statvfs(os.path.expanduser("~"))
        disk_gb = (stat.f_blocks * stat.f_frsize) / (1024 ** 3)
    except Exception:
        pass

    profile = {
        "_note": "This file is in JSON format. Ask Lennox to convert it to plain English if you don't understand it.",
        "user": {
            "username": os.environ.get("USER", ""),
            "home_dir": os.path.expanduser("~"),
            "shell":    os.environ.get("SHELL", ""),
            "language": os.environ.get("LANG", "en")[:2]
        },
        "system": {
            "os":         run(["lsb_release", "-ds"]) or "Linux",
            "os_version": run(["lsb_release", "-rs"]),
            "cpu":        run(["grep", "-m1", "model name", "/proc/cpuinfo"]).split(":")[-1].strip(),
            "ram_gb":     round(ram_gb, 1),
            "disk_gb":    round(disk_gb, 1),
            "gpu":        run(["lspci"]).split("\n")[0] if shutil.which("lspci") else "unknown",
            "display":    os.environ.get("DISPLAY", ":0")
        },
        "preferences": {
            "inferred": [],
            "explicit": []
        },
        "hardware_limits": {
            "can_run_wine64":             ram_gb >= 2,
            "recommended_model_size_gb":  round(ram_gb * 0.6, 1)
        },
        "last_updated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

    atomic_json_write(PROFILE_FILE, profile)
    print("[Lennox] Profile saved.", flush=True)


# ─── llama.cpp server ──────────────────────────────────────────────────────────

def find_llama_server() -> str | None:
    which = shutil.which("llama-server")
    if which:
        return which
    for path in LLAMA_BIN_PATHS:
        expanded = os.path.expanduser(path)
        if os.path.isfile(expanded) and os.access(expanded, os.X_OK):
            return expanded
    return None


def validate_gguf(path: str) -> bool:
    """True if file is a valid, loadable GGUF model."""
    try:
        with open(path, "rb") as f:
            if f.read(4) != b"GGUF":
                return False
            version = struct.unpack("<I", f.read(4))[0]
            if version not in (2, 3):
                print(f"[Lennox] Warning: {path} has unsupported GGUF version {version}")
                return False
            tensor_count = struct.unpack("<Q", f.read(8))[0]
            if tensor_count == 0:
                return False
        return True
    except Exception:
        return False


def find_models() -> list:
    found = []
    home  = os.path.expanduser("~")
    for root, dirs, files in os.walk(home):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("proc","sys","dev","snap")]
        for f in files:
            if f.endswith(".gguf"):
                full = os.path.abspath(os.path.join(root, f))
                if validate_gguf(full):
                    found.append(full)
    return sorted(set(found))


def pick_model(models: list) -> str:
    if not models:
        print("[Lennox] No .gguf models found.")
        path = input("         Enter full path to model: ").strip()
        if not os.path.isfile(path):
            print("[Lennox] File not found. Exiting.")
            sys.exit(1)
        if not validate_gguf(path):
            print("[Lennox] That file does not appear to be a valid GGUF model.")
            sys.exit(1)
        return path

    print("\n[Lennox] Available models:\n")
    for i, path in enumerate(models, 1):
        size_gb = os.path.getsize(path) / (1024 ** 3)
        print(f"  [{i}] {os.path.basename(path)}  ({size_gb:.1f} GB)")
        print(f"      {path}")
    print()

    while True:
        raw = input(f"Pick a model [1-{len(models)}]: ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(models):
            return models[int(raw) - 1]
        print("  Invalid choice.")


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def wait_for_server(timeout: int = 60) -> bool:
    print("[Lennox] Waiting for model to load", end="", flush=True)
    start = time.time()
    while time.time() - start < timeout:
        try:
            r = requests.get(f"{LLAMA_SERVER_URL}/health", timeout=2)
            if r.status_code == 200:
                print(" ready!")
                return True
        except Exception:
            pass
        print(".", end="", flush=True)
        time.sleep(1)
    print(" timed out.")
    return False


def stop_server():
    global SERVER_PROCESS
    if SERVER_PROCESS and SERVER_PROCESS.poll() is None:
        SERVER_PROCESS.send_signal(signal.SIGTERM)
        try:
            SERVER_PROCESS.wait(timeout=5)
        except subprocess.TimeoutExpired:
            SERVER_PROCESS.kill()
            SERVER_PROCESS.wait()


def start_server(model_path: str):
    global SERVER_PROCESS

    if port_in_use(LLAMA_SERVER_PORT):
        print(f"[Lennox] Model server already running on port {LLAMA_SERVER_PORT}.")
        return

    binary = find_llama_server()
    if not binary:
        print("[Lennox] llama-server not found.")
        print("         Build it: cd ~/llama.cpp && make llama-server")
        sys.exit(1)

    print(f"[Lennox] Loading model: {os.path.basename(model_path)}")

    SERVER_PROCESS = subprocess.Popen(
        [
            binary,
            "-m",                  model_path,
            "--port",              str(LLAMA_SERVER_PORT),
            "--host",              "127.0.0.1",
            "-c",                  "8192",
            "-n",                  "1024",
            "--reasoning-budget",  "0",
            "-ngl",                "0",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

    atexit.register(stop_server)

    if not wait_for_server():
        print("[Lennox] Model failed to load. Check the model file.")
        sys.exit(1)


# ─── Model call ───────────────────────────────────────────────────────────────

def call_model(user_input: str, memory: dict) -> dict | None:
    """Send user input + memory context to the model. Returns parsed JSON."""

    fs_log   = memory.get("filesystem_log", {})
    snapshot = fs_log.get("snapshot", {})
    recent_changes = fs_log.get("changes", [])[-20:]

    memory_context = {
        "profile":        memory.get("profile", {}),
        "app_history":    memory.get("app_history", {}).get("apps", {}),
        "error_patterns": memory.get("error_patterns", {}).get("patterns", [])[-10:],
        "filesystem": {
            "last_scan":      fs_log.get("last_scan", ""),
            "directory_count": len(snapshot),
            "recent_changes": recent_changes
        }
    }

    messages = [
        {
            "role":    "system",
            "content": build_system_prompt()
        },
        {
            "role":    "user",
            "content": f"MEMORY CONTEXT:\n{json.dumps(memory_context, indent=2)}\n\nUSER REQUEST:\n{user_input}"
        }
    ]

    try:
        resp = requests.post(
            f"{LLAMA_SERVER_URL}/v1/chat/completions",
            json={
                "messages":    messages,
                "temperature": 0.1,
                "max_tokens":  1024,
                "stream":      False
            },
            timeout=120
        )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"[Lennox] Model error: {e}")
        return None

    if raw.startswith("```"):
        parts = raw.split("```")
        raw   = parts[1] if len(parts) > 1 else raw
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        print(f"[Lennox] Model returned invalid JSON:\n{raw}")
        return None


# ─── Action executor ──────────────────────────────────────────────────────────

def run_command(cmd: list, env_extra: dict = None) -> tuple[bool, str]:
    """Run a shell command. Returns (success, output)."""
    env = {**os.environ, **(env_extra or {})}
    try:
        result = subprocess.run(
            cmd, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=300
        )
        output  = (result.stdout + result.stderr).strip()
        success = result.returncode == 0
        return success, output
    except subprocess.TimeoutExpired:
        return False, "command timed out after 5 minutes"
    except Exception as e:
        return False, str(e)


def run_interactive(cmd: list, env_extra: dict = None) -> tuple[bool, str]:
    """
    Run a command interactively — output goes directly to terminal
    so sudo password prompts and apt progress are visible to the user.
    Returns (success, brief_summary).
    """
    env = {**os.environ, **(env_extra or {})}
    try:
        result = subprocess.run(
            cmd, env=env,
            text=True, timeout=300
        )
        success = result.returncode == 0
        summary = "completed successfully" if success else f"exited with code {result.returncode}"
        return success, summary
    except subprocess.TimeoutExpired:
        return False, "command timed out after 5 minutes"
    except Exception as e:
        return False, str(e)


def execute_action(action: dict, reply: str) -> tuple[bool, str]:
    """
    Execute whatever action the model decided on.
    Returns (success, output_summary).
    """
    action_type = action.get("type", "none")
    command     = action.get("command", [])
    app_name    = action.get("app_name", "")

    if DRY_RUN:
        print(f"[DRY RUN] Would execute: {' '.join(str(c) for c in command)}")
        return True, "dry run — no changes made"

    if action_type == "answer" or action_type == "none":
        return True, ""

    # Write status before executing
    write_status("running", command)

    if action_type == "apt_install":
        success, output = run_interactive(["sudo", "apt", "install", "-y", app_name or command[-1]])
        if not success and app_name:
            print(f"[Lennox] apt failed for {app_name} — trying Wine path...")
            success, output = run_wine_install(app_name)
        write_status("idle" if success else "error", command, output)
        return success, output

    if action_type == "wine_install":
        result = run_wine_install(app_name or (command[-1] if command else ""))
        write_status("idle" if result[0] else "error", command, result[1])
        return result

    if action_type == "apt_remove":
        result = run_interactive(["sudo", "apt", "remove", "-y", app_name or command[-1]])
        write_status("idle" if result[0] else "error", command, result[1])
        return result

    if action_type == "wine_remove":
        prefix = os.path.expanduser(f"~/.wine_{(app_name or '').lower().replace(' ', '_')}")
        if os.path.exists(prefix):
            result = run_command(["rm", "-rf", prefix])
            write_status("idle" if result[0] else "error", command, result[1])
            return result
        return False, f"no Wine prefix found for {app_name}"

    if action_type == "shell":
        if not command:
            return False, "no command provided"
        result = run_command(command)
        write_status("idle" if result[0] else "error", command, result[1])
        return result

    if action_type == "file_op":
        if not command:
            return False, "no command provided"
        result = run_command(command)
        write_status("idle" if result[0] else "error", command, result[1])
        return result

    write_status("idle")
    return False, f"unknown action type: {action_type}"


def run_wine_install(app_name: str) -> tuple[bool, str]:
    """Delegate to wine_installer.py with --app argument."""
    wine_installer = os.path.join(BASE_DIR, "wine_installer.py")
    if not os.path.isfile(wine_installer):
        return False, "wine_installer.py not found"
    success, output = run_command([sys.executable, wine_installer, "--app", app_name])
    return success, output


def handle_override(memory: dict):
    """Reverse the last action."""
    last = get_last_action()
    if not last:
        print("Lennox: Nothing to undo.")
        return

    print(f"Lennox: Undoing — {last['description']}")

    result = call_model(
        f"Undo this action: {json.dumps(last)}",
        memory
    )
    if result:
        action  = result.get("action", {})
        success, output = execute_action(action, result.get("reply", ""))
        log_action(
            description=f"OVERRIDE: {last['description']}",
            command=action.get("command", []),
            success=success,
            output=output,
            is_override=True
        )
        print(f"Lennox: {result.get('reply', 'Done.')}")
    else:
        print("Lennox: Could not figure out how to undo that.")


# ─── Main loop ────────────────────────────────────────────────────────────────

OVERRIDE_WORDS = {"undo", "reverse", "override", "revert", "rollback"}


def is_override_request(text: str) -> bool:
    words = set(text.lower().split())
    return bool(words & OVERRIDE_WORDS)


def main():
    print("╔══════════════════════════════════════════╗")
    print("║              Lennox  v0.1                ║")
    print("║   Your personal Linux assistant          ║")
    print("╚══════════════════════════════════════════╝")

    if DRY_RUN:
        print("[Lennox] Running in DRY RUN mode — no changes will be made.\n")

    os.makedirs(LOG_DIR, exist_ok=True)
    os.makedirs(MEMORY_DIR, exist_ok=True)

    build_profile()
    start_heartbeat()

    # On-demand filesystem scan at startup only
    from watcher import run_scan
    print("[Lennox] Scanning filesystem...", flush=True)
    run_scan()
    print("[Lennox] Scan complete.", flush=True)

    models     = find_models()
    model_path = pick_model(models)
    start_server(model_path)

    print("\nLennox is ready. Type what you need or 'quit' to exit.\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[Lennox] Goodbye.")
            break

        if not user_input:
            continue

        if user_input.lower() in ("quit", "exit", "bye"):
            print("Lennox: Goodbye!")
            break

        memory = load_all_memory()

        if is_override_request(user_input):
            handle_override(memory)
            continue

        result = call_model(user_input, memory)
        if result is None:
            print("Lennox: Something went wrong with the model. Try again.")
            continue

        reply  = result.get("reply", "Done.")
        action = result.get("action", {})

        print(f"\nLennox: {reply} \n")

        if action.get("type") not in ("answer", "none", None):
            success, output = execute_action(action, reply)

            log_action(
                description=action.get("description", user_input),
                command=action.get("command", []),
                success=success,
                output=output
            )

            # Self-write memory after every action — don't rely solely on model returning memory_updates
            app_name = action.get("app_name", "")
            if app_name and action.get("type") in ("apt_install", "wine_install"):
                current = load_json_locked(APP_HISTORY, {"apps": {}})
                current["apps"][app_name.lower()] = {
                    "path":         action.get("type"),
                    "status":       "success" if success else "failed",
                    "installed_on": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "output":       output[:300]
                }
                atomic_json_write(APP_HISTORY, current)

            if not success and output:
                print(f"[Lennox] Something went wrong — asking model to fix it...")
                for _ in range(10):
                    try:
                        r = requests.get(f"{LLAMA_SERVER_URL}/health", timeout=2)
                        if r.status_code == 200:
                            break
                    except Exception:
                        pass
                    time.sleep(2)
                memory = load_all_memory()
                fix_result = call_model(
                    f"The action failed. Error: {output[:1000]}\nOriginal request: {user_input}",
                    memory
                )
                if fix_result:
                    fix_reply  = fix_result.get("reply", "")
                    fix_action = fix_result.get("action", {})
                    print(f"Lennox: {fix_reply}\n")
                    if fix_action.get("type") not in ("answer", "none", None):
                        fix_success, fix_output = execute_action(fix_action, fix_reply)
                        log_action(
                            description=f"FIX: {fix_action.get('description', '')}",
                            command=fix_action.get("command", []),
                            success=fix_success,
                            output=fix_output
                        )
                        apply_memory_updates(fix_result.get("memory_updates", {}))
                        if not fix_success:
                            print(f"Lennox: I tried but couldn't fix it. Here's what happened: {fix_output[:200]}")

        apply_memory_updates(result.get("memory_updates", {}))

    stop_server()


if __name__ == "__main__":
    main()
