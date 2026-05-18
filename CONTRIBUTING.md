# Contributing to Lennox

Thanks for wanting to help. A few ground rules:

## Before opening a PR

- Test on a real machine, not just a VM
- Make sure `--dry-run` still works with your changes
- Don't break the memory file structure — other people's existing memory files need to keep working
- Keep the user-facing language simple — Lennox is for non-technical users

## Areas that need help

- **Distro support** — currently tested on Linux Mint and Ubuntu. Arch and Fedora support (pacman, dnf) would be valuable
- **New action types** — network management, printer setup, Bluetooth, display settings
- **Better filesystem scanning** — the on-demand scanner works but could be faster or more selective
- **Model routing** — using a tiny model for simple tasks and a larger one for complex ones
- **GUI front end** — a simple Tkinter or GTK window instead of xterm

## What not to change

- The memory file structure (profile, app_history, error_patterns, filesystem_log) — these are the core contract
- The offline-first design — Lennox should never require internet to function
- The autorun behavior — adding confirmation dialogs defeats the purpose for non-technical users

## Submitting

Open a PR with a clear description of what you changed and why. Include before/after examples if it affects user-facing behavior.
