#!/usr/bin/env python3
"""
AutoPostula · Centro de Comando NOC & Multi-Buscador (14 Portales Reales Chile)
Puerto: 8087
Soporta tanto Prácticas Profesionales Duoc UC (Ingeniería en Infraestructura y Plataformas Tecnológicas · 360h)
como cualquier pega / empleo formal TI en Chile.
"""

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PORT = 8087
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from database import (
    PORTALES_SOPORTADOS,
    actualizar_estado_postulacion,
    eliminar_respuesta_aprendida,
    get_conn,
    guardar_estado_scheduler,
    guardar_filtros,
    guardar_o_actualizar_respuesta,
    init_db,
    obtener_alertas_pendientes,
    obtener_catalogo_portales,
    obtener_estado_scheduler,
    obtener_filtros,
    obtener_logs_scheduler,
    obtener_perfil,
    obtener_postulaciones,
    obtener_respuestas_aprendidas,
)
from daemon_scheduler import calcular_proximo_horario
from modules.auto_applier import (
    abrir_sesion_persistente_portales,
    generar_bookmarklet_universal,
    generar_mailto_postulacion,
)

DAEMON_SCRIPT = os.path.join(BASE_DIR, "daemon_scheduler.py")
PYTHON_BIN = sys.executable or "/usr/bin/python3"
INVESTIGACIONES_DIR = os.path.join(BASE_DIR, "data", "investigaciones")


def _disparar_investigacion(data: dict) -> dict:
    """Guarda el contexto de la pestaña y la nota del usuario y lanza el Agente Investigador (agy) una sola vez por req_id."""
    os.makedirs(INVESTIGACIONES_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    req_id = str(data.get("req_id") or f"inv_{ts}")
    req_safe = "".join(c for c in req_id if c.isalnum() or c in ("_", "-"))[:60] or f"inv_{ts}"
    report_path = os.path.join(INVESTIGACIONES_DIR, f"{req_safe}.json")
    ultimo_path = os.path.join(INVESTIGACIONES_DIR, "ultimo.json")

    if os.path.exists(report_path):
        try:
            with open(report_path, "r", encoding="utf-8") as fh:
                return {"ok": True, "dedup": True, "report": json.load(fh)}
        except Exception:
            pass

    note = (data.get("note") or "").strip()
    url = (data.get("url") or "").strip()
    report = {
        "req_id": req_safe,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "status": "investigando",
        "note": note,
        "url": url,
        "title": (data.get("title") or "").strip(),
        "oferta_id": data.get("oferta_id"),
        "empresa": data.get("empresa") or "",
        "titulo": data.get("titulo") or "",
        "portal": data.get("portal") or "",
        "url_oferta": data.get("url_oferta") or "",
        "errors": data.get("errors") or [],
        "buttons": data.get("buttons") or [],
        "inputs": data.get("inputs") or [],
        "bodySnippet": (data.get("bodySnippet") or "")[:2200],
        "report_path": report_path,
    }
    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    with open(ultimo_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)

    agy_bin = "/home/dhamit/.local/bin/agy" if os.path.exists("/home/dhamit/.local/bin/agy") else (shutil.which("agy") or "agy")
    log_path = os.path.join(INVESTIGACIONES_DIR, f"{req_safe}.log")
    res_path = os.path.join(INVESTIGACIONES_DIR, "ultimo_resultado.json")
    parent_ws = os.path.dirname(BASE_DIR)
    backup_script = os.path.join(parent_ws, ".agents", "scripts", "backup_helper.py")
    di_cmd = _resolve_di_cmd()
    di_hint = f"{' '.join(di_cmd)} --final" if di_cmd else "síntesis de voz"
    prompt = (
        f"Eres el Agente Investigador de AutoPostula llamado desde el botón flotante de Brave.\n"
        f"Lee primero el archivo de contexto completo en: {report_path}\n"
        f"Nota del usuario: {note or '(sin nota adicional, revisar por qué no se completó ni envió la postulación)'}\n"
        f"URL activa reportada: {url}\n"
        f"1) Conecta por CDP a Brave en http://127.0.0.1:9222 para inspeccionar el DOM real, errores, formularios, modales o botones bloqueados de esa pestaña.\n"
        f"2) Identifica exactamente por qué no se ha postulado o qué campo/botón faltó, complétalo o corrige bucle_pantalla_interactivo.py (respaldando primero con {backup_script} y validando con py_compile).\n"
        f"3) Guarda el resumen JSON en {res_path} con claves req_id='{req_safe}', status='completado', summary, timestamp.\n"
        f"4) Anuncia el hallazgo y resultado en voz alta ejecutando {di_hint}."
    )
    try:
        with open(log_path, "w", encoding="utf-8") as log_f:
            subprocess.Popen(
                [agy_bin, "--dangerously-skip-permissions", "--mode", "accept-edits", "--add-dir", parent_ws, "-p", prompt],
                stdin=subprocess.DEVNULL,
                stdout=log_f,
                stderr=subprocess.STDOUT,
                cwd=BASE_DIR,
                start_new_session=(sys.platform != "win32"),
            )
        launched = True
    except Exception as e:
        launched = False
        report["launch_error"] = str(e)
    if launched:
        hablar_no_bloqueante(f"Agente investigador activado para {report['empresa'] or 'esta oferta'}. Ya recibí tu nota y estoy analizando la pestaña.")
    return {"ok": True, "launched": launched, "report": report}


def _find_python_script_pids(pattern: str) -> list[int]:
    """Busca PIDs de un script Python de forma compatible con Linux y Windows 11."""
    try:
        if sys.platform == "win32":
            ps = (
                f"Get-CimInstance Win32_Process | "
                f"Where-Object {{ $_.CommandLine -like '*{pattern}*' -and $_.ProcessId -ne {os.getpid()} }} | "
                f"Select-Object -ExpandProperty ProcessId"
            )
            out = subprocess.check_output(["powershell", "-NoProfile", "-Command", ps], text=True, stderr=subprocess.DEVNULL).strip()
        else:
            out = subprocess.check_output(["pgrep", "-f", pattern], text=True, stderr=subprocess.DEVNULL).strip()
        return [int(p) for p in out.splitlines() if p.strip().isdigit() and int(p) != os.getpid()]
    except Exception:
        return []


def is_daemon_running():
    pids = _find_python_script_pids("daemon_scheduler.py")
    return (len(pids) > 0), (pids[0] if pids else None)


def is_bot_running():
    pids = _find_python_script_pids("bucle_pantalla_interactivo.py")
    return (len(pids) > 0), (pids[0] if pids else None)


def contar_pendientes_validas():
    try:
        from bucle_pantalla_interactivo import obtener_ofertas_validas_pendientes
        return len(obtener_ofertas_validas_pendientes(limite=500))
    except Exception:
        return 0


def _resolve_di_cmd() -> list[str] | None:
    if sys.platform == "win32":
        di_cmd = os.path.join(BASE_DIR, "bin", "Di.cmd")
        if os.path.exists(di_cmd):
            return [di_cmd]
    for cand in ("/home/dhamit/.local/bin/Di", os.path.join(BASE_DIR, "bin", "Di")):
        if os.path.exists(cand):
            return [cand]
    di_py = os.path.join(BASE_DIR, "bin", "di.py")
    if os.path.exists(di_py):
        return [PYTHON_BIN, di_py]
    found = shutil.which("Di")
    return [found] if found else None


def hablar_no_bloqueante(msg: str):
    try:
        cmd = _resolve_di_cmd()
        if cmd:
            def _run(txt: str):
                try:
                    p_di = subprocess.Popen(cmd, stdin=subprocess.PIPE, text=True)
                    p_di.communicate(input=txt, timeout=10)
                except Exception:
                    pass
            threading.Thread(target=_run, args=(msg,), daemon=True).start()
        elif sys.platform == "win32":
            clean = msg.replace('"', "").replace("'", "")
            ps_cmd = f'Add-Type -AssemblyName System.Speech; (New-Object System.Speech.Synthesis.SpeechSynthesizer).Speak("{clean}")'
            subprocess.Popen(["powershell", "-NoProfile", "-Command", ps_cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def _launch_bot_process(extra_args: list[str]):
    env = os.environ.copy()
    sh_path = os.path.join(BASE_DIR, "bot_pantalla.sh")
    if sys.platform != "win32":
        env.setdefault("DISPLAY", ":0")
        if os.path.exists(os.path.expanduser("~/.Xauthority")):
            env.setdefault("XAUTHORITY", os.path.expanduser("~/.Xauthority"))
        if os.path.exists(sh_path):
            cmd = [sh_path] + extra_args
        else:
            cmd = [PYTHON_BIN, os.path.join(BASE_DIR, "bucle_pantalla_interactivo.py")] + extra_args
        subprocess.Popen(cmd, env=env, cwd=BASE_DIR, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    else:
        cmd = [PYTHON_BIN, os.path.join(BASE_DIR, "bucle_pantalla_interactivo.py")] + extra_args
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen(cmd, env=env, cwd=BASE_DIR, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)


def _stop_bot_processes():
    if sys.platform == "win32":
        for pid in _find_python_script_pids("bucle_pantalla_interactivo.py"):
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        subprocess.run(["pkill", "-SIGTERM", "-f", "bucle_pantalla_interactivo.py"], check=False)


def get_stats():
    init_db()
    active, pid = is_daemon_running()
    sched = obtener_estado_scheduler()
    filtros = obtener_filtros()
    perfil = obtener_perfil()
    alertas = obtener_alertas_pendientes()
    catalogo = obtener_catalogo_portales()

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM postulaciones;")
    total = cur.fetchone()[0] or 0

    cur.execute("""
        SELECT COUNT(*) FROM postulaciones
        WHERE estado LIKE '%Postulado Confirmado%'
           OR estado LIKE '%Abierta en Brave%'
           OR estado LIKE '%entrevista%'
           OR estado LIKE '%Oferta recibida%';
    """)
    enviadas_abiertas = cur.fetchone()[0] or 0

    cur.execute("SELECT COUNT(*) FROM postulaciones WHERE estado LIKE '%Cerrada%' OR estado LIKE '%Expirada%';")
    cerradas_total = cur.fetchone()[0] or 0

    cur.execute("SELECT COUNT(*) FROM postulaciones WHERE estado LIKE '%Pausada%' OR estado LIKE '%Captcha%' OR estado LIKE '%Login%';")
    pausadas_manual_total = cur.fetchone()[0] or 0

    cur.execute("SELECT COUNT(*) FROM postulaciones WHERE estado LIKE '%Cola%';")
    en_cola = cur.fetchone()[0] or 0

    cur.execute("SELECT COUNT(*) FROM postulaciones WHERE email_reclutador IS NOT NULL AND TRIM(email_reclutador) != '';")
    con_email = cur.fetchone()[0] or 0

    cur.execute("SELECT portal, COUNT(*) AS cnt FROM postulaciones GROUP BY portal ORDER BY cnt DESC;")
    por_portal = {r["portal"]: r["cnt"] for r in cur.fetchall()}
    conn.close()

    validas_disponibles = contar_pendientes_validas()

    return {
        "daemon_active": active,
        "daemon_pid": pid,
        "scheduler_enabled": int(sched.get("activo") or 0) == 1,
        "proximo_horario": sched.get("proximo_escaneo") or "Pendiente",
        "ultimo_escaneo": sched.get("ultimo_escaneo") or "Reciente",
        "horarios_config": sched.get("horarios") or "01:00, 04:00, 06:30, 08:00, 12:00, 14:00, 17:00, 22:00",
        "total_postulaciones": total,
        "postuladas_activas": enviadas_abiertas,
        "cerradas_total": cerradas_total,
        "pausadas_manual_total": pausadas_manual_total,
        "validas_disponibles": validas_disponibles,
        "en_cola_1clic": en_cola,
        "con_email_directo": con_email,
        "alertas_pendientes": len(alertas),
        "portales_integrados": len(catalogo),
        "modo_busqueda": filtros.get("modo_busqueda", "practica_duoc"),
        "termino_personalizado": filtros.get("termino_personalizado", "infraestructura plataformas soporte redes cloud"),
        "carrera": perfil.get("carrera", "Ingeniería en Infraestructura y Plataformas Tecnológicas"),
        "candidato": perfil.get("nombre", "Dhani Amit Elgueta Ríos"),
        "horas_duoc": perfil.get("horas_requeridas", 360),
        "conteo_postulaciones_portal": por_portal,
        "bookmarklet": generar_bookmarklet_universal(perfil),
    }


def get_ofertas_14_portales():
    """Devuelve todas las ofertas de los 14 portales clasificadas en válidas disponibles, postuladas, pausadas/manual, cerradas y otras en cola."""
    init_db()
    perfil = obtener_perfil()
    try:
        from bucle_pantalla_interactivo import obtener_ofertas_validas_pendientes
        validas_ids = {int(o["id"]) for o in obtener_ofertas_validas_pendientes(limite=500)}
    except Exception:
        validas_ids = set()

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM postulaciones ORDER BY id DESC;")
    rows = cur.fetchall()
    conn.close()

    counts = {"validas": 0, "postuladas": 0, "pausadas": 0, "cerradas": 0, "cola_ia": 0, "total": len(rows)}
    items = []
    for r in rows:
        item = dict(r)
        est = item.get("estado") or ""
        oid = int(item.get("id") or 0)
        if oid in validas_ids:
            cat = "validas"
        elif "Cerrada" in est or "Expirada" in est or "No aceptado" in est or "Descartada" in est:
            cat = "cerradas"
        elif "Postulado Confirmado" in est or "entrevista" in est or "Oferta recibida" in est or "Abierta en Brave" in est:
            cat = "postuladas"
        elif "Pausada" in est or "Captcha" in est or "Login" in est:
            cat = "pausadas"
        elif "Cola" in est or "Lista" in est:
            cat = "cola_ia"
        else:
            cat = "pausadas"
        counts[cat] += 1
        item["categoria"] = cat
        item["es_valida_bot"] = oid in validas_ids
        item["mailto_url"] = generar_mailto_postulacion(item, perfil) if item.get("email_reclutador") else ""
        items.append(item)

    return {"ok": True, "counts": counts, "items": items}


def serialize_postulaciones(filtro_estado="Todos", filtro_portal="Todos", search_q="", limit=200):
    init_db()
    perfil = obtener_perfil()
    rows = obtener_postulaciones(filtro_estado=filtro_estado, filtro_portal=filtro_portal)
    q = (search_q or "").strip().lower()
    out = []
    for r in rows:
        if q:
            blob = f"{r.get('titulo','')} {r.get('empresa','')} {r.get('portal','')} {r.get('ubicacion','')}".lower()
            if q not in blob:
                continue
        r_copy = dict(r)
        r_copy["mailto_url"] = generar_mailto_postulacion(r_copy, perfil) if r_copy.get("email_reclutador") else ""
        out.append(r_copy)
        if len(out) >= limit:
            break
    return out


HTML_PAGE = """<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AutoPostula NOC · 14 Portales Chile · Duoc UC & Empleos TI</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-base: #090D16;
            --bg-surface: #0F172A;
            --bg-elevated: #162033;
            --bg-row-sticky: #0F172A;
            --border-subtle: rgba(255, 255, 255, 0.09);
            --border-strong: rgba(56, 189, 248, 0.32);
            --text-main: #F8FAFC;
            --text-secondary: #CBD5E1;
            --text-muted: #94A3B8;
            --accent-emerald: #10B981;
            --accent-sky: #38BDF8;
            --accent-indigo: #6366F1;
            --accent-amber: #F59E0B;
            --accent-rose: #F43F5E;
        }

        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            background-color: var(--bg-base);
            background-image:
                radial-gradient(circle at 12% 10%, rgba(16, 185, 129, 0.09), transparent 40%),
                radial-gradient(circle at 88% 12%, rgba(56, 189, 248, 0.08), transparent 42%);
            color: var(--text-main);
            font-family: 'Plus Jakarta Sans', system-ui, -apple-system, sans-serif;
            min-height: 100vh;
            padding: 20px 24px 48px;
            line-height: 1.45;
        }

        .shell {
            max-width: 1440px;
            margin: 0 auto;
            display: flex;
            flex-direction: column;
            gap: 20px;
        }

        header.topbar {
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 16px;
            padding: 18px 22px;
            background: rgba(15, 23, 42, 0.85);
            border: 1px solid var(--border-subtle);
            border-radius: 16px;
            backdrop-filter: blur(12px);
        }

        .brand {
            display: flex;
            align-items: center;
            gap: 14px;
        }
        .logo-badge {
            width: 46px;
            height: 46px;
            border-radius: 12px;
            background: linear-gradient(135deg, var(--accent-emerald), #0284C7);
            display: grid;
            place-items: center;
            font-size: 22px;
            box-shadow: 0 6px 20px rgba(16, 185, 129, 0.28);
        }
        .brand h1 {
            font-size: 1.32rem;
            font-weight: 800;
            letter-spacing: -0.02em;
        }
        .brand p {
            font-size: 0.82rem;
            color: var(--text-muted);
        }

        .top-actions {
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
            align-items: center;
        }

        button, .btn-link {
            font-family: inherit;
            font-size: 0.82rem;
            font-weight: 700;
            border-radius: 10px;
            padding: 9px 14px;
            cursor: pointer;
            border: 1px solid var(--border-subtle);
            background: var(--bg-elevated);
            color: var(--text-main);
            text-decoration: none;
            display: inline-flex;
            align-items: center;
            gap: 7px;
            transition: background 0.16s ease, border-color 0.16s ease, transform 0.16s ease;
        }
        button:hover, .btn-link:hover {
            background: #1E293B;
            border-color: var(--border-strong);
            transform: translateY(-1px);
        }
        .btn-emerald {
            background: linear-gradient(135deg, #10B981, #059669);
            border-color: rgba(16, 185, 129, 0.5);
            color: #FFFFFF;
        }
        .btn-sky {
            background: linear-gradient(135deg, #0284C7, #2563EB);
            border-color: rgba(56, 189, 248, 0.5);
            color: #FFFFFF;
        }
        .btn-sm {
            padding: 5px 10px;
            font-size: 0.75rem;
            border-radius: 8px;
        }

        /* Bento KPI Strip */
        .kpi-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(205px, 1fr));
            gap: 14px;
        }
        .kpi-card {
            background: var(--bg-surface);
            border: 1px solid var(--border-subtle);
            border-radius: 14px;
            padding: 16px 18px;
            display: flex;
            flex-direction: column;
            gap: 6px;
        }
        .kpi-label {
            font-size: 0.72rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.06em;
            color: var(--text-muted);
        }
        .kpi-val {
            font-family: 'JetBrains Mono', monospace;
            font-variant-numeric: tabular-nums;
            font-size: 1.65rem;
            font-weight: 800;
            color: var(--text-main);
        }
        .kpi-sub {
            font-size: 0.78rem;
            color: var(--text-muted);
        }

        /* Duoc UC Compliance & Mode Control Bar */
        .control-grid {
            display: grid;
            grid-template-columns: 1.35fr 1fr;
            gap: 16px;
        }
        @media (max-width: 1024px) {
            .control-grid { grid-template-columns: 1fr; }
        }

        .panel {
            background: var(--bg-surface);
            border: 1px solid var(--border-subtle);
            border-radius: 16px;
            padding: 18px 20px;
        }

        /* Optimization for complex multi-column layout regions (modern-web-guidance: interactions-in-complex-layouts) */
        .board-column {
            content-visibility: auto;
            contain-intrinsic-size: auto 340px auto 680px;
        }
        @supports not (content-visibility: auto) {
            .board-column {
                contain: layout style paint;
            }
        }

        .panel-title {
            font-size: 0.98rem;
            font-weight: 800;
            margin-bottom: 12px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 10px;
            flex-wrap: wrap;
        }

        .form-row {
            display: grid;
            grid-template-columns: 1fr 1.3fr auto;
            gap: 10px;
            align-items: end;
        }
        @media (max-width: 780px) {
            .form-row { grid-template-columns: 1fr; }
        }
        label.field-label {
            display: block;
            font-size: 0.75rem;
            font-weight: 600;
            color: var(--text-muted);
            margin-bottom: 5px;
        }
        select, input[type="text"], input[type="search"] {
            width: 100%;
            background: #090D16;
            color: var(--text-main);
            border: 1px solid var(--border-subtle);
            border-radius: 9px;
            padding: 9px 12px;
            font-family: inherit;
            font-size: 0.84rem;
        }
        select:focus, input:focus {
            outline: 2px solid var(--accent-sky);
            border-color: transparent;
        }

        .duoc-checklist {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
            gap: 8px;
            margin-top: 8px;
        }
        .check-pill {
            background: rgba(16, 185, 129, 0.08);
            border: 1px solid rgba(16, 185, 129, 0.25);
            border-radius: 10px;
            padding: 8px 11px;
            font-size: 0.78rem;
            color: #D1FAE5;
        }

        /* 14 Portals NOC Grid */
        .portals-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(235px, 1fr));
            gap: 10px;
        }
        .portal-card {
            background: #0B111E;
            border: 1px solid var(--border-subtle);
            border-radius: 12px;
            padding: 12px 14px;
            display: flex;
            flex-direction: column;
            gap: 6px;
        }
        .portal-head {
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 8px;
        }
        .portal-name {
            font-weight: 700;
            font-size: 0.86rem;
            color: #F1F5F9;
        }
        .dot-live {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: var(--accent-emerald);
            box-shadow: 0 0 8px var(--accent-emerald);
            display: inline-block;
        }
        .portal-meta {
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.71rem;
            color: var(--accent-sky);
        }
        .portal-notes {
            font-size: 0.74rem;
            color: var(--text-muted);
        }

        /* Responsive Sticky + Container-Queried Stacked Table (modern-web-guidance: responsive-table) */
        .table-toolbar {
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 12px;
        }
        .table-filters {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            align-items: center;
        }

        .table-wrapper {
            overflow: auto;
            max-inline-size: 100%;
            max-block-size: min(640px, 78vh);
            container-type: inline-size;
            border: 1px solid var(--border-subtle);
            border-radius: 12px;
            background: #0B111E;
        }
        .table-wrapper:focus-visible {
            outline: 2px solid var(--accent-sky);
        }

        table.offers-table {
            border-collapse: separate;
            border-spacing: 0;
            width: 100%;
            font-size: 0.83rem;
            --label-2: "Portal";
            --label-3: "Empresa y Ubicación";
            --label-4: "Afinidad Duoc";
            --label-5: "Estado";
            --label-6: "Acciones 1-Clic";
        }
        caption.sr-caption {
            text-align: left;
            padding: 10px 14px;
            font-size: 0.78rem;
            color: var(--text-muted);
            background: #090D16;
            border-bottom: 1px solid var(--border-subtle);
        }

        table.offers-table thead {
            position: sticky;
            inset-block-start: 0;
            z-index: 2;
        }
        table.offers-table thead th {
            background: #131C31;
            color: #94A3B8;
            font-size: 0.73rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            padding: 12px 14px;
            text-align: left;
            border-bottom: 1px solid var(--border-subtle);
            white-space: nowrap;
        }

        table.offers-table tbody th {
            position: sticky;
            inset-inline-start: 0;
            z-index: 1;
            background: var(--bg-row-sticky);
            color: #F8FAFC;
            font-weight: 700;
            text-align: left;
            padding: 12px 14px;
            border-bottom: 1px solid rgba(255,255,255,0.05);
            min-width: 230px;
            max-width: 330px;
        }
        table.offers-table tbody td {
            padding: 12px 14px;
            border-bottom: 1px solid rgba(255,255,255,0.05);
            color: var(--text-secondary);
            vertical-align: middle;
        }
        table.offers-table tbody tr:hover th,
        table.offers-table tbody tr:hover td {
            background: #162238;
        }

        @container (width < 600px) {
            table.offers-table,
            table.offers-table thead,
            table.offers-table tbody,
            table.offers-table tr,
            table.offers-table th,
            table.offers-table td {
                display: block;
            }
            table.offers-table thead {
                /* MANDATORY: Hide column header from layout and screen readers. */
                display: none;
            }
            table.offers-table tr {
                margin-block-end: 1.25rem;
                border: 1px solid rgba(255, 255, 255, 0.14);
                border-radius: 10px;
            }
            table.offers-table tbody th {
                position: sticky;
                inset-block-start: 0;
                z-index: 2;
                background: #1E293B;
                max-width: none;
            }
            table.offers-table td:nth-child(2)::before {
                content: var(--label-2) ": ";
                font-weight: 700;
                color: var(--text-muted);
            }
            table.offers-table td:nth-child(3)::before {
                content: var(--label-3) ": ";
                font-weight: 700;
                color: var(--text-muted);
            }
            table.offers-table td:nth-child(4)::before {
                content: var(--label-4) ": ";
                font-weight: 700;
                color: var(--text-muted);
            }
            table.offers-table td:nth-child(5)::before {
                content: var(--label-5) ": ";
                font-weight: 700;
                color: var(--text-muted);
            }
            table.offers-table td:nth-child(6)::before {
                content: var(--label-6) ": ";
                font-weight: 700;
                color: var(--text-muted);
            }
        }

        .badge {
            display: inline-flex;
            align-items: center;
            gap: 5px;
            padding: 3px 9px;
            border-radius: 999px;
            font-size: 0.73rem;
            font-weight: 700;
            white-space: nowrap;
        }
        .badge-portal {
            background: rgba(56, 189, 248, 0.14);
            color: #7DD3FC;
            border: 1px solid rgba(56, 189, 248, 0.3);
        }
        .badge-score {
            font-family: 'JetBrains Mono', monospace;
            background: rgba(16, 185, 129, 0.15);
            color: #6EE7B7;
            border: 1px solid rgba(16, 185, 129, 0.35);
        }

        .actions-cell {
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
            align-items: center;
        }

        .logs-box {
            background: #070A12;
            border: 1px solid var(--border-subtle);
            border-radius: 12px;
            padding: 12px 14px;
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.77rem;
            max-height: 230px;
            overflow-y: auto;
            display: flex;
            flex-direction: column;
            gap: 6px;
        }

        /* Light-dismiss Modal Dialogs (modern-web-guidance: light-dismiss-a-dialog) */
        dialog {
            margin: auto;
            width: min(680px, 92vw);
            background: #0F172A;
            color: var(--text-main);
            border: 1px solid var(--border-strong);
            border-radius: 16px;
            padding: 22px;
            box-shadow: 0 24px 60px rgba(0, 0, 0, 0.65);
        }
        dialog::backdrop {
            background-color: rgba(2, 6, 23, 0.72);
            backdrop-filter: blur(4px);
        }
        .qa-item {
            background: #090D16;
            border: 1px solid var(--border-subtle);
            border-radius: 10px;
            padding: 12px;
            margin-top: 10px;
        }
        .qa-q {
            font-size: 0.8rem;
            font-weight: 700;
            color: var(--accent-sky);
            margin-bottom: 5px;
        }
        .qa-a {
            font-size: 0.83rem;
            color: var(--text-secondary);
            white-space: pre-wrap;
        }
        .v14-box-inline {
            margin-top: 6px;
            padding: 6px 8px;
            border-radius: 8px;
            background: rgba(56, 189, 248, 0.10);
            border: 1px solid rgba(56, 189, 248, 0.28);
            font-size: 0.74rem;
            font-weight: 700;
            color: #BAE6FD;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 6px;
        }
        dialog#validas14Dialog {
            width: min(1080px, 95vw);
            max-height: 90vh;
            overflow-y: auto;
        }
        .v14-hero {
            display: grid;
            grid-template-columns: auto 1fr;
            gap: 16px;
            align-items: center;
            background: linear-gradient(135deg, rgba(14, 165, 233, 0.16), rgba(16, 185, 129, 0.10));
            border: 1px solid rgba(56, 189, 248, 0.35);
            border-radius: 14px;
            padding: 14px 18px;
            margin-bottom: 12px;
        }
        .v14-big-num {
            font-family: 'JetBrains Mono', monospace;
            font-size: 3.1rem;
            font-weight: 800;
            line-height: 1;
            color: #38BDF8;
            min-width: 82px;
            text-align: center;
        }
        .v14-tabs {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            margin-bottom: 10px;
        }
        .v14-tab {
            background: #090D16;
            color: var(--text-secondary);
            border: 1px solid var(--border-subtle);
            border-radius: 999px;
            padding: 6px 12px;
            font-size: 0.78rem;
            font-weight: 700;
            cursor: pointer;
        }
        .v14-tab.active {
            background: #0284C7;
            color: #FFFFFF;
            border-color: #38BDF8;
        }
        .v14-grid {
            display: grid;
            grid-template-columns: minmax(280px, 0.95fr) 1.35fr;
            gap: 14px;
            margin-top: 10px;
        }
        @media (max-width: 860px) {
            .v14-grid { grid-template-columns: 1fr; }
        }
        .v14-detail-box {
            background: #090D16;
            border: 1px solid var(--border-subtle);
            border-radius: 12px;
            padding: 14px;
            max-height: 52vh;
            overflow-y: auto;
        }

        #toast {
            position: fixed;
            bottom: 22px;
            right: 22px;
            background: #10B981;
            color: #042F2E;
            font-weight: 800;
            font-size: 0.84rem;
            padding: 12px 18px;
            border-radius: 12px;
            box-shadow: 0 12px 30px rgba(0,0,0,0.45);
            display: none;
            z-index: 50;
        }
    </style>
</head>
<body>
<div class="shell">
    <header class="topbar">
        <div class="brand">
            <div class="logo-badge">🎯</div>
            <div>
                <h1>AutoPostula NOC · 14 Portales Reales de Chile</h1>
                <p id="subtitle-carrera">Ingeniería en Infraestructura y Plataformas Tecnológicas · Duoc UC (360h) &amp; Empleos TI</p>
            </div>
        </div>
        <div class="top-actions">
            <span id="bot-live-badge" style="padding:6px 10px; border-radius:999px; font-size:12px; font-weight:700; background:rgba(148,163,184,0.16); color:#cbd5e1; border:1px solid rgba(148,163,184,0.3);">⚪ Bot Inactivo</span>
            <button id="btn-interactive-loop" class="btn-emerald" onclick="startInteractiveLoop()" style="background: linear-gradient(135deg, #0284c7, #2563eb); border: none; font-weight:800;" title="Ejecuta el bucle de postulación sobre pantalla con Brave, lectura por voz y widget flotante de 30s">🤖 Bucle en Pantalla (30s)</button>
            <button type="button" id="btn-revisar-14" class="btn-sky" onclick="openValidas14Modal('validas')" style="background: linear-gradient(135deg, #0ea5e9, #6366f1); color:#fff; border:none; font-weight:800;" title="Despliega el total en grande y separa Válidas Disponibles, Postuladas, Pausadas y Cerradas en los 14 portales">🔍 Revisar Válidas (14 Portales)</button>
            <button class="btn-emerald" onclick="startHeadlessBatch()" style="background: linear-gradient(135deg, #059669, #10b981); border: none; font-weight:800;" title="Postula automáticamente a todas las ofertas válidas en segundo plano sin pausar">⚡ Postular Todas (Segundo Plano)</button>
            <button class="btn-danger" onclick="stopBotProcess()" style="background: #ef4444; color: white; border: none; font-weight:800; border-radius:8px; padding:6px 12px; cursor:pointer;" title="Detiene cualquier proceso activo del bot de pantalla">🛑 Detener Bot</button>
            <button class="btn-emerald" onclick="openNextBatch()">🚀 Abrir Lote 10 en Brave</button>
            <button class="btn-sky" onclick="triggerManualScan()">🌐 Escanear 14 Portales Ahora</button>
            <button onclick="openPersistentSession()">🔐 Sesión Persistente</button>
            <button onclick="openBookmarkletModal()">⚡ Bookmarklet</button>
            <a class="btn-link" href="http://localhost:8503" target="_blank" rel="noopener">📊 Streamlit (:8503)</a>
        </div>
    </header>

    <section class="kpi-grid">
        <div class="kpi-card">
            <span class="kpi-label">Portales Reales Activos</span>
            <span class="kpi-val" id="kpi-portales">14</span>
            <span class="kpi-sub">API JSON + SSR + Playwright Brave</span>
            <div class="v14-box-inline">
                <span id="kpi-validas-text">Válidas bot: 0</span>
                <button type="button" class="btn-sm btn-sky" onclick="openValidas14Modal('validas')" style="padding:3px 8px; font-size:0.72rem;">🔍 Ver 14</button>
            </div>
        </div>
        <div class="kpi-card">
            <span class="kpi-label">Total Ofertas Procesadas IA</span>
            <span class="kpi-val" id="kpi-total">0</span>
            <span class="kpi-sub" id="kpi-email-sub">0 con correo directo reclutador</span>
        </div>
        <div class="kpi-card">
            <span class="kpi-label">Postuladas Confirmadas</span>
            <span class="kpi-val" id="kpi-activas" style="color:#34D399">0</span>
            <span class="kpi-sub" id="kpi-activas-sub">Confirmadas en navegador · Cerradas: 0</span>
        </div>
        <div class="kpi-card">
            <span class="kpi-label">En Cola 1-Clic (Respuestas IA)</span>
            <span class="kpi-val" id="kpi-cola" style="color:#38BDF8">0</span>
            <span class="kpi-sub">Usa «Abrir Lote 10» o «Revisar Válidas»</span>
        </div>
        <div class="kpi-card">
            <span class="kpi-label">Piloto Automático 24/7</span>
            <span class="kpi-val" id="kpi-daemon" style="font-size:1.15rem">Verificando...</span>
            <span class="kpi-sub" id="kpi-proximo">Próximo: --</span>
        </div>
    </section>

    <section class="control-grid">
        <div class="panel board-column">
            <div class="panel-title">
                <span>⚙️ Modo Universal: Práctica Duoc UC o Cualquier Pega / Empleo</span>
                <button class="btn-sm" onclick="toggleScheduler()" id="btn-toggle-sched">⏱️ Alternar Piloto 24/7</button>
            </div>
            <div class="form-row">
                <div>
                    <label class="field-label" for="modo-select">Objetivo de Búsqueda y Postulación</label>
                    <select id="modo-select">
                        <option value="practica_duoc">🎓 Solo Práctica Profesional Duoc UC (Ing. Infraestructura · 360h)</option>
                        <option value="empleo_ti">💼 Cualquier Pega / Empleo TI Regular</option>
                        <option value="ambos">🌐 Ambos (Prácticas Profesionales + Empleos TI)</option>
                    </select>
                </div>
                <div>
                    <label class="field-label" for="termino-input">Especialidad / Cargo a Buscar en los 14 Portales</label>
                    <input type="text" id="termino-input" value="infraestructura plataformas soporte redes cloud" placeholder="Ej: infraestructura cloud redes linux devops soporte">
                </div>
                <div>
                    <button class="btn-emerald" onclick="saveFiltersAndScan()">💾 Guardar &amp; Escanear</button>
                </div>
            </div>
            <div class="duoc-checklist">
                <div class="check-pill">✅ <strong>Carrera:</strong> Ing. en Infraestructura y Plataformas Tecnológicas</div>
                <div class="check-pill">✅ <strong>Duración Oficial:</strong> 360 horas cronológicas mínimas</div>
                <div class="check-pill">✅ <strong>Cobertura:</strong> Seguro Escolar Duoc UC Ley 16.744</div>
                <div class="check-pill">✅ <strong>Horario:</strong> Diurno inmediato (estudio vespertino A. Varas)</div>
            </div>
        </div>

        <div class="panel board-column">
            <div class="panel-title">
                <span>📜 Registro en Vivo del Motor Multi-Portal</span>
                <span style="font-size:0.75rem; color:var(--text-muted)">8 ventanas horarias diarias</span>
            </div>
            <div class="logs-box" id="logs-container">
                <span>Cargando actividad del motor...</span>
            </div>
        </div>
    </section>

    <section class="panel board-column">
        <div class="panel-title">
            <span>📡 Radar Técnico de los 14 Portales Conectados en Chile</span>
            <span style="font-size:0.78rem; color:var(--text-muted)">Selectores DOM, APIs públicas y sesiones mapeadas</span>
        </div>
        <div class="portals-grid" id="portals-grid"></div>
    </section>

    <section class="panel board-column">
        <div class="table-toolbar">
            <div class="panel-title" style="margin-bottom:0">
                <span>📋 Cola Activa e Historial de Postulaciones</span>
            </div>
            <div class="table-filters">
                <input type="search" id="search-offers" placeholder="Buscar empresa, cargo, nube, redes..." oninput="loadPostulaciones()" style="width:230px">
                <select id="filter-portal" onchange="loadPostulaciones()" style="width:190px">
                    <option value="Todos">Todos los portales (14)</option>
                </select>
                <select id="filter-estado" onchange="loadPostulaciones()" style="width:220px">
                    <option value="Todos">Todos los estados</option>
                    <option value="🟢 Postulado Confirmado">🟢 Postulado Confirmado</option>
                    <option value="🟡 Lista con IA (En Cola 1-Clic)">🟡 En Cola 1-Clic (Lista con IA)</option>
                    <option value="🟡 Pausada (Requiere Login / Revisión)">🟡 Pausada (Login / Revisión)</option>
                    <option value="🛡️ Captcha / Verificación Manual">🛡️ Captcha / Verificación Manual</option>
                    <option value="🔑 Requiere Login Manual">🔑 Requiere Login Manual</option>
                    <option value="🔴 Oferta Cerrada / Expirada">🔴 Oferta Cerrada / Expirada</option>
                    <option value="🟡 Postulado (Abierta en Brave)">🟡 Abierta en Brave</option>
                    <option value="🟣 En entrevista">🟣 En entrevista</option>
                    <option value="🟢 Oferta recibida">🟢 Oferta recibida</option>
                    <option value="🔴 No aceptado">🔴 No aceptado</option>
                </select>
            </div>
        </div>

        <div tabindex="0" role="region" aria-labelledby="postulaciones-table-caption" class="table-wrapper">
            <table class="offers-table">
                <caption id="postulaciones-table-caption" class="sr-caption">
                    Ofertas reales filtradas para Ingeniería en Infraestructura y Plataformas Tecnológicas (Duoc UC) y Empleos TI
                </caption>
                <thead>
                    <tr>
                        <th scope="col">Cargo / Vacante</th>
                        <th scope="col">Portal</th>
                        <th scope="col">Empresa y Ubicación</th>
                        <th scope="col">Afinidad Duoc</th>
                        <th scope="col">Estado</th>
                        <th scope="col">Acciones 1-Clic</th>
                    </tr>
                </thead>
                <tbody id="postulaciones-body">
                    <tr><th scope="row">Cargando...</th><td colspan="5">Consultando base SQLite...</td></tr>
                </tbody>
            </table>
        </div>
    </section>
</div>

<!-- Modal Respuestas IA con Light-Dismiss declarativo (closedby="any") -->
<dialog id="respuestasDialog" closedby="any" aria-labelledby="dialogTitle">
    <form method="dialog">
        <div style="display:flex; justify-content:space-between; align-items:center; gap:12px; margin-bottom:8px;">
            <h2 id="dialogTitle" style="font-size:1.05rem;">🤖 Respuestas IA Personalizadas</h2>
            <button type="submit" class="btn-sm">✕ Cerrar</button>
        </div>
        <p id="dialogSubtitle" style="font-size:0.82rem; color:var(--text-muted); margin-bottom:10px;"></p>
        <div id="dialogQuestionsBody"></div>
    </form>
</dialog>

<!-- Modal Bookmarklet Universal 14 Portales -->
<dialog id="bookmarkletDialog" closedby="any" aria-labelledby="bmDialogTitle">
    <form method="dialog">
        <div style="display:flex; justify-content:space-between; align-items:center; gap:12px; margin-bottom:10px;">
            <h2 id="bmDialogTitle" style="font-size:1.05rem;">⚡ Bookmarklet Universal 1-Clic (14 Portales)</h2>
            <button type="submit" class="btn-sm">✕ Cerrar</button>
        </div>
        <p style="font-size:0.83rem; color:var(--text-secondary); margin-bottom:12px;">
            Arrastra el botón verde a tu barra de marcadores de Brave o copia el código. Al abrir cualquier oferta en
            <strong>DuocLaboral, FirstJob, Trabajando.cl, Laborum, Computrabajo, LinkedIn, Get on Board, BNE o Buk/AIRA</strong>,
            haz 1 clic para rellenar automáticamente RUT, teléfono, correo Duoc, 360 horas, Seguro Escolar y respuestas técnicas de Infraestructura.
        </p>
        <div style="display:flex; gap:10px; align-items:center; margin-bottom:12px; flex-wrap:wrap;">
            <a id="bmDragLink" href="#" class="btn-link btn-emerald" onclick="event.preventDefault(); copyBookmarklet();">⚡ Rellenar Práctica Infra Duoc</a>
            <button type="button" class="btn-sky" onclick="copyBookmarklet()">📋 Copiar Código JS</button>
        </div>
        <textarea id="bmCodeArea" readonly style="width:100%; height:130px; background:#070A12; color:#7DD3FC; border:1px solid var(--border-subtle); border-radius:10px; padding:10px; font-family:'JetBrains Mono',monospace; font-size:0.74rem;"></textarea>
    </form>
</dialog>

<!-- Modal Revisar Ofertas Válidas en los 14 Portales -->
<dialog id="validas14Dialog" closedby="any" aria-labelledby="v14DialogTitle">
    <form method="dialog">
        <div style="display:flex; justify-content:space-between; align-items:center; gap:12px; margin-bottom:10px;">
            <h2 id="v14DialogTitle" style="font-size:1.08rem;">🔍 Ofertas en los 14 Portales · Válidas, Postuladas y Cerradas</h2>
            <button type="submit" class="btn-sm">✕ Cerrar</button>
        </div>

        <div class="v14-hero">
            <div class="v14-big-num" id="v14-big-count">0</div>
            <div>
                <div id="v14-big-label" style="font-size:0.95rem; font-weight:800; color:#F8FAFC;">Ofertas en categoría seleccionada</div>
                <div id="v14-summary-line" style="font-size:0.78rem; color:var(--text-secondary); margin-top:4px;">Cargando resumen de los 14 portales...</div>
            </div>
        </div>

        <div class="v14-tabs">
            <button type="button" class="v14-tab active" data-cat="validas" onclick="setValidas14Tab('validas')">✅ Válidas Disponibles (<span id="v14-c-validas">0</span>)</button>
            <button type="button" class="v14-tab" data-cat="postuladas" onclick="setValidas14Tab('postuladas')">🟢 Postulados (<span id="v14-c-postuladas">0</span>)</button>
            <button type="button" class="v14-tab" data-cat="pausadas" onclick="setValidas14Tab('pausadas')">🟡 Pausadas / CAPTCHA (<span id="v14-c-pausadas">0</span>)</button>
            <button type="button" class="v14-tab" data-cat="cerradas" onclick="setValidas14Tab('cerradas')">🔴 Ofertas Cerradas (<span id="v14-c-cerradas">0</span>)</button>
            <button type="button" class="v14-tab" data-cat="cola_ia" onclick="setValidas14Tab('cola_ia')">📦 Otras en Cola IA (<span id="v14-c-cola_ia">0</span>)</button>
        </div>

        <div style="display:flex; gap:8px; flex-wrap:wrap;">
            <input type="search" id="v14-search" placeholder="Filtrar por cargo, empresa, notas..." oninput="renderValidas14List()" style="flex:1; min-width:200px;">
            <select id="v14-portal" onchange="renderValidas14List()" style="width:210px;">
                <option value="Todos">Todos los portales (14)</option>
            </select>
        </div>

        <div class="v14-grid">
            <div>
                <label class="field-label" for="v14-select">Lista desplegable / seleccionable (haz clic para ver detalle)</label>
                <select id="v14-select" size="12" onchange="selectValidas14ById(this.value)" style="width:100%; height:52vh; background:#090D16; color:#F8FAFC; border:1px solid var(--border-subtle); border-radius:12px; padding:6px; font-size:0.8rem;">
                    <option value="">Cargando...</option>
                </select>
            </div>
            <div class="v14-detail-box" id="v14-detail">
                <p style="color:var(--text-muted); font-size:0.84rem;">Selecciona una oferta de la lista para ver sus detalles, descripción, estado y abrir su enlace.</p>
            </div>
        </div>
    </form>
</dialog>

<div id="toast"></div>

<script>
    let CACHED_POSTS = {};
    let BOOKMARKLET_RAW = "";

    // Fallback obligatorio para navegadores sin soporte nativo de closedby en <dialog>
    if (!('closedBy' in HTMLDialogElement.prototype)) {
        document.querySelectorAll('dialog').forEach((dialog) => {
            dialog.addEventListener('click', (event) => {
                if (event.target !== dialog) return;
                const rect = dialog.getBoundingClientRect();
                const isDialogContent = (
                    rect.top <= event.clientY &&
                    event.clientY <= rect.top + rect.height &&
                    rect.left <= event.clientX &&
                    event.clientX <= rect.left + rect.width
                );
                if (isDialogContent) return;
                dialog.close();
            });
        });
    }

    function escapeHtml(str) {
        return String(str ?? "")
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;");
    }

    function showToast(msg) {
        const t = document.getElementById("toast");
        t.textContent = msg;
        t.style.display = "block";
        clearTimeout(window.__toastTimer);
        window.__toastTimer = setTimeout(() => { t.style.display = "none"; }, 3800);
    }

    async function loadStats() {
        try {
            const res = await fetch("/api/stats");
            const d = await res.json();
            document.getElementById("kpi-portales").textContent = d.portales_integrados;
            document.getElementById("kpi-total").textContent = d.total_postulaciones;
            document.getElementById("kpi-email-sub").textContent = `${d.con_email_directo} con correo directo de reclutador`;
            document.getElementById("kpi-activas").textContent = d.postuladas_activas;
            const kActSub = document.getElementById("kpi-activas-sub");
            if (kActSub) kActSub.textContent = `Confirmadas en navegador · Cerradas: ${d.cerradas_total ?? 0}`;
            const kValTxt = document.getElementById("kpi-validas-text");
            if (kValTxt) kValTxt.textContent = `Válidas disponibles: ${d.validas_disponibles ?? 0}`;
            const btnRev = document.getElementById("btn-revisar-14");
            if (btnRev) btnRev.textContent = `🔍 Revisar Válidas (${d.validas_disponibles ?? 0} / 14 Portales)`;
            document.getElementById("kpi-cola").textContent = d.en_cola_1clic;

            const kDaemon = document.getElementById("kpi-daemon");
            if (d.daemon_active && d.scheduler_enabled) {
                kDaemon.textContent = `🟢 ACTIVO (PID ${d.daemon_pid})`;
                kDaemon.style.color = "#34D399";
            } else if (d.daemon_active) {
                kDaemon.textContent = `🟡 EN ESPERA (PID ${d.daemon_pid})`;
                kDaemon.style.color = "#FBBF24";
            } else {
                kDaemon.textContent = "⚪ MODO MANUAL";
                kDaemon.style.color = "#94A3B8";
            }
            document.getElementById("kpi-proximo").textContent = `Próximo: ${d.proximo_horario}`;

            if (!window.__filtersInitialized) {
                document.getElementById("modo-select").value = d.modo_busqueda || "practica_duoc";
                document.getElementById("termino-input").value = d.termino_personalizado || "";
                window.__filtersInitialized = true;
            }
            if (d.bookmarklet) {
                BOOKMARKLET_RAW = d.bookmarklet;
                document.getElementById("bmCodeArea").value = d.bookmarklet;
                document.getElementById("bmDragLink").setAttribute("href", d.bookmarklet);
            }
            return d.conteo_postulaciones_portal || {};
        } catch (e) {
            return {};
        }
    }

    async function loadPortales(conteoPorPortal) {
        try {
            const res = await fetch("/api/portales");
            const portales = await res.json();
            const sel = document.getElementById("filter-portal");
            if (sel.options.length <= 1) {
                portales.forEach(p => {
                    const opt = document.createElement("option");
                    opt.value = p.nombre;
                    opt.textContent = p.nombre;
                    sel.appendChild(opt);
                });
            }
            const grid = document.getElementById("portals-grid");
            grid.innerHTML = portales.map(p => {
                const enBd = (conteoPorPortal && conteoPorPortal[p.nombre]) ? conteoPorPortal[p.nombre] : 0;
                const tags = [
                    p.soporta_practicas ? "Prácticas" : null,
                    p.soporta_empleos ? "Empleos" : null
                ].filter(Boolean).join(" · ");
                return `
                    <div class="portal-card">
                        <div class="portal-head">
                            <span class="portal-name"><span class="dot-live"></span> ${escapeHtml(p.nombre)}</span>
                            <span class="badge badge-score">${enBd} en cola/BD</span>
                        </div>
                        <div class="portal-meta">${escapeHtml(p.tipo_extraccion)}</div>
                        <div class="portal-notes">${escapeHtml(tags)} — ${escapeHtml((p.notas_tecnicas || "").slice(0, 92))}</div>
                    </div>
                `;
            }).join("");
        } catch (e) {}
    }

    async function loadPostulaciones() {
        const tbody = document.getElementById("postulaciones-body");
        const portal = document.getElementById("filter-portal").value;
        const estado = document.getElementById("filter-estado").value;
        const q = document.getElementById("search-offers").value;
        try {
            const params = new URLSearchParams({ portal, estado, q });
            const res = await fetch("/api/postulaciones?" + params.toString());
            const items = await res.json();
            CACHED_POSTS = {};
            if (!items.length) {
                tbody.innerHTML = `<tr><th scope="row">Sin resultados</th><td colspan="5">No hay ofertas que coincidan con el filtro seleccionado.</td></tr>`;
                return;
            }
            const estadosOpts = [
                "🟡 Lista con IA (En Cola 1-Clic)",
                "🟡 Postulado (Abierta en Brave)",
                "🟢 Postulado Confirmado",
                "🟣 En entrevista",
                "🟢 Oferta recibida",
                "🔴 No aceptado"
            ];
            tbody.innerHTML = items.map(p => {
                CACHED_POSTS[p.id] = p;
                const emailBtn = p.mailto_url
                    ? `<a class="btn-link btn-sm btn-emerald" href="${escapeHtml(p.mailto_url)}" title="Enviar correo pre-redactado a ${escapeHtml(p.email_reclutador)}">✉️ Email Directo</a>`
                    : "";
                const optsHtml = estadosOpts.map(st =>
                    `<option value="${escapeHtml(st)}" ${p.estado === st ? "selected" : ""}>${escapeHtml(st)}</option>`
                ).join("");

                return `
                    <tr>
                        <th scope="row">
                            <div style="font-size:0.86rem; line-height:1.3;">${escapeHtml(p.titulo)}</div>
                            <div style="font-size:0.72rem; color:var(--text-muted); font-weight:500; margin-top:3px;">
                                📅 ${escapeHtml(p.fecha_oferta || p.fecha_postulacion || "Reciente")} · ${escapeHtml(p.asignacion_dinero || "Asignación")}
                            </div>
                        </th>
                        <td><span class="badge badge-portal">${escapeHtml(p.portal)}</span></td>
                        <td>
                            <div style="font-weight:700; color:#F1F5F9;">${escapeHtml(p.empresa)}</div>
                            <div style="font-size:0.74rem; color:var(--text-muted);">📍 ${escapeHtml(p.ubicacion || "Santiago, RM")}</div>
                        </td>
                        <td><span class="badge badge-score">${Number(p.score_duoc || 90)}% Duoc</span></td>
                        <td>
                            <select onchange="updateOfferStatus(${p.id}, this.value)" style="padding:5px 8px; font-size:0.75rem; min-width:175px;">
                                ${optsHtml}
                            </select>
                        </td>
                        <td>
                            <div class="actions-cell">
                                <a class="btn-link btn-sm btn-sky" href="${escapeHtml(p.url_oferta || '#')}" target="_blank" rel="noopener">🌐 Abrir Portal</a>
                                <button type="button" class="btn-sm" onclick="showAnswersModal(${p.id})">📝 Respuestas IA</button>
                                ${emailBtn}
                            </div>
                        </td>
                    </tr>
                `;
            }).join("");
        } catch (e) {
            tbody.innerHTML = `<tr><th scope="row">Error</th><td colspan="5">No se pudo cargar la tabla de postulaciones.</td></tr>`;
        }
    }

    async function loadLogs() {
        const box = document.getElementById("logs-container");
        try {
            const res = await fetch("/api/logs");
            const logs = await res.json();
            if (!logs.length) {
                box.innerHTML = `<span style="color:var(--text-muted)">Sin ejecuciones previas registradas.</span>`;
                return;
            }
            box.innerHTML = logs.map(l => `
                <div>
                    <span style="color:#64748B">[${escapeHtml(l.fecha_hora)}]</span>
                    <span style="color:#34D399; font-weight:700;"> +${Number(l.ofertas_postuladas || 0)} procesadas</span>
                    <span style="color:#CBD5E1"> — ${escapeHtml(l.detalle || "")}</span>
                </div>
            `).join("");
        } catch (e) {}
    }

    function showAnswersModal(id) {
        const p = CACHED_POSTS[id];
        if (!p) return;
        const dlg = document.getElementById("respuestasDialog");
        document.getElementById("dialogTitle").textContent = `🤖 Respuestas IA · ${p.empresa}`;
        document.getElementById("dialogSubtitle").textContent = `${p.titulo} (${p.portal})` + (p.email_reclutador ? ` · ✉️ Contacto directo: ${p.email_reclutador}` : "");
        const body = document.getElementById("dialogQuestionsBody");
        let parsed = {};
        try { parsed = JSON.parse(p.respuestas_formulario || "{}"); } catch (e) {}
        const entries = Object.entries(parsed);
        if (!entries.length) {
            body.innerHTML = `<p style="color:var(--text-muted)">Sin preguntas específicas registradas.</p>`;
        } else {
            body.innerHTML = entries.map(([q, a]) => `
                <div class="qa-item">
                    <div class="qa-q">❓ ${escapeHtml(q)}</div>
                    <div class="qa-a">${escapeHtml(a)}</div>
                </div>
            `).join("");
        }
        dlg.showModal();
    }

    function openBookmarkletModal() {
        document.getElementById("bookmarkletDialog").showModal();
    }

    async function copyBookmarklet() {
        if (!BOOKMARKLET_RAW) return;
        try {
            await navigator.clipboard.writeText(BOOKMARKLET_RAW);
            showToast("⚡ Bookmarklet copiado al portapapeles");
        } catch (e) {
            document.getElementById("bmCodeArea").select();
            document.execCommand("copy");
            showToast("⚡ Bookmarklet copiado");
        }
    }

    async function updateOfferStatus(id, estado) {
        await fetch("/api/update-status", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ id, estado })
        });
        showToast("✅ Estado actualizado");
        loadStats();
    }

    async function saveFiltersAndScan() {
        const modo_busqueda = document.getElementById("modo-select").value;
        const termino_personalizado = document.getElementById("termino-input").value;
        await fetch("/api/save-filters", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ modo_busqueda, termino_personalizado })
        });
        await triggerManualScan();
    }

    async function triggerManualScan() {
        showToast("🌐 Escaneando los 14 portales en segundo plano...");
        await fetch("/api/run-scan", { method: "POST" });
        setTimeout(refreshAll, 4500);
    }

    async function startInteractiveLoop() {
        showToast("🚀 Verificando cola y lanzando bucle interactivo (30s)...");
        const res = await fetch("/api/start-interactive-loop", { method: "POST" });
        const d = await res.json();
        if (d.ok) {
            showToast("✅ Bucle interactivo iniciado en pantalla (" + (d.pendientes ?? "") + " ofertas).");
        } else {
            showToast("⚠️ " + (d.error || "No se pudo iniciar"));
        }
        setTimeout(refreshAll, 2000);
    }

    async function startHeadlessBatch() {
        if (!confirm("¿Deseas iniciar la postulación continua en segundo plano para todas las ofertas válidas de la RM?")) return;
        showToast("⚡ Verificando cola y lanzando postulación en segundo plano...");
        const res = await fetch("/api/start-headless-batch", { method: "POST" });
        const d = await res.json();
        if (d.ok) {
            showToast("✅ Postulación en segundo plano ejecutándose.");
        } else {
            showToast("⚠️ " + (d.error || "No se pudo iniciar"));
        }
        setTimeout(refreshAll, 2000);
    }

    async function stopBotProcess() {
        showToast("🛑 Deteniendo procesos del bot...");
        const res = await fetch("/api/stop-bot", { method: "POST" });
        const d = await res.json();
        showToast(d.ok ? "🛑 Procesos del bot detenidos." : "No había procesos activos.");
        setTimeout(refreshAll, 1500);
    }

    async function openNextBatch() {
        const res = await fetch("/api/open-next-batch", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ cantidad: 10 })
        });
        const d = await res.json();
        showToast(`🚀 Abiertas ${d.abiertas || 0} ofertas prioritarias en Brave`);
        refreshAll();
    }

    async function openPersistentSession() {
        const res = await fetch("/api/open-session", { method: "POST" });
        const d = await res.json();
        if (d.ok) {
            showToast("🔐 Ventana Brave Persistente abierta para loguearse 1 sola vez");
        } else {
            showToast("⚠️ No se pudo abrir la ventana persistente");
        }
    }

    async function toggleScheduler() {
        const res = await fetch("/api/toggle-scheduler", { method: "POST" });
        const d = await res.json();
        showToast(d.activo ? "🟢 Piloto 24/7 activado" : "⏸️ Piloto 24/7 en pausa");
        refreshAll();
    }

    async function loadBotStatus() {
        try {
            const res = await fetch("/api/bot-status");
            const d = await res.json();
            const badge = document.getElementById("bot-live-badge");
            if (badge) {
                if (d.running) {
                    badge.innerText = "🟢 Bot Activo (PID " + d.pid + ")";
                    badge.style.background = "rgba(16,185,129,0.2)";
                    badge.style.color = "#34d399";
                    badge.style.borderColor = "rgba(16,185,129,0.45)";
                } else {
                    badge.innerText = "⚪ Bot Inactivo · " + (d.pendientes_validas ?? 0) + " válidas TI en cola";
                    badge.style.background = "rgba(148,163,184,0.16)";
                    badge.style.color = "#cbd5e1";
                    badge.style.borderColor = "rgba(148,163,184,0.3)";
                }
            }
        } catch (e) {}
    }

    async function refreshAll() {
        const conteo = await loadStats();
        await Promise.all([
            loadBotStatus(),
            loadPortales(conteo),
            loadPostulaciones(),
            loadLogs()
        ]);
    }

    let V14_DATA = { counts: {}, items: [] };
    let V14_CAT = "validas";
    let V14_FILTERED = [];

    async function openValidas14Modal(initialCat = "validas") {
        V14_CAT = initialCat || "validas";
        const dlg = document.getElementById("validas14Dialog");
        if (dlg && !dlg.open) dlg.showModal();
        try {
            const res = await fetch("/api/ofertas-14-portales");
            V14_DATA = await res.json();
        } catch (e) {
            showToast("❌ Error cargando ofertas de los 14 portales");
            return;
        }
        const c = V14_DATA.counts || {};
        ["validas", "postuladas", "pausadas", "cerradas", "cola_ia"].forEach(k => {
            const sp = document.getElementById("v14-c-" + k);
            if (sp) sp.textContent = String(c[k] ?? 0);
        });
        const selP = document.getElementById("v14-portal");
        if (selP && selP.options.length <= 1) {
            const portalesUnicos = [...new Set((V14_DATA.items || []).map(x => x.portal).filter(Boolean))].sort();
            portalesUnicos.forEach(pt => {
                const o = document.createElement("option");
                o.value = pt;
                o.textContent = pt;
                selP.appendChild(o);
            });
        }
        const sumEl = document.getElementById("v14-summary-line");
        if (sumEl) {
            sumEl.textContent = `Total BD: ${c.total ?? 0} · ✅ Válidas bot: ${c.validas ?? 0} · 🟢 Postulados: ${c.postuladas ?? 0} · 🟡 Pausadas/CAPTCHA: ${c.pausadas ?? 0} · 🔴 Cerradas: ${c.cerradas ?? 0} · 📦 Otras Cola IA: ${c.cola_ia ?? 0}`;
        }
        setValidas14Tab(V14_CAT);
    }

    function setValidas14Tab(cat) {
        V14_CAT = cat;
        document.querySelectorAll(".v14-tab").forEach(btn => {
            btn.classList.toggle("active", btn.getAttribute("data-cat") === cat);
        });
        renderValidas14List();
    }

    function renderValidas14List() {
        const q = (document.getElementById("v14-search")?.value || "").trim().toLowerCase();
        const pt = document.getElementById("v14-portal")?.value || "Todos";
        const all = (V14_DATA.items || []).filter(x => x.categoria === V14_CAT);
        V14_FILTERED = all.filter(x => {
            if (pt !== "Todos" && x.portal !== pt) return false;
            if (q) {
                const blob = `${x.id} ${x.titulo || ""} ${x.empresa || ""} ${x.portal || ""} ${x.ubicacion || ""} ${x.notas || ""}`.toLowerCase();
                if (!blob.includes(q)) return false;
            }
            return true;
        });

        const labels = {
            validas: "Ofertas Válidas Disponibles (listas para postular en 14 portales)",
            postuladas: "Postulados Confirmados",
            pausadas: "Ofertas Pausadas / CAPTCHA / Requieren Login Manual",
            cerradas: "Ofertas Cerradas / Expiradas / Descartadas",
            cola_ia: "Otras en Cola IA (excluidas por filtro estricto TI/Práctica RM)"
        };
        document.getElementById("v14-big-count").textContent = String(V14_FILTERED.length);
        document.getElementById("v14-big-label").textContent = labels[V14_CAT] || "Ofertas";

        const sel = document.getElementById("v14-select");
        if (!V14_FILTERED.length) {
            sel.innerHTML = `<option value="">(0 ofertas en esta categoría/filtro)</option>`;
            document.getElementById("v14-detail").innerHTML = `<p style="color:var(--text-muted); font-size:0.84rem;">No hay ofertas en esta vista. Cambia de pestaña arriba (por ejemplo Postulados, Pausadas/CAPTCHA, Cerradas u Otras en Cola IA).</p>`;
            return;
        }
        sel.innerHTML = V14_FILTERED.map((o, idx) =>
            `<option value="${o.id}" ${idx === 0 ? "selected" : ""}>#${o.id} · [${escapeHtml(o.portal)}] ${escapeHtml(o.empresa)} — ${escapeHtml((o.titulo || "").slice(0, 62))}</option>`
        ).join("");
        selectValidas14ById(V14_FILTERED[0].id);
    }

    function selectValidas14ById(idVal) {
        const oid = parseInt(idVal, 10);
        const item = V14_FILTERED.find(x => Number(x.id) === oid);
        const box = document.getElementById("v14-detail");
        if (!item || !box) return;
        const urlOk = item.url_oferta && String(item.url_oferta).startsWith("http");
        const linkHtml = urlOk
            ? `<a class="btn-link btn-sky" href="${escapeHtml(item.url_oferta)}" target="_blank" rel="noopener">🌐 Abrir Oferta en Navegador</a>`
            : `<span class="badge" style="background:rgba(148,163,184,0.16); color:#94A3B8;">Sin URL directa registrada</span>`;
        const emailHtml = item.mailto_url
            ? `<a class="btn-link btn-emerald" href="${escapeHtml(item.mailto_url)}">✉️ Correo (${escapeHtml(item.email_reclutador)})</a>`
            : "";
        let qaHtml = "";
        try {
            const parsedQa = typeof item.respuestas_formulario === "string" ? JSON.parse(item.respuestas_formulario || "{}") : (item.respuestas_formulario || {});
            const ents = Object.entries(parsedQa);
            if (ents.length) {
                qaHtml = `<div style="margin-top:10px; font-weight:700; color:#38BDF8; font-size:0.78rem;">Respuestas IA preparadas:</div>` +
                    ents.map(([q, a]) => `<div class="qa-item"><div class="qa-q">${escapeHtml(q)}</div><div class="qa-a">${escapeHtml(a)}</div></div>`).join("");
            }
        } catch (e) {}

        box.innerHTML = `
            <div style="display:flex; justify-content:space-between; gap:8px; align-items:flex-start; flex-wrap:wrap; margin-bottom:8px;">
                <div>
                    <span class="badge badge-portal">#${item.id} · ${escapeHtml(item.portal)}</span>
                    <span class="badge badge-score">${Number(item.score_duoc || 90)}% Duoc</span>
                </div>
                <span style="font-size:0.78rem; font-weight:700; color:#FBBF24;">${escapeHtml(item.estado || "")}</span>
            </div>
            <h3 style="font-size:1.02rem; color:#F8FAFC; margin-bottom:4px;">${escapeHtml(item.titulo || "")}</h3>
            <div style="font-size:0.84rem; color:#CBD5E1; margin-bottom:8px;">
                🏢 <strong>${escapeHtml(item.empresa || "")}</strong> · 📍 ${escapeHtml(item.ubicacion || "RM")} · 💼 ${escapeHtml(item.modalidad || "Presencial")} · 💰 ${escapeHtml(item.asignacion_dinero || "Práctica")}
            </div>
            <div style="font-size:0.75rem; color:var(--text-muted); margin-bottom:10px;">
                📅 Oferta: ${escapeHtml(item.fecha_oferta || "--")} · Postulación: ${escapeHtml(item.fecha_postulacion || "--")}
            </div>
            <div style="display:flex; gap:8px; flex-wrap:wrap; margin-bottom:10px;">
                ${linkHtml}
                <button type="button" class="btn-emerald" onclick="launchSingleOffer(${item.id})">🤖 Postular #${item.id} en Brave</button>
                ${emailHtml}
            </div>
            ${item.notas ? `<div style="background:rgba(245,158,11,0.10); border:1px solid rgba(245,158,11,0.3); border-radius:8px; padding:8px 10px; font-size:0.77rem; color:#FDE68A; margin-bottom:10px;"><strong>Notas:</strong> ${escapeHtml(item.notas)}</div>` : ""}
            <div style="font-size:0.78rem; font-weight:700; color:#94A3B8; margin-bottom:4px;">Descripción / Detalle:</div>
            <div style="font-size:0.8rem; color:#CBD5E1; white-space:pre-wrap; line-height:1.45; background:#070A12; border:1px solid var(--border-subtle); border-radius:8px; padding:10px;">${escapeHtml((item.descripcion || "Sin descripción almacenada.").slice(0, 3500))}</div>
            ${qaHtml}
        `;
    }

    async function launchSingleOffer(oid) {
        showToast(`🤖 Lanzando oferta #${oid} en Brave (30s)...`);
        const res = await fetch("/api/start-single-offer", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ id: oid })
        });
        const d = await res.json();
        showToast(d.ok ? `✅ Oferta #${oid} abierta en el bot de pantalla.` : `⚠️ ${d.error || "No se pudo lanzar"}`);
        setTimeout(refreshAll, 2000);
    }

    refreshAll();
    if (window.location.hash === "#validas14") {
        setTimeout(() => openValidas14Modal("validas"), 350);
    }
    setInterval(refreshAll, 15000);
</script>
</body>
</html>
"""

MEMORY_HTML_PAGE = """<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AutoPostula · Memoria de Preguntas y Respuestas</title>
    <style>
        :root {
            --bg: #090d16;
            --card: #111827;
            --border: rgba(56, 189, 248, 0.25);
            --text: #f8fafc;
            --muted: #94a3b8;
            --accent: #38bdf8;
            --green: #10b981;
            --red: #ef4444;
            --indigo: #6366f1;
        }
        * { box-sizing: border-box; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
        body { background: var(--bg); color: var(--text); margin: 0; padding: 24px; }
        .container { max-width: 1100px; margin: 0 auto; }
        header {
            display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 16px;
            padding-bottom: 20px; border-bottom: 1px solid var(--border); margin-bottom: 24px;
        }
        h1 { margin: 0; font-size: 24px; color: var(--accent); display: flex; align-items: center; gap: 10px; }
        .subtitle { color: var(--muted); font-size: 14px; margin-top: 4px; }
        .actions { display: flex; gap: 12px; align-items: center; }
        .btn {
            border: none; border-radius: 10px; padding: 10px 16px; font-weight: 700; font-size: 13px;
            cursor: pointer; transition: all 0.15s ease; color: #fff; text-decoration: none; display: inline-flex; align-items: center; gap: 6px;
        }
        .btn-primary { background: var(--indigo); }
        .btn-primary:hover { background: #4f46e5; }
        .btn-green { background: var(--green); }
        .btn-green:hover { background: #059669; }
        .btn-red { background: rgba(239, 68, 68, 0.15); border: 1px solid var(--red); color: #fca5a5; }
        .btn-red:hover { background: var(--red); color: #fff; }
        .btn-edit { background: rgba(56, 189, 248, 0.15); border: 1px solid var(--accent); color: #7dd3fc; }
        .btn-edit:hover { background: var(--accent); color: #090d16; }
        .btn-secondary { background: #1e293b; border: 1px solid #334155; }

        .search-bar {
            width: 100%; padding: 12px 16px; border-radius: 12px; border: 1px solid var(--border);
            background: var(--card); color: var(--text); font-size: 14px; margin-bottom: 20px; outline: none;
        }
        .search-bar:focus { border-color: var(--accent); }

        .qa-grid { display: flex; flex-direction: column; gap: 14px; }
        .qa-card {
            background: var(--card); border: 1px solid var(--border); border-radius: 14px; padding: 18px;
            display: flex; flex-direction: column; gap: 10px; transition: border-color 0.2s;
        }
        .qa-card:hover { border-color: var(--accent); }
        .qa-header { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }
        .qa-question { font-size: 15px; font-weight: 700; color: #e2e8f0; margin: 0; line-height: 1.4; }
        .qa-badge {
            background: rgba(99, 102, 241, 0.2); border: 1px solid rgba(99, 102, 241, 0.4); color: #a5b4fc;
            font-size: 11px; font-weight: 700; padding: 3px 8px; border-radius: 999px; white-space: nowrap;
        }
        .qa-answer {
            background: #0b1120; border: 1px solid rgba(255,255,255,0.06); border-radius: 10px;
            padding: 12px; font-size: 14px; color: #cbd5e1; line-height: 1.5; white-space: pre-wrap;
        }
        .qa-footer { display: flex; justify-content: space-between; align-items: center; margin-top: 4px; }
        .qa-meta { font-size: 12px; color: var(--muted); }
        .qa-btns { display: flex; gap: 8px; }

        /* Modal */
        .modal-backdrop {
            display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.75); z-index: 1000;
            align-items: center; justify-content: center; padding: 20px; backdrop-filter: blur(4px);
        }
        .modal-backdrop.open { display: flex; }
        .modal {
            background: #111827; border: 2px solid var(--accent); border-radius: 16px; width: 100%; max-width: 650px;
            padding: 24px; display: flex; flex-direction: column; gap: 16px; box-shadow: 0 25px 50px rgba(0,0,0,0.8);
        }
        .modal h2 { margin: 0; font-size: 18px; color: var(--accent); }
        .field { display: flex; flex-direction: column; gap: 6px; }
        .field label { font-size: 13px; font-weight: 700; color: var(--muted); }
        .field input, .field textarea, .field select {
            background: #090d16; border: 1px solid #334155; border-radius: 10px; padding: 10px 12px;
            color: var(--text); font-size: 14px; outline: none;
        }
        .field textarea { min-height: 130px; resize: vertical; line-height: 1.5; }
        .field input:focus, .field textarea:focus { border-color: var(--accent); }
        .modal-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 6px; }
    </style>
</head>
<body>
<div class="container">
    <header>
        <div>
            <h1>🧠 Memoria de Preguntas y Respuestas (AutoPostula)</h1>
            <div class="subtitle">Todas las modificaciones que hagas en vivo y las reglas creadas aquí tienen prioridad absoluta al responder formularios.</div>
        </div>
        <div class="actions">
            <a href="/" class="btn btn-secondary">⬅️ Volver al Panel NOC</a>
            <button onclick="openModal()" class="btn btn-green">➕ Crear Pregunta / Respuesta</button>
        </div>
    </header>

    <input type="text" id="searchInput" class="search-bar" placeholder="🔍 Buscar por palabra clave de pregunta o respuesta..." oninput="renderList()">

    <div id="qaList" class="qa-grid">Cargando memoria...</div>
</div>

<!-- Modal Crear / Editar -->
<div id="qaModal" class="modal-backdrop">
    <div class="modal">
        <h2 id="modalTitle">✏️ Editar Pregunta y Respuesta</h2>
        <input type="hidden" id="editId">
        <div class="field">
            <label>Pregunta o Palabra Clave del Reclutador (editable)</label>
            <input type="text" id="editPregunta" placeholder="Ej: ¿Disponibilidad para comenzar en diciembre o hasta enero?">
        </div>
        <div class="field">
            <label>Categoría</label>
            <select id="editCategoria">
                <option value="disponibilidad">disponibilidad / plazos</option>
                <option value="editada_en_vivo">editada_en_vivo</option>
                <option value="general">general</option>
                <option value="tecnica">tecnica / ti</option>
                <option value="motivacion">motivacion</option>
                <option value="datos_personales">datos_personales</option>
            </select>
        </div>
        <div class="field">
            <label>Respuesta Actual Oficial (lo que escribirá el bot)</label>
            <textarea id="editRespuesta" placeholder="Escribe la respuesta exacta que debe usar AutoPostula..."></textarea>
        </div>
        <div class="modal-actions">
            <button onclick="closeModal()" class="btn btn-secondary">Cancelar</button>
            <button onclick="saveItem()" class="btn btn-primary">💾 Guardar Cambios</button>
        </div>
    </div>
</div>

<script>
let allItems = [];

async function loadMemoria() {
    const res = await fetch('/api/memoria');
    allItems = await res.json();
    renderList();
}

function escapeHtml(str) {
    return (str || '').replace(/[&<>'"]/g, tag => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
    }[tag] || tag));
}

function renderList() {
    const q = (document.getElementById('searchInput').value || '').toLowerCase();
    const filtered = allItems.filter(item =>
        (item.pregunta_clave || '').toLowerCase().includes(q) ||
        (item.respuesta || '').toLowerCase().includes(q) ||
        (item.categoria || '').toLowerCase().includes(q)
    );
    const container = document.getElementById('qaList');
    if (filtered.length === 0) {
        container.innerHTML = '<div style="color:#94a3b8; text-align:center; padding:40px;">No hay preguntas guardadas que coincidan.</div>';
        return;
    }
    container.innerHTML = filtered.map(item => `
        <div class="qa-card">
            <div class="qa-header">
                <h3 class="qa-question">❓ ${escapeHtml(item.pregunta_clave)}</h3>
                <span class="qa-badge">${escapeHtml(item.categoria || 'general')}</span>
            </div>
            <div class="qa-answer">${escapeHtml(item.respuesta)}</div>
            <div class="qa-footer">
                <span class="qa-meta">ID #${item.id} · Usada ${item.veces_usada || 1} veces · Guardado: ${escapeHtml(item.created_at || '')}</span>
                <div class="qa-btns">
                    <button class="btn btn-edit" onclick="editItem(${item.id})">✏️ Modificar</button>
                    <button class="btn btn-red" onclick="deleteItem(${item.id})">🗑️ Borrar</button>
                </div>
            </div>
        </div>
    `).join('');
}

function openModal(item = null) {
    document.getElementById('editId').value = item ? item.id : '';
    document.getElementById('editPregunta').value = item ? item.pregunta_clave : '';
    document.getElementById('editRespuesta').value = item ? item.respuesta : '';
    document.getElementById('editCategoria').value = item ? (item.categoria || 'general') : 'general';
    document.getElementById('modalTitle').innerText = item ? '✏️ Modificar Pregunta y Respuesta' : '➕ Nueva Pregunta y Respuesta';
    document.getElementById('qaModal').classList.add('open');
}

function closeModal() {
    document.getElementById('qaModal').classList.remove('open');
}

function editItem(id) {
    const item = allItems.find(x => x.id === id);
    if (item) openModal(item);
}

async function saveItem() {
    const idVal = document.getElementById('editId').value;
    const pregunta = document.getElementById('editPregunta').value.trim();
    const respuesta = document.getElementById('editRespuesta').value.trim();
    const categoria = document.getElementById('editCategoria').value;

    if (!pregunta || !respuesta) {
        alert('Tanto la pregunta como la respuesta son obligatorias.');
        return;
    }

    const res = await fetch('/api/memoria/save', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            id: idVal ? parseInt(idVal, 10) : null,
            pregunta_clave: pregunta,
            respuesta: respuesta,
            categoria: categoria
        })
    });
    const data = await res.json();
    if (data.ok) {
        closeModal();
        loadMemoria();
    } else {
        alert('Error al guardar: ' + (data.error || 'Desconocido'));
    }
}

async function deleteItem(id) {
    if (!confirm('¿Seguro que deseas borrar esta pregunta y respuesta de la memoria del bot?')) return;
    const res = await fetch('/api/memoria/delete', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ id: id })
    });
    const data = await res.json();
    if (data.ok) {
        loadMemoria();
    }
}

loadMemoria();
</script>
</body>
</html>
"""


class AutoPostulaHandler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        try:
            self.request.settimeout(15)
        except Exception:
            pass

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.end_headers()

    def _send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw) if raw else {}

    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)

        if parsed.path in ("/", "/index.html"):
            body = HTML_PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif parsed.path == "/memoria":
            body = MEMORY_HTML_PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif parsed.path == "/api/memoria":
            init_db()
            self._send_json(obtener_respuestas_aprendidas())
        elif parsed.path == "/api/stats":
            self._send_json(get_stats())
        elif parsed.path == "/api/ofertas-14-portales":
            self._send_json(get_ofertas_14_portales())
        elif parsed.path == "/api/portales":
            init_db()
            self._send_json(obtener_catalogo_portales())
        elif parsed.path == "/api/postulaciones":
            estado = (qs.get("estado") or ["Todos"])[0]
            portal = (qs.get("portal") or ["Todos"])[0]
            q = (qs.get("q") or [""])[0]
            self._send_json(serialize_postulaciones(estado, portal, q, limit=200))
        elif parsed.path == "/api/logs":
            init_db()
            self._send_json(obtener_logs_scheduler(limit=30))
        elif parsed.path == "/api/bot-status":
            running, pid = is_bot_running()
            self._send_json({
                "ok": True,
                "running": running,
                "pid": pid,
                "pendientes_validas": contar_pendientes_validas(),
            })
        elif parsed.path == "/api/agente-investigador":
            ult, res_ult = None, None
            for p_src, k in [(os.path.join(INVESTIGACIONES_DIR, "ultimo.json"), "req"), (os.path.join(INVESTIGACIONES_DIR, "ultimo_resultado.json"), "res")]:
                if os.path.exists(p_src):
                    try:
                        with open(p_src, "r", encoding="utf-8") as fh:
                            if k == "req":
                                ult = json.load(fh)
                            else:
                                res_ult = json.load(fh)
                    except Exception:
                        pass
            self._send_json({"ok": True, "ultimo": ult, "resultado": res_ult})
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)

        if parsed.path == "/api/memoria/save":
            try:
                data = self._read_json()
                preg = (data.get("pregunta_clave") or "").strip()
                resp = (data.get("respuesta") or "").strip()
                cat = (data.get("categoria") or "general").strip()
                item_id = data.get("id")
                if not preg or not resp:
                    self._send_json({"ok": False, "error": "Pregunta y respuesta obligatorias"}, status=400)
                    return
                new_id = guardar_o_actualizar_respuesta(preg, resp, categoria=cat, item_id=item_id)
                self._send_json({"ok": True, "id": new_id})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, status=500)

        elif parsed.path == "/api/memoria/delete":
            try:
                data = self._read_json()
                item_id = data.get("id")
                if not item_id:
                    self._send_json({"ok": False, "error": "ID requerido"}, status=400)
                    return
                ok = eliminar_respuesta_aprendida(int(item_id))
                self._send_json({"ok": ok})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, status=500)

        elif parsed.path == "/api/start-interactive-loop":
            try:
                running, pid = is_bot_running()
                if running:
                    self._send_json({"ok": False, "already_running": True, "error": f"El bot ya está activo en pantalla (PID {pid})."})
                    return
                pend = contar_pendientes_validas()
                if pend == 0:
                    hablar_no_bloqueante("No hay ofertas válidas pendientes en cola. Usa Revisar Válidas o Escanear 14 Portales primero.")
                    self._send_json({"ok": False, "empty_queue": True, "pendientes": 0, "error": "No hay ofertas válidas pendientes (RM + Perfil TI). Pulsa '🔍 Revisar Válidas' o '🌐 Escanear 14 Portales Ahora'."})
                    return
                _launch_bot_process(["--segundos", "30"])
                self._send_json({"ok": True, "modo": "interactivo", "pendientes": pend})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, status=500)

        elif parsed.path == "/api/start-single-offer":
            try:
                data = self._read_json()
                oid = int(data.get("id") or 0)
                if not oid:
                    self._send_json({"ok": False, "error": "ID de oferta requerido"}, status=400)
                    return
                running, pid = is_bot_running()
                if running:
                    self._send_json({"ok": False, "already_running": True, "error": f"Detén primero el bot activo (PID {pid}) antes de lanzar la oferta #{oid}."})
                    return
                _launch_bot_process(["--id", str(oid), "--segundos", "30"])
                self._send_json({"ok": True, "id": oid})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, status=500)

        elif parsed.path == "/api/start-headless-batch":
            try:
                running, pid = is_bot_running()
                if running:
                    self._send_json({"ok": False, "already_running": True, "error": f"El bot ya está activo (PID {pid})."})
                    return
                pend = contar_pendientes_validas()
                if pend == 0:
                    hablar_no_bloqueante("No hay ofertas válidas pendientes en cola para postular en segundo plano.")
                    self._send_json({"ok": False, "empty_queue": True, "pendientes": 0, "error": "No hay ofertas válidas pendientes (RM + Perfil TI). Pulsa '🌐 Escanear 14 Portales Ahora'."})
                    return
                _launch_bot_process(["--segundos", "0", "--no-focus"])
                self._send_json({"ok": True, "modo": "segundo_plano", "pendientes": pend})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, status=500)

        elif parsed.path == "/api/stop-bot":
            try:
                _stop_bot_processes()
                self._send_json({"ok": True})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, status=500)

        elif parsed.path == "/api/run-scan":
            try:
                subprocess.Popen(
                    [PYTHON_BIN, DAEMON_SCRIPT, "--once"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                self._send_json({"ok": True})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, status=500)

        elif parsed.path == "/api/save-filters":
            data = self._read_json()
            guardar_filtros({
                "modo_busqueda": data.get("modo_busqueda", "practica_duoc"),
                "termino_personalizado": data.get("termino_personalizado", "infraestructura plataformas soporte redes cloud"),
            })
            self._send_json({"ok": True})

        elif parsed.path == "/api/update-status":
            data = self._read_json()
            if data.get("id") and data.get("estado"):
                actualizar_estado_postulacion(int(data["id"]), str(data["estado"]))
            self._send_json({"ok": True})

        elif parsed.path == "/api/open-next-batch":
            data = self._read_json()
            cantidad = max(1, min(20, int(data.get("cantidad") or 10)))
            conn = get_conn()
            cur = conn.cursor()
            cur.execute("""
                SELECT id, url_oferta FROM postulaciones
                WHERE estado LIKE '%Cola%'
                ORDER BY score_duoc DESC, id ASC
                LIMIT ?;
            """, (cantidad,))
            rows = cur.fetchall()
            abiertas = 0
            for r in rows:
                u = r["url_oferta"] or ""
                if u.startswith("http"):
                    try:
                        if sys.platform != "win32" and shutil.which("brave-browser"):
                            subprocess.Popen(["brave-browser", u], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        else:
                            webbrowser.open(u)
                        cur.execute("UPDATE postulaciones SET estado = '🟡 Postulado (Abierta en Brave)', metodo_postulacion = 'abierta_en_brave' WHERE id = ?;", (r["id"],))
                        abiertas += 1
                        time.sleep(0.35)
                    except Exception:
                        pass
            conn.commit()
            conn.close()
            self._send_json({"ok": True, "abiertas": abiertas})

        elif parsed.path == "/api/open-session":
            res = abrir_sesion_persistente_portales()
            self._send_json(res)

        elif parsed.path == "/api/toggle-scheduler":
            actual = obtener_estado_scheduler()
            nuevo = 0 if int(actual.get("activo") or 0) == 1 else 1
            prox = calcular_proximo_horario(actual.get("horarios", "01:00, 04:00, 06:30, 08:00, 12:00, 14:00, 17:00, 22:00"))
            guardar_estado_scheduler(activo=nuevo, proximo_escaneo=prox)
            self._send_json({"ok": True, "activo": nuevo, "proximo_escaneo": prox})

        elif parsed.path == "/api/agente-investigador":
            try:
                data = self._read_json()
                resp = _disparar_investigacion(data)
                self._send_json(resp)
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, status=500)

        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        return


def run():
    init_db()
    ThreadingHTTPServer.allow_reuse_address = True
    ThreadingHTTPServer.daemon_threads = True
    server = ThreadingHTTPServer(("0.0.0.0", PORT), AutoPostulaHandler)
    print("=" * 64)
    print(f"🎯 AutoPostula NOC Dashboard (14 Portales) activo en:")
    print(f"   👉 http://localhost:{PORT}")
    print("=" * 64)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()


if __name__ == "__main__":
    run()
