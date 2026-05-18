# Lennox 🤖

A personal Linux assistant that understands plain English, runs fully offline using a local AI model, scans your filesystem on startup, and gets smarter the longer it runs on your machine.

> No API keys. No cloud. No subscription. Just you, your machine, and Lennox.

---

## What Lennox does

- **Understands plain English** — type "install discord" or "free up some disk space" and it figures out what to do
- **Installs apps the right way** — tries apt first, falls back to Wine automatically for Windows-only apps
- **Scans your home directory on startup** — builds a snapshot of your files so it can answer questions about what's on your machine
- **Remembers everything** — keeps a local memory of what worked, what failed, and what you prefer, so it gets better over time
- **Logs everything** — full activity log of every action taken, in JSON format (ask Lennox to explain it in plain English if needed)
- **Undo anything** — just say "undo" or "reverse that" and Lennox reverses the last action
- **Live dashboard** — run `dashboard.py` in a separate terminal to see what Lennox is doing in real time
- **Dry run mode** — run with `--dry-run` to see what Lennox would do without actually doing it

---

## Requirements

- Ubuntu / Linux Mint (or any Debian-based distro)
- Python 3.10+
- [llama.cpp](https://github.com/ggerganov/llama.cpp) built with `llama-server`
- A `.gguf` model file (Gemma 4 E2B Q4_K_M recommended for low-RAM machines)
- `tmux` (installed automatically by the setup script)

---

## Installation

```bash
git clone https://github.com/yourusername/lennox
cd lennox
bash setup/install.sh
```

That's it. The script installs all dependencies, sets up the systemd service to run on boot, and creates a desktop icon.

**First time opening Lennox:**
Click the desktop icon. It will ask you to pick a model from any `.gguf` files it finds on your machine, then it's ready to use.

---

## Usage

Click the **Lennox** icon on your desktop. A terminal window opens connected to the running assistant.

```
You: install discord
Lennox: Discord is installed and ready.

You: install photoshop
Lennox: Photoshop CS6 is set up via Wine and ready to use.

You: what did you do today
Lennox: I installed Discord and Photoshop CS6 for you.

You: undo the photoshop install
Lennox: Photoshop has been removed.

You: what's in my Downloads folder
Lennox: You have 3 files there — setup.exe (450MB), notes.pdf, and vacation.zip.
```

---

## Live Dashboard

Want to see what Lennox is doing in the background? Open a second terminal and run:

```bash
cd ~/lennox
python3 dashboard.py
```

The dashboard shows:
- Whether Lennox is alive (heartbeat)
- Current command being executed
- Live output from that command
- Last 5 actions taken

---

## Dry run mode

Not sure you trust it yet? Run with `--dry-run` and Lennox will tell you exactly what it would do without touching anything:

```bash
python3 lennox.py --dry-run
```

---

## File structure

```
lennox/
    lennox.py               Main assistant
    watcher.py              Filesystem scanner (on-demand, startup only)
    wine_installer.py         Windows app installer via Wine
    dashboard.py              Live status viewer
    memory/
        profile.json        System info and preferences
        app_history.json    Every app installed and how it went
        error_patterns.json Known errors and their fixes
        filesystem_log.json Home directory snapshot and changes
    logs/
        activity.json       Full log of everything Lennox has done
    setup/
        install.sh          Run once to set everything up
        lennox.service      Systemd user service (auto-restart)
        lennox.desktop      Desktop icon entry
        icon.png            App icon
```

> **Note:** The `memory/` and `logs/` directories are gitignored. They contain information about your machine and are never pushed to GitHub. Template versions are included so you know what structure to expect.

---

## How memory works

Lennox keeps four memory files:

- **profile.json** — your system hardware, preferences Lennox has learned about you
- **app_history.json** — every app ever installed, which path worked, what errors came up
- **error_patterns.json** — errors Lennox has seen before and what fixed them
- **filesystem_log.json** — a snapshot of your home directory plus a log of changes detected during scans

These files are in JSON format. If you open one and can't understand it, just ask Lennox to explain it.

---

## Overriding Lennox

Since Lennox autoruns without asking for confirmation, you can always override it:

```
You: undo
You: reverse that
You: override the last thing you did
```

All overrides are logged separately so Lennox can learn from them.

---

## Known limitations

- **Small models make mistakes** — Gemma 4 E2B is fast and lightweight but will occasionally misclassify an app or hallucinate a package name. The error feedback loop catches most of this.
- **Filesystem scan on startup** — the first scan of your home directory takes 30–60 seconds depending on how many files you have. Subsequent boots use the cached snapshot.
- **Wine installs are not guaranteed** — the model does its best but obscure Windows apps may need manual winetricks configuration.

---

## Contributing

Pull requests welcome. Areas that would help most:

- Support for Arch / Fedora based distros
- More action types (network management, printer setup, etc.)
- Better model routing (different models for different task types)
- GUI front end

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

---

## License

MIT — do whatever you want with it.

---

Built in Accra, Ghana 🇬🇭
