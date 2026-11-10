#!/usr/bin/env python3
"""
play_vlc_managed.sh
Gestor persistente y autónomo de VLC para el comando Di (Antigravity + ChatGPT Codex + Terminal):
1. Autodetecta entorno gráfico y de audio (DISPLAY, XDG_RUNTIME_DIR, DBUS_SESSION_BUS_ADDRESS, PULSE_SERVER)
   incluso cuando se invoca desde entornos con variables recortadas como ChatGPT Codex (`inherit = "core"`).
2. Inicia SIEMPRE minimizado (--qt-start-minimized --qt-system-tray --qt-notification=0):
   jamás aparece encima de la pantalla ni roba foco mientras el usuario escribe.
3. Cola estricta FIFO con flock: nunca suenan 2 audios al mismo tiempo.
4. Prioridad inteligente de Respuesta Final (`Di --final`):
   - Cuando la tarea termina rápido y entra una respuesta `--final`, detiene de inmediato cualquier audio
     de desarrollo/avance (`--progress`) que siga hablando o esperando en la cola, y comienza a leer de
     inmediato la respuesta final `[#N FINAL]` para que el usuario sepa al instante que ya terminó.
5. Numeración secuencial ([#1], [#2], [#3]...) y máximo 3 instancias VLC abiertas simultáneamente
   (al entrar la 4ta se cierra la más antigua).
6. Permite abrir/maximizar, cambiar ancho/tamaño y repetir el audio desde 00:00 (--play-and-stop).
7. Cierre automático estricto tras 60 segundos (1 minuto) de inactividad real (SIGTERM -> SIGKILL).
"""

import fcntl
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import time

QUEUE_DIR = "/tmp/di_vlc_queue"
STATE_LOCK = os.path.join(QUEUE_DIR, "state.lock")
PLAY_LOCK = os.path.join(QUEUE_DIR, "playback.lock")
COUNTER_FILE = os.path.join(QUEUE_DIR, "counter.txt")
LAST_DONE_FILE = os.path.join(QUEUE_DIR, "last_done_seq.txt")
ACTIVE_JSON = os.path.join(QUEUE_DIR, "active_instances.json")

MAX_OPEN_WINDOWS = 3
INACTIVITY_CLOSE_SECS = 60.0
POLL_INTERVAL_SECS = 2.0


def ensure_desktop_audio_env():
    uid = os.getuid()
    runtime_dir = f"/run/user/{uid}"
    if not os.environ.get("XDG_RUNTIME_DIR") and os.path.isdir(runtime_dir):
        os.environ["XDG_RUNTIME_DIR"] = runtime_dir
    dbus_sock = f"{runtime_dir}/bus"
    if not os.environ.get("DBUS_SESSION_BUS_ADDRESS") and os.path.exists(dbus_sock):
        try:
            if stat.S_ISSOCK(os.stat(dbus_sock).st_mode):
                os.environ["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={dbus_sock}"
        except Exception:
            pass
    pulse_sock = f"{runtime_dir}/pulse/native"
    if not os.environ.get("PULSE_SERVER") and os.path.exists(pulse_sock):
        try:
            if stat.S_ISSOCK(os.stat(pulse_sock).st_mode):
                os.environ["PULSE_SERVER"] = f"unix:{pulse_sock}"
        except Exception:
            pass
    if not os.environ.get("DISPLAY") and not os.environ.get("SSH_CLIENT"):
        os.environ["DISPLAY"] = ":0.0"


def ignore_hangup():
    for sig in (signal.SIGHUP,):
        try:
            signal.signal(sig, signal.SIG_IGN)
        except Exception:
            pass


def pid_alive(pid: int) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        with open(f"/proc/{pid}/stat", "r", encoding="utf-8", errors="ignore") as f:
            stat_txt = f.read()
        m = re.search(r"\)\s+([A-Z])", stat_txt)
        if m and m.group(1) == "Z":
            return False
        return True
    except Exception:
        return False


def terminate_pid_reliably(pid: int):
    if not pid_alive(pid):
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except Exception:
        return
    for _ in range(15):
        time.sleep(0.08)
        if not pid_alive(pid):
            return
    try:
        os.kill(pid, signal.SIGKILL)
    except Exception:
        pass


def get_proc_cpu_ticks(pid: int) -> int:
    try:
        with open(f"/proc/{pid}/stat", "r", encoding="utf-8", errors="ignore") as f:
            data = f.read()
        rparen = data.rfind(")")
        fields = data[rparen + 2 :].split()
        utime = int(fields[11])
        stime = int(fields[12])
        return utime + stime
    except Exception:
        return 0


def is_pulseaudio_uncorked(pid: int) -> bool:
    if not shutil.which("pactl"):
        return False
    try:
        out = subprocess.check_output(
            ["pactl", "list", "sink-inputs"],
            stderr=subprocess.DEVNULL,
            timeout=2.0,
        ).decode("utf-8", errors="ignore")
    except Exception:
        return False

    target = str(pid)
    blocks = re.split(r"(?:Sink Input #|Entrada del destino #)", out)
    for b in blocks[1:]:
        m_pid = re.search(r'application\.process\.id\s*=\s*"(\d+)"', b)
        if not m_pid or m_pid.group(1) != target:
            continue
        corked = re.search(r"(?:Corked|En espera)\s*:\s*(yes|sí|si|no)", b, re.IGNORECASE)
        if corked and corked.group(1).lower() == "no":
            return True
    return False


def get_window_geometry_and_set_title(pid: int, title: str, disp: str) -> str:
    if not shutil.which("wmctrl"):
        return ""
    env = os.environ.copy()
    env["DISPLAY"] = disp
    try:
        out = subprocess.check_output(
            ["wmctrl", "-lpG"],
            env=env,
            stderr=subprocess.DEVNULL,
            timeout=2.0,
        ).decode("utf-8", errors="ignore")
    except Exception:
        return ""

    sigs = []
    for line in out.splitlines():
        parts = line.split(None, 7)
        if len(parts) < 7:
            continue
        wid, _desk, wpid, x, y, w, h = parts[:7]
        if wpid != str(pid):
            continue
        sigs.append(f"{wid}:{x},{y},{w},{h}")
        try:
            subprocess.run(
                ["wmctrl", "-i", "-r", wid, "-b", "remove,above,demands_attention"],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=1.5,
            )
            subprocess.run(
                ["wmctrl", "-i", "-r", wid, "-N", title],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=1.5,
            )
            if shutil.which("xdotool"):
                subprocess.run(
                    ["xdotool", "set_window", "--name", title, "--icon-name", title, wid],
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=1.5,
                )
        except Exception:
            pass
    return "|".join(sigs)


def probe_audio_duration(filepath: str) -> float:
    if shutil.which("ffprobe"):
        try:
            out = subprocess.check_output(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    filepath,
                ],
                stderr=subprocess.DEVNULL,
                timeout=4.0,
            ).decode("utf-8", errors="ignore").strip()
            val = float(out)
            if val > 0:
                return val
        except Exception:
            pass
    return 10.0


def load_active_instances() -> list:
    if not os.path.exists(ACTIVE_JSON):
        return []
    try:
        with open(ACTIVE_JSON, "r", encoding="utf-8") as f:
            items = json.load(f)
        if isinstance(items, list):
            return [x for x in items if isinstance(x, dict) and pid_alive(int(x.get("pid", 0)))]
    except Exception:
        pass
    return []


def save_active_instances(items: list):
    tmp = ACTIVE_JSON + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f)
    os.replace(tmp, ACTIVE_JSON)


def read_int_file(path: str, default: int = 0) -> int:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return int(f.read().strip())
    except Exception:
        return default


def write_int_file(path: str, val: int):
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"{val}\n")


def read_float_file(path: str, default: float = 0.0) -> float:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return float(f.read().strip())
    except Exception:
        return default


def write_float_file(path: str, val: float):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(f"{val:.6f}\n")
    os.replace(tmp, path)


def speaker_slug(speaker: str) -> str:
    slug = re.sub(r"[^a-z0-9_-]+", "_", (speaker or "Di").strip().lower())
    return slug.strip("_") or "di"


def interrupt_file_for(speaker: str) -> str:
    return os.path.join(QUEUE_DIR, f"interrupt_before_{speaker_slug(speaker)}.txt")


def is_progress_interrupted(is_final: bool, invoked_at: float, speaker: str) -> bool:
    if is_final:
        return False
    cutoff = read_float_file(interrupt_file_for(speaker), 0.0)
    return cutoff > 0.0 and invoked_at <= cutoff


def interrupt_running_progress_audios(cutoff_ts: float, speaker: str):
    """
    Registra el timestamp de corte de la respuesta final y detiene de inmediato cualquier
    audio de desarrollo ('progress') que esté hablando en este instante.
    """
    ensure_desktop_audio_env()
    os.makedirs(QUEUE_DIR, exist_ok=True)
    pids_to_stop = []
    with open(STATE_LOCK, "w") as sf:
        fcntl.flock(sf.fileno(), fcntl.LOCK_EX)
        interrupt_file = interrupt_file_for(speaker)
        prev_cutoff = read_float_file(interrupt_file, 0.0)
        if cutoff_ts > prev_cutoff:
            write_float_file(interrupt_file, cutoff_ts)
        alive = load_active_instances()
        kept = []
        for inst in alive:
            pid = int(inst.get("pid", 0))
            kind = inst.get("kind", "progress")
            inst_speaker = inst.get("speaker", "Di")
            inv_ts = float(inst.get("invokedAt", 0.0))
            speaking = bool(inst.get("currentlySpeaking", False)) or is_pulseaudio_uncorked(pid)
            if kind != "final" and inst_speaker == speaker and inv_ts <= cutoff_ts and speaking:
                pids_to_stop.append(pid)
            else:
                kept.append(inst)
        save_active_instances(kept)

    for pid in pids_to_stop:
        terminate_pid_reliably(pid)


def set_instance_speaking_flag(vlc_pid: int, flag: bool):
    with open(STATE_LOCK, "w") as sf:
        fcntl.flock(sf.fileno(), fcntl.LOCK_EX)
        alive = load_active_instances()
        for inst in alive:
            if int(inst.get("pid", 0)) == vlc_pid:
                inst["currentlySpeaking"] = flag
        save_active_instances(alive)


def remove_instance_pid(vlc_pid: int):
    with open(STATE_LOCK, "w") as sf:
        fcntl.flock(sf.fileno(), fcntl.LOCK_EX)
        alive = [x for x in load_active_instances() if int(x.get("pid", 0)) != vlc_pid]
        save_active_instances(alive)


def run_worker(slot_file: str, seq: int, title: str, is_final: bool, invoked_at: float, speaker: str):
    ignore_hangup()
    ensure_desktop_audio_env()
    os.makedirs(QUEUE_DIR, exist_ok=True)
    disp = os.environ.get("DISPLAY") or ":0.0"

    # 1. Todos los audios, incluidos dos finales simultáneos, respetan el orden FIFO global.
    # Una respuesta final solo puede cancelar avances anteriores del mismo agente.
    wait_start = time.monotonic()
    while time.monotonic() - wait_start < 300.0:
        if is_progress_interrupted(is_final, invoked_at, speaker):
            write_int_file(LAST_DONE_FILE, max(read_int_file(LAST_DONE_FILE, 0), seq))
            return
        last_done = read_int_file(LAST_DONE_FILE, 0)
        if seq <= last_done + 1:
            break
        if time.monotonic() - wait_start >= 2.5:
            try:
                with open(PLAY_LOCK, "w") as lf:
                    fcntl.flock(lf.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
                break
            except BlockingIOError:
                pass
        time.sleep(0.2)

    # 2. Adquirir mutex exclusivo de reproducción: jamás suenan 2 audios al mismo tiempo
    play_lock_f = open(PLAY_LOCK, "w")
    fcntl.flock(play_lock_f.fileno(), fcntl.LOCK_EX)

    vlc_pid = 0
    interrupted_by_final = False
    try:
        if is_progress_interrupted(is_final, invoked_at, speaker):
            write_int_file(LAST_DONE_FILE, max(read_int_file(LAST_DONE_FILE, 0), seq))
            return

        dur = probe_audio_duration(slot_file)
        speak_wait_secs = max(1.2, dur + 0.5)

        # 3. Aplicar tope máximo de 3 ventanas abiertas: si entra la 4ta, cerrar la más antigua
        with open(STATE_LOCK, "w") as sf:
            fcntl.flock(sf.fileno(), fcntl.LOCK_EX)
            alive = load_active_instances()
            while len(alive) >= MAX_OPEN_WINDOWS:
                oldest = alive.pop(0)
                terminate_pid_reliably(int(oldest.get("pid", 0)))
            save_active_instances(alive)

        # 4. Lanzar VLC SIEMPRE minimizado en bandeja/panel (--qt-start-minimized --qt-system-tray)
        env = os.environ.copy()
        env["DISPLAY"] = disp
        proc = subprocess.Popen(
            [
                "vlc",
                "--qt-start-minimized",
                "--qt-system-tray",
                "--no-qt-pause-minimized",
                "--qt-notification=0",
                "--no-qt-name-in-title",
                "--role",
                "communication",
                "--no-volume-save",
                "--no-one-instance",
                "--no-loop",
                "--no-repeat",
                "--play-and-stop",
                f"--meta-title={title}",
                f"--video-title={title}",
                slot_file,
            ],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        vlc_pid = proc.pid

        with open(STATE_LOCK, "w") as sf:
            fcntl.flock(sf.fileno(), fcntl.LOCK_EX)
            alive = load_active_instances()
            alive.append(
                {
                    "pid": vlc_pid,
                    "seq": seq,
                    "kind": "final" if is_final else "progress",
                    "speaker": speaker,
                    "invokedAt": invoked_at,
                    "currentlySpeaking": True,
                    "startedAt": time.time(),
                    "title": title,
                }
            )
            save_active_instances(alive)

        if os.path.isfile("/home/dhamit/ajustar_vlc_inicial.py"):
            subprocess.Popen(
                ["python3", "/home/dhamit/ajustar_vlc_inicial.py", str(vlc_pid)],
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )

        # 5. Esperar mientras termina de hablar inicialmente (chequeo rápido cada 0.2s para corte por --final)
        t0 = time.monotonic()
        while time.monotonic() - t0 < speak_wait_secs:
            if not pid_alive(vlc_pid):
                break
            if is_progress_interrupted(is_final, invoked_at, speaker):
                interrupted_by_final = True
                terminate_pid_reliably(vlc_pid)
                break
            get_window_geometry_and_set_title(vlc_pid, title, disp)
            time.sleep(0.2)

        if vlc_pid and not interrupted_by_final:
            set_instance_speaking_flag(vlc_pid, False)
        write_int_file(LAST_DONE_FILE, max(read_int_file(LAST_DONE_FILE, 0), seq))
    finally:
        try:
            fcntl.flock(play_lock_f.fileno(), fcntl.LOCK_UN)
            play_lock_f.close()
        except Exception:
            pass

    if interrupted_by_final or not pid_alive(vlc_pid):
        remove_instance_pid(vlc_pid)
        return

    # 6. Fase post-lectura: cerrar exactamente tras 60 segundos de inactividad real
    last_activity = time.monotonic()
    last_geom = get_window_geometry_and_set_title(vlc_pid, title, disp)
    last_cpu_ticks = get_proc_cpu_ticks(vlc_pid)

    while pid_alive(vlc_pid):
        time.sleep(POLL_INTERVAL_SECS)
        if not pid_alive(vlc_pid):
            break

        cur_geom = get_window_geometry_and_set_title(vlc_pid, title, disp)
        cur_cpu_ticks = get_proc_cpu_ticks(vlc_pid)
        cpu_delta = cur_cpu_ticks - last_cpu_ticks
        last_cpu_ticks = cur_cpu_ticks

        geom_changed = bool(cur_geom and cur_geom != last_geom)
        last_geom = cur_geom
        playing_now = is_pulseaudio_uncorked(vlc_pid) and cpu_delta > 0

        if geom_changed or playing_now:
            last_activity = time.monotonic()
            continue

        if time.monotonic() - last_activity >= INACTIVITY_CLOSE_SECS:
            break

    # 7. Cerrar VLC de forma garantizada (SIGTERM -> SIGKILL)
    terminate_pid_reliably(vlc_pid)
    remove_instance_pid(vlc_pid)


def enqueue_audio(input_file: str, raw_label: str, is_final: bool, invoked_at: float, speaker: str):
    ensure_desktop_audio_env()
    if not input_file or not os.path.isfile(input_file):
        sys.exit(1)

    if is_final:
        interrupt_running_progress_audios(invoked_at, speaker)
    elif is_progress_interrupted(False, invoked_at, speaker):
        return

    os.makedirs(QUEUE_DIR, exist_ok=True)
    with open(STATE_LOCK, "w") as sf:
        fcntl.flock(sf.fileno(), fcntl.LOCK_EX)
        if not is_final and is_progress_interrupted(False, invoked_at, speaker):
            return

        seq = read_int_file(COUNTER_FILE, 0)
        alive = load_active_instances()
        save_active_instances(alive)

        if not alive or is_final:
            try:
                with open(PLAY_LOCK, "w") as pf:
                    fcntl.flock(pf.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    write_int_file(LAST_DONE_FILE, seq)
                    fcntl.flock(pf.fileno(), fcntl.LOCK_UN)
            except BlockingIOError:
                pass

        seq += 1
        write_int_file(COUNTER_FILE, seq)

        slot_idx = seq % 16
        slot_file = os.path.join(QUEUE_DIR, f"di_slot_{slot_idx}.wav")
        shutil.copyfile(input_file, slot_file)

        clean_preview = re.sub(r"\s+", " ", (raw_label or "").strip())[:46] or "Audio Di"
        tag = f"[#{seq} FINAL]" if is_final else f"[#{seq}]"
        title = f"{tag} {speaker}: {clean_preview}"

        env = os.environ.copy()
        env["DISPLAY"] = env.get("DISPLAY") or ":0.0"
        subprocess.Popen(
            [
                sys.executable,
                os.path.abspath(__file__),
                "--worker",
                slot_file,
                str(seq),
                title,
                "1" if is_final else "0",
                f"{invoked_at:.6f}",
                speaker,
            ],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )


def main():
    args = sys.argv[1:]
    if not args:
        sys.exit(1)

    if args[0] == "--interrupt-progress":
        ts = float(args[1]) if len(args) >= 2 else time.time()
        speaker = args[2] if len(args) >= 3 else "Di"
        interrupt_running_progress_audios(ts, speaker)
        return

    if args[0] == "--worker" and len(args) >= 7:
        run_worker(args[1], int(args[2]), args[3], args[4] == "1", float(args[5]), args[6])
        return

    is_final = False
    invoked_at = time.time()
    speaker = "Di"
    positional = []
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-f", "--final"):
            is_final = True
            i += 1
        elif a == "--progress":
            is_final = False
            i += 1
        elif a == "--invoked-at" and i + 1 < len(args):
            try:
                invoked_at = float(args[i + 1])
            except Exception:
                invoked_at = time.time()
            i += 2
        elif a == "--speaker" and i + 1 < len(args):
            speaker = args[i + 1]
            i += 2
        else:
            positional.append(a)
            i += 1

    input_file = positional[0] if len(positional) >= 1 else ""
    raw_label = positional[1] if len(positional) >= 2 else ""
    enqueue_audio(input_file, raw_label, is_final, invoked_at, speaker)


if __name__ == "__main__":
    main()
