#!/usr/bin/env bash
# ==============================================================================
# AutoPostula — Instalador Automático para Linux Base Debian / Ubuntu
# Compatible con: Debian 12+, Ubuntu 22.04/24.04+, Linux Mint, Xubuntu, Kali
# ==============================================================================
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "🐧 Instalando dependencias base Debian/Ubuntu para AutoPostula..."
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip vlc wmctrl xclip curl

echo "📦 Creando entorno virtual 'venv'..."
cd "$DIR"
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
python3 -m playwright install chromium

echo "🔊 Configurando herramienta nativa de voz Di (Edge-TTS + VLC FIFO)..."
mkdir -p "$HOME/.local/bin"
ln -sf "$DIR/bin/habla.sh" "$HOME/.local/bin/Di"
chmod +x "$DIR/bin/habla.sh" "$DIR/bin/play_vlc_managed.sh" "$DIR/bot_pantalla.sh"

echo "📝 Verificando .env..."
if [ ! -f "$DIR/.env" ] && [ -f "$DIR/.env.example" ]; then
    cp "$DIR/.env.example" "$DIR/.env"
    echo "⚠️ Recuerda agregar tu GEMINI_API_KEY en $DIR/.env"
fi

echo "✅ Instalación completada."
echo "👉 Paso 1: Abre Brave/Chrome con depuración remota: brave-browser --remote-debugging-port=9222 &"
echo "👉 Paso 2: Inicia el gestor web y memoria: python3 web_dashboard.py &"
echo "👉 Paso 3: Ejecuta el bot interactivo: ./bot_pantalla.sh --segundos 30"
