import subprocess
import sys
import os
import shutil
import glob
import time
import signal
import atexit
import socket
import json
import argparse

try:
    import requests
except ImportError:
    requests = None

# ─── Config ───────────────────────────────────────────────────────────────────

LLAMA_SERVER_PORT = 8080
LLAMA_SERVER_URL  = f"http://localhost:{LLAMA_SERVER_PORT}"
SERVER_PROCESS    = None
LLAMA_SERVER_BIN  = None

LLAMA_BIN_SEARCH_PATHS = [
    "~/llama.cpp/llama-server",
    "~/llama.cpp/build/bin/llama-server",
    "~/llama.cpp/build/llama-server",
    "/usr/local/bin/llama-server",
    "/usr/bin/llama-server",
]

# ─── Dependency handling ───────────────────────────────────────────────────────

APT_PACKAGES = {
    "wine":       "wine",
    "winetricks": "winetricks",
    "wget":       "wget",
    "curl":       "curl",
    "cabextract": "cabextract",
    "unzip":      "unzip",
    "7z":         "p7zip-full",
    "Xvfb":       "xvfb",
    "winbind":    "winbind",
    "locate":     "mlocate",
}

PIP_PACKAGES = ["requests"]


def install_apt(package_name: str) -> bool:
    print(f"  [apt] Installing {package_name}...")
    result = subprocess.run(
        ["sudo", "apt", "install", "-y", package_name],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    if result.returncode != 0:
        print(f"  [!] Failed to install {package_name}:")
        print(result.stderr.strip())
        return False
    print(f"  [✓] {package_name} installed.")
    return True


def install_pip(package_name: str) -> bool:
    print(f"  [pip] Installing {package_name} into current environment...")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", package_name],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    if result.returncode != 0:
        print(f"  [!] Failed to install {package_name}:")
        print(result.stderr.strip())
        return False
    print(f"  [✓] {package_name} installed.")
    return True


def check_and_install_dependencies():
    print("\n[*] Checking dependencies...\n")

    for pkg in PIP_PACKAGES:
        try:
            __import__(pkg)
            print(f"  [✓] Python : {pkg}")
        except ImportError:
            print(f"  [✗] Python : {pkg} missing — installing...")
            if install_pip(pkg):
                import importlib
                globals()[pkg] = importlib.import_module(pkg)

    global requests
    if requests is None:
        try:
            import requests as _requests
            requests = _requests
        except ImportError:
            print("[!] Could not import requests after install. Exiting.")
            sys.exit(1)

    for cmd, apt_pkg in APT_PACKAGES.items():
        if shutil.which(cmd):
            print(f"  [✓] System : {cmd}")
        else:
            print(f"  [✗] System : {cmd} missing — installing...")
            install_apt(apt_pkg)

    if not os.environ.get("DISPLAY"):
        print("  [!] $DISPLAY not set — starting Xvfb on :0")
        install_apt("xvfb")
        subprocess.Popen(
            ["Xvfb", ":0", "-screen", "0", "1024x768x24"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        os.environ["DISPLAY"] = ":0"
        time.sleep(1)
        print("  [✓] Xvfb started on :0")
    else:
        print(f"  [✓] DISPLAY : {os.environ['DISPLAY']}")

    print("\n[✓] Dependency check complete.\n")


# ─── Model scanning ────────────────────────────────────────────────────────────

def scan_for_models() -> list:
    found = []
    home  = os.path.expanduser("~")

    print("[*] Scanning for .gguf models (this may take a moment)...")

    for root, dirs, files in os.walk(home):
        dirs[:] = [
            d for d in dirs
            if not d.startswith(".")
            and d not in ("proc", "sys", "dev", "run", "snap", "boot", "tmp")
        ]
        for file in files:
            if file.endswith(".gguf"):
                found.append(os.path.abspath(os.path.join(root, file)))

    if shutil.which("locate"):
        result = subprocess.run(
            ["locate", "--existing", "*.gguf"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True
        )
        for line in result.stdout.strip().splitlines():
            line = line.strip()
            if line and os.path.exists(line):
                found.append(line)

    deduplicated = sorted(set(found))
    print(f"[*] Found {len(deduplicated)} model(s).\n")
    return deduplicated


def get_free_ram_gb() -> float | None:
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if "MemAvailable" in line:
                    kb = int(line.split()[1])
                    return kb / (1024 ** 2)
    except Exception:
        pass
    return None


def pick_model(models: list) -> str:
    free_gb = get_free_ram_gb()

    if not models:
        print("[!] No .gguf models found anywhere under home or via locate.")
        manual = input("    Enter full path to your model file: ").strip()
        if not os.path.isfile(manual):
            print("[!] File not found. Exiting.")
            sys.exit(1)
        return manual

    print("[*] Available models:\n")
    for i, path in enumerate(models, 1):
        size_gb = os.path.getsize(path) / (1024 ** 3)
        name    = os.path.basename(path)
        warn    = ""
        if free_gb is not None and size_gb > free_gb:
            warn = f"  ⚠  exceeds available RAM ({free_gb:.1f} GB free)"
        print(f"  [{i}] {name}  ({size_gb:.1f} GB){warn}")
        print(f"      {path}")

    print()
    while True:
        raw = input(f"Pick a model [1-{len(models)}]: ").strip()
        if raw.isdigit():
            idx = int(raw)
            if 1 <= idx <= len(models):
                chosen    = models[idx - 1]
                size_gb   = os.path.getsize(chosen) / (1024 ** 3)
                if free_gb is not None and size_gb > free_gb:
                    print(f"\n  [!] Warning: model is {size_gb:.1f} GB but only {free_gb:.1f} GB RAM free.")
                    if input("      Continue anyway? [y/N]: ").strip().lower() != "y":
                        continue
                return chosen
        print("  Invalid choice, try again.")


# ─── llama.cpp server ──────────────────────────────────────────────────────────

def find_llama_server() -> str | None:
    on_path = shutil.which("llama-server")
    if on_path:
        return on_path
    for path in LLAMA_BIN_SEARCH_PATHS:
        expanded = os.path.expanduser(path)
        if os.path.isfile(expanded) and os.access(expanded, os.X_OK):
            return expanded
    return None


def check_port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def wait_for_server(timeout: int = 60) -> bool:
    print("  [*] Waiting for server to be ready", end="", flush=True)
    start = time.time()
    while time.time() - start < timeout:
        try:
            resp = requests.get(f"{LLAMA_SERVER_URL}/health", timeout=2)
            if resp.status_code == 200:
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
        print("\n[*] Stopping llama.cpp server...")
        SERVER_PROCESS.send_signal(signal.SIGTERM)
        try:
            SERVER_PROCESS.wait(timeout=5)
        except subprocess.TimeoutExpired:
            print("  [!] SIGTERM timed out — force killing...")
            SERVER_PROCESS.kill()
            SERVER_PROCESS.wait()
        print("[✓] Server stopped.")


def _handle_sigint(sig, frame):
    print("\n\n[!] Interrupted.")
    stop_server()
    sys.exit(0)


def start_llama_server(model_path: str):
    global SERVER_PROCESS, LLAMA_SERVER_BIN

    if check_port_in_use(LLAMA_SERVER_PORT):
        print(f"[*] Port {LLAMA_SERVER_PORT} already occupied — reusing existing server.\n")
        return

    LLAMA_SERVER_BIN = find_llama_server()
    if not LLAMA_SERVER_BIN:
        print("[!] llama-server binary not found.")
        print("    Build it:  cd ~/llama.cpp && make llama-server")
        print("    Or:        cd ~/llama.cpp && cmake -B build && cmake --build build --target llama-server")
        sys.exit(1)

    print(f"\n[*] Starting llama.cpp server...")
    print(f"    Binary : {LLAMA_SERVER_BIN}")
    print(f"    Model  : {os.path.basename(model_path)}")
    print(f"    Port   : {LLAMA_SERVER_PORT}\n")

    cmd = [
        LLAMA_SERVER_BIN,
        "-m",                   model_path,
        "--port",               str(LLAMA_SERVER_PORT),
        "--host",               "127.0.0.1",
        "-c",                   "4096",
        "-n",                   "1024",
        "--reasoning-budget",   "0",
        "-ngl",                 "0",
    ]

    SERVER_PROCESS = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

    atexit.register(stop_server)
    signal.signal(signal.SIGINT, _handle_sigint)
    signal.signal(signal.SIGTERM, _handle_sigint)

    if not wait_for_server(timeout=60):
        print("[!] Server failed to become ready.")
        print("    Check that the model path is valid and you have enough RAM.")
        stop_server()
        sys.exit(1)

    print(f"[✓] llama.cpp server running (PID {SERVER_PROCESS.pid})\n")


# ─── Model prompting ───────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a Wine (Windows compatibility layer) expert on Linux.
When given a Windows application name respond ONLY with a valid JSON object.
No markdown fences. No explanation. No preamble. Raw JSON only.

Required schema:
{
  "app": "application name",
  "winearch": "win32 or win64",
  "winetricks": ["list", "of", "verbs"],
  "env": {"OPTIONAL_KEY": "OPTIONAL_VALUE"},
  "steps": [
    {
      "description": "human readable description of this step",
      "cmd": ["command", "arg1", "arg2"]
    }
  ],
  "notes": "any important caveats or post-install tips"
}

Rules:
- winetricks verbs examples: vcrun2019, dotnet48, d3dx9, corefonts, msxml6, etc.
- always isolate using WINEPREFIX=~/.wine_<appname_no_spaces>
- if a .exe must be downloaded first include a wget step before the wine step
- steps must be real executable shell commands
- return ONLY the JSON object, nothing else"""


def _call_server(messages: list) -> str:
    payload = {
        "messages":    messages,
        "temperature": 0.1,
        "max_tokens":  1024,
        "stream":      False,
    }
    resp = requests.post(
        f"{LLAMA_SERVER_URL}/v1/chat/completions",
        json=payload,
        timeout=120
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


def _parse_json(raw: str) -> dict | None:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        parts   = cleaned.split("```")
        cleaned = parts[1] if len(parts) > 1 else cleaned
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
    cleaned = cleaned.strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return None


def query_model(app_name: str) -> dict:
    print(f"[*] Querying model for: {app_name}")
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": f"How do I install {app_name} using Wine on Linux?"}
    ]
    try:
        raw = _call_server(messages)
    except requests.exceptions.ConnectionError:
        print("[!] Cannot reach llama.cpp server.")
        sys.exit(1)
    except Exception as e:
        print(f"[!] Query failed: {e}")
        sys.exit(1)

    result = _parse_json(raw)
    if result is None:
        print("[!] Model returned invalid JSON:")
        print(raw)
        sys.exit(1)
    return result


def query_fix(failed_cmd: list, error_output: str, app_name: str) -> dict | None:
    prompt = f"""A Wine installation step for "{app_name}" failed.

Failed command: {' '.join(str(c) for c in failed_cmd)}

Error output (last 3000 chars):
{error_output[-3000:]}

Respond ONLY with a JSON object, no markdown, no explanation:
{{
  "diagnosis": "short explanation of what went wrong",
  "fix_steps": [
    {{
      "description": "what this fix does",
      "cmd": ["command", "arg1"]
    }}
  ],
  "skip": false
}}

If the error is non-fatal and safe to ignore set skip to true and leave fix_steps empty.
Return ONLY the JSON object."""

    print("\n[*] Sending error to model for diagnosis...")
    try:
        raw = _call_server([{"role": "user", "content": prompt}])
    except requests.exceptions.ConnectionError:
        print("[!] Lost connection to llama.cpp server.")
        return None
    except Exception as e:
        print(f"[!] Fix query failed: {e}")
        return None

    result = _parse_json(raw)
    if result is None:
        print("[!] Model returned invalid fix JSON — skipping auto-fix.")
    return result


# ─── Plan display ──────────────────────────────────────────────────────────────

def show_plan(plan: dict):
    steps   = plan.get("steps", [])
    tricks  = plan.get("winetricks", [])
    env     = plan.get("env", {})

    print("\n" + "=" * 58)
    print(f"  App        : {plan.get('app', '?')}")
    print(f"  WINEARCH   : {plan.get('winearch', 'win64')}")
    print(f"  Winetricks : {' '.join(tricks) if tricks else 'none'}")
    if env:
        for k, v in env.items():
            print(f"  Env        : {k}={v}")
    print(f"\n  Steps ({len(steps)}):")
    for i, step in enumerate(steps, 1):
        print(f"\n    {i}. {step.get('description', '?')}")
        print(f"       $ {' '.join(str(c) for c in step.get('cmd', []))}")
    if plan.get("notes"):
        print(f"\n  Notes: {plan['notes']}")
    print("=" * 58 + "\n")


# ─── Winetricks ────────────────────────────────────────────────────────────────

def run_winetricks(verbs: list, env_extra: dict, app_name: str):
    if not verbs:
        return

    env = {**os.environ, **env_extra}
    cmd = ["winetricks", "--unattended"] + verbs

    print(f"[>] Running winetricks: {' '.join(verbs)}")

    result = subprocess.run(
        cmd, env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    if result.stdout.strip():
        print(result.stdout.strip())
    if result.stderr.strip():
        print(result.stderr.strip())

    if result.returncode == 0:
        print("[✓] Winetricks done.\n")
        return

    print(f"\n[!] Winetricks failed (exit {result.returncode})")
    error_output = (result.stderr or "") + (result.stdout or "")
    fix = query_fix(cmd, error_output, app_name)

    if fix is None:
        print("[!] No fix available from model. Continuing.")
        return

    print(f"\n  [i] Diagnosis: {fix.get('diagnosis', '?')}")

    if fix.get("skip"):
        print("[~] Model says non-fatal. Continuing.\n")
        return

    fix_steps = fix.get("fix_steps", [])
    if not fix_steps:
        print("[!] Model provided no fix steps. Continuing.")
        return

    print("\n  Proposed fix:")
    for i, fs in enumerate(fix_steps, 1):
        print(f"    {i}. {fs.get('description', '?')}")
        print(f"       $ {' '.join(str(c) for c in fs.get('cmd', []))}")

    if input("\n  Apply fix? [y/N]: ").strip().lower() != "y":
        return

    for fs in fix_steps:
        print(f"\n  [fix] $ {' '.join(str(c) for c in fs['cmd'])}")
        subprocess.run(fs["cmd"], env=env)

    print("\n[*] Retrying winetricks after fix...")
    retry = subprocess.run(
        cmd, env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    if retry.stdout.strip():
        print(retry.stdout.strip())
    if retry.returncode == 0:
        print("[✓] Winetricks done.\n")
    else:
        print("[!] Winetricks still failing after fix. Continuing anyway.\n")


# ─── Step runner ───────────────────────────────────────────────────────────────

def run_step(step: dict, env_extra: dict, app_name: str, max_retries: int = 2):
    cmd  = step.get("cmd", [])
    desc = step.get("description", "?")

    if not cmd:
        print(f"[!] Step has no command: {desc} — skipping.")
        return

    print(f"[>] {desc}")
    print(f"    $ {' '.join(str(c) for c in cmd)}")

    env = {**os.environ, **env_extra}

    for attempt in range(1, max_retries + 2):
        result = subprocess.run(
            cmd, env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        if result.stdout.strip():
            print(result.stdout.strip())
        if result.stderr.strip():
            print(result.stderr.strip())

        if result.returncode == 0:
            print("[✓] Step done.\n")
            return

        print(f"\n[!] Step failed (exit {result.returncode})")
        if attempt > max_retries:
            print(f"[!] Still failing after {max_retries} fix attempt(s).")
            if input("    Force continue? [y/N]: ").strip().lower() != "y":
                print("[x] Aborted.")
                sys.exit(1)
            return

        error_output = (result.stderr or "") + (result.stdout or "")
        fix = query_fix(cmd, error_output, app_name)

        if fix is None:
            if input("    No fix available. Continue anyway? [y/N]: ").strip().lower() != "y":
                print("[x] Aborted.")
                sys.exit(1)
            return

        print(f"\n  [i] Diagnosis: {fix.get('diagnosis', '?')}")

        if fix.get("skip"):
            print("[~] Model says non-fatal. Skipping step.\n")
            return

        fix_steps = fix.get("fix_steps", [])
        if not fix_steps:
            if input("    Model had no fix steps. Continue anyway? [y/N]: ").strip().lower() != "y":
                print("[x] Aborted.")
                sys.exit(1)
            return

        print(f"\n  Proposed fix (attempt {attempt} of {max_retries}):")
        for i, fs in enumerate(fix_steps, 1):
            print(f"    {i}. {fs.get('description', '?')}")
            print(f"       $ {' '.join(str(c) for c in fs.get('cmd', []))}")

        if input("\n  Apply fix? [y/N]: ").strip().lower() != "y":
            if input("  Skip this step instead? [y/N]: ").strip().lower() == "y":
                print("[~] Step skipped.\n")
                return
            print("[x] Aborted.")
            sys.exit(1)

        for fs in fix_steps:
            print(f"\n  [fix] $ {' '.join(str(c) for c in fs['cmd'])}")
            subprocess.run(fs["cmd"], env=env)

        print(f"\n[*] Retrying step (attempt {attempt + 1} of {max_retries + 1})...")


# ─── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Wine AI Installer (llama.cpp)")
    parser.add_argument("--app", type=str, help="Windows application to install (non-interactive mode)")
    args = parser.parse_args()

    print("╔══════════════════════════════════════╗")
    print("║      Wine AI Installer (llama.cpp)   ║")
    print("╚══════════════════════════════════════╝\n")

    # When --app is passed, Lennox is calling us — server already running, no confirmation needed
    called_from_lennox = args.app is not None

    if called_from_lennox:
        app_name = args.app.strip()
        print(f"[*] Installing: {app_name}")
    else:
        check_and_install_dependencies()
        models     = scan_for_models()
        model_path = pick_model(models)
        start_llama_server(model_path)
        app_name   = input("Windows application to install: ").strip()

    if not app_name:
        print("[!] No application name given. Exiting.")
        sys.exit(1)

    plan = query_model(app_name)
    show_plan(plan)

    # Only ask for confirmation in interactive/standalone mode
    if not called_from_lennox:
        if input("Proceed with installation? [y/N]: ").strip().lower() != "y":
            print("Aborted.")
            sys.exit(0)

    prefix_name = app_name.lower().replace(" ", "_")
    wineprefix  = os.path.expanduser(f"~/.wine_{prefix_name}")
    env_extra   = {
        "WINEPREFIX": wineprefix,
        "WINEARCH":   plan.get("winearch", "win64"),
        "DISPLAY":    os.environ.get("DISPLAY", ":0"),
    }
    env_extra.update(plan.get("env", {}))

    print(f"\n[*] WINEPREFIX : {wineprefix}")
    print(f"[*] WINEARCH   : {env_extra['WINEARCH']}\n")

    print("[>] Initialising Wine prefix...")
    init = subprocess.run(
        ["wineboot", "--init"],
        env={**os.environ, **env_extra},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    if init.stdout.strip():
        print(init.stdout.strip())
    if init.returncode == 0:
        print("[✓] Prefix ready.\n")
    else:
        print("[!] wineboot returned non-zero — may still be fine, continuing.\n")

    run_winetricks(plan.get("winetricks", []), env_extra, app_name)

    total = len(plan.get("steps", []))
    for idx, step in enumerate(plan.get("steps", []), 1):
        print(f"── Step {idx}/{total} ──────────────────────────────────")
        run_step(step, env_extra, app_name)

    print("\n" + "=" * 58)
    print("[✓] Installation complete!")
    if plan.get("notes"):
        print(f"[i] Notes: {plan['notes']}")
    print(f"[i] Prefix location: {wineprefix}")
    print("=" * 58)


if __name__ == "__main__":
    main()
