#!/usr/bin/env python3
"""
Di - Motor de Síntesis de Voz Multiplataforma (Linux + Windows 11)
Integrado para AutoPostula y Asistentes IA
Utiliza Edge-TTS Neural con soporte para lectura stdin, argumentos CLI,
prioridad de respuesta final (--final), y reproducción sincronizada.
"""

import argparse
import asyncio
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Configuración por defecto
DEFAULT_VOICE = os.environ.get("DI_VOICE", "es-CL-CatalinaNeural")
FALLBACK_VOICES = [
    DEFAULT_VOICE,
    "es-CL-CatalinaNeural",
    "es-CO-SalomeNeural",
    "es-MX-DaliaNeural",
    "es-ES-ElviraNeural",
    "es-AR-ElenaNeural",
]
DEFAULT_RATE = os.environ.get("DI_RATE", "+10%")
QUEUE_DIR = Path(tempfile.gettempdir()) / "autopostula_di_queue"
LOCK_FILE = QUEUE_DIR / "playback.lock"
STATE_FILE = QUEUE_DIR / "current_playback.json"


def sanitizar_texto(texto: str) -> str:
    """Limpia el texto eliminando Markdown pesado, URLs crudas, código y emojis."""
    t = texto or ""
    # Omitir URLs crudas tipo file:// o https://
    t = re.sub(r"file://\S+", "", t)
    t = re.sub(r"https?://\S+", "", t)
    # Reemplazar enlaces Markdown [texto](url) por 'texto'
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
    # Omitir bloques de código Markdown ```...```
    t = re.sub(r"```[\s\S]*?```", "", t)
    # Convertir paréntesis en pausas naturales
    t = re.sub(r"\(([^)]+)\)", r", \1, ", t)
    # Quitar negritas y cursivas
    t = re.sub(r"\*\*([^*]+)\*\*", r"\1", t)
    t = re.sub(r"\*([^*]+)\*", r"\1", t)
    # Quitar encabezados (# Header) y viñetas
    t = re.sub(r"^[ \t]*#+[ \t]*", "", t, flags=re.MULTILINE)
    t = re.sub(r"^[ \t]*[•\*\-][ \t]+", " ", t, flags=re.MULTILINE)
    # Reemplazar comillas dobles y simples
    t = t.replace('"', ' ').replace("'", ' ').replace("`", " ")
    # Reducir espacios en blanco continuos
    t = re.sub(r"\s+", " ", t).strip()
    return t


def detener_reproduccion_previa():
    """Si hay una locución en curso, la detiene (vital para --final)."""
    if not STATE_FILE.exists():
        return
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        pid = data.get("pid")
        if pid:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                try:
                    os.kill(pid, signal.SIGTERM)
                    time.sleep(0.1)
                    os.kill(pid, signal.SIGKILL)
                except Exception:
                    pass
    except Exception:
        pass


def registrar_reproduccion(pid: int, is_final: bool):
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"pid": pid, "is_final": is_final, "timestamp": time.time()}, f)
    except Exception:
        pass


def liberar_reproduccion():
    try:
        if STATE_FILE.exists():
            STATE_FILE.unlink(missing_ok=True)
    except Exception:
        pass


async def sintetizar_con_edge_tts(texto: str, output_path: str, rate: str = DEFAULT_RATE) -> bool:
    """Genera el audio WAV/MP3 usando la librería o CLI de edge-tts."""
    try:
        import edge_tts
        for v in FALLBACK_VOICES:
            try:
                communicate = edge_tts.Communicate(texto, v, rate=rate)
                await communicate.save(output_path)
                if os.path.exists(output_path) and os.path.getsize(output_path) > 100:
                    return True
            except Exception:
                continue
    except ImportError:
        pass

    # Fallback CLI si no está el módulo python en runtime
    if shutil.which("edge-tts"):
        for v in FALLBACK_VOICES:
            cmd = ["edge-tts", "--voice", v, "--text", texto, "--rate", rate, "--write-media", output_path]
            try:
                res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if res.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 100:
                    return True
            except Exception:
                pass
    return False


def reproducir_audio(audio_path: str, is_final: bool, raw_label: str = ""):
    """Reproduce el audio de forma nativa en Linux o Windows 11."""
    # 1. En Linux, si existe play_vlc_managed.sh original del usuario, usarlo
    if sys.platform != "win32" and os.path.exists("/home/dhamit/play_vlc_managed.sh"):
        mode = "--final" if is_final else "--progress"
        cmd = ["/home/dhamit/play_vlc_managed.sh", mode, audio_path, raw_label]
        subprocess.run(cmd, check=False)
        return

    # 2. Reproductor nativo en Windows 11
    if sys.platform == "win32":
        # Intentar VLC en Windows si existe
        vlc_paths = [
            r"C:\Program Files\VideoLAN\VLC\vlc.exe",
            r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe",
        ]
        vlc_win = next((p for p in vlc_paths if os.path.exists(p)), None)
        if vlc_win:
            proc = subprocess.Popen([vlc_win, "--play-and-exit", "--qt-start-minimized", "--qt-notification=0", audio_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            registrar_reproduccion(proc.pid, is_final)
            proc.wait()
            liberar_reproduccion()
            return

        # Fallback nativo: PowerShell MediaPlayer
        ps_cmd = f"""
        $player = New-Object System.Windows.Media.MediaPlayer
        $player.Open([System.Uri]'{audio_path}')
        $player.Play()
        Start-Sleep -Milliseconds 500
        while ($player.NaturalDuration.HasTimeSpan -and ($player.Position -lt $player.NaturalDuration.TimeSpan)) {{
            Start-Sleep -Milliseconds 200
        }}
        """
        proc = subprocess.Popen(["powershell", "-NoProfile", "-Command", ps_cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        registrar_reproduccion(proc.pid, is_final)
        proc.wait()
        liberar_reproduccion()
        return

    # 3. Reproductor nativo en Linux (VLC / mpv / aplay / paplay)
    reproductores = [
        ["vlc", "--play-and-exit", "--qt-start-minimized", "--qt-notification=0", audio_path],
        ["mpv", "--no-video", "--volume=100", audio_path],
        ["paplay", audio_path],
        ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", audio_path],
        ["aplay", audio_path],
    ]
    for cmd in reproductores:
        if shutil.which(cmd[0]):
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                registrar_reproduccion(proc.pid, is_final)
                proc.wait()
                liberar_reproduccion()
                return
            except Exception:
                pass


def fallback_voz_sapi(texto: str):
    """Fallback si no hay conexión a internet para Edge-TTS."""
    if sys.platform == "win32":
        clean = texto.replace('"', '').replace("'", "")
        ps_script = f'Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; $s.Speak("{clean}")'
        subprocess.run(["powershell", "-NoProfile", "-Command", ps_script], check=False)
    else:
        print(f"📢 [VOZ]: {texto}")


def main():
    parser = argparse.ArgumentParser(description="Di - Síntesis de voz Edge-TTS Multiplataforma")
    parser.add_argument("-f", "--final", action="store_true", help="Prioridad de respuesta final (detiene audios de avance)")
    parser.add_argument("-v", "--voice", default=DEFAULT_VOICE, help="Voz de Edge-TTS (ej. es-CL-CatalinaNeural)")
    parser.add_argument("-s", "--speed", default=DEFAULT_RATE, help="Velocidad (ej. +10%, +20%)")
    parser.add_argument("texto", nargs="*", help="Texto a narrar")
    args = parser.parse_args()

    # 1. Obtener texto desde argumentos o stdin
    raw_text = " ".join(args.texto).strip()
    if not raw_text and not sys.stdin.isatty():
        raw_text = sys.stdin.read().strip()

    if not raw_text:
        sys.exit(0)

    # 2. Si es --final, detener de inmediato cualquier audio previo de avance
    if args.final or os.environ.get("DI_FINAL") in ("1", "true"):
        detener_reproduccion_previa()

    # 3. Sanitizar texto
    texto_limpio = sanitizar_texto(raw_text)
    if not texto_limpio:
        sys.exit(0)

    # 4. Generar audio temporal
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    temp_audio = QUEUE_DIR / f"di_{os.getpid()}_{int(time.time()*1000)}.mp3"

    ok = asyncio.run(sintetizar_con_edge_tts(texto_limpio, str(temp_audio), rate=args.speed))

    if ok and temp_audio.exists():
        reproducir_audio(str(temp_audio), is_final=args.final, raw_label=texto_limpio[:50])
        try:
            temp_audio.unlink(missing_ok=True)
        except Exception:
            pass
    else:
        fallback_voz_sapi(texto_limpio)


if __name__ == "__main__":
    main()

