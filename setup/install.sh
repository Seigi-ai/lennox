#!/bin/bash
# install.sh — Lennox setup script
# Run once: bash setup/install.sh

set -e

GREEN='[0;32m'
RED='[0;31m'
YELLOW='[1;33m'
NC='[0m'

info()    { echo -e "${GREEN}[Lennox]${NC} $1"; }
warn()    { echo -e "${YELLOW}[Lennox]${NC} $1"; }
error()   { echo -e "${RED}[Lennox]${NC} $1"; exit 1; }

LENNOX_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOME_DIR="$HOME"

echo "╔══════════════════════════════════════════╗"
echo "║         Lennox Installer v0.1            ║"
echo "╚══════════════════════════════════════════╝"
echo ""
info "Installing from: $LENNOX_DIR"
info "Home directory : $HOME_DIR"
echo ""

# ── Check for llama-server binary ─────────────────────────────────────────────

info "Checking for llama-server binary..."

LLAMA_SERVER_FOUND=false
for path in     "$HOME/llama.cpp/llama-server"     "$HOME/llama.cpp/build/bin/llama-server"     "$HOME/llama.cpp/build/llama-server"     "/usr/local/bin/llama-server"     "/usr/bin/llama-server"; do
    if [ -x "$path" ]; then
        LLAMA_SERVER_FOUND=true
        info "Found llama-server at: $path"
        break
    fi
done

if ! command -v llama-server &>/dev/null && [ "$LLAMA_SERVER_FOUND" = false ]; then
    error "llama-server binary not found.\n\n"           "Lennox requires a local llama.cpp server to run the AI model.\n"           "Please build it first:\n\n"           "  cd ~/llama.cpp\n"           "  make llama-server\n\n"           "Or with CMake:\n\n"           "  cd ~/llama.cpp\n"           "  cmake -B build\n"           "  cmake --build build --target llama-server\n\n"           "Then re-run this installer."
fi

# ── Check for .gguf model ───────────────────────────────────────────────────

info "Checking for .gguf model files..."
MODEL_COUNT=$(find "$HOME" -name "*.gguf" -type f 2>/dev/null | wc -l)
if [ "$MODEL_COUNT" -eq 0 ]; then
    warn "No .gguf model files found in your home directory."
    warn "Lennox will ask you to provide a model path on first run,"
    warn "but it's better to download one now (e.g. Gemma 4 E2B Q4_K_M)."
    echo ""
fi

# ── System dependencies ───────────────────────────────────────────────────────

info "Installing system dependencies..."
sudo apt update -qq

PACKAGES=(
    tmux
    python3
    python3-venv
    python3-full
    xterm
    libnotify-bin
    wine
    winetricks
    wget
    curl
    cabextract
    unzip
    p7zip-full
    xvfb
    winbind
)

for pkg in "${PACKAGES[@]}"; do
    if dpkg -s "$pkg" &>/dev/null; then
        echo "  [✓] $pkg already installed"
    else
        echo "  [+] Installing $pkg..."
        sudo apt install -y "$pkg" -qq
    fi
done

# ── Python virtual environment ────────────────────────────────────────────────

VENV_DIR="$LENNOX_DIR/venv"
info "Setting up Python virtual environment..."

if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR"
    echo "  [+] Virtual environment created at $VENV_DIR"
else
    echo "  [✓] Virtual environment already exists"
fi

"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet requests
echo "  [✓] Python packages installed into venv"

# ── Memory and log directories ────────────────────────────────────────────────

info "Setting up memory and log directories..."
mkdir -p "$LENNOX_DIR/memory"
mkdir -p "$LENNOX_DIR/logs"

for template in "$LENNOX_DIR"/memory/*.template.json; do
    [ -e "$template" ] || continue
    actual="${template%.template.json}.json"
    if [ ! -f "$actual" ]; then
        cp "$template" "$actual"
        echo "  [+] Created $(basename "$actual")"
    else
        echo "  [✓] $(basename "$actual") already exists — keeping it"
    fi
done

for template in "$LENNOX_DIR"/logs/*.template.json; do
    [ -e "$template" ] || continue
    actual="${template%.template.json}.json"
    if [ ! -f "$actual" ]; then
        cp "$template" "$actual"
        echo "  [+] Created $(basename "$actual")"
    else
        echo "  [✓] $(basename "$actual") already exists — keeping it"
    fi
done

# ── Icon setup ────────────────────────────────────────────────────────────────

info "Setting up icon..."
ICON_PATH="$LENNOX_DIR/setup/icon.png"
USER_ICON="$LENNOX_DIR/L(1).png"

# Use user's provided icon if available, otherwise fall back to generation
if [ -f "$USER_ICON" ]; then
    cp "$USER_ICON" "$ICON_PATH"
    info "Using your provided icon: L(1).png"
elif [ ! -f "$ICON_PATH" ]; then
    # Generate a simple fallback icon with PIL
    python3 -c "
try:
    from PIL import Image, ImageDraw, ImageFont
    img  = Image.new('RGB', (128, 128), color='#1a1a2e')
    draw = ImageDraw.Draw(img)
    draw.ellipse([10,10,118,118], fill='#16213e', outline='#0f3460', width=3)
    draw.text((42, 40), 'L', fill='#e94560')
    img.save('$ICON_PATH')
    print('  [+] Icon generated with PIL')
except ImportError:
    pass
" 2>/dev/null || true

    # If PIL failed, copy a system icon as fallback
    if [ ! -f "$ICON_PATH" ]; then
        SYSTEM_ICON=$(find /usr/share/icons -name "utilities-terminal.png" 2>/dev/null | head -1)
        if [ -n "$SYSTEM_ICON" ]; then
            cp "$SYSTEM_ICON" "$ICON_PATH"
            warn "Used system terminal icon as fallback. Replace $ICON_PATH with your own icon."
        else
            warn "No icon generated. Add your own icon.png to $LENNOX_DIR/setup/"
        fi
    fi
else
    echo "  [✓] Icon already exists"
fi
# ── Systemd user service ──────────────────────────────────────────────────────

info "Installing systemd user service..."
SERVICE_DIR="$HOME/.config/systemd/user"
mkdir -p "$SERVICE_DIR"

sed "s|%h|$HOME_DIR|g" "$LENNOX_DIR/setup/lennox.service" > "$SERVICE_DIR/lennox.service"

systemctl --user daemon-reload
systemctl --user enable lennox.service
systemctl --user start lennox.service

if systemctl --user is-active --quiet lennox.service; then
    info "Lennox service is running."
else
    warn "Service started but status unclear. Check: systemctl --user status lennox"
fi

# ── Desktop icon ──────────────────────────────────────────────────────────────

info "Installing desktop icon..."
DESKTOP_DIR="$HOME/.local/share/applications"
mkdir -p "$DESKTOP_DIR"
mkdir -p "$HOME/Desktop"

sed "s|%h|$HOME_DIR|g" "$LENNOX_DIR/setup/lennox.desktop" > "$DESKTOP_DIR/lennox.desktop"
cp "$DESKTOP_DIR/lennox.desktop" "$HOME/Desktop/lennox.desktop"
chmod +x "$HOME/Desktop/lennox.desktop"

if command -v gio &>/dev/null; then
    gio set "$HOME/Desktop/lennox.desktop" metadata::trusted true 2>/dev/null || true
fi

info "Desktop icon created."

# ── Done ──────────────────────────────────────────────────────────────────────

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║         Lennox is ready!                 ║"
echo "╚══════════════════════════════════════════╝"
echo ""
info "Lennox is running in the background."
info "Click the Lennox icon on your desktop to open it."
info ""
info "Useful commands:"
echo "  Check status : systemctl --user status lennox"
echo "  Stop Lennox  : systemctl --user stop lennox"
echo "  Start Lennox : systemctl --user start lennox"
echo "  View logs    : journalctl --user -u lennox -f"
echo "  Live dashboard: python3 $LENNOX_DIR/dashboard.py"
echo ""
if [ "$MODEL_COUNT" -eq 0 ]; then
    warn "Remember: you still need a .gguf model file. Place it in ~/models/ or anywhere in ~/"
fi
