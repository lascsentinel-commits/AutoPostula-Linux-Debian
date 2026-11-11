#!/usr/bin/env python3
"""
AutoPostula v3.7 — Bucle Interactivo sobre Pantalla con Botón Flotante
Esquina Inferior Izquierda: Botón flotante para Pausar / Reanudar el Bot en vivo.
Filtro: Prácticas Técnicas en la Región Metropolitana (o Remoto/Híbrido).

Flujo Completo:
1. Navega a la oferta y enfoca Brave (wmctrl).
2. Inyecta el botón flotante en la esquina inferior izquierda (Pausar/Reanudar + Timer).
3. Presiona 'Postula fácil' (botón azul inicial).
4. Si aparece modal con 'Comenzar', lo presiona.
5. Rellena las preguntas con el perfil Duoc UC (smart_qa_solver).
6. Lee en voz alta CADA pregunta y su respuesta con Di.
7. Inicia los 30 segundos de pausa para revisión (el usuario puede pausar con el botón flotante).
8. Si transcurren los 30 segundos sin pausa ni clic manual, pulsa el segundo 'Postular'.
9. Verifica confirmación de éxito, actualiza autopostula.db y continúa en bucle.
"""

import argparse
import asyncio
import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime

# Limpiar wrapper que causa conflicto con python3.13 en kali
from pathlib import Path
import shutil

os.environ.pop("PLAYWRIGHT_NODEJS_PATH", None)

from playwright.async_api import async_playwright

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

import database
from modules.smart_qa_solver import (
    resolver_pregunta_reclutador,
    resolver_preguntas_con_gemini_en_lote,
    resolver_seleccion_dropdown,
    limpiar_texto_pregunta,
    obtener_perfil_activo,
)

DB_PATH = os.environ.get("AUTOPOSTULA_DB_PATH", str(BASE_DIR / "data" / "autopostula.db"))
CV_MASTER_PDF = os.environ.get("CV_MASTER_PDF", "/home/dhamit/Documentos/CV-2026-01/1_CV_Dhani_Amit_Elgueta_Ríos_U-26.pdf")
if not os.path.exists(CV_MASTER_PDF):
    local_cv = BASE_DIR / "cv.pdf"
    if local_cv.exists():
        CV_MASTER_PDF = str(local_cv)

# Comunas y referencias válidas para la Región Metropolitana
COMUNAS_RM = [
    "santiago", "metropolitana", "rm", "las condes", "condes", "providencia",
    "pudahuel", "cerrillos", "san bernardo", "independencia", "quilicura",
    "huechuraba", "renca", "maipú", "maipu", "la florida", "ñuñoa", "nunoa",
    "vitacura", "lo barnechea", "pedro aguirre cerda", "estación central",
    "estacion central", "centro", "recoleta", "ciudad empresarial", "san joaquín",
    "san joaquin", "macul", "peñalolén", "penalolen", "quinta normal", "conchalí",
    "conchali", "lo prado", "cerro navia", "colina", "lampa",
    "chicureo", "remoto", "remota", "híbrido", "hibrido", "teletrabajo", "region13"
]

# Regiones fuera de la RM que deben ser descartadas
REGIONES_EXCLUIDAS = [
    "talca", "valdivia", "concepción", "concepcion", "antofagasta", "la serena",
    "laserena", "coquimbo", "temuco", "puerto montt", "puerto varas", "rancagua",
    "iquique", "arica", "chillán", "chillan", "punta arenas", "quilpué", "quilpue",
    "valparaíso", "valparaiso", "viña del mar", "viña", "vina", "maule",
    "biobío", "biobio", "araucanía", "araucania", "los lagos", "los ríos",
    "los rios", "ohiggins", "o'higgins", "calama", "copiapó", "copiapo"
]


GLOBAL_CON_VOZ = True
GLOBAL_ENFOCAR = True

def hablar(texto: str, final: bool = False):
    """Ejecuta Di para narrar en tiempo real si está habilitado; soporta Linux y Windows 11 con Edge-TTS."""
    if not GLOBAL_CON_VOZ:
        return

    di_system = "/home/dhamit/.local/bin/Di"
    local_di_py = BASE_DIR / "bin" / "di.py"

    if os.path.exists(di_system):
        cmd = [di_system]
    elif local_di_py.exists():
        cmd = [sys.executable, str(local_di_py)]
    elif shutil.which("Di"):
        cmd = ["Di"]
    else:
        cmd = None

    if cmd:
        if final:
            cmd.append("--final")
        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, text=True)
            proc.communicate(input=texto)
            return
        except Exception as e:
            print(f"Error Di: {e}", file=sys.stderr)

    # Si estamos en Windows y no hay Di ejecutable, usar síntesis SAPI nativa
    if sys.platform == "win32":
        try:
            clean_txt = texto.replace('"', '').replace("'", "").replace("`", "")
            ps_cmd = f'Add-Type -AssemblyName System.Speech; $speak = New-Object System.Speech.Synthesis.SpeechSynthesizer; $speak.Speak("{clean_txt}")'
            subprocess.Popen(["powershell", "-NoProfile", "-Command", ps_cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except Exception:
            pass

    # Fallback si no hay motor de voz activo
    print(f"📢 [VOZ]: {texto}")


def traer_brave_al_frente():
    """Enfoca la ventana del navegador en la pantalla (Linux X11 o Windows)."""
    if not GLOBAL_ENFOCAR:
        return
    if sys.platform != "win32":
        try:
            subprocess.run(
                ["wmctrl", "-a", "Brave"],
                env={"DISPLAY": os.environ.get("DISPLAY", ":0"), "XAUTHORITY": os.environ.get("XAUTHORITY", "/home/dhamit/.Xauthority")},
                check=False
            )
        except Exception:
            pass
    else:
        try:
            subprocess.run(["powershell", "-NoProfile", "-Command", "(New-Object -ComObject WScript.Shell).AppActivate('Brave')"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass


def es_region_metropolitana(ubicacion: str, titulo: str, descripcion: str = "") -> bool:
    """Verifica si la oferta pertenece estrictamente a la Región Metropolitana o es Remota."""
    texto_total = f"{ubicacion} {titulo} {descripcion}".lower()

    for reg in REGIONES_EXCLUIDAS:
        if reg in texto_total:
            if not any(rm in ubicacion.lower() for rm in ["santiago", "metropolitana", "rm", "region13"]):
                return False

    if any(rm in texto_total for rm in COMUNAS_RM):
        return True

    if "chile" in ubicacion.lower() and not any(reg in ubicacion.lower() for reg in REGIONES_EXCLUIDAS):
        return True

    return False


def obtener_ofertas_validas_pendientes(limite: int = 100, solo_id: int = None, solo_portal: str | None = None):
    """Filtra y devuelve estrictamente ofertas de práctica técnica en la Región Metropolitana (priorizando las más recientes)."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    if solo_id:
        c.execute("SELECT * FROM postulaciones WHERE id = ?", (solo_id,))
        rows = [dict(r) for r in c.fetchall()]
        conn.close()
        return rows

    filtro_portal_sql = "AND LOWER(portal) LIKE ?" if solo_portal else ""
    query = f"""
    SELECT *
    FROM postulaciones
    WHERE (estado LIKE '%Cola%' OR estado LIKE '%Lista%')
      {filtro_portal_sql}
      AND (
           LOWER(titulo) LIKE '%práctica%'
           OR LOWER(titulo) LIKE '%practica%'
           OR LOWER(titulo) LIKE '%practicante%'
           OR LOWER(titulo) LIKE '%alumno%'
           OR LOWER(titulo) LIKE '%trainee%'
      )
      AND NOT (
           LOWER(titulo) LIKE '%rrhh%'
           OR LOWER(titulo) LIKE '%bodega%'
           OR LOWER(titulo) LIKE '%abastecimiento%'
           OR LOWER(titulo) LIKE '%psicolog%'
           OR LOWER(titulo) LIKE '%enfermer%'
           OR LOWER(titulo) LIKE '%prevencion%'
           OR LOWER(titulo) LIKE '%comercial%'
           OR LOWER(titulo) LIKE '%marketing%'
           OR LOWER(titulo) LIKE '%social%'
           OR LOWER(titulo) LIKE '%redes sociales%'
           OR LOWER(titulo) LIKE '%quimic%'
           OR LOWER(titulo) LIKE '%geolog%'
           OR LOWER(titulo) LIKE '%administrativ%'
           OR LOWER(titulo) LIKE '%contadur%'
           OR LOWER(titulo) LIKE '%facility%'
           OR LOWER(titulo) LIKE '%seleccion%'
           OR LOWER(titulo) LIKE '%selección%'
           OR LOWER(titulo) LIKE '%contralor%'
           OR LOWER(titulo) LIKE '%s&op%'
           OR LOWER(titulo) LIKE '%industrial%'
           OR LOWER(titulo) LIKE '%negocios%'
           OR LOWER(titulo) LIKE '%administración pública%'
           OR LOWER(titulo) LIKE '%administracion publica%'
           OR LOWER(titulo) LIKE '%banco de talentos%'
           OR LOWER(titulo) LIKE '%mantenimiento industrial%'
           OR LOWER(titulo) LIKE '%desarrollo de producto%'
           OR LOWER(titulo) LIKE '%desarrollo organizacional%'
           OR LOWER(titulo) LIKE '%desarrollo de personas%'
           OR LOWER(titulo) LIKE '%desarrollo comercial%'
      )
    ORDER BY (CASE WHEN created_at >= date('now', '-5 days') THEN 1 ELSE 0 END) DESC, score_duoc DESC, id DESC;
    """
    if solo_portal:
        c.execute(query, (f"%{solo_portal.lower()}%",))
    else:
        c.execute(query)
    candidatas = [dict(r) for r in c.fetchall()]
    conn.close()

    pat_ti_estricto = re.compile(
        r'\b(ti|it|t\.i|infraestructura|redes|conectividad|telecomunicaciones|ciberseguridad|seguridad|vulnerabilid\w*|pentest\w*|soc|auditor[ií]a ti|riesgos tecnol\w*|soporte|sistemas|cloud|devops|servidores|linux|plataformas|mesa de servicio|informática|informatica|base[s]? de datos|sql|contenedores|docker|kubernetes|github|ia|inteligencia artificial|agente[s]?|prompt|automatizaci[oó]n)\b',
        re.I
    )
    pat_afinidad_clave = re.compile(
        r'(ciberseguridad|seguridad|vulnerabilid|pentest|\bsoc\b|auditor[ií]a ti|riesgos tecnol|infraestructura|redes|conectividad|telecomunicaciones|servidores|linux|cloud|devops|contenedores|docker|kubernetes|github|ci/cd|base[s]? de datos|\bsql\b|postgres|\bia\b|inteligencia artificial|agente|prompt|\bllm\b|\brag\b|plataformas)',
        re.I
    )
    pat_solo_dev = re.compile(
        r'(desarrollo de software|programaci[oó]n|programador|fullstack|frontend|backend|data engineering|data engineer|analista programador)',
        re.I
    )

    def _afinidad_score(titulo_s: str, desc_s: str) -> int:
        t = f"{titulo_s} {desc_s[:600]}".lower()
        sc = 0
        if any(k in t for k in ["ciberseguridad", "seguridad", "vulnerabilid", "pentest", "soc", "auditoría ti", "auditoria ti", "riesgos"]):
            sc += 60
        if any(k in t for k in ["agente", "agentes", "inteligencia artificial", "prompt", "llm", "rag", " ia ", "| ia", "con ia"]):
            sc += 50
        if any(k in t for k in ["docker", "kubernetes", "contenedores", "devops", "github", "cloud", "aws", "gcp", "azure", "linux", "servidores"]):
            sc += 40
        if any(k in t for k in ["base de datos", "bases de datos", "sql", "postgres", "infraestructura", "redes", "conectividad", "telecomunicaciones", "plataformas"]):
            sc += 35
        if any(k in t for k in ["soporte ti", "mesa de servicio", "sistemas", "ti"]):
            sc += 15
        return sc

    filtradas = []
    vistos_keys = set()
    for cand in candidatas:
        ubi = cand.get("ubicacion") or ""
        tit = cand.get("titulo") or ""
        emp = cand.get("empresa") or ""
        desc = cand.get("descripcion") or ""
        if not pat_ti_estricto.search(tit):
            continue
        if "redes sociales" in tit.lower():
            continue
        # Saltar prácticas exclusivamente de desarrollo/programación sin relación con seguridad, infra, DB, contenedores o IA
        if pat_solo_dev.search(tit) and not pat_afinidad_clave.search(f"{tit} {desc[:400]}"):
            continue
        if not es_region_metropolitana(ubi, tit, desc):
            continue
        dedup_key = (emp.lower().strip(), tit.lower().strip())
        if dedup_key in vistos_keys:
            continue
        vistos_keys.add(dedup_key)
        cand["_afinidad"] = _afinidad_score(tit, desc)
        filtradas.append(cand)

    filtradas.sort(key=lambda r: (r.get("_afinidad", 0), r.get("score_duoc") or 0, r.get("id") or 0), reverse=True)
    return filtradas[:limite]


def _parece_login_pagina(url: str, body_text: str = "") -> bool:
    """Detecta si el portal está pidiendo iniciar sesión en lugar de mostrar una oferta cerrada."""
    u = (url or "").lower()
    if any(k in u for k in ("/ingresar", "/login", "/signin", "/sign-in", "/usuarios/ingresar", "/auth/", "accounts.google.com")):
        return True
    bt = (body_text or "").lower()[:4000]
    tiene_login = any(k in bt for k in ("iniciar sesión", "inicia sesión", "iniciar sesión con google", "acceder con google", "olvidaste tu contraseña", "sign in to"))
    tiene_cerrada = any(k in bt for k in ("oferta cerrada", "ya no se aceptan solicitudes", "este anuncio ha expirado"))
    return tiene_login and not tiene_cerrada


def es_url_catalogo(url: str, portal: str = "") -> bool:
    """Detecta si la URL actual es una página de catálogo, búsqueda o lista general de ofertas."""
    if not url:
        return False
    u = url.split("?")[0].rstrip("/").lower()
    if any(k in u for k in ("/ingresar", "/login", "/signin", "/sign-in", "/usuarios/", "/auth", "/registro", "/register", "/apply", "/postular")):
        return False
    
    # Rutas típicas de catálogo/búsqueda general
    if u.endswith(("/ofertas", "/trabajos", "/empleos", "/buscar", "/search", "/portal", "/postulaciones", "/concursos")):
        return True
    
    # FirstJob: Las ofertas individuales son https://firstjob.me/oferta/<id>/...
    if "firstjob.me" in u:
        if "/oferta/" not in u:
            return True
            
    # Computrabajo: Las ofertas individuales tienen /oferta-de-trabajo-de-
    if "computrabajo.cl" in u:
        if "/ofertas-de-trabajo" in u and "/oferta-de-trabajo-de-" not in u:
            return True
        if u.endswith("/empleos") or ("/trabajo-de-" in u and "/oferta-de-trabajo-de-" not in u):
            return True

    # Trabajando:
    if "trabajando.cl" in u:
        if u.endswith(("/trabajo", "/ofertas-empleo", "/buscar")):
            return True

    return False


FRASES_CERRADA = [
    "ya no se aceptan solicitudes", "no longer accepting applications", "no se aceptan más solicitudes",
    "solicitudes cerradas", "no admite más solicitudes",
    "oferta finalizada", "oferta cerrada", "proceso finalizado", "ya no está disponible",
    "aviso finalizado", "404", "ha cerrado su proceso", "ya ha cerrado", "cerrado su proceso",
    "proceso de postulación cerrado", "este aviso ha expirado", "este anuncio ha expirado",
    "anuncio ha expirado", "ha sido desactivado por el empleador", "la publicación ha finalizado",
    "esta oferta ha expirado", "esta búsqueda ha finalizado", "convocatoria ha finalizado",
    "oferta pausada", "no se encuentra disponible", "lo sentimos! esta oferta",
    "lo sentimos, esta oferta", "esta oferta ya no", "no disponible actualmente"
]

FRASES_YA_POSTULADO = [
    "usted ya ha presentado solicitud", "ya ha presentado solicitud",
    "ya estás participando en el proceso", "ya ha sido utilizado para postularte",
    "ya ha realizado una solicitud", "ya has realizado una solicitud",
    "you have already applied", "ya te has postulado", "ya has postulado",
    "ya estás postulado", "ya te postulaste", "ya postulaste",
    "postulación enviada", "solicitud enviada", "candidatura enviada",
    "postulación recibida", "ver tu postulación", "you already applied",
]

FRASES_CONFIRMACION_ENVIO = [
    "postulación exitosa", "postulacion exitosa", "solicitud completada",
    "postulación completada", "postulacion completada", "postulación enviada",
    "postulacion enviada", "solicitud enviada", "envió tu solicitud",
    "tu solicitud se envió", "ha presentado solicitud exitosamente",
    "presentado solicitud exitosamente", "ha presentado una solicitud",
    "tu postulación fue recibida", "postulación recibida", "solicitud recibida",
    "enviada correctamente", "gracias por postular", "gracias por su solicitud",
    "gracias por tu solicitud", "hemos recibido su solicitud", "gracias por aplicar",
    "te postulaste correctamente", "has postulado con éxito", "ya te has postulado", "ya has postulado",
    "ya estás postulado", "ya postulaste", "postular de nuevo",
    "usted ya ha presentado solicitud", "ya ha presentado solicitud",
    "ya estás participando en el proceso", "ya ha sido utilizado para postularte",
    "ya ha realizado una solicitud", "ya has realizado una solicitud",
    "application was sent", "application submitted", "application received",
    "thank you for applying", "thank you for your application",
    "candidatura enviada",
]


GLOBAL_BOT_CONTROL = {
    "paused": False,
    "stop": False,
    "skip_wait": False,
    "next_offer": False,
    "segundos": 30,
    "timer_text": "30s",
    "last_cmd_ts": 0,
    "panel_open": False,
    "leer_descripcion": False,
    "leer_qa": True,
    "max_tabs": 4,
    "read_desc_now": False,
    "clean_tabs_now": False,
    "open_prev_now": False,
    "investigate_payload": None,
    "inv_status": "",
    "desc_actual": "",
    "empresa_actual": "",
    "oferta_actual": {},
}

BOT_OWNED_PAGES: set = set()
TABS_PROTEGIDAS_MANUAL: set = set()
HISTORIAL_OFERTAS: list[dict] = []


def _registrar_historial_oferta(of: dict, url_real: str = ""):
    """Guarda en memoria la secuencia de ofertas visitadas para poder volver con 'Link anterior'."""
    if not of:
        return
    u = (url_real or of.get("url_oferta") or "").strip()
    if not u.startswith("http"):
        return
    entry = {
        "id": of.get("id"),
        "empresa": of.get("empresa") or "Oferta",
        "titulo": of.get("titulo") or "",
        "portal": of.get("portal") or "",
        "url_oferta": of.get("url_oferta") or u,
        "url_real": u,
    }
    if HISTORIAL_OFERTAS and HISTORIAL_OFERTAS[-1].get("id") == entry["id"]:
        HISTORIAL_OFERTAS[-1].update(entry)
    else:
        HISTORIAL_OFERTAS.append(entry)
        if len(HISTORIAL_OFERTAS) > 30:
            HISTORIAL_OFERTAS.pop(0)


def _obtener_oferta_anterior() -> dict | None:
    """Devuelve la oferta inmediatamente anterior a la actual (o la última procesada en BD si recién partió el bot)."""
    cur_id = (GLOBAL_BOT_CONTROL.get("oferta_actual") or {}).get("id")
    if len(HISTORIAL_OFERTAS) >= 2:
        cand = HISTORIAL_OFERTAS[-2]
        if cand.get("id") != cur_id:
            return cand
    for item in reversed(HISTORIAL_OFERTAS):
        if item.get("id") != cur_id:
            return item
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT id, empresa, titulo, portal, url_oferta
            FROM postulaciones
            WHERE id != ? AND url_oferta LIKE 'http%'
              AND (fecha_postulacion IS NOT NULL OR estado LIKE '%Postulado%' OR estado LIKE '%Cerrada%' OR estado LIKE '%Descartada%')
            ORDER BY COALESCE(fecha_postulacion, created_at) DESC, id DESC
            LIMIT 1
            """,
            (cur_id or -1,),
        ).fetchone()
        conn.close()
        if row:
            d = dict(row)
            d["url_real"] = d["url_oferta"]
            return d
    except Exception:
        pass
    return None


async def _detectar_nueva_pestana_del_bot(tabs_antes: list, parent_page):
    """Devuelve la nueva pestaña SOLO si fue abierta por `parent_page` (`opener == parent_page`) para no capturar pestañas del usuario."""
    tabs_despues = [t for t in parent_page.context.pages if t not in tabs_antes]
    for t in reversed(tabs_despues):
        try:
            if await t.opener() == parent_page:
                BOT_OWNED_PAGES.add(t)
                return t
        except Exception:
            pass
    return None

_WIDGET_JS = """
(() => {
    if (window.top !== window.self) {
        const cleanIframeWidget = () => {
            document.querySelectorAll('#autopostula-root-container, #autopostula-floating-control').forEach(el => el.remove());
        };
        cleanIframeWidget();
        if (!window.__ap_iframe_clean_bound) {
            window.__ap_iframe_clean_bound = true;
            document.addEventListener('DOMContentLoaded', cleanIframeWidget, { once: true });
        }
        return;
    }
    const WIDGET_VER = '4.5';
    window.__autopostula_widget_version = WIDGET_VER;
    if (window.__autopostula_interval && window.__autopostula_interval_ver !== WIDGET_VER) {
        clearInterval(window.__autopostula_interval);
        window.__autopostula_interval = null;
    }
    window.__autopostula_paused = window.__autopostula_paused || false;
    window.__autopostula_stop = window.__autopostula_stop || false;
    window.__autopostula_skip_wait = false;
    window.__autopostula_timer_text = window.__autopostula_timer_text || "30s";
    window.__autopostula_segundos_revision = window.__autopostula_segundos_revision || 30;
    window.__autopostula_panel_open = window.__autopostula_panel_open !== undefined ? window.__autopostula_panel_open : false;
    window.__autopostula_leer_desc = window.__autopostula_leer_desc !== undefined ? window.__autopostula_leer_desc : false;
    window.__autopostula_leer_qa = window.__autopostula_leer_qa !== undefined ? window.__autopostula_leer_qa : true;
    window.__autopostula_max_tabs = window.__autopostula_max_tabs || 4;
    window.__autopostula_inv_open = window.__autopostula_inv_open || false;
    window.__autopostula_inv_status = window.__autopostula_inv_status || "";
    window.__autopostula_hovering = false;

    function pushCmd(action, value = null) {
        if (typeof window.__autopostula_host_cmd === 'function') {
            try {
                window.__autopostula_host_cmd(action, value);
                return;
            } catch(e) {}
        }
        window.__autopostula_pending_cmd = { action, value, ts: Date.now() };
    }

    function updateWidgetState() {
        const pill = document.getElementById('autopostula-floating-pill');
        const modal = document.getElementById('autopostula-floating-modal');
        if (!pill) return;

        const pillIcon = document.getElementById('autopostula-pill-icon');
        const pillLabel = document.getElementById('autopostula-pill-label');
        const pillTimer = document.getElementById('autopostula-pill-timer');

        const modalToggleBtn = document.getElementById('autopostula-modal-toggle');
        const modalTimer = document.getElementById('autopostula-modal-timer');
        const autoDescBtn = document.getElementById('autopostula-modal-autodesc');
        const readQaBtn = document.getElementById('autopostula-modal-readqa');
        const maxTabsVal = document.getElementById('autopostula-max-tabs-val');
        const invBox = document.getElementById('autopostula-inv-box');
        const invStat = document.getElementById('autopostula-inv-status');

        if (maxTabsVal) maxTabsVal.innerText = String(window.__autopostula_max_tabs || 4);
        if (invBox) invBox.style.setProperty('display', window.__autopostula_inv_open ? 'flex' : 'none', 'important');
        if (invStat) invStat.innerText = window.__autopostula_inv_status || '';
        if (autoDescBtn) {
            const on = !!window.__autopostula_leer_desc;
            autoDescBtn.innerText = on ? '🔊 Lectura Auto Descripción (Edge-TTS): ON' : '🔇 Lectura Auto Descripción (Edge-TTS): OFF';
            autoDescBtn.style.background = on ? '#0ea5e9' : 'rgba(255,255,255,0.10)';
            autoDescBtn.style.borderColor = on ? '#38bdf8' : 'rgba(255,255,255,0.22)';
        }
        if (readQaBtn) {
            const qaOn = window.__autopostula_leer_qa !== false;
            readQaBtn.innerText = qaOn ? '🔊 Leer Preguntas y Respuestas: ON' : '🔇 Leer Preguntas y Respuestas: OFF';
            readQaBtn.style.background = qaOn ? '#0f766e' : 'rgba(255,255,255,0.10)';
            readQaBtn.style.borderColor = qaOn ? '#2dd4bf' : 'rgba(255,255,255,0.22)';
        }

        if (window.__autopostula_stop) {
            pill.style.borderColor = '#ef4444';
            if (pillLabel) pillLabel.innerText = 'Cerrando bot...';
            if (pillTimer) { pillTimer.innerText = 'STOP'; pillTimer.style.background = '#dc2626'; }
            if (modal) modal.style.setProperty('display', 'none', 'important');
            return;
        }

        if (window.__autopostula_paused) {
            pill.style.borderColor = '#f59e0b';
            pill.style.boxShadow = '0 0 25px rgba(245, 158, 11, 0.9)';
            if (pillIcon) pillIcon.innerText = '▶️';
            if (pillLabel) pillLabel.innerText = 'Pausado';
            if (pillTimer) { pillTimer.innerText = 'PAUSA'; pillTimer.style.background = '#d97706'; }
            if (modalTimer) { modalTimer.innerText = 'PAUSA'; modalTimer.style.color = '#fbbf24'; }

            if (modalToggleBtn) {
                modalToggleBtn.innerText = '▶️ REANUDAR BOT';
                modalToggleBtn.style.background = '#f59e0b';
            }
        } else {
            pill.style.borderColor = '#38bdf8';
            pill.style.boxShadow = '0 0 25px rgba(56, 189, 248, 0.85)';
            if (pillIcon) pillIcon.innerText = '⏸️';
            if (pillLabel) pillLabel.innerText = 'En Marcha';
            const ttxt = window.__autopostula_timer_text || '30s';
            if (pillTimer) { pillTimer.innerText = ttxt; pillTimer.style.background = '#0284c7'; }
            if (modalTimer) { modalTimer.innerText = ttxt + ' restantes'; modalTimer.style.color = '#cbd5e1'; }

            if (modalToggleBtn) {
                modalToggleBtn.innerText = '⏸️ PAUSAR BOT';
                modalToggleBtn.style.background = '#0284c7';
            }
        }

        if (modal) {
            modal.style.setProperty('display', window.__autopostula_panel_open ? 'flex' : 'none', 'important');
        }
    }

    function createOrAttachWidget() {
        if (window.__autopostula_disabled) return;
        const targetParent = document.documentElement || document.body;
        if (!targetParent) return;

        const allRoots = Array.from(document.querySelectorAll('#autopostula-root-container'));
        for (let k = 1; k < allRoots.length; k++) allRoots[k].remove();
        document.querySelectorAll('#autopostula-floating-control').forEach(el => el.remove());

        let root = allRoots[0] || document.getElementById('autopostula-root-container');
        const esViejo = root && root.getAttribute('data-ap-ver') !== WIDGET_VER;
        if (!root || root.parentElement !== targetParent || esViejo) {
            if (root && root.remove) root.remove();

            root = document.createElement('div');
            root.id = 'autopostula-root-container';
            root.setAttribute('data-ap-ver', WIDGET_VER);
            root.style.cssText = `
                position: fixed !important;
                bottom: 28px !important;
                left: 28px !important;
                z-index: 2147483647 !important;
                display: flex !important;
                flex-direction: column !important;
                align-items: flex-start !important;
                gap: 10px !important;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif !important;
                user-select: none !important;
            `;

            const modal = document.createElement('div');
            modal.id = 'autopostula-floating-modal';
            modal.style.cssText = `
                display: ${window.__autopostula_panel_open ? 'flex' : 'none'} !important;
                flex-direction: column !important;
                gap: 9px !important;
                width: 335px !important;
                background: rgba(13, 17, 28, 0.98) !important;
                border: 2px solid #38bdf8 !important;
                box-shadow: 0 10px 40px rgba(0, 0, 0, 0.9), 0 0 30px rgba(56, 189, 248, 0.4) !important;
                border-radius: 16px !important;
                padding: 14px !important;
                color: #ffffff !important;
                backdrop-filter: blur(20px) !important;
            `;

            modal.innerHTML = `
                <div style="display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid rgba(255,255,255,0.15); padding-bottom:7px;">
                    <div style="font-size:14px; font-weight:800; color:#38bdf8; display:flex; align-items:center; gap:6px;">
                        <span>⚙️ Panel AutoPostula</span>
                    </div>
                    <button id="autopostula-modal-close-window" style="background:transparent; border:none; color:#94a3b8; font-size:16px; font-weight:bold; cursor:pointer;">✕</button>
                </div>

                <div id="autopostula-modal-timer" style="font-size:13px; font-weight:700; color:#cbd5e1; text-align:center; padding:2px 0;">
                    ${window.__autopostula_timer_text} restantes
                </div>

                <button id="autopostula-modal-toggle" style="background:#0284c7; border:none; border-radius:10px; color:#ffffff; font-size:14px; font-weight:800; padding:9px; cursor:pointer; transition:all 0.15s ease;">
                    ${window.__autopostula_paused ? '▶️ REANUDAR BOT' : '⏸️ PAUSAR BOT'}
                </button>

                <div style="display:flex; flex-direction:column; gap:5px;">
                    <span style="font-size:11px; font-weight:700; color:#94a3b8;">⏱️ Tiempo de espera antes de enviar:</span>
                    <div style="display:flex; gap:6px; justify-content:space-between;">
                        <button class="autopostula-time-preset" data-seconds="15" style="flex:1; background:rgba(255,255,255,0.08); border:1px solid rgba(255,255,255,0.2); border-radius:8px; color:#fff; font-size:12px; font-weight:700; padding:4px 0; cursor:pointer;">15s</button>
                        <button class="autopostula-time-preset" data-seconds="30" style="flex:1; background:rgba(255,255,255,0.08); border:1px solid rgba(255,255,255,0.2); border-radius:8px; color:#fff; font-size:12px; font-weight:700; padding:4px 0; cursor:pointer;">30s</button>
                        <button class="autopostula-time-preset" data-seconds="60" style="flex:1; background:rgba(255,255,255,0.08); border:1px solid rgba(255,255,255,0.2); border-radius:8px; color:#fff; font-size:12px; font-weight:700; padding:4px 0; cursor:pointer;">60s</button>
                        <button class="autopostula-time-preset" data-seconds="120" style="flex:1; background:rgba(255,255,255,0.08); border:1px solid rgba(255,255,255,0.2); border-radius:8px; color:#fff; font-size:12px; font-weight:700; padding:4px 0; cursor:pointer;">2m</button>
                    </div>
                </div>

                <div style="display:flex; flex-direction:column; gap:5px; background:rgba(255,255,255,0.04); padding:8px; border-radius:10px; border:1px solid rgba(255,255,255,0.12);">
                    <div style="display:flex; justify-content:space-between; align-items:center;">
                        <span style="font-size:11px; font-weight:700; color:#cbd5e1;">🗂️ Máx. pestañas laborales:</span>
                        <div style="display:flex; align-items:center; gap:6px;">
                            <button id="autopostula-tabs-dec" style="background:rgba(255,255,255,0.12); border:1px solid rgba(255,255,255,0.25); color:#fff; width:24px; height:24px; border-radius:6px; font-weight:800; cursor:pointer;">-</button>
                            <span id="autopostula-max-tabs-val" style="font-size:13px; font-weight:900; color:#38bdf8; min-width:18px; text-align:center;">${window.__autopostula_max_tabs}</span>
                            <button id="autopostula-tabs-inc" style="background:rgba(255,255,255,0.12); border:1px solid rgba(255,255,255,0.25); color:#fff; width:24px; height:24px; border-radius:6px; font-weight:800; cursor:pointer;">+</button>
                        </div>
                    </div>
                    <button id="autopostula-modal-cleantabs" style="background:rgba(245,158,11,0.18); border:1px solid rgba(245,158,11,0.45); border-radius:8px; color:#fbbf24; font-size:11px; font-weight:800; padding:5px; cursor:pointer;">
                        🧹 Cerrar pestañas sin proceso ahora
                    </button>
                </div>

                <button id="autopostula-modal-autodesc" style="background:rgba(255,255,255,0.10); border:1px solid rgba(255,255,255,0.22); border-radius:10px; color:#ffffff; font-size:12px; font-weight:800; padding:7px; cursor:pointer;">
                    🔇 Lectura Auto Descripción (Edge-TTS): OFF
                </button>

                <button id="autopostula-modal-readqa" style="background:#0f766e; border:1px solid #2dd4bf; border-radius:10px; color:#ffffff; font-size:12px; font-weight:800; padding:8px; cursor:pointer;">
                    🔊 Leer Preguntas y Respuestas: ON
                </button>

                <button id="autopostula-modal-skip" style="background:#10b981; border:none; border-radius:10px; color:#ffffff; font-size:13px; font-weight:800; padding:8px; cursor:pointer;">
                    🚀 Enviar Postulación Ahora
                </button>

                <div style="display:flex; gap:6px;">
                    <button id="autopostula-modal-prev" style="flex:1; background:#334155; border:1px solid rgba(255,255,255,0.2); border-radius:10px; color:#f8fafc; font-size:12px; font-weight:800; padding:7px 6px; cursor:pointer;">
                        ⬅️ Link anterior
                    </button>
                    <button id="autopostula-modal-next" style="flex:1; background:#0369a1; border:1px solid rgba(56,189,248,0.45); border-radius:10px; color:#ffffff; font-size:12px; font-weight:800; padding:7px 6px; cursor:pointer;">
                        ⏭️ Continuar siguiente
                    </button>
                </div>

                <button id="autopostula-modal-investigar" style="background:linear-gradient(135deg,#7c3aed,#4f46e5); border:1px solid #a78bfa; border-radius:10px; color:#ffffff; font-size:12px; font-weight:800; padding:8px; cursor:pointer;">
                    🕵️ Agente investigador
                </button>

                <div id="autopostula-inv-box" style="display:none; flex-direction:column; gap:6px; background:rgba(76,29,149,0.28); border:1px solid rgba(167,139,250,0.45); border-radius:10px; padding:8px;">
                    <textarea id="autopostula-inv-note" rows="3" placeholder="Explícame qué ves o qué falta para postular..." style="width:100%; box-sizing:border-box; background:rgba(15,23,42,0.95); color:#f8fafc; border:1px solid rgba(148,163,184,0.45); border-radius:8px; padding:6px; font-size:12px; resize:vertical; font-family:inherit; user-select:text;"></textarea>
                    <button id="autopostula-inv-send" style="background:#8b5cf6; border:none; border-radius:8px; color:#ffffff; font-size:12px; font-weight:800; padding:7px; cursor:pointer;">
                        🚀 Enviar nota al Agente Investigador
                    </button>
                    <div id="autopostula-inv-status" style="font-size:11px; color:#ddd6fe; line-height:1.3;"></div>
                </div>

                <button id="autopostula-modal-memory" style="background:#6366f1; border:none; border-radius:10px; color:#ffffff; font-size:12px; font-weight:800; padding:7px; cursor:pointer;">
                    🧠 Ver / Editar Memoria (Q&A)
                </button>

                <button id="autopostula-modal-stop-bot" style="background:transparent; border:1px solid #ef4444; border-radius:10px; color:#ef4444; font-size:12px; font-weight:700; padding:6px; cursor:pointer;">
                    🛑 Detener Bot por Completo
                </button>
            `;

            const pill = document.createElement('div');
            pill.id = 'autopostula-floating-pill';
            pill.style.cssText = `
                display: flex !important;
                align-items: center !important;
                gap: 10px !important;
                background: rgba(13, 17, 28, 0.98) !important;
                border: 3px solid #38bdf8 !important;
                box-shadow: 0 0 25px rgba(56, 189, 248, 0.85), 0 8px 24px rgba(0, 0, 0, 0.8) !important;
                border-radius: 9999px !important;
                padding: 10px 18px !important;
                color: #ffffff !important;
                cursor: pointer !important;
                font-size: 14px !important;
                font-weight: 800 !important;
                backdrop-filter: blur(16px) !important;
                transition: transform 0.15s ease, box-shadow 0.2s ease !important;
            `;

            pill.innerHTML = `
                <span id="autopostula-pill-icon" style="font-size:16px;">${window.__autopostula_paused ? '▶️' : '⏸️'}</span>
                <span id="autopostula-pill-label">${window.__autopostula_paused ? 'Pausado' : 'En Marcha'}</span>
                <span id="autopostula-pill-timer" style="background:#0284c7; color:#fff; padding:2px 8px; border-radius:9999px; font-size:12px; font-weight:800;">${window.__autopostula_timer_text}</span>
                <span style="font-size:14px; opacity:0.8; margin-left:4px;">⚙️</span>
            `;

            let leaveTimer = null;
            root.addEventListener('mouseenter', () => {
                window.__autopostula_hovering = true;
                if (leaveTimer) { clearTimeout(leaveTimer); leaveTimer = null; }
            });
            root.addEventListener('mouseleave', () => {
                window.__autopostula_hovering = false;
                const ae = document.activeElement;
                const typingInv = !!(ae && ae.id === 'autopostula-inv-note') || !!window.__autopostula_inv_open;
                if (window.__autopostula_panel_open && !typingInv) {
                    leaveTimer = setTimeout(() => {
                        window.__autopostula_panel_open = false;
                        pushCmd('set_panel_open', false);
                        updateWidgetState();
                    }, 220);
                }
            });

            pill.addEventListener('click', (e) => {
                e.stopPropagation();
                window.__autopostula_panel_open = !window.__autopostula_panel_open;
                pushCmd('set_panel_open', window.__autopostula_panel_open);
                updateWidgetState();
            });

            root.appendChild(modal);
            root.appendChild(pill);
            targetParent.appendChild(root);

            modal.querySelector('#autopostula-modal-close-window')?.addEventListener('click', (e) => {
                e.stopPropagation();
                window.__autopostula_panel_open = false;
                pushCmd('set_panel_open', false);
                updateWidgetState();
            });

            modal.querySelector('#autopostula-modal-toggle')?.addEventListener('click', (e) => {
                e.stopPropagation();
                const nextPaused = !window.__autopostula_paused;
                window.__autopostula_paused = nextPaused;
                pushCmd(nextPaused ? 'pause' : 'resume', nextPaused);
                updateWidgetState();
            });

            modal.querySelector('#autopostula-tabs-dec')?.addEventListener('click', (e) => {
                e.stopPropagation();
                const nv = Math.max(1, (window.__autopostula_max_tabs || 4) - 1);
                window.__autopostula_max_tabs = nv;
                pushCmd('set_max_tabs', nv);
                updateWidgetState();
            });

            modal.querySelector('#autopostula-tabs-inc')?.addEventListener('click', (e) => {
                e.stopPropagation();
                const nv = Math.min(15, (window.__autopostula_max_tabs || 4) + 1);
                window.__autopostula_max_tabs = nv;
                pushCmd('set_max_tabs', nv);
                updateWidgetState();
            });

            modal.querySelector('#autopostula-modal-cleantabs')?.addEventListener('click', (e) => {
                e.stopPropagation();
                pushCmd('clean_tabs_now', true);
            });

            modal.querySelector('#autopostula-modal-autodesc')?.addEventListener('click', (e) => {
                e.stopPropagation();
                window.__autopostula_leer_desc = !window.__autopostula_leer_desc;
                pushCmd('toggle_autodesc', window.__autopostula_leer_desc);
                updateWidgetState();
            });

            modal.querySelector('#autopostula-modal-readqa')?.addEventListener('click', (e) => {
                e.stopPropagation();
                window.__autopostula_leer_qa = !(window.__autopostula_leer_qa !== false);
                pushCmd('toggle_readqa', window.__autopostula_leer_qa);
                updateWidgetState();
            });

            modal.querySelector('#autopostula-modal-skip')?.addEventListener('click', (e) => {
                e.stopPropagation();
                window.__autopostula_skip_wait = true;
                pushCmd('skip_wait', true);
                updateWidgetState();
            });

            modal.querySelector('#autopostula-modal-prev')?.addEventListener('click', (e) => {
                e.stopPropagation();
                window.__autopostula_paused = true;
                pushCmd('prev_link', true);
                updateWidgetState();
            });

            modal.querySelector('#autopostula-modal-next')?.addEventListener('click', (e) => {
                e.stopPropagation();
                window.__autopostula_paused = false;
                window.__autopostula_inv_open = false;
                pushCmd('next_offer', true);
                updateWidgetState();
            });

            modal.querySelector('#autopostula-modal-investigar')?.addEventListener('click', (e) => {
                e.stopPropagation();
                window.__autopostula_inv_open = !window.__autopostula_inv_open;
                if (window.__autopostula_inv_open) {
                    window.__autopostula_paused = true;
                    pushCmd('pause', true);
                    setTimeout(() => document.getElementById('autopostula-inv-note')?.focus(), 60);
                }
                updateWidgetState();
            });

            modal.querySelector('#autopostula-inv-send')?.addEventListener('click', (e) => {
                e.stopPropagation();
                const noteEl = document.getElementById('autopostula-inv-note');
                const noteTxt = (noteEl?.value || '').trim();
                const errs = Array.from(document.querySelectorAll('[data-automation-id*="error"], [id*="error"], .invalid-feedback, .field-validation-error, [role="alert"], .text-danger'))
                    .filter(el => el.offsetParent !== null && !el.closest('#autopostula-root-container'))
                    .map(el => (el.innerText || '').trim()).filter(Boolean).slice(0, 12);
                const btns = Array.from(document.querySelectorAll('button, a.btn, input[type="submit"], input[type="button"]'))
                    .filter(el => el.offsetParent !== null && !el.closest('#autopostula-root-container, header, nav, footer'))
                    .map(el => (el.innerText || el.value || '').trim()).filter(Boolean).slice(0, 15);
                const inps = Array.from(document.querySelectorAll('input:not([type="hidden"]), select, textarea'))
                    .filter(el => (el.offsetParent !== null || el.type === 'file') && !el.closest('#autopostula-root-container'))
                    .map(el => ({
                        tag: el.tagName, type: el.type || '', id: el.id || '', name: el.name || '',
                        val: el.type === 'password' ? '***' : String(el.value || '').slice(0, 40)
                    })).slice(0, 20);
                const reqId = 'inv_' + Date.now() + '_' + Math.random().toString(36).slice(2, 6);
                const payload = {
                    req_id: reqId,
                    note: noteTxt,
                    url: location.href,
                    title: document.title,
                    errors: errs,
                    buttons: btns,
                    inputs: inps,
                    bodySnippet: (document.body ? document.body.innerText : '').replace(/\\s+/g, ' ').trim().slice(0, 1800)
                };
                window.__autopostula_paused = true;
                window.__autopostula_inv_status = '⏳ Agente investigador analizando esta pestaña...';
                updateWidgetState();
                pushCmd('call_investigator', payload);
                if (typeof window.__autopostula_host_cmd !== 'function') {
                    fetch('http://localhost:8087/api/agente-investigador', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(payload)
                    }).catch(() => {});
                }
            });

            modal.querySelector('#autopostula-modal-memory')?.addEventListener('click', (e) => {
                e.stopPropagation();
                window.__autopostula_paused = true;
                pushCmd('pause', true);
                updateWidgetState();
                window.open('http://localhost:8087/memoria', '_blank');
            });

            modal.querySelector('#autopostula-modal-stop-bot')?.addEventListener('click', (e) => {
                e.stopPropagation();
                window.__autopostula_stop = true;
                pushCmd('stop', true);
                updateWidgetState();
            });

            modal.querySelectorAll('.autopostula-time-preset').forEach(btn => {
                btn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    const s = parseInt(btn.getAttribute('data-seconds'), 10);
                    window.__autopostula_segundos_revision = s;
                    pushCmd('set_seconds', s);
                    modal.querySelectorAll('.autopostula-time-preset').forEach(b => {
                        b.style.background = 'rgba(255,255,255,0.08)';
                        b.style.borderColor = 'rgba(255,255,255,0.2)';
                    });
                    btn.style.background = '#0284c7';
                    btn.style.borderColor = '#38bdf8';
                });
            });
        }

        updateWidgetState();
    }

    window.__ensure_autopostula_widget = createOrAttachWidget;
    window.__autopostula_sync_state = (st) => {
        window.__autopostula_disabled = false;
        window.__autopostula_paused = !!st.paused;
        window.__autopostula_stop = !!st.stop;
        window.__autopostula_timer_text = st.timer_text || '30s';
        window.__autopostula_segundos_revision = st.segundos || 30;
        window.__autopostula_leer_desc = !!st.leer_descripcion;
        if (typeof st.leer_qa === 'boolean') {
            window.__autopostula_leer_qa = st.leer_qa;
        }
        window.__autopostula_max_tabs = st.max_tabs || 4;
        if (typeof st.inv_status === 'string' && st.inv_status) {
            window.__autopostula_inv_status = st.inv_status;
        }
        const ae = document.activeElement;
        const typingInv = !!(ae && ae.id === 'autopostula-inv-note') || !!window.__autopostula_inv_open;
        if (typeof st.panel_open === 'boolean' && !window.__autopostula_hovering && !typingInv) {
            window.__autopostula_panel_open = st.panel_open;
        }
        createOrAttachWidget();
    };
    createOrAttachWidget();

    if (!window.__autopostula_interval) {
        window.__autopostula_interval_ver = WIDGET_VER;
        window.__autopostula_interval = setInterval(createOrAttachWidget, 600);
    }
})();
"""


def es_pagina_comercial_no_laboral(url: str, body_text: str) -> bool:
    """Detecta si una URL o página abierta corresponde a marketing/ventas/servicios (ej. LinkedIn Service Marketplace) en vez de postulación laboral."""
    u = (url or "").lower()
    if any(k in u for k in ["service-marketplace", "careercoaching", "/checkout", "/cart", "/pricing", "/subscribe", "premium/products", "/courses/"]):
        return True
    bt = (body_text or "").lower()
    if any(k in bt for k in ["linkedIn service marketplace".lower(), "elige un plan de coaching", "contratar un coach", "añadir al carrito", "comprar ahora", "método de pago"]):
        tiene_job = any(j in bt for j in ["currículum", "curriculum", "cv", "postular", "solicitud enviada", "vacante", "práctica profesional"])
        if not tiene_job:
            return True
    return False


async def gestionar_limite_pestanas(browser, max_tabs: int | None = None, tabs_protegidas: set | None = None) -> int:
    """Mantiene abiertas como máximo `max_tabs` pestañas laborales (incluyendo portales ATS externos), cerrando las cerradas, completadas, spam comercial o sin postulación en curso."""
    if not browser:
        return 0
    if max_tabs is None:
        max_tabs = GLOBAL_BOT_CONTROL.get("max_tabs", 4)
    limite = max(0, int(max_tabs))
    TABS_PROTEGIDAS_MANUAL.difference_update({p for p in list(TABS_PROTEGIDAS_MANUAL) if p.is_closed()})
    protegidas = set(tabs_protegidas or set()) | TABS_PROTEGIDAS_MANUAL
    keep_personal = [
        "mis-postulaciones", "campusvirtual.duoc.cl", "localhost:8087",
        "/postulantes/curriculum", "linkedin.com/in/", "github.com", "google.com/search"
    ]

    evaluadas = []
    BOT_OWNED_PAGES.difference_update({p for p in list(BOT_OWNED_PAGES) if p.is_closed()})
    for ctx in list(browser.contexts):
        for pg in list(ctx.pages):
            if pg not in BOT_OWNED_PAGES:
                continue
            u = (pg.url or "").lower()
            if not u.startswith("http") or any(k in u for k in keep_personal):
                continue
            try:
                info = await pg.evaluate(r'''() => {
                    const bt = (document.body?.innerText || '').toLowerCase();
                    const hasModalForm = document.querySelectorAll('dialog[open], #form-postulation-questions textarea, form#formularioPreguntasOferta textarea, .jobs-easy-apply-modal').length > 0;
                    const typed = Array.from(document.querySelectorAll('textarea, input[type="text"]:not([id*="search"]):not([id*="react-select"]):not([id*="select-footer"])')).filter(e => e.offsetParent !== null && (e.value || '').trim().length > 2).length;
                    const closed = bt.includes('ya no se aceptan solicitudes') || bt.includes('no longer accepting applications') || bt.includes('oferta cerrada') || bt.includes('ya no está disponible');
                    const done = bt.includes('solicitud enviada') || bt.includes('postulación enviada') || bt.includes('ha presentado solicitud exitosamente') || bt.includes('postulación exitosa') || bt.includes('solicitud completada') || bt.includes('gracias por su solicitud') || bt.includes('hemos recibido su solicitud') || bt.includes('ya ha realizado una solicitud') || Array.from(document.querySelectorAll('button, a')).some(e => (e.innerText || '').includes('Ver estadísticas'));
                    return { hasModalForm, typed, closed, done };
                }''')
            except Exception:
                info = {"hasModalForm": False, "typed": 0, "closed": False, "done": False}

            spam = es_pagina_comercial_no_laboral(u, "")
            en_proceso = (pg in protegidas) or info.get("hasModalForm") or (info.get("typed", 0) > 0 and not info.get("done"))
            evaluadas.append({
                "pg": pg,
                "url": u.split("?")[0],
                "en_proceso": en_proceso,
                "cerrable_inmediata": spam or info.get("closed") or info.get("done"),
            })

    cerradas = 0
    vistos_urls = set()
    restantes = []

    # 1. Cerrar de inmediato spam comercial, ofertas cerradas/completadas y duplicadas sin proceso activo
    for item in evaluadas:
        pg = item["pg"]
        u_base = item["url"]
        es_dup = (u_base in vistos_urls) and not item["en_proceso"]
        vistos_urls.add(u_base)
        if pg not in protegidas and (item["cerrable_inmediata"] or es_dup):
            try:
                await pg.close()
                cerradas += 1
            except Exception:
                restantes.append(item)
        else:
            restantes.append(item)

    # 2. Si aún superan el límite `max_tabs`, cerrar primero sin proceso y luego las más antiguas no protegidas hasta cumplir <= limite
    while len(restantes) > limite:
        candidato = next((x for x in restantes if not x["en_proceso"] and x["pg"] not in protegidas), None)
        if not candidato:
            candidato = next((x for x in restantes if x["pg"] not in protegidas), None)
        if not candidato:
            break
        restantes.remove(candidato)
        try:
            await candidato["pg"].close()
            cerradas += 1
        except Exception:
            pass

    if cerradas > 0:
        print(f"🧹 Limpieza automática: se cerraron {cerradas} pestañas laborales (quedaron {len(restantes)}/{limite}).")
    return cerradas


def _aplicar_comando_control(act: str, val=None):
    """Aplica de forma atómica e instantánea cualquier comando recibido desde cualquier pestaña o ventana."""
    if act == "pause":
        GLOBAL_BOT_CONTROL["paused"] = True
    elif act == "resume":
        GLOBAL_BOT_CONTROL["paused"] = False
    elif act == "toggle_pause":
        GLOBAL_BOT_CONTROL["paused"] = not GLOBAL_BOT_CONTROL["paused"]
    elif act == "stop":
        GLOBAL_BOT_CONTROL["stop"] = True
    elif act == "skip_wait":
        GLOBAL_BOT_CONTROL["skip_wait"] = True
    elif act == "set_seconds" and val:
        GLOBAL_BOT_CONTROL["segundos"] = int(val)
    elif act == "set_panel_open":
        GLOBAL_BOT_CONTROL["panel_open"] = bool(val)
    elif act == "toggle_autodesc":
        GLOBAL_BOT_CONTROL["leer_descripcion"] = bool(val)
        estado_txt = "activada" if GLOBAL_BOT_CONTROL["leer_descripcion"] else "desactivada"
        hablar(f"Lectura automática de descripciones {estado_txt}.")
    elif act == "read_desc_now":
        GLOBAL_BOT_CONTROL["read_desc_now"] = True
    elif act == "toggle_readqa":
        GLOBAL_BOT_CONTROL["leer_qa"] = bool(val)
        estado_qa = "activada" if GLOBAL_BOT_CONTROL["leer_qa"] else "desactivada"
        hablar(f"Lectura de preguntas y respuestas {estado_qa}.")
    elif act == "set_max_tabs" and val:
        GLOBAL_BOT_CONTROL["max_tabs"] = max(1, min(15, int(val)))
        GLOBAL_BOT_CONTROL["clean_tabs_now"] = True
    elif act == "clean_tabs_now":
        GLOBAL_BOT_CONTROL["clean_tabs_now"] = True
    elif act == "prev_link":
        GLOBAL_BOT_CONTROL["paused"] = True
        GLOBAL_BOT_CONTROL["open_prev_now"] = True
    elif act == "next_offer":
        GLOBAL_BOT_CONTROL["paused"] = False
        GLOBAL_BOT_CONTROL["next_offer"] = True
    elif act == "call_investigator" and isinstance(val, dict):
        GLOBAL_BOT_CONTROL["paused"] = True
        GLOBAL_BOT_CONTROL["timer_text"] = "INVESTIGA"
        GLOBAL_BOT_CONTROL["inv_status"] = "⏳ Agente investigador analizando esta pestaña..."
        GLOBAL_BOT_CONTROL["investigate_payload"] = val


async def sincronizar_widgets_navegador(browser):
    """Mantiene visible el panel flotante en TODAS las pestañas/ventanas HTTP activas y sincroniza pausar/reanudar/detener/lectura/límite."""
    if not browser:
        return
    paginas = []
    for ctx in list(browser.contexts):
        for pg in list(ctx.pages):
            try:
                if pg.url and pg.url.startswith("http"):
                    paginas.append(pg)
            except Exception:
                pass

    async def _poll_pg(pg):
        try:
            for fr in pg.frames[1:]:
                try:
                    await fr.evaluate("""() => {
                        if (window.__autopostula_interval) {
                            clearInterval(window.__autopostula_interval);
                            window.__autopostula_interval = null;
                        }
                        document.querySelectorAll('#autopostula-root-container, #autopostula-floating-control').forEach(e => e.remove());
                    }""")
                except Exception:
                    pass
            res = await pg.evaluate('''() => {
                const cmd = window.__autopostula_pending_cmd || null;
                window.__autopostula_pending_cmd = null;
                return {
                    ready: (
                        typeof window.__autopostula_sync_state === 'function' &&
                        window.__autopostula_widget_version === '4.5' &&
                        document.querySelectorAll('#autopostula-root-container').length === 1 &&
                        !!document.getElementById('autopostula-modal-readqa')
                    ),
                    cmd
                };
            }''')
            if not res.get("ready"):
                await pg.evaluate(_WIDGET_JS)
            return res.get("cmd")
        except Exception:
            return None

    cmds = await asyncio.gather(*[_poll_pg(pg) for pg in paginas], return_exceptions=True)
    cmds_validos = [c for c in cmds if isinstance(c, dict) and c.get("ts", 0) > GLOBAL_BOT_CONTROL["last_cmd_ts"]]
    cmds_validos.sort(key=lambda c: c.get("ts", 0))

    for cmd in cmds_validos:
        GLOBAL_BOT_CONTROL["last_cmd_ts"] = cmd["ts"]
        _aplicar_comando_control(cmd.get("action"), cmd.get("value"))

    if GLOBAL_BOT_CONTROL["read_desc_now"]:
        GLOBAL_BOT_CONTROL["read_desc_now"] = False
        desc_txt = (GLOBAL_BOT_CONTROL.get("desc_actual") or "").strip()
        emp_txt = GLOBAL_BOT_CONTROL.get("empresa_actual") or "esta oferta"
        if desc_txt:
            fragmento = re.sub(r'\s+', ' ', desc_txt)[:850]
            hablar(f"Descripción completa de {emp_txt}: {fragmento}")
        else:
            hablar("Aún no hay una descripción capturada en la oferta actual.")

    if GLOBAL_BOT_CONTROL["clean_tabs_now"]:
        GLOBAL_BOT_CONTROL["clean_tabs_now"] = False
        await gestionar_limite_pestanas(browser, GLOBAL_BOT_CONTROL["max_tabs"])

    if GLOBAL_BOT_CONTROL["open_prev_now"]:
        GLOBAL_BOT_CONTROL["open_prev_now"] = False
        prev_of = _obtener_oferta_anterior()
        if prev_of and prev_of.get("url_real"):
            try:
                ctx_b = browser.contexts[0]
                prev_pg = await ctx_b.new_page()
                BOT_OWNED_PAGES.add(prev_pg)
                TABS_PROTEGIDAS_MANUAL.add(prev_pg)
                await prev_pg.goto(prev_of["url_real"], wait_until="domcontentloaded", timeout=20000)
                await prev_pg.bring_to_front()
                await prev_pg.evaluate(_WIDGET_JS)
                GLOBAL_BOT_CONTROL["timer_text"] = "LINK ANT."
                emp_p = prev_of.get("empresa") or "la oferta anterior"
                print(f"\n⬅️  Abriendo enlace anterior de {emp_p}: {prev_of['url_real']}")
                hablar(f"Abriendo el enlace anterior de {emp_p} en una pestaña para que puedas revisarlo.")
            except Exception as e:
                print(f"Aviso abriendo link anterior: {e}")
        else:
            hablar("Todavía no hay una oferta anterior registrada en el historial.")

    if GLOBAL_BOT_CONTROL["investigate_payload"]:
        inv_pl = GLOBAL_BOT_CONTROL["investigate_payload"]
        GLOBAL_BOT_CONTROL["investigate_payload"] = None
        of_act = GLOBAL_BOT_CONTROL.get("oferta_actual") or {}
        inv_pl.setdefault("oferta_id", of_act.get("id"))
        inv_pl.setdefault("empresa", of_act.get("empresa") or "")
        inv_pl.setdefault("titulo", of_act.get("titulo") or "")
        inv_pl.setdefault("portal", of_act.get("portal") or "")
        inv_pl.setdefault("url_oferta", of_act.get("url_oferta") or "")
        for ctx_b in list(browser.contexts):
            for pg_b in list(ctx_b.pages):
                if (pg_b.url or "") == (inv_pl.get("url") or ""):
                    TABS_PROTEGIDAS_MANUAL.add(pg_b)
        def _post_inv(pl):
            import urllib.request
            try:
                req = urllib.request.Request(
                    "http://127.0.0.1:8087/api/agente-investigador",
                    data=json.dumps(pl, ensure_ascii=False).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                urllib.request.urlopen(req, timeout=8)
            except Exception as ex:
                print(f"Aviso llamando Agente Investigador: {ex}")
        threading.Thread(target=_post_inv, args=(inv_pl,), daemon=True).start()
        print(f"\n🕵️  Llamado Agente Investigador para #{inv_pl.get('oferta_id')} ({inv_pl.get('empresa')}) | Nota: {inv_pl.get('note', '')[:80]}")

    res_inv_path = BASE_DIR / "data" / "investigaciones" / "ultimo_resultado.json"
    if res_inv_path.exists():
        try:
            with open(res_inv_path, "r", encoding="utf-8") as fh_r:
                rj = json.load(fh_r)
            if rj.get("status") == "completado" and rj.get("summary"):
                GLOBAL_BOT_CONTROL["inv_status"] = f"✅ {str(rj['summary'])[:140]}"
        except Exception:
            pass

    payload = {
        "paused": GLOBAL_BOT_CONTROL["paused"],
        "stop": GLOBAL_BOT_CONTROL["stop"],
        "timer_text": GLOBAL_BOT_CONTROL["timer_text"],
        "segundos": GLOBAL_BOT_CONTROL["segundos"],
        "panel_open": GLOBAL_BOT_CONTROL["panel_open"],
        "leer_descripcion": GLOBAL_BOT_CONTROL["leer_descripcion"],
        "leer_qa": GLOBAL_BOT_CONTROL["leer_qa"],
        "max_tabs": GLOBAL_BOT_CONTROL["max_tabs"],
        "inv_status": GLOBAL_BOT_CONTROL["inv_status"],
    }

    async def _push_pg(pg):
        try:
            await pg.evaluate("(st) => { if (window.__autopostula_sync_state) window.__autopostula_sync_state(st); }", payload)
        except Exception:
            pass

    await asyncio.gather(*[_push_pg(pg) for pg in paginas], return_exceptions=True)


async def desactivar_widgets_navegador(browser):
    """Elimina el panel flotante de todas las pestañas/ventanas cuando el bot termina o se detiene."""
    if not browser:
        return
    for ctx in list(browser.contexts):
        for pg in list(ctx.pages):
            for fr in list(pg.frames):
                try:
                    await fr.evaluate('''() => {
                        window.__autopostula_disabled = true;
                        if (window.__autopostula_interval) {
                            clearInterval(window.__autopostula_interval);
                            window.__autopostula_interval = null;
                        }
                        document.querySelectorAll('#autopostula-root-container, #autopostula-floating-control').forEach(el => el.remove());
                    }''')
                except Exception:
                    pass


async def inyectar_boton_flotante_control(page):
    """Inyecta y sincroniza el panel flotante global en todas las pestañas y ventanas abiertas de Brave."""
    try:
        if page and page.context and page.context.browser:
            await sincronizar_widgets_navegador(page.context.browser)
            return
    except Exception:
        pass
    try:
        await page.evaluate(_WIDGET_JS)
    except Exception:
        pass


async def comprobar_pausa_y_parada(page) -> str:
    """Verifica si el usuario activó la pausa o la detención total desde cualquier pestaña o ventana."""
    browser = page.context.browser if page and page.context else None
    if browser:
        await sincronizar_widgets_navegador(browser)

    if GLOBAL_BOT_CONTROL["stop"]:
        return "stop"

    if GLOBAL_BOT_CONTROL["next_offer"]:
        return "next"

    if GLOBAL_BOT_CONTROL["paused"]:
        GLOBAL_BOT_CONTROL["timer_text"] = "PAUSA"
        if browser:
            await sincronizar_widgets_navegador(browser)
        print("\n⏸️  BOT EN ESTADO PAUSADO por el usuario. Esperando reanudación...")
        hablar("Bot pausado. Esperando que presiones reanudar.")
        while GLOBAL_BOT_CONTROL["paused"] and not GLOBAL_BOT_CONTROL["stop"] and not GLOBAL_BOT_CONTROL["next_offer"]:
            await asyncio.sleep(0.4)
            if browser:
                await sincronizar_widgets_navegador(browser)
        if GLOBAL_BOT_CONTROL["stop"]:
            return "stop"
        if GLOBAL_BOT_CONTROL["next_offer"]:
            return "next"
        print("\n▶️  BOT REANUDADO por el usuario.")
        hablar("Bot reanudado.")
    return "ok"


async def pulsar_primer_postula(page) -> bool:
    """Busca y hace clic en el botón inicial 'Postula fácil' / 'Postularme' / 'Solicitud sencilla' / 'Solicitar'."""
    # Si ya está abierto un modal de LinkedIn Easy Apply, no intentar hacer clic debajo
    try:
        if await page.locator("dialog[open], div.jobs-easy-apply-modal").count() > 0:
            return True
    except Exception:
        pass

    # En SPAs como Laborum, esperar brevemente hidratación y rellenar sueldo pretendido (=0 para prácticas) si ya está visible
    if "laborum.cl" in page.url.lower():
        try:
            await page.locator("button:has-text('Postularme'), input#salarioPretendido").first.wait_for(state="visible", timeout=3500)
        except Exception:
            pass
        try:
            sal = page.locator("input#salarioPretendido, input[name='salarioPretendido']").first
            if await sal.count() > 0 and await sal.is_visible():
                cur_v = (await sal.input_value() or "").strip()
                if not cur_v:
                    await sal.fill("0")
                    await sal.evaluate("el => { el.dataset.autopostulaFilled = '1'; el.dispatchEvent(new Event('input', {bubbles:true})); el.dispatchEvent(new Event('change', {bubbles:true})); }")
                    print("💵 Sueldo pretendido en Laborum completado automáticamente en 0.")
        except Exception:
            pass

    selectores = [
        "button.btn.primary-btn.blue.aim",
        "button:has-text('Postula fácil')",
        "button:has-text('Postularme')",
        "a:has-text('Postularme')",
        "button.primary-btn:has-text('Postular')",
        "button:has-text('Postularse')",
        "a:has-text('Postularse')",
        "a:has-text('POSTULAR A ESTA VACANTE')",
        "button:has-text('POSTULAR A ESTA VACANTE')",
        "a:has-text('APLICAR A ESTE PROCESO')",
        "button:has-text('APLICAR A ESTE PROCESO')",
        "a:has-text('Postúlate Ahora')",
        "button:has-text('Postúlate Ahora')",
        "button:has-text('Postular')",
        "a:has-text('Postular')",
        "a:has-text('Postula fácil')",
        "button[aria-label*='Solicitud sencilla']",
        "button:has-text('Solicitud sencilla')",
        "button[aria-label*='Easy Apply']",
        "button.jobs-apply-button",
        "a[aria-label*='Solicitar en el sitio']",
        "a:has-text('Solicitar')",
        "button:has-text('Solicitar')",
        "a#btnPostular",
        "#applyOfferSticky",
    ]
    for sel in selectores:
        locs = await page.locator(sel).all()
        for loc in locs:
            try:
                if await loc.is_visible():
                    # Evitar falsos positivos en tarjetas recomendadas, navegación, soporte o marketplace comercial
                    es_invalido = await loc.evaluate('''el => {
                        if (el.closest('.job-card-container, .jobs-similar-jobs, aside, footer, nav, header, #autopostula-root-container, #supportModal, .modal:not(.show), [aria-hidden="true"]')) return true;
                        const href = (el.getAttribute('href') || '').toLowerCase();
                        const txt = (el.innerText || '').toLowerCase();
                        const bad = ['service-marketplace', 'careercoaching', 'premium', 'learning', 'pricing', 'checkout', 'curso', 'coaching', 'comprar', 'salarios', '/messaging/'];
                        return bad.some(b => href.includes(b) || txt.includes(b));
                    }''')
                    if es_invalido:
                        continue
                    await loc.scroll_into_view_if_needed()
                    try:
                        await loc.click(timeout=4000)
                    except Exception:
                        await loc.evaluate("el => el.click()")
                    return True
            except Exception:
                pass

    return await page.evaluate('''() => {
        const isLinkedin = location.hostname.includes('linkedin.com');
        const btns = Array.from(document.querySelectorAll('button, a.btn, a')).filter(el => {
            if (el.closest('.job-card-container, .jobs-similar-jobs, aside, footer, nav, header, #autopostula-root-container, #supportModal, .modal:not(.show), [aria-hidden="true"]')) return false;
            if (isLinkedin && el.closest('ul')) return false;
            const href = (el.getAttribute('href') || '').toLowerCase();
            if (['service-marketplace', 'careercoaching', 'premium', 'learning', 'pricing', 'checkout', '/messaging/'].some(b => href.includes(b))) return false;
            const txt = (el.innerText || el.textContent || "").trim().toLowerCase();
            const aria = (el.getAttribute('aria-label') || "").toLowerCase();
            return (
                txt === 'postular' ||
                txt === 'postularme' ||
                txt === 'postularse' ||
                txt.includes('postula fácil') ||
                txt.includes('postular a esta vacante') ||
                txt.includes('aplicar a este proceso') ||
                txt.includes('postúlate ahora') ||
                txt.includes('solicitud sencilla') ||
                txt === 'solicitar' ||
                aria.includes('solicitud sencilla') ||
                aria.includes('solicitar en el sitio')
            ) && el.offsetParent !== null;
        });
        if (btns.length > 0) {
            btns[0].scrollIntoView({ block: 'center' });
            btns[0].click();
            return true;
        }
        return false;
    }''')


async def pulsar_comenzar_si_existe(page):
    """Si aparece el modal de Trabajando.cl con 'Comenzar', lo pulsa."""
    await asyncio.sleep(1.2)
    comenzar_locs = await page.locator("button:has-text('Comenzar'), a:has-text('Comenzar')").all()
    for c in comenzar_locs:
        try:
            if await c.is_visible():
                await c.click()
                print("Botón 'Comenzar' pulsado con éxito.")
                await asyncio.sleep(1.0)
                break
        except Exception:
            pass


async def pulsar_segundo_postular(page) -> bool:
    """Pulsa el botón final de envío del formulario ('Postular' / 'Enviar solicitud' / 'Enviar' / 'Enviar mi CV')."""
    selectores_finales = [
        "dialog[open] button:has-text('Enviar solicitud')",
        "dialog[open] button:has-text('Submit application')",
        "div[role='dialog'] button:has-text('Enviar solicitud')",
        "input[type='submit'].enviar-postula",
        "input[type='submit'][value*='Enviar postulación' i]",
        "input[type='submit'][name='commit']",
        "div.jobs-easy-apply-modal button:has-text('Enviar solicitud')",
        "button:has-text('Enviar solicitud')",
        "button#postulation-questions",
        "#form-postulation-questions button[type='submit']",
        "button.submit.submit-auto-width",
        "button.primary-btn:has-text('Postular'):visible",
        "button.w-100.primary-btn",
        "form.formularioPreguntasOferta button:has-text('Postular')",
        ".modal.show button:has-text('Postular')",
        "button:has-text('Enviar postulación')",
        "button:has-text('Confirmar postulación')",
        "button:has-text('Finalizar postulación')",
        "#btnKiller",
        "input[type='submit'][value*='Enviar' i]",
        "button:has-text('Enviar')",
        "button:has-text('Responder')",
        "button.primary-btn:has-text('Continuar')",
    ]
    for sel in selectores_finales:
        locs = await page.locator(sel).all()
        for loc in locs:
            try:
                if await loc.is_visible():
                    es_ajeno = await loc.evaluate('''el => {
                        if (el.closest('#autopostula-root-container, #supportModal, .modal:not(.show), [aria-hidden="true"], header, nav, footer')) return true;
                        const frm = el.closest('form');
                        if (frm && frm.querySelector('#supportTextfield, [name*="support" i], [id*="support" i]')) return true;
                        return false;
                    }''')
                    if es_ajeno:
                        continue
                    await loc.evaluate("el => { el.removeAttribute('disabled'); el.removeAttribute('aria-disabled'); el.classList.remove('boton-deshabilitado'); }")
                    await loc.scroll_into_view_if_needed()
                    try:
                        await loc.click(timeout=5000)
                    except Exception:
                        await loc.evaluate("el => el.click()")
                    return True
            except Exception:
                pass

    return await page.evaluate('''() => {
        const btns = Array.from(document.querySelectorAll('button, a.btn, input[type=\\\"submit\\\"]')).filter(b => {
            if (b.closest('#autopostula-root-container, #supportModal, .modal:not(.show), [aria-hidden="true"], header, nav, footer')) return false;
            const frm = b.closest('form');
            if (frm && frm.querySelector('#supportTextfield, [name*="support" i], [id*="support" i]')) return false;
            const t = (b.innerText || b.value || "").trim().toLowerCase();
            return (t === 'postular' || t.includes('enviar solicitud') || t.includes('enviar postulación') || t === 'enviar' || t.includes('finalizar') || t === 'submit application' || t === 'responder') && b.offsetParent !== null;
        });
        if (btns.length > 0) {
            btns[0].removeAttribute('disabled');
            btns[0].removeAttribute('aria-disabled');
            btns[0].classList.remove('boton-deshabilitado');
            btns[0].click();
            return true;
        }
        return false;
    }''')


def _cargar_perfil_usuario() -> dict:
    """Carga los datos personales reales del perfil desde SQLite con respaldo seguro."""
    perfil = {
        "nombre_completo": "Dhani Amit Elgueta Ríos",
        "primer_nombre": "Dhani Amit",
        "primer_apellido": "Elgueta",
        "segundo_apellido": "Ríos",
        "apellidos": "Elgueta Ríos",
        "email": "dh.elgueta@duocuc.cl",
        "email_login": "correof1553@gmail.com",
        "sexo": "Hombre",
        "telefono": "+56 9 4850 8528",
        "rut": "20.338.608-7",
        "direccion": "Las Condes, Santiago",
        "ciudad": "Santiago",
        "comuna": "Las Condes",
        "region": "Región Metropolitana",
        "pais": "Chile",
        "codigo_postal": "7550000",
        "institucion": "Duoc UC",
        "carrera": "Ingeniería en Infraestructura y Plataformas Tecnológicas",
        "linkedin": "https://www.linkedin.com/in/dhani-amit-elgueta-rios-613470292/",
        "ruta_cv": CV_MASTER_PDF,
    }
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM perfil LIMIT 1").fetchone()
        conn.close()
        if row:
            r = dict(row)
            nombre = (r.get("nombre") or perfil["nombre_completo"]).strip()
            partes = nombre.split()
            perfil["nombre_completo"] = nombre
            if len(partes) >= 4:
                perfil["primer_nombre"] = " ".join(partes[:-2])
                perfil["primer_apellido"] = partes[-2]
                perfil["segundo_apellido"] = partes[-1]
                perfil["apellidos"] = " ".join(partes[-2:])
            elif len(partes) >= 2:
                perfil["primer_nombre"] = partes[0]
                perfil["apellidos"] = " ".join(partes[1:])
                perfil["primer_apellido"] = partes[1]
            for k_db, k_dst in [("email", "email"), ("email_secundario", "email_login"), ("telefono", "telefono"), ("rut", "rut"), ("comuna", "comuna"), ("region", "region"), ("institucion", "institucion"), ("carrera", "carrera"), ("ruta_cv_pdf", "ruta_cv")]:
                if r.get(k_db):
                    perfil[k_dst] = str(r[k_db]).strip()
            if perfil["comuna"]:
                perfil["direccion"] = f"{perfil['comuna']}, {perfil['ciudad']}"
    except Exception:
        pass
    return perfil


async def manejar_redireccion_externa(page):
    """Detecta avisos ('Entendido' / 'Ya has ingresado...') y botones CTA externos ('Solicitar ahora' / 'Postularse' / 'APLICAR A ESTE PROCESO') siguiendo nuevas pestañas."""
    for ronda in range(3):
        tabs_antes = list(page.context.pages)
        clic_redir = False

        # 1. Buscar SweetAlert2 / modal de redirección con botón 'Entendido' o 'Continuar'
        try:
            body_low = (await page.inner_text("body") or "").lower()
            es_aviso_redir = any(w in body_low for w in [
                "te redireccionaremos", "has ingresado a esta postulación", "completar el formulario de inscripción",
                "sitio externo", "sitio web de la empresa", "página externa"
            ])
            selectores_redir = [
                ".swal2-popup button.swal2-confirm",
                ".swal2-confirm:has-text('Entendido')",
                "button:has-text('Entendido')",
                "a:has-text('Ir al formulario')",
                "a:has-text('Continuar en sitio externo')",
            ]
            for sel in selectores_redir:
                if sel == "button:has-text('Entendido')" and not es_aviso_redir:
                    continue
                loc = page.locator(sel).first
                if await loc.count() > 0 and await loc.is_visible():
                    txt_b = (await loc.inner_text() or "").strip()
                    print(f"🔀 Detectado aviso de redirección externa. Pulsando '{txt_b}'...")
                    await loc.click(timeout=4000)
                    clic_redir = True
                    await asyncio.sleep(3.0)
                    break
        except Exception:
            pass

        # Si abrió una nueva pestaña tras pulsar Entendido/Continuar, cerrar la intermedia y cambiar el foco del bot hacia ella
        nueva_tab = await _detectar_nueva_pestana_del_bot(tabs_antes, page)
        if nueva_tab is not None:
            print(f"🌐 Cambiando a nueva pestaña externa: {nueva_tab.url}")
            try:
                await nueva_tab.wait_for_load_state("domcontentloaded", timeout=15000)
            except Exception:
                pass
            old_page = page
            page = nueva_tab
            try:
                if old_page and old_page != page and "localhost:8087" not in (old_page.url or ""):
                    await old_page.close()
            except Exception:
                pass
            await page.bring_to_front()
            await asyncio.sleep(2.0)
            await inyectar_boton_flotante_control(page)

        # 1.5 Si quedó abierto el dropdown de Pandape ('APLICAR A ESTE PROCESO'), elegir primero 'Computrabajo' (/ApplyCT/)
        if "pandape" in page.url.lower():
            try:
                dd_item = page.locator(".dropdown-menu.show a:has-text('Computrabajo'), .dropdown-menu.show a[href*='ApplyCT'], .dropdown-menu.show a:has-text('Redactar currículum')").first
                if await dd_item.count() > 0 and await dd_item.is_visible():
                    txt_dd = (await dd_item.inner_text() or "").strip()
                    print(f"🚀 Pandape: eligiendo '{txt_dd}' del menú de postulación...")
                    await dd_item.click(timeout=5000)
                    clic_redir = True
                    await asyncio.sleep(3.0)
            except Exception:
                pass

        # 2. En portales ATS externos (CSOD, Workday, AIRA, Buk, Greenhouse, HiringRoom, Pandape, TrabajaEnElChile, Teamtailor, Get on Board), esperar montaje SPA y pulsar el botón CTA
        u_low = page.url.lower()
        es_portal_principal = any(dom in u_low for dom in ["firstjob.me", "trabajando.cl", "laborum.cl", "duoclaboral.cl", "computrabajo", "chiletrabajos"]) and ("pandape" not in u_low)
        if not es_portal_principal and ronda == 0:
            try:
                await page.locator(
                    "button:has-text('Solicitar ahora'), a:has-text('Solicitar ahora'), "
                    "a:has-text('APLICAR A ESTE PROCESO'), a.js_customDDbt, "
                    "button:has-text('Postúlate Ahora'), button:has-text('Postularse'), "
                    "a:has-text('Rellenar automáticamente con currículo'), a:has-text('Autofill with Resume'), "
                    "button:has-text('Enviar solicitud'), button:has-text('Apply now'), "
                    "button[data-automation-id='GoogleSignInButton'], input[type='file']"
                ).first.wait_for(state="visible", timeout=7000)
            except Exception:
                pass
        if "teamtailor.com" in u_low:
            try:
                modal_abierto = await page.locator("input#candidate_first_name:visible").count() > 0
                if not modal_abierto:
                    btn_tt = page.locator("button.careersite-button:has-text('Enviar solicitud'), button:has-text('Enviar solicitud'):visible").first
                    if await btn_tt.count() > 0 and await btn_tt.is_visible():
                        print("🚀 Teamtailor detectado. Pulsando 'Enviar solicitud' para abrir formulario de candidatura...")
                        await btn_tt.scroll_into_view_if_needed()
                        await btn_tt.click(timeout=5000)
                        await asyncio.sleep(2.5)
                        clic_redir = True
            except Exception:
                pass
        if not es_portal_principal or ronda == 1:
            selectores_cta_ext = [
                "a:has-text('POSTULAR A ESTA VACANTE')",
                "button:has-text('POSTULAR A ESTA VACANTE')",
                "a:has-text('Postularse')",
                "button:has-text('Postularse')",
                "a:has-text('APLICAR A ESTE PROCESO')",
                "button:has-text('APLICAR A ESTE PROCESO')",
                "a:has-text('Postúlate Ahora')",
                "button:has-text('Postúlate Ahora')",
                "a:has-text('Rellenar automáticamente con currículo')",
                "button:has-text('Rellenar automáticamente con currículo')",
                "a:has-text('Autofill with Resume')",
                "button:has-text('Solicitar ahora')",
                "a:has-text('Solicitar ahora')",
                "button:has-text('Postular ahora')",
                "a:has-text('Postular ahora')",
                "a:text-is('Postular')",
                "button:text-is('Postular')",
                "button:has-text('Apply now')",
                "a:has-text('Apply now')",
                "button:has-text('Iniciar postulación')",
                "button:has-text('Comenzar postulación')",
            ]
            tabs_cta_antes = list(page.context.pages)
            for sel in selectores_cta_ext:
                try:
                    locs = await page.locator(sel).all()
                    for loc in locs:
                        if await loc.is_visible():
                            en_zona_ajena = await loc.evaluate('''el => {
                                if (el.closest('header, nav, footer, aside, #autopostula-root-container, #supportModal, .modal:not(.show), [aria-hidden="true"], .gb-results-list, .similar-jobs, [class*="related"]')) return true;
                                const href = (el.getAttribute('href') || '').toLowerCase();
                                if (href.includes('widget_related_jobs')) return true;
                                return false;
                            }''')
                            if en_zona_ajena:
                                continue
                            # Evitar repetir clic si ya estamos dentro de un formulario activo (/application o con inputs de login/contacto)
                            ya_en_form = await page.locator("input[type='email']:visible, input[data-automation-id='email']:visible, input[type='file'], input[type='password']:visible, input[name*='name']:visible").count() > 0
                            if ya_en_form:
                                break
                            txt_cta = (await loc.inner_text() or "").strip()
                            print(f"🚀 Portal externo detectado. Pulsando '{txt_cta}' para abrir formulario...")
                            await loc.scroll_into_view_if_needed()
                            await loc.click(timeout=5000)
                            clic_redir = True
                            await asyncio.sleep(3.0)
                            # Si abrió dropdown de Pandape, seleccionar de inmediato Computrabajo (/ApplyCT/) o Redactar currículum
                            if "pandape" in page.url.lower():
                                dd_item = page.locator(".dropdown-menu.show a:has-text('Computrabajo'), .dropdown-menu.show a[href*='ApplyCT'], .dropdown-menu.show a:has-text('Redactar currículum')").first
                                if await dd_item.count() == 0 or not await dd_item.is_visible():
                                    await page.evaluate("() => { document.querySelector('a.js_customDDbt')?.click(); }")
                                    await asyncio.sleep(0.8)
                                dd_item = page.locator(".dropdown-menu.show a:has-text('Computrabajo'), .dropdown-menu.show a[href*='ApplyCT'], .dropdown-menu.show a:has-text('Redactar currículum')").first
                                if await dd_item.count() > 0 and await dd_item.is_visible():
                                    txt_dd = (await dd_item.inner_text() or "").strip()
                                    print(f"🚀 Pandape: eligiendo '{txt_dd}' tras abrir menú...")
                                    await dd_item.click(timeout=5000)
                                    await asyncio.sleep(3.0)
                                else:
                                    href_ct = await page.locator("a[href*='/ApplyCT/']").first.get_attribute("href")
                                    if href_ct:
                                        from urllib.parse import urljoin
                                        print(f"🚀 Pandape: abriendo formulario directo {href_ct}...")
                                        await page.goto(urljoin(page.url, href_ct), wait_until="domcontentloaded", timeout=20000)
                                        await asyncio.sleep(2.5)
                            break
                    if clic_redir:
                        break
                except Exception:
                    pass

            nueva_tab = await _detectar_nueva_pestana_del_bot(tabs_cta_antes, page)
            if nueva_tab is not None:
                try:
                    await nueva_tab.wait_for_load_state("domcontentloaded", timeout=15000)
                except Exception:
                    pass
                old_page = page
                page = nueva_tab
                try:
                    if old_page and old_page != page and "localhost:8087" not in (old_page.url or ""):
                        await old_page.close()
                except Exception:
                    pass
                await page.bring_to_front()
                await asyncio.sleep(1.5)
                await inyectar_boton_flotante_control(page)

        if not clic_redir:
            break

    return page


async def manejar_autenticacion_portal(page, empresa: str = "", solo_auto: bool = False):
    """
    Maneja el inicio de sesión o creación de cuenta en portales externos siguiendo el orden solicitado:
    1) Botón de Google -> elegir correof1553@gmail.com -> 'Probar otro método' (hasta 2 veces) -> correo/celular o 'Continuar'.
    2) Si no hay OAuth Google o sigue pidiendo acceso: correo correof1553@gmail.com + autocompletado Bitwarden/Brave.
    3) Si aún requiere intervención humana (clave/2FA), pausa interactiva en Brave salvo en modo `solo_auto`.
    """
    browser = page.context.browser if page and page.context else None
    u_low = (page.url or "").lower()

    # En Workday, esperar brevemente a que React monte los botones de SSO o el input de archivo
    if "myworkdayjobs.com" in u_low and "/apply" in u_low:
        for _ in range(12):
            try:
                listo = await page.locator(
                    "button[data-automation-id='GoogleSignInButton'], button[data-automation-id='SignInWithEmailButton'], input[type='file'], input[data-automation-id='email']"
                ).count() > 0
                if listo:
                    break
            except Exception:
                pass
            await asyncio.sleep(0.5)

    # Detectar si realmente estamos en un contexto de autenticación/SSO
    try:
        ctx_auth = await page.evaluate(r'''() => {
            const u = location.href.toLowerCase();
            const bt = (document.body?.innerText || '').toLowerCase().slice(0, 5000);
            const gBtn = !!document.querySelector("button[data-automation-id='GoogleSignInButton']");
            const eBtn = !!document.querySelector("button[data-automation-id='SignInWithEmailButton']");
            const hasPass = Array.from(document.querySelectorAll('input[type="password"]')).some(e => e.offsetParent !== null);
            const hasFile = document.querySelectorAll("input[type='file']").length > 0;
            const txtAuth = ['iniciar sesión', 'inicia sesión', 'crear cuenta', 'sign in', 'create account', 'conexión con google', 'acceder con google', 'elige una cuenta', 'identificar tu candidatura', 'cuál es tu correo'].some(k => bt.includes(k));
            return { isGoogle: u.includes('accounts.google.com'), gBtn, eBtn, hasPass, hasFile, txtAuth, u };
        }''')
    except Exception:
        return page, True, "no_requiere_login"

    es_auth = (
        ctx_auth["isGoogle"]
        or ctx_auth["gBtn"]
        or ctx_auth["eBtn"]
        or (ctx_auth["hasPass"] and not ctx_auth["hasFile"])
        or (ctx_auth["txtAuth"] and not ctx_auth["hasFile"] and ("myworkdayjobs.com" in ctx_auth["u"] or "pandape" in ctx_auth["u"] or "/applyct" in ctx_auth["u"] or "login" in ctx_auth["u"] or "iniciar" in ctx_auth["u"]))
    )
    if not es_auth:
        return page, True, "no_requiere_login"

    # PRIORIDAD 1: Botón de Iniciar / Conectar con Google
    if not ctx_auth["isGoogle"]:
        g_selectors = [
            "button[data-automation-id='GoogleSignInButton']",
            "button:has-text('Conexión con Google')",
            "button:has-text('Continuar con Google')",
            "a:has-text('Continuar con Google')",
            "button:has-text('Iniciar sesión con Google')",
            "a:has-text('Iniciar sesión con Google')",
            "button:has-text('Sign in with Google')",
            "a:has-text('Sign in with Google')",
        ]
        for sel in g_selectors:
            try:
                loc = page.locator(sel).first
                if await loc.count() > 0 and await loc.is_visible():
                    en_footer = await loc.evaluate("el => !!el.closest('footer, #autopostula-root-container')")
                    if en_footer:
                        continue
                    print("🔐 Prioridad 1: Pulsando inicio de sesión con Google...")
                    hablar("Iniciando acceso con Google para usar correo f 1 5 5 3.")
                    tabs_antes = list(page.context.pages)
                    await loc.scroll_into_view_if_needed()
                    await loc.click(timeout=5000)
                    await asyncio.sleep(4.0)
                    pop = await _detectar_nueva_pestana_del_bot(tabs_antes, page)
                    if pop is not None:
                        try:
                            await pop.wait_for_load_state("domcontentloaded", timeout=12000)
                        except Exception:
                            pass
                        page = pop
                        await page.bring_to_front()
                        await inyectar_boton_flotante_control(page)
                    break
            except Exception:
                pass

    # Paso B: Flujo automatizado dentro de accounts.google.com
    intentos_otro_metodo = 0
    for _paso_g in range(8):
        if "accounts.google.com" not in (page.url or "").lower():
            break

        # 1. Selector de cuenta -> correof1553@gmail.com
        try:
            acc = page.locator("[data-identifier='correof1553@gmail.com'], [data-email='correof1553@gmail.com']").first
            if await acc.count() > 0 and await acc.is_visible():
                print("📧 Google SSO: seleccionando correof1553@gmail.com...")
                await acc.click(timeout=5000)
                await asyncio.sleep(3.5)
                continue
        except Exception:
            pass

        # 2. Pantalla de consentimiento / 'Te damos la bienvenida nuevamente' -> 'Continuar'
        try:
            btn_cont = page.locator("button:has-text('Continuar'), button:has-text('Continue'), button:has-text('Permitir'), button:has-text('Allow')").first
            if await btn_cont.count() > 0 and await btn_cont.is_visible():
                print("✅ Google SSO: confirmando 'Continuar' para volver al portal...")
                await btn_cont.click(timeout=5000)
                await asyncio.sleep(4.5)
                continue
        except Exception:
            pass

        # 3. Opciones de verificación alternativa: priorizar correo de recuperación o teléfono/SMS si está en lista selection
        try:
            bt_g = (await page.inner_text("body") or "").lower()
            if "elige" in bt_g or "verific" in bt_g or "obtén un código" in bt_g:
                alt_loc = page.locator("li:has-text('correo'), li:has-text('correof1553'), li:has-text('dhanielgueta'), div[role='link']:has-text('correo'), li:has-text('teléfono'), li:has-text('celular'), li:has-text('8528'), li:has-text('mensaje de texto')").first
                if await alt_loc.count() > 0 and await alt_loc.is_visible():
                    txt_alt = (await alt_loc.inner_text() or "").strip().replace("\n", " ")
                    print(f"📲 Google 2FA/Alternativa: eligiendo '{txt_alt[:50]}'...")
                    await alt_loc.click(timeout=5000)
                    await asyncio.sleep(3.5)
                    continue
        except Exception:
            pass

        # 4. Si pide contraseña en Google: dar margen a Bitwarden/Brave; si sigue vacía, pulsar 'Probar otro método' hasta 2 veces
        try:
            pwd_loc = page.locator("input[type='password']:visible").first
            if await pwd_loc.count() > 0:
                await pwd_loc.focus()
                await asyncio.sleep(1.5)
                cur_pwd = await pwd_loc.input_value()
                if cur_pwd:
                    sig = page.locator("button:has-text('Siguiente'), button:has-text('Next')").first
                    if await sig.count() > 0:
                        await sig.click(timeout=5000)
                        await asyncio.sleep(4.0)
                        continue
        except Exception:
            pass

        if intentos_otro_metodo < 2:
            try:
                otro_btn = page.locator("div[role='link']:has-text('Probar otro método'), li:has-text('Probar otro método'), button:has-text('Probar otro método'), button:has-text('Probar otra manera'), button:has-text('Try another way')").first
                if await otro_btn.count() > 0 and await otro_btn.is_visible():
                    intentos_otro_metodo += 1
                    print(f"🔀 Google: pulsando 'Probar otro método' ({intentos_otro_metodo}/2)...")
                    await otro_btn.click(timeout=5000)
                    await asyncio.sleep(3.5)
                    continue
            except Exception:
                pass
        break

    # Si Google redirigió de vuelta a Workday u otro portal, esperar carga de formularios
    if "myworkdayjobs.com" in (page.url or "").lower():
        await asyncio.sleep(2.5)

    # PRIORIDAD 2: Si no se logró con Google (o el portal usa correo/clave propio como TrabajaEnElChile / iCIMS / Workday Email)
    try:
        sign_email_btn = page.locator("button[data-automation-id='SignInWithEmailButton']").first
        if await sign_email_btn.count() > 0 and await sign_email_btn.is_visible():
            print("📧 Pulsando 'Conexión con correo electrónico' como respaldo...")
            await sign_email_btn.click(timeout=4000)
            await asyncio.sleep(2.5)
    except Exception:
        pass

    await autocompletar_datos_personales_y_cv(page)

    # En Pandape (/ApplyCT/), si rellenó el correo electrónico y no pide clave aún, pulsar CONTINUAR
    if ("pandape" in (page.url or "").lower() or "/applyct" in (page.url or "").lower()) and await page.locator("input[type='password']:visible").count() == 0:
        try:
            em_v = await page.locator("#Email:visible, input[type='email']:visible").first.input_value()
            if (em_v or "").strip():
                btn_c = page.locator("button:has-text('CONTINUAR'), input[type='submit'][value*='CONTINUAR' i], button:has-text('Continuar')").first
                if await btn_c.count() > 0 and await btn_c.is_visible():
                    print("📧 Pandape: correo ingresado, pulsando CONTINUAR...")
                    await btn_c.click(timeout=5000)
                    await asyncio.sleep(3.0)
        except Exception:
            pass

    # Intentar Bitwarden / gestor de contraseñas guardado en Brave si hay input password visible
    try:
        pwd_portal = page.locator("input[type='password']:visible").first
        if await pwd_portal.count() > 0:
            await pwd_portal.click(timeout=2000)
            await asyncio.sleep(2.0)
            val_pwd = await pwd_portal.input_value()
            if val_pwd:
                btn_login = page.locator("button[data-automation-id='signInSubmitButton'], button:has-text('Conectar'), button:has-text('INICIAR SESIÓN'), button:has-text('Iniciar sesión'), button:has-text('Sign in'), button:has-text('Siguiente')").first
                if await btn_login.count() > 0 and await btn_login.is_visible():
                    print("🔓 Clave autocompletada por navegador/Bitwarden. Pulsando acceso...")
                    await btn_login.click(timeout=5000)
                    await asyncio.sleep(4.0)
    except Exception:
        pass

    # Verificar si ya superó el muro de acceso
    try:
        sigue_bloqueado = await page.evaluate(r'''() => {
            const u = location.href.toLowerCase();
            if (u.includes('accounts.google.com')) return true;
            const hasPass = Array.from(document.querySelectorAll('input[type="password"]')).some(e => e.offsetParent !== null);
            const hasFile = document.querySelectorAll("input[type='file']").length > 0;
            const hasEmailLogin = !!document.querySelector("button[data-automation-id='GoogleSignInButton'], button[data-automation-id='SignInWithEmailButton']");
            return hasEmailLogin || (hasPass && !hasFile);
        }''')
    except Exception:
        sigue_bloqueado = False

    if not sigue_bloqueado:
        return page, True, "autenticado_ok"

    if solo_auto:
        return page, False, "requiere_login_manual"

    # PRIORIDAD 3: Pausar en pantalla para que el usuario aplique Bitwarden / código por correo o celular / Crear cuenta y pulse Reanudar
    GLOBAL_BOT_CONTROL["paused"] = True
    GLOBAL_BOT_CONTROL["timer_text"] = "PAUSA"
    traer_brave_al_frente()
    if browser:
        await sincronizar_widgets_navegador(browser)
    msg_auth = f"Acceso preparado con correo f 1 5 5 3 en {empresa}. Completa tu contraseña de Bitwarden o el código en pantalla y pulsa Reanudar Bot."
    print(f"\n🔐 {msg_auth}")
    hablar(msg_auth)

    while GLOBAL_BOT_CONTROL["paused"] and not GLOBAL_BOT_CONTROL["stop"]:
        if browser:
            await sincronizar_widgets_navegador(browser)
        await asyncio.sleep(0.6)

    if GLOBAL_BOT_CONTROL["stop"]:
        return page, False, "stop"

    await asyncio.sleep(2.0)
    await inyectar_boton_flotante_control(page)
    return page, True, "autenticado_manual"


async def autocompletar_datos_personales_y_cv(page) -> int:
    """Autocompleta campos de identidad, contacto, ubicación, educación y sube CV PDF en formularios externos o nativos."""
    perfil = _cargar_perfil_usuario()
    completados = 0

    # 1. Subida automática de CV en cualquier <input type='file'> disponible
    ruta_cv = perfil.get("ruta_cv") or CV_MASTER_PDF
    if not os.path.exists(ruta_cv):
        ruta_cv = CV_MASTER_PDF
    if ruta_cv and os.path.exists(ruta_cv):
        file_inputs = await page.locator("input[type='file']").all()
        for f_el in file_inputs:
            try:
                f_info = await f_el.evaluate(r'''el => {
                    if (el.dataset.autopostulaFilled === '1') return {skip: true};
                    let txt = ((el.id || '') + ' ' + (el.name || '') + ' ' + (el.getAttribute('aria-label') || '')).toLowerCase();
                    let p = el.parentElement;
                    for (let k = 0; k < 4 && p; k++) {
                        txt += ' ' + (p.innerText || '').toLowerCase().slice(0, 200);
                        p = p.parentElement;
                    }
                    return {skip: false, txt: txt, filesCount: el.files ? el.files.length : 0};
                }''')
                if f_info.get("skip") or f_info.get("filesCount", 0) > 0:
                    continue
                t_file = f_info.get("txt", "")
                if any(k in t_file for k in ["cv", "curríc", "curric", "resume", "hoja de vida", "archivo", "upload", "adjuntar", "pdf"]) or len(file_inputs) == 1:
                    await f_el.set_input_files(ruta_cv)
                    await f_el.evaluate("el => { el.dataset.autopostulaFilled = '1'; el.dispatchEvent(new Event('change', {bubbles:true})); }")
                    print(f"📄 CV PDF adjuntado automáticamente en formulario externo: {os.path.basename(ruta_cv)}")
                    completados += 1
                    await asyncio.sleep(2.5)
            except Exception as e:
                print(f"Aviso adjuntando CV en input file: {e}")

    # 2. Autocompletado de campos de texto/email/tel personal si están vacíos
    try:
        es_contexto_login_o_cuenta = await page.evaluate(r'''() => {
            const u = location.href.toLowerCase();
            const bt = (document.body?.innerText || '').toLowerCase().slice(0, 5000);
            const hasPass = Array.from(document.querySelectorAll('input[type="password"]')).some(e => e.offsetParent !== null);
            const urlAuth = ['/login', '/signin', '/sign-in', '/register', '/signup', '/create-account', '/crear-cuenta', '/iniciar', '/auth', '/applyct', 'icims.com'].some(k => u.includes(k));
            const txtAuth = ['iniciar sesión', 'inicia sesión', 'inicio de sesión', 'crear cuenta', 'crea tu cuenta', 'regístrate', 'registrarse', 'sign in', 'create account', 'log in', 'identificar tu candidatura'].some(k => bt.includes(k));
            return hasPass || urlAuth || txtAuth;
        }''')
    except Exception:
        es_contexto_login_o_cuenta = False

    inputs = []
    frames_seguros = [page] + [
        fr for fr in page.frames[1:]
        if fr.url and not any(k in fr.url.lower() for k in ["captcha", "turnstile", "challenges.cloudflare", "about:blank"])
    ]
    for tgt in frames_seguros:
        try:
            loc_inps = await tgt.locator("input[type='text']:visible, input[type='email']:visible, input[type='tel']:visible, input[type='url']:visible, input:not([type]):visible").all()
            if tgt == page:
                inputs.extend(loc_inps)
            else:
                # En iframes externos seguros (ej. iCIMS login), procesar también campos de correo/cuenta/contacto
                inputs.extend([el for el in loc_inps])
        except Exception:
            pass
    reglas_texto = [
        (["sueldo", "salario", "salariopretendido", "renta pretendida", "remuneración", "remuneracion", "salarymin"], "0"),
        (["fecha de nacimiento", "birthdate", "birth date", "nacimiento"], "11/02/2000"),
        (["addressnumber", "número de calle", "numero exterior", "nro casa"], "100"),
        (["addresscomplement", "complemento"], "Depto 1"),
        (["nombre completo", "full name", "nombre y apellido"], perfil["nombre_completo"]),
        (["primer apellido", "apellido paterno", "last name", "surname", "apellidos", "apellido"], perfil["apellidos"]),
        (["segundo nombre", "middle name"], ""),
        (["primer nombre", "first name", "nombre", "nombres", "given name"], perfil["primer_nombre"]),
        (["correo", "email", "e-mail", "mail"], perfil["email_login"] if es_contexto_login_o_cuenta else perfil["email"]),
        (["teléfono", "telefono", "celular", "móvil", "movil", "phone", "mobile", "whatsapp"], perfil["telefono"]),
        (["rut", "run", "dni", "cédula", "cedula", "identificación", "documento", "cpf"], perfil["rut"]),
        (["línea de dirección 2", "address line 2"], ""),
        (["línea de dirección", "dirección", "direccion", "address", "domicilio", "calle"], perfil["direccion"]),
        (["comuna", "district"], perfil["comuna"]),
        (["ciudad", "city", "localidad"], perfil["ciudad"]),
        (["región", "region", "estado", "provincia", "state"], perfil["region"]),
        (["código postal", "codigo postal", "postal", "zip"], perfil["codigo_postal"]),
        (["universidad", "institución", "institucion", "casa de estudios", "school"], perfil["institucion"]),
        (["carrera", "título", "titulo", "degree", "major"], perfil["carrera"]),
        (["linkedin"], perfil["linkedin"]),
    ]

    for inp in inputs:
        try:
            meta = await inp.evaluate(r'''el => {
                if (el.dataset.autopostulaFilled === '1' || el.disabled || el.readOnly) return {skip: true};
                if (el.getAttribute('data-automation-id') === 'beecatcher' || el.id === 'full_email') return {skip: true};
                const parentTxt = (el.closest('div, label, fieldset')?.innerText || '').toLowerCase();
                if (parentTxt.includes('for robots only') || parentTxt.includes('do not enter if you')) return {skip: true};
                if (el.closest('header') || el.closest('nav') || el.closest('footer') || el.closest('.search-box') || el.closest('#search')) return {skip: true};
                const val = (el.value || '').trim();
                if (val.length > 0) return {skip: true};
                let label = '';
                if (el.id) {
                    const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
                    if (l) label = l.innerText.trim();
                }
                const lby = el.getAttribute('aria-labelledby');
                if (!label && lby) label = (document.getElementById(lby)?.innerText || '').trim();
                if (!label) label = (el.getAttribute('aria-label') || el.placeholder || '').trim();
                if (!label) {
                    let p = el.parentElement;
                    for (let k = 0; k < 4 && p; k++) {
                        const candidates = Array.from(p.querySelectorAll('label, legend, span, div, p')).map(x => (x.innerText||'').trim()).filter(t => t && t.length > 1 && t.length < 70 && t.indexOf('\n') === -1 && t !== '*');
                        if (candidates.length > 0) {
                            label = candidates[0];
                            break;
                        }
                        p = p.parentElement;
                    }
                }
                return {skip: false, label: label, ph: el.placeholder || '', type: (el.type||'').toLowerCase(), id: el.id||'', name: el.name||''};
            }''')
            if meta.get("skip"):
                continue

            etiqueta = f"{meta.get('label', '')} {meta.get('ph', '')} {meta.get('name', '')} {meta.get('id', '')}".lower().replace("*", "").strip()
            if any(blk in etiqueta for blk in ["search", "buscar", "filtro", "keywords", "react-select", "select-footer"]):
                continue

            valor = None
            if meta.get("type") == "email" or any(k in etiqueta for k in ["correo", "email", "e-mail", "mail"]):
                if any(k in etiqueta for k in ["institucional", "duoc"]):
                    valor = perfil["email"]
                elif es_contexto_login_o_cuenta or any(k in etiqueta for k in ["personal", "gmail", "cuenta", "login", "iniciar", "registr"]):
                    valor = perfil["email_login"]
                else:
                    valor = perfil["email"]
            elif meta.get("type") == "tel":
                valor = perfil["telefono"]
            elif any(s in etiqueta for s in ["sueldo", "salario", "renta pretendida", "remunerac"]):
                valor = "0"
            else:
                for claves, val_regla in reglas_texto:
                    if any(re.search(r'(?<!\w)' + re.escape(c) + r'(?!\w)', etiqueta) for c in claves):
                        valor = val_regla
                        break

            if valor:
                await inp.fill(valor, force=True)
                await inp.evaluate("el => { el.dataset.autopostulaFilled = '1'; el.dispatchEvent(new Event('input', {bubbles:true})); el.dispatchEvent(new Event('change', {bubbles:true})); el.dispatchEvent(new Event('blur', {bubbles:true})); }")
                completados += 1
        except Exception:
            pass

    # 3. Autocompletado de <select> comunes (género=Hombre, estado civil=Soltero, hijos=No, país/región/comuna)
    try:
        sel_count = await page.evaluate(r'''(perfil) => {
            let cnt = 0;
            const selects = Array.from(document.querySelectorAll('select')).filter(s => s.offsetParent !== null && !s.disabled);
            for (const s of selects) {
                let lbl = ((s.id || '') + ' ' + (s.name || '') + ' ' + (s.getAttribute('aria-label') || '')).toLowerCase();
                let p = s.parentElement;
                for (let k = 0; k < 4 && p; k++) {
                    const l = p.querySelector('label, legend');
                    if (l) { lbl += ' ' + l.innerText.toLowerCase(); break; }
                    p = p.parentElement;
                }
                const optsArr = Array.from(s.options);
                const optsTxt = optsArr.map(o => o.text.trim().toLowerCase());

                // Género / Sexo: SIEMPRE corregir a Hombre / Masculino incluso si un parser de CV preseleccionó Mujer
                const esSexo = /\b(sexo|sex|género|genero|gender|idsex)\b/.test(lbl) || (
                    optsTxt.some(t => /\b(hombre|masculino|male|varón|varon)\b/.test(t)) &&
                    optsTxt.some(t => /\b(mujer|femenino|female)\b/.test(t))
                );
                if (esSexo) {
                    const optH = optsArr.find(o => /\b(hombre|masculino|male|varón|varon)\b/.test(o.text.trim().toLowerCase()));
                    if (optH && s.value !== optH.value) {
                        s.value = optH.value;
                        s.dispatchEvent(new Event('change', {bubbles: true}));
                        s.dispatchEvent(new Event('input', {bubbles: true}));
                        cnt++;
                    }
                    continue;
                }

                // Estado civil -> Soltero(a)
                if (/\b(estado civil|marital|idmaritalstatus)\b/.test(lbl)) {
                    const optS = optsArr.find(o => o.text.trim().toLowerCase().includes('solter'));
                    if (optS && s.value !== optS.value) {
                        s.value = optS.value;
                        s.dispatchEvent(new Event('change', {bubbles: true}));
                        s.dispatchEvent(new Event('input', {bubbles: true}));
                        cnt++;
                    }
                    continue;
                }

                // Hijos -> No
                if (/\b(hijos|children|idchildren)\b/.test(lbl)) {
                    const optN = optsArr.find(o => o.text.trim().toLowerCase() === 'no');
                    if (optN && s.value !== optN.value) {
                        s.value = optN.value;
                        s.dispatchEvent(new Event('change', {bubbles: true}));
                        s.dispatchEvent(new Event('input', {bubbles: true}));
                        cnt++;
                    }
                    continue;
                }

                const selOpt = s.options[s.selectedIndex];
                const curTxt = (selOpt ? selOpt.text : '').trim().toLowerCase();
                const isPlaceholder = !s.value || curTxt.includes('seleccione') || curTxt.includes('select') || curTxt === '--';
                if (!isPlaceholder) continue;

                let desired = null;
                if (lbl.includes('país') || lbl.includes('pais') || lbl.includes('country')) desired = 'chile';
                else if (lbl.includes('región') || lbl.includes('region') || lbl.includes('estado') || lbl.includes('state')) desired = 'metropolitana';
                else if (lbl.includes('comuna') || lbl.includes('ciudad') || lbl.includes('city')) desired = 'las condes';

                if (desired) {
                    const opt = optsArr.find(o => o.text.toLowerCase().includes(desired) || (desired === 'las condes' && o.text.toLowerCase().includes('santiago')));
                    if (opt) {
                        s.value = opt.value;
                        s.dispatchEvent(new Event('change', {bubbles: true}));
                        s.dispatchEvent(new Event('input', {bubbles: true}));
                        cnt++;
                    }
                }
            }
            return cnt;
        }''', perfil)
        completados += sel_count or 0
    except Exception:
        pass

    if completados > 0:
        print(f"🪪 Autocompletados {completados} campos de identidad, contacto, ubicación y/o CV PDF.")

    # 4. Marcar checkboxes de privacidad / términos obligatorios (sin marcar discapacidad ni "sin número")
    for tgt in frames_seguros:
        try:
            chk_cnt = await tgt.evaluate(r'''() => {
                let c = 0;
                const chks = Array.from(document.querySelectorAll('input[type="checkbox"]')).filter(e => e.offsetParent !== null && !e.disabled && !e.checked);
                for (const el of chks) {
                    const idn = ((el.id || '') + ' ' + (el.name || '')).toLowerCase();
                    if (/deficiency|discapacidad|withoutnumber|checkBoxSN/i.test(idn)) continue;
                    const lbl = (document.querySelector('label[for="' + CSS.escape(el.id) + '"]')?.innerText || el.closest('label')?.innerText || el.parentElement?.innerText || '').toLowerCase();
                    if (/privacidad|privacy|términos|terminos|condiciones|accept_privacy|hasacceptedterms|disponibilidad inmediata|dispoin/i.test(idn + ' ' + lbl)) {
                        el.click();
                        c++;
                    }
                }
                return c;
            }''')
            completados += chk_cnt or 0
        except Exception:
            pass

    return completados


async def rellenar_formulario_actual(page, empresa: str, titulo: str) -> list[dict]:
    """Detecta, resuelve con Gemini/Memoria y rellena todas las preguntas visibles en la pantalla o paso actual."""
    if "/candidate/postapply" in (page.url or "").lower():
        return []
    loc_tas = []
    # Si hay un dialog abierto (LinkedIn Easy Apply SDUI), priorizar estrictamente los campos dentro de él
    tiene_dialog = False
    try:
        tiene_dialog = await page.locator("dialog[open], div.jobs-easy-apply-modal").count() > 0
    except Exception:
        pass

    if tiene_dialog:
        todos_candidatos = await page.locator("dialog[open] input[type='text']:visible, dialog[open] input[type='number']:visible, dialog[open] textarea:visible, div.jobs-easy-apply-modal input[type='text']:visible, div.jobs-easy-apply-modal textarea:visible").all()
    else:
        todos_candidatos = await page.locator("form#form-postulation-questions input[type='text']:visible, form#form-postulation-questions textarea:visible, form#formularioPreguntasOferta textarea, div.offcanvas textarea, textarea:visible, input[name*='filter_questions']:visible, input[name*='pregunta']:visible, .form-group input[type='text']:visible, textarea, input[type='text']:visible, input[type='number']:visible").all()
    
    seen_elements = set()
    for el in todos_candidatos:
        try:
            if not await el.is_visible():
                continue
            es_valido = await el.evaluate(r'''el => {
                if (el.dataset.autopostulaFilled === '1' || el.disabled || el.readOnly || el.name === 'supportTextfield' || el.closest('[aria-hidden="true"]')) return false;
                if (el.getAttribute('data-automation-id') === 'beecatcher') return false;
                const ptxt = (el.closest('div, label, fieldset')?.innerText || '').toLowerCase();
                if (ptxt.includes('for robots only') || ptxt.includes('do not enter if you')) return false;
                if (el.type === 'search' || el.type === 'hidden' || el.type === 'submit' || el.type === 'radio' || el.type === 'checkbox' || el.type === 'file' || el.type === 'email' || el.type === 'tel') return false;
                if (el.closest('header') || el.closest('nav') || el.closest('footer') || el.closest('#navbar') || el.closest('.search-box') || el.closest('#search') || el.closest('.filters') || el.closest('.sidebar') || el.closest('.banner')) return false;
                
                // En LinkedIn Easy Apply, si el input ya viene prellenado con teléfono/ciudad/nombre y no es una pregunta, respetarlo
                if (el.closest('dialog[open]') || el.closest('.jobs-easy-apply-modal')) {
                    const v = (el.value || '').trim();
                    const container = el.parentElement && el.parentElement.parentElement && el.parentElement.parentElement.parentElement;
                    const ctxt = container ? container.innerText.toLowerCase() : '';
                    if (v && (ctxt.includes('teléfono') || ctxt.includes('phone') || ctxt.includes('correo') || ctxt.includes('email') || ctxt.includes('ciudad') || ctxt.includes('city'))) {
                        return false;
                    }
                    return true;
                }

                // Si está dentro de un formulario de postulación explícito o su name es filter_questions, ES VÁLIDO DE INMEDIATO
                if (el.closest('#form-postulation-questions') || el.closest('form#formularioPreguntasOferta') || el.closest('.modal') || el.closest('.offcanvas') || el.closest('#formPostular') || el.closest('.kq-container') || (el.name && el.name.startsWith('filter_questions'))) {
                    return true;
                }

                const name = (el.name || "").toLowerCase();
                const id = (el.id || "").toLowerCase();
                const placeholder = (el.placeholder || "").toLowerCase();
                const aria = (el.getAttribute('aria-label') || "").toLowerCase();
                const cls = (el.className || "").toLowerCase();

                const blacklist = ['search', 'buscar', 'busqueda', 'filtro', 'title', 'keywords', 'keyword', 'query', 'cargo', 'location', 'ubicacion', 'selectized', 'form_co', 'form_category', 'form_client', 'email_suscribir', 'newsletter', 'react-select', 'select-footer'];
                for (let b of blacklist) {
                    if (name === b || id === b || id.includes(b) || placeholder.includes(b) || aria.includes(b) || cls.includes(b)) {
                        return false;
                    }
                }
                return true;
            }''')
            if es_valido:
                handle_id = await el.evaluate('el => el.name || el.id || el.placeholder || Math.random()')
                if handle_id not in seen_elements:
                    seen_elements.add(handle_id)
                    loc_tas.append(el)
        except Exception:
            pass
    
    preguntas_para_llm = []
    elementos_info = []

    for idx, ta in enumerate(loc_tas):
        try:
            info_pregunta = await ta.evaluate(r'''el => {
                let num = '';
                let txt = '';
                const group = el.closest('.input-style') || el.closest('div[id^="pregunta_"]') || el.closest('.form-group') || el.closest('.mb-3') || el.closest('.b_input') || el.closest('.b_textarea') || el.closest('.fb-dash-form-element') || el.parentElement;
                if (group) {
                    const l2 = group.querySelector('label.type2');
                    if (l2 && l2.innerText.trim()) {
                        txt = l2.innerText.trim();
                        const l1 = group.querySelector('label.type1');
                        if (l1) num = l1.innerText.trim();
                    }

                    if (!txt) {
                        const headings = Array.from(group.querySelectorAll('h6, h5, h4, h3, legend, label')).filter(h => {
                            const t = h.innerText.toLowerCase();
                            return !t.includes('premium') && !t.includes('caracteres') && !t.includes('automática') && !t.includes('currículum');
                        });
                        if (headings.length > 0) {
                            txt = headings[0].innerText.trim();
                        }
                    }

                    if (!txt) {
                        const lines = group.innerText.split(/\r?\n/).map(s => s.trim()).filter(s => {
                            const sl = s.toLowerCase();
                            return s && !sl.includes('caracteres') && !sl.includes('premium') && !sl.includes('obligatorio') && !sl.includes('automática') && !/^\d+\/\d+$/.test(sl);
                        });
                        if (lines.length > 0) txt = lines[0];
                    }
                }

                // Prioridad: atributos ARIA / label[for] estándar de portales ATS (CSOD, Workday, Greenhouse)
                const lbyId = el.getAttribute('aria-labelledby');
                if (lbyId) {
                    const lbyEl = document.getElementById(lbyId);
                    if (lbyEl && lbyEl.innerText.trim().length > 3) txt = lbyEl.innerText.trim();
                }
                if (!txt && el.id) {
                    const fLbl = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
                    if (fLbl && fLbl.innerText.trim().length > 3) txt = fLbl.innerText.trim();
                }
                if (!txt) {
                    const aLbl = (el.getAttribute('aria-label') || '').trim();
                    if (aLbl.length > 3 && !aLbl.toLowerCase().includes('buscar')) txt = aLbl;
                }

                // Fallback SDUI LinkedIn / ATS: subir hasta 6 ancestros buscando el enunciado de la pregunta
                if (!txt) {
                    let cur = el.parentElement;
                    for (let depth = 0; depth < 6 && cur; depth++) {
                        const lines = (cur.innerText || "").split(/\r?\n/).map(s => s.trim()).filter(s => {
                            const sl = s.toLowerCase();
                            return s.length > 5 && !sl.includes('caracteres') && !/^\d+\/\d+$/.test(sl) && sl !== 'yes' && sl !== 'no';
                        });
                        if (lines.length > 0) {
                            txt = lines[0];
                            break;
                        }
                        cur = cur.parentElement;
                    }
                }
                return { num: num, txt: txt };
            }''')

            q_num = info_pregunta.get("num") or f"Pregunta {idx + 1}"
            q_text = info_pregunta.get("txt") or f"Pregunta {idx + 1}"
            preguntas_para_llm.append({"id": idx, "num": q_num, "pregunta": q_text})
            elementos_info.append({"ta": ta, "num": q_num, "pregunta": q_text})
        except Exception as e:
            print(f"Aviso al extraer pregunta {idx}: {e}")

    respuestas_llm = {}
    if preguntas_para_llm:
        print(f"🤖 Consultando a Gemini 3.5 Flash-Lite / Memoria para {len(preguntas_para_llm)} preguntas...")
        respuestas_llm = resolver_preguntas_con_gemini_en_lote(preguntas_para_llm, empresa=empresa, cargo=titulo)

    qa_list = []
    seen = set()

    for idx, item in enumerate(elementos_info):
        try:
            ta = item["ta"]
            q_num = item["num"]
            q_text = item["pregunta"]

            info_campo = await ta.evaluate(r'''el => {
                const isNumType = el.type === 'number' || el.inputMode === 'numeric' || (el.getAttribute('pattern') && el.getAttribute('pattern').includes('[0-9]'));
                let group = el.closest('.input-style') || el.closest('div[id^="pregunta_"]') || el.closest('.form-group') || el.closest('.mb-3') || el.parentElement;
                if (el.closest('dialog[open]') && el.parentElement && el.parentElement.parentElement && el.parentElement.parentElement.parentElement) {
                    group = el.parentElement.parentElement.parentElement;
                }
                const gText = ((group ? group.innerText : "") + " " + (el.getAttribute('placeholder') || "")).toLowerCase();
                const hasNumNotice = gText.includes('solo acepta números') || gText.includes('solo números') || gText.includes('numérico') || gText.includes('solo dígitos') || gText.includes('de 20 caracteres') || gText.includes('cuántos años');
                const maxLen = parseInt(el.getAttribute('maxlength') || '9999', 10);
                return {
                    esNumero: isNumType || hasNumNotice || (maxLen <= 11),
                    maxLen: maxLen,
                    tagName: el.tagName.toLowerCase()
                };
            }''')

            es_numero = info_campo.get("esNumero", False)
            max_len = info_campo.get("maxLen", 9999)

            resp = respuestas_llm.get(idx) or resolver_pregunta_reclutador(q_text, empresa, tipo_input="number" if es_numero else "textarea")

            # 1. Regla de Oro: Prohibición absoluta de N/A en motivación o preguntas abiertas
            p_lower = q_text.lower()
            if any(m in p_lower for m in ["motiva", "interés", "interes", "por qué", "por que", "expectativa", "desafío", "desafio"]):
                if not resp or resp.strip().upper() in ["N/A", "NO APLICA", "NINGUNA", "NA", "NO", ""] or len(resp.strip()) < 15:
                    resp = f"Me motiva profundamente realizar mi práctica profesional en {empresa} para aportar con proactividad, disciplina y sólidos conocimientos técnicos en infraestructura, redes y continuidad operacional. Busco aprender de los desafíos reales del equipo, resolver incidentes y agregar valor tangible desde el primer día."

            # 1.5 Regla Administrativa Duoc UC: Consolidar convenio de mínimo 360 horas antes del 15 de noviembre
            if not es_numero and any(k in p_lower for k in ["disponibilidad", "comenzar", "iniciar", "fecha", "diciembre", "enero", "febrero", "marzo", "verano", "hasta", "cuándo", "cuando", "partir", "duración", "duracion", "meses"]):
                if "seguro" not in p_lower and "15 de noviembre" not in resp.lower():
                    nota_15_nov = " Para que pueda validarse y extenderse hasta enero, en Duoc UC debemos consolidar formalmente el convenio de práctica de mínimo 360 horas antes del 15 de noviembre (y así evitar pagar un semestre extra solo para la práctica)."
                    if (len(resp) + len(nota_15_nov)) <= max_len:
                        resp = resp.rstrip(".") + "." + nota_15_nov

            # 2. Manejo estricto de campos numéricos (ej. WOM horas=360, LinkedIn años experiencia=4, edad=26)
            if es_numero:
                perf = obtener_perfil_activo()
                horas_default = str(perf.get("condiciones_practica", {}).get("horas_cronologicas_requeridas", "360")) if perf else "360"
                renta_default = str(perf.get("condiciones_legales_y_renta", {}).get("pretension_renta", "0")) if perf else "0"
                tel_default = re.sub(r'\D', '', perf.get("datos_personales", {}).get("telefono", "56948508528")) if perf else "56948508528"

                if "hora" in p_lower:
                    resp = horas_default
                elif any(k in p_lower for k in ["años de experiencia", "experiencia", "office", "excel"]):
                    resp = "4"
                elif any(k in p_lower for k in ["edad", "años tienes"]):
                    resp = "26"
                elif any(k in p_lower for k in ["renta", "pretension", "pretensión", "sueldo", "remuneracion", "remuneración", "monto", "esperada"]):
                    resp = renta_default
                elif any(k in p_lower for k in ["telefono", "teléfono", "celular", "fono"]):
                    resp = tel_default
                else:
                    solo_digitos = re.sub(r'\D', '', resp)
                    resp = solo_digitos if solo_digitos else "0"

            if max_len and max_len < len(resp):
                resp = resp[:max_len]

            await ta.scroll_into_view_if_needed(timeout=3000)
            try:
                await ta.click(timeout=1500)
            except Exception:
                await ta.evaluate("el => el.focus()")
            await ta.fill(resp, timeout=3500, force=True)
            await ta.evaluate('''(el, payload) => {
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                window.__autopostula_qa_map = window.__autopostula_qa_map || {};
                window.__autopostula_qa_map[payload.qid] = {
                    pregunta: payload.pregunta,
                    bot_answer: payload.resp,
                    current_answer: el.value
                };
                if (!el.dataset.apListening) {
                    el.dataset.apListening = '1';
                    const syncEdit = () => {
                        if (window.__autopostula_qa_map && window.__autopostula_qa_map[payload.qid]) {
                            window.__autopostula_qa_map[payload.qid].current_answer = el.value;
                        }
                    };
                    el.addEventListener('input', syncEdit);
                    el.addEventListener('change', syncEdit);
                }
            }''', {"qid": str(idx), "pregunta": q_text, "resp": resp, "esNumero": es_numero})

            if q_text not in seen:
                seen.add(q_text)
                qa_list.append({
                    "num": q_num,
                    "pregunta": q_text,
                    "respuesta": resp
                })
            await asyncio.sleep(0.15)
        except Exception as e:
            print(f"Aviso al llenar campo {idx}: {e}")

    # Marcar TODOS los grupos de radios y selects con coincidencia semántica de perfil y sí/no
    qa_radios_selects = await page.evaluate(r'''() => {
        const answered = [];
        const cleanLine = (s) => (s || '').replace(/\s+/g, ' ').trim().split(/\r?\n/)[0].slice(0, 140);
        const radioNames = new Set();
        document.querySelectorAll('input[type="radio"]').forEach(r => {
            if (r.name) radioNames.add(r.name);
        });

        for (let name of radioNames) {
            const inputs = Array.from(document.querySelectorAll(`input[name="${name}"]`));
            if (inputs.length === 0) continue;
            if (inputs.some(inp => inp.checked)) continue;

            // Extraer texto contextual completo de la pregunta (hasta 4 niveles arriba)
            let container = inputs[0].closest('.form-group') || inputs[0].closest('div[class*="question"]') || inputs[0].closest('fieldset') || inputs[0].closest('[role="radiogroup"]');
            let qtxt = container ? container.innerText.toLowerCase() : "";
            let cur = container || inputs[0].parentElement;
            for (let k = 0; k < 4 && cur; k++) {
                const t = (cur.innerText || "").toLowerCase();
                if (t.includes('?') || t.includes('carrera') || t.includes('estudio') || t.includes('inglés') || t.includes('ingles') || t.includes('ley 21.015') || t.includes('vacaciones')) {
                    qtxt = t.slice(0, 500);
                    break;
                }
                cur = cur.parentElement;
            }

            const getOptText = (inp) => {
                const lbl = inp.closest('label') || (inp.id ? document.querySelector(`label[for="${CSS.escape(inp.id)}"]`) : null) || inp.parentElement;
                return { lbl, txt: (lbl ? lbl.innerText : (inp.value || "")).trim() };
            };

            const opts = inputs.map(inp => ({ inp, ...getOptText(inp) }));
            let chosen = null;

            const isNoExpected = qtxt.includes("inclusi") || qtxt.includes("ley 21.015") || qtxt.includes("ajuste razonable") || qtxt.includes("discapacidad") || qtxt.includes("vacaciones programadas") || qtxt.includes("6 meses") || qtxt.includes("seis meses") || qtxt.includes("mínimo de 6") || qtxt.includes("minimo de 6") || qtxt.includes("trabajado alguna vez") || qtxt.includes("trabajado antes") || qtxt.includes("proyecto con") || qtxt.includes("filial") || qtxt.includes("previousworker");

            if (qtxt.includes("carrera") || qtxt.includes("título") || qtxt.includes("profesión")) {
                const norm = (s) => s.toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '');
                const prefs = ["infraestructura", "plataformas tecnol", "otras", "otra"];
                for (const p of prefs) {
                    chosen = opts.find(o => norm(o.txt).includes(p));
                    if (chosen) break;
                }
            } else if (qtxt.includes("casa de estudio") || qtxt.includes("universidad") || qtxt.includes("institución") || qtxt.includes("institucion")) {
                chosen = opts.find(o => o.txt.toLowerCase().includes("duoc"));
            } else if (qtxt.includes("inglés") || qtxt.includes("ingles")) {
                chosen = opts.find(o => o.txt.toLowerCase().includes("intermedio"));
            } else if (
                qtxt.includes("sexo") || qtxt.includes("género") || qtxt.includes("genero") ||
                opts.some(o => /\b(hombre|masculino|male|varón|varon)\b/i.test(o.txt))
            ) {
                chosen = opts.find(o => /\b(hombre|masculino|male|varón|varon)\b/i.test(o.txt));
            } else if (qtxt.includes("estado civil") || opts.some(o => /solter/i.test(o.txt))) {
                chosen = opts.find(o => /solter/i.test(o.txt));
            }

            if (!chosen) {
                for (const o of opts) {
                    const u = o.txt.toUpperCase();
                    if (isNoExpected) {
                        if (u === "NO" || u.startsWith("NO ")) { chosen = o; break; }
                    } else {
                        if (u === "SI" || u === "SÍ" || u === "YES" || u.startsWith("SI ") || u.startsWith("SÍ ") || u.startsWith("YES ")) {
                            chosen = o; break;
                        }
                    }
                }
            }

            if (!chosen && !isNoExpected && opts.length <= 3) {
                chosen = opts[0];
            }

            if (chosen) {
                chosen.inp.checked = true;
                if (chosen.lbl) chosen.lbl.click();
                chosen.inp.click();
                chosen.inp.dispatchEvent(new Event('change', { bubbles: true }));
                chosen.inp.dispatchEvent(new Event('input', { bubbles: true }));
                const pq = cleanLine(container ? container.innerText : qtxt);
                if (pq && chosen.txt) answered.push({ pregunta: pq, respuesta: chosen.txt });
            }
        }

        const allContainers = Array.from(document.querySelectorAll('.cb-container, label.radio, .radio-label, .btn-check')).filter(l => {
            const t = l.innerText.trim().toUpperCase();
            return t === 'SI' || t === 'SÍ' || t === 'YES';
        });
        for (let l of allContainers) {
            const inp = l.querySelector('input[type="radio"]');
            if (inp && !inp.checked) {
                const siblings = Array.from(document.querySelectorAll(`input[name="${inp.name}"]`));
                if (!siblings.some(s => s.checked)) {
                    inp.checked = true;
                    l.click();
                    inp.dispatchEvent(new Event('change', { bubbles: true }));
                }
            }
        }

        const selects = Array.from(document.querySelectorAll('form select, .input-style select, dialog[open] select, select:not([name*="search"]):not([name="idioma"])'));
        for (let sel of selects) {
            if (!sel.offsetParent) continue;
            const container = sel.closest('.input-style') || sel.closest('.form-group') || sel.parentElement;
            const txt = ((sel.id || '') + ' ' + (sel.name || '') + ' ' + (container ? container.innerText : '')).toLowerCase();
            const options = Array.from(sel.querySelectorAll('option'));
            const optsTxt = options.map(o => (o.innerText || o.textContent || '').trim().toLowerCase());

            // Sexo / Género SIEMPRE Hombre / Masculino (aunque el sitio preseleccione Mujer)
            const isSexo = /\b(sexo|sex|género|genero|gender|idsex)\b/.test(txt) || (
                optsTxt.some(t => /\b(hombre|masculino|male|varón|varon)\b/.test(t)) &&
                optsTxt.some(t => /\b(mujer|femenino|female)\b/.test(t))
            );
            if (isSexo) {
                const optH = options.find(o => /\b(hombre|masculino|male|varón|varon)\b/.test((o.innerText || o.textContent || '').trim().toLowerCase()));
                if (optH && sel.value !== optH.value) {
                    sel.value = optH.value;
                    sel.dispatchEvent(new Event('change', { bubbles: true }));
                    sel.dispatchEvent(new Event('input', { bubbles: true }));
                }
                continue;
            }

            if (sel.value && sel.value !== "" && !sel.value.toLowerCase().includes("select")) continue;
            if (/\b(deficiency|discapacidad|marital|children|hijos|worktype)\b/.test(txt)) continue;

            const isNo = txt.includes("inclusión") || txt.includes("discapacidad") || txt.includes("ajuste razonable");
            let targetOption = null;

            for (let opt of options) {
                const oText = (opt.innerText || opt.textContent || "").trim().toUpperCase();
                if (!opt.value || opt.value === "" || oText.includes("SELECCION")) continue;

                if (isNo) {
                    if (oText === "NO" || oText.startsWith("NO")) {
                        targetOption = opt;
                        break;
                    }
                } else {
                    if (oText === "SÍ" || oText === "SI" || oText === "YES" || oText.startsWith("SÍ") || oText.startsWith("SI") || oText.startsWith("YES")) {
                        targetOption = opt;
                        break;
                    }
                }
            }

            if (!targetOption && options.length > 1 && options.length <= 4) {
                for (let opt of options) {
                    if (opt.value && opt.value !== "") {
                        targetOption = opt;
                        break;
                    }
                }
            }

            if (targetOption) {
                sel.value = targetOption.value;
                sel.dispatchEvent(new Event('change', { bubbles: true }));
                sel.dispatchEvent(new Event('input', { bubbles: true }));
                const sq = cleanLine(container ? container.innerText : txt);
                const sa = (targetOption.innerText || targetOption.textContent || '').trim();
                if (sq && sa) answered.push({ pregunta: sq, respuesta: sa });
            }
        }
        return answered;
    }''')

    for qa_rs in (qa_radios_selects or []):
        qp = (qa_rs.get("pregunta") or "").strip()
        qr = (qa_rs.get("respuesta") or "").strip()
        if qp and qr and qp not in seen:
            seen.add(qp)
            qa_list.append({"num": f"Pregunta {len(qa_list) + 1}", "pregunta": qp, "respuesta": qr})

    return qa_list


async def _rellenar_paso_workday(page):
    """Completa controles especiales de Workday (React spinbuttons, comboboxes, listboxes y datos reales) en pasos 2..5."""
    perfil = _cargar_perfil_usuario()

    async def _type_spin(loc, val: str):
        await loc.evaluate("el => { el.scrollIntoView({block:'center'}); el.focus(); el.select?.(); }")
        await asyncio.sleep(0.1)
        await page.keyboard.press("Control+a")
        await page.keyboard.type(str(val), delay=35)
        await page.keyboard.press("Tab")
        await asyncio.sleep(0.15)

    # A. Paso Mi información: corregir nombres all-caps erróneos del parser CV y asegurar teléfono sin prefijo duplicado
    try:
        fn = page.locator("#name--legalName--firstName, input[name='legalName--firstName']").first
        if await fn.count() > 0 and await fn.is_visible():
            cur_fn = (await fn.input_value() or "").strip()
            if not cur_fn or cur_fn.upper() in ["MAESTRO", "INTEGRAL", "CV", "CURRICULUM"] or (cur_fn.isupper() and len(cur_fn) > 3):
                await fn.fill(perfil["primer_nombre"], force=True)
        ln = page.locator("#name--legalName--lastName, input[name='legalName--lastName']").first
        if await ln.count() > 0 and await ln.is_visible():
            cur_ln = (await ln.input_value() or "").strip()
            if not cur_ln or cur_ln.upper() in ["MAESTRO", "INTEGRAL", "CV", "CURRICULUM"] or (cur_ln.isupper() and len(cur_ln) > 3):
                await ln.fill(perfil["primer_apellido"], force=True)
        sln = page.locator("#name--legalName--secondaryLastName, input[name='legalName--secondaryLastName']").first
        if await sln.count() > 0 and await sln.is_visible():
            cur_sln = (await sln.input_value() or "").strip()
            if not cur_sln and perfil.get("segundo_apellido"):
                await sln.fill(perfil["segundo_apellido"], force=True)

        comuna_inp = page.locator("#address--regionSubdivision1").first
        if await comuna_inp.count() > 0 and await comuna_inp.is_visible():
            cur_com = (await comuna_inp.input_value() or "").strip()
            if not cur_com or len(cur_com) > 35:
                await comuna_inp.scroll_into_view_if_needed()
                await comuna_inp.fill("Las Condes", force=True)

        ext_inp = page.locator("#phoneNumber--extension").first
        if await ext_inp.count() > 0 and await ext_inp.is_visible():
            cur_ext = (await ext_inp.input_value() or "").strip()
            if cur_ext and (len(cur_ext) > 6 or not cur_ext.isdigit()):
                await ext_inp.scroll_into_view_if_needed()
                await ext_inp.fill("", force=True)

        ph_inp = page.locator("#phoneNumber--phoneNumber, input[name='phoneNumber']").first
        if await ph_inp.count() > 0 and await ph_inp.is_visible():
            has_56 = await page.evaluate("() => document.body.innerText.includes('Chile (+56)')")
            if has_56:
                cur_ph = (await ph_inp.input_value() or "").strip()
                if cur_ph.startswith("+56") or not cur_ph or len(re.sub(r'\D', '', cur_ph)) != 9:
                    await ph_inp.scroll_into_view_if_needed()
                    await ph_inp.fill("9 4850 8528")
    except Exception:
        pass

    # B. Paso Mi información: radio "¿Ha trabajado alguna vez..." -> siempre No
    try:
        fs_no = page.locator("fieldset:has-text('trabajado') label:text-is('No'), fieldset:has-text('filial') label:text-is('No'), label[for='p00nb']").first
        if await fs_no.count() > 0 and await fs_no.is_visible():
            inp_no = page.locator("fieldset:has-text('trabajado') input[value='false'], input[name='candidateIsPreviousWorker'][value='false'], #p00nb").first
            if await inp_no.count() > 0 and not await inp_no.is_checked():
                await fs_no.scroll_into_view_if_needed()
                await fs_no.click()
                await asyncio.sleep(0.3)
                if not await inp_no.is_checked():
                    await inp_no.check(force=True)
    except Exception:
        pass

    # C. Paso Mi información: "¿Cómo nos ha conocido?" (#source--source)
    try:
        src = page.locator("#source--source").first
        if await src.count() > 0 and await src.is_visible():
            ya_sel = await src.evaluate("el => !!el.closest('[data-automation-id]')?.querySelector('li[data-automation-id=\"menuItem\"]')")
            if not ya_sel:
                await src.scroll_into_view_if_needed()
                await src.fill("", force=True)
                await src.click(force=True)
                await asyncio.sleep(0.9)
                opt_src = page.locator("[role='option']:has-text('LinkedIn'), [role='option']:has-text('Red social'), [role='option']:has-text('Redes sociales'), [role='option']:has-text('Corporate Citizenship'), [data-automation-id='promptOption']").first
                if await opt_src.count() > 0 and await opt_src.is_visible():
                    await opt_src.click(force=True)
                    await asyncio.sleep(0.8)
                    sub_src = page.locator("[role='option']:has-text('LinkedIn'), [data-automation-id='promptOption']").first
                    if await sub_src.count() > 0 and await sub_src.is_visible():
                        await sub_src.click(force=True)
                        await asyncio.sleep(0.5)
    except Exception:
        pass

    # D. Paso Mi experiencia: eliminar bloques erróneos del parser y llenar fechas reales en spinbuttons + educación Duoc UC
    try:
        # Eliminar bloques basura del parser CV (ej. "Arquitectura integral", "ASA")
        titles = await page.locator("input[id^='workExperience-'][id$='--jobTitle']").all()
        comps = await page.locator("input[id^='workExperience-'][id$='--companyName']").all()
        for c_loc in reversed(comps):
            try:
                c_val = (await c_loc.input_value() or "").strip().lower()
                if any(b in c_val for b in ["arquitectura integral", "asa", "maestro"]):
                    del_b = c_loc.locator("xpath=ancestor::div[contains(@aria-labelledby, 'Experiencia')]//button[contains(., 'Eliminar')]").first
                    if await del_b.count() > 0 and await del_b.is_visible():
                        await del_b.evaluate("el => el.scrollIntoView({block:'center'})")
                        await del_b.click(force=True)
                        await asyncio.sleep(0.5)
            except Exception:
                pass

        # Fechas en experiencias restantes (BDO Chile 03/2025 - 07/2025 por defecto)
        sm_locs = await page.locator("input[id^='workExperience-'][id$='--startDate-dateSectionMonth-input']").all()
        sy_locs = await page.locator("input[id^='workExperience-'][id$='--startDate-dateSectionYear-input']").all()
        em_locs = await page.locator("input[id^='workExperience-'][id$='--endDate-dateSectionMonth-input']").all()
        ey_locs = await page.locator("input[id^='workExperience-'][id$='--endDate-dateSectionYear-input']").all()
        for idx, sm in enumerate(sm_locs):
            if not (await sm.input_value() or "").strip():
                await _type_spin(sm, "03")
                if idx < len(sy_locs) and not (await sy_locs[idx].input_value() or "").strip():
                    await _type_spin(sy_locs[idx], "2025")
            if idx < len(em_locs) and not (await em_locs[idx].input_value() or "").strip():
                await _type_spin(em_locs[idx], "07")
                if idx < len(ey_locs) and not (await ey_locs[idx].input_value() or "").strip():
                    await _type_spin(ey_locs[idx], "2025")

        # Educación: Duoc UC, Titulación y años 2023-2026
        sch = page.locator("input[id^='education-'][id$='--school']").first
        if await sch.count() > 0 and await sch.is_visible():
            ya_sch = await sch.evaluate("el => !!el.closest('[data-automation-id]')?.querySelector('li[data-automation-id=\"menuItem\"]')")
            if not ya_sch and not (await sch.input_value() or "").strip():
                await sch.evaluate("el => { el.scrollIntoView({block:'center'}); el.focus(); }")
                await page.keyboard.type("Duoc", delay=40)
                await page.keyboard.press("Enter")
                await asyncio.sleep(1.2)
                opt_duoc = page.locator("[role='option'], [data-automation-id='promptOption']").filter(has_text="Duoc").first
                if await opt_duoc.count() > 0 and await opt_duoc.is_visible():
                    await opt_duoc.click(force=True)
                    await asyncio.sleep(0.5)

        deg = page.locator("button[id^='education-'][id$='--degree']").first
        if await deg.count() > 0 and await deg.is_visible():
            if "seleccione" in (await deg.inner_text() or "").lower():
                await deg.evaluate("el => el.scrollIntoView({block:'center'})")
                await deg.click(force=True)
                await asyncio.sleep(0.8)
                for kw in ["Ingenier", "Licenciatura", "Universitario", "Bachelor", "Técnico", "Otro"]:
                    cand = page.locator("ul[role='listbox'] li[role='option'], div[role='listbox'] [role='option']").filter(has_text=kw).first
                    if await cand.count() > 0 and await cand.is_visible():
                        await cand.click(force=True)
                        await asyncio.sleep(0.4)
                        break

        fos = page.locator("input[id^='education-'][id$='--fieldOfStudy']").first
        if await fos.count() > 0 and await fos.is_visible():
            ya_fos = await fos.evaluate("el => !!el.closest('[data-automation-id]')?.querySelector('li[data-automation-id=\"menuItem\"]')")
            if not ya_fos and not (await fos.input_value() or "").strip():
                await fos.evaluate("el => { el.scrollIntoView({block:'center'}); el.focus(); }")
                await page.keyboard.type("Informática", delay=40)
                await page.keyboard.press("Enter")
                await asyncio.sleep(1.0)
                opt_fos = page.locator("[role='option']:has-text('Inform'), [data-automation-id='promptOption']").first
                if await opt_fos.count() > 0 and await opt_fos.is_visible():
                    await opt_fos.click(force=True)
                    await asyncio.sleep(0.4)

        edu_fy = page.locator("input[id^='education-'][id$='--firstYearAttended-dateSectionYear-input']").first
        if await edu_fy.count() > 0 and not (await edu_fy.input_value() or "").strip():
            await _type_spin(edu_fy, "2023")
        edu_ly = page.locator("input[id^='education-'][id$='--lastYearAttended-dateSectionYear-input']").first
        if await edu_ly.count() > 0 and not (await edu_ly.input_value() or "").strip():
            await _type_spin(edu_ly, "2026")

        ruta_cv = perfil.get("ruta_cv") or CV_MASTER_PDF
        if ruta_cv and os.path.exists(ruta_cv):
            f_wd = page.locator("input[data-automation-id='file-upload-input-ref'][type='file']").first
            if await f_wd.count() > 0:
                ya_cv = await page.evaluate("() => document.body.innerText.includes('.pdf') || document.body.innerText.includes('1_CV_')")
                if not ya_cv:
                    await f_wd.set_input_files(ruta_cv)
                    await asyncio.sleep(1.5)
    except Exception:
        pass

    # E. Paso Preguntas de solicitud: botones listbox de primaryQuestionnaire
    try:
        q_items = await page.evaluate("""() => [...document.querySelectorAll("button[aria-haspopup=listbox][id^=primaryQuestionnaire]")].map(b => {
            let p = b.parentElement;
            let qtxt = "";
            for (let k=0; k<5 && p; k++) {
                const t = (p.innerText || "").trim();
                if (t.includes("?")) { qtxt = t.split("\\n")[0]; break; }
                p = p.parentElement;
            }
            return {id: b.id, cur: b.innerText.trim(), qtxt};
        })""")
        for q in q_items:
            if "seleccione" not in q["cur"].lower():
                continue
            qid = q["id"]
            qt = q["qtxt"].lower()
            btn = page.locator("[id=\"" + qid + "\"]").first
            await btn.evaluate("el => el.scrollIntoView({block:'center'})")
            await btn.click()
            await asyncio.sleep(0.6)
            opts = await page.evaluate("""() => {
                const lists = [...document.querySelectorAll("ul[role=listbox], div[role=listbox]")].filter(l => l.offsetParent !== null);
                const last = lists[lists.length - 1];
                return last ? [...last.querySelectorAll("[role=option], li")].map(o => (o.innerText||"").trim()).filter(Boolean) : [];
            }""")
            es_no = any(k in qt for k in ["proyecto con accenture", "24 meses", "trabajado antes", "trabajado alguna vez", "filial", "ajuste razonable", "discapacidad", "inclusi"])
            if es_no:
                pick = next((o for o in opts if o.lower() == "no"), opts[-1] if opts else None)
            else:
                pick = next((o for o in opts if o.lower() in ["sí", "si", "yes"] or any(w in o.lower() for w in ["acepto", "consiento", "autorizo", "implicando inteligencia artificial"])), None)
                if not pick and len(opts) > 1:
                    pick = opts[1]
            if pick:
                await page.locator("ul[role='listbox'] li[role='option'], div[role='listbox'] [role='option']").filter(has_text=pick).last.click()
                await asyncio.sleep(0.35)
    except Exception:
        pass

    # F. Paso Revelación voluntaria: género = Hombre y checkbox Acepto términos
    try:
        gbtn = page.locator("#personalInfoPerson--gender, button[aria-haspopup='listbox'][id$='--gender']").first
        if await gbtn.count() > 0 and await gbtn.is_visible() and "seleccione" in (await gbtn.inner_text() or "").lower():
            await gbtn.evaluate("el => el.scrollIntoView({block:'center'})")
            await gbtn.click()
            await asyncio.sleep(0.7)
            for gk in ["Hombre", "Masculino", "Male", "Varón"]:
                gopt = page.locator("ul[role='listbox'] li[role='option'], div[role='listbox'] [role='option']").filter(has_text=gk).last
                if await gopt.count() > 0 and await gopt.is_visible():
                    await gopt.click()
                    await asyncio.sleep(0.35)
                    break
        tchk = page.locator("#termsAndConditions--acceptTermsAndAgreements").first
        if await tchk.count() > 0 and not await tchk.is_checked():
            await tchk.evaluate("el => el.scrollIntoView({block:'center'})")
            await tchk.check(force=True)
    except Exception:
        pass


async def avanzar_wizard_linkedin_si_existe(page, empresa: str, titulo: str) -> list[dict]:
    """Completa formularios de 1 o múltiples pasos en LinkedIn Easy Apply (<dialog open> o role=dialog) y portales ATS externos (CSOD, AIRA, Buk, Workday, Pandape, etc.), rellenando datos personales, CV PDF y preguntas."""
    qa_acumulada = []
    sel_dlg = "dialog[open], div.jobs-easy-apply-modal, div[data-test-modal], div.artdeco-modal, div[role='dialog']:has-text('Enviar solicitud'), div[role='dialog']:has-text('Siguiente'), div[role='dialog']:has-text('Revisar')"
    try:
        tiene_dlg = await page.locator(sel_dlg).count() > 0
    except Exception:
        tiene_dlg = False

    url_low = page.url.lower()
    es_portal_nativo = any(dom in url_low for dom in ["firstjob.me", "trabajando.cl", "laborum.cl", "duoclaboral.cl", "computrabajo", "chiletrabajos", "serviciocivil.cl"]) and ("pandape" not in url_low)

    if not tiene_dlg and (es_portal_nativo or "linkedin.com" in url_low):
        if not es_portal_nativo:
            return qa_acumulada
        await autocompletar_datos_personales_y_cv(page)
        return await rellenar_formulario_actual(page, empresa, titulo)

    modo = "LinkedIn Easy Apply" if tiene_dlg else "ATS / Formulario Externo"
    print(f"🔗 Detectado asistente multipaso ({modo})...")

    for paso in range(1, 8):
        if "myworkdayjobs.com" in url_low:
            await _rellenar_paso_workday(page)
        else:
            await autocompletar_datos_personales_y_cv(page)
            qa_step = await rellenar_formulario_actual(page, empresa, titulo)
            qa_acumulada.extend(qa_step)
        await asyncio.sleep(0.6)

        if tiene_dlg:
            sel_enviar = "dialog[open] button:has-text('Enviar solicitud'), dialog[open] button:has-text('Submit application'), div[role='dialog'] button:has-text('Enviar solicitud'), .jobs-easy-apply-modal button:has-text('Enviar solicitud')"
            sel_avanzar = "dialog[open] button:has-text('Revisar'), dialog[open] button:has-text('Siguiente'), dialog[open] button:has-text('Next'), dialog[open] button:has-text('Review'), div[role='dialog'] button:has-text('Revisar'), div[role='dialog'] button:has-text('Siguiente'), .jobs-easy-apply-modal button:has-text('Siguiente'), .jobs-easy-apply-modal button:has-text('Revisar')"
        else:
            sel_enviar = "button:has-text('Enviar solicitud'), button:has-text('Enviar postulación'), button:has-text('Finalizar postulación'), button:has-text('Submit application'), button:text-is('Enviar'), input[type='submit'][value*='Enviar' i], input[type='submit'][name='commit']"
            sel_avanzar = "button:has-text('Siguiente'), button:has-text('Guardar y continuar'), button:has-text('Continuar'), input[type='submit'][value*='Continuar' i], button:has-text('Next'), button:has-text('Continue'), button:has-text('Revisar')"

        # ¿Ya estamos en el paso final?
        try:
            locs_env = await page.locator(sel_enviar).all()
            encontro_env = False
            for loc_e in locs_env:
                if await loc_e.is_visible():
                    es_ajeno = await loc_e.evaluate('''el => {
                        if (el.getAttribute('aria-haspopup') === 'listbox') return true;
                        if (el.closest('header, nav, footer, #autopostula-root-container, #supportModal, .modal:not(.show), [aria-hidden="true"]')) return true;
                        const frm = el.closest('form');
                        return !!(frm && frm.querySelector('#supportTextfield, [name*="support" i], [id*="support" i]'));
                    }''')
                    if not es_ajeno:
                        print(f"✅ Asistente ({modo}) llegó al paso final de envío en paso {paso}.")
                        encontro_env = True
                        break
            if encontro_env:
                break
        except Exception:
            pass

        # Si hay botón de siguiente paso y aún no es el envío final, avanzarlo
        try:
            locs_av = await page.locator(sel_avanzar).all()
            btn_avanzar = None
            for cand_av in locs_av:
                if await cand_av.is_visible():
                    es_ajeno = await cand_av.evaluate("el => el.getAttribute('aria-haspopup') === 'listbox' || !!(el.closest('header, nav, footer, #autopostula-root-container, #supportModal, .modal:not(.show), [aria-hidden=\"true\"]'))")
                    if not es_ajeno:
                        btn_avanzar = cand_av
                        break
            if btn_avanzar is not None:
                lbl_btn = (await btn_avanzar.inner_text() or await btn_avanzar.get_attribute("value") or "").strip()
                print(f"➡️  Paso {paso} ({modo}) completado. Pulsando '{lbl_btn}'...")
                url_antes = page.url
                txt_antes = (await page.inner_text("body") or "")[:1200]
                await btn_avanzar.scroll_into_view_if_needed()
                await btn_avanzar.click(timeout=5000)
                await asyncio.sleep(2.5)
                # Solo detener si la pantalla NO cambió tras el clic Y hay errores visibles reales de validación
                txt_despues = (await page.inner_text("body") or "")[:1200]
                misma_pantalla = (page.url == url_antes and txt_antes == txt_despues)
                err_visibles = await page.locator("[data-automation-id='errorMessage']:visible, [id^='error'][id*='--']:visible, [aria-invalid='true']:visible, .field-validation-error:visible, .invalid-feedback:visible, .artdeco-inline-feedback--error:visible, [role='alert']:visible").count()
                if misma_pantalla and err_visibles > 0:
                    print(f"⚠️  Formulario ({modo}) detuvo el avance por un campo obligatorio pendiente; pasando a revisión en pantalla.")
                    break
            else:
                break
        except Exception as e:
            print(f"Aviso avanzando paso {paso} ({modo}): {e}")
            break

    return qa_acumulada




async def leer_y_validar_descripcion_oferta(page, oferta: dict) -> tuple[bool, str, str]:
    """Expande ('Ver más' / '… más' / expandable-text-button), lee y guarda la descripción completa ('Acerca del empleo') y valida si la oferta cumple el perfil del candidato."""
    of_id = oferta.get("id")
    empresa = oferta.get("empresa", "la empresa")
    titulo = oferta.get("titulo", "")
    ubicacion = oferta.get("ubicacion", "")

    # 1. Hacer clic en el botón '… más' / 'Ver más' / 'See more' de la descripción
    expand_selectors = [
        "[data-testid='expandable-text-button']",
        "button.jobs-description__footer-button",
        "button[aria-label*='ver más']",
        "button[aria-label*='Ver más']",
        "button:has-text('… más')",
        "button:has-text('... más')",
        "button:has-text('Ver más')",
        "button:has-text('See more')",
    ]
    for sel in expand_selectors:
        try:
            locs = await page.locator(sel).all()
            for loc in locs:
                if await loc.is_visible():
                    valido_btn = await loc.evaluate('''el => {
                        if (el.closest('header') || el.closest('nav') || el.closest('#autopostula-root-container') || el.closest('.job-card-container') || el.closest('.jobs-similar-jobs')) return false;
                        const t = (el.innerText || '').toLowerCase();
                        return !t.includes('empleos como este');
                    }''')
                    if valido_btn:
                        await loc.scroll_into_view_if_needed()
                        await loc.click(timeout=2500)
                        await asyncio.sleep(0.6)
                        break
        except Exception:
            pass

    # 2. Extraer el texto completo de 'Acerca del empleo' o contenedor de descripción del portal
    desc_extraida = ""
    try:
        desc_extraida = await page.evaluate(r'''() => {
            const h = Array.from(document.querySelectorAll('h1, h2, h3, h4, span, p')).find(e => {
                const t = (e.innerText || '').trim().toLowerCase();
                return t === 'acerca del empleo' || t === 'about the job' || t === 'descripción del empleo' || t === 'descripción de la oferta' || t === 'funciones del cargo';
            });
            if (h) {
                let sec = h;
                for (let k = 0; k < 6 && sec; k++) {
                    const tx = (sec.innerText || '').trim();
                    if (tx.length > 220 && tx.length < 14000) return tx;
                    sec = sec.parentElement;
                }
            }
            const known = document.querySelector('.jobs-description-content__text, #job-details, .job-description, #oferta-descripcion, .descripcion-oferta');
            if (known && (known.innerText || '').trim().length > 100) {
                return known.innerText.trim();
            }
            return ((document.querySelector('main') || document.body).innerText || '').trim().slice(0, 6000);
        }''') or ""
    except Exception:
        desc_extraida = ""

    desc_extraida = re.sub(r'\n{3,}', '\n\n', desc_extraida).strip()

    # Guardar descripción completa en SQLite si aportó más detalle
    if of_id and len(desc_extraida) > len(oferta.get("descripcion") or ""):
        oferta["descripcion"] = desc_extraida
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.execute("UPDATE postulaciones SET descripcion = ? WHERE id = ?", (desc_extraida[:8000], of_id))
            conn.commit()
            conn.close()
        except Exception:
            pass

    desc_low = f"{titulo}\n{desc_extraida}".lower()

    # 3. Validar restricciones excluyentes dentro de la descripción expandida
    # A) Duración mínima incompatible (>= 6 meses obligatorios sin flexibilidad de 3 meses)
    if re.search(r'(m[ií]nimo|duraci[oó]n\s+m[ií]nima|disponibilidad\s+m[ií]nima|excluyente)\s*(de\s*)?(6|seis|6\+|12)\s*meses', desc_low) and not re.search(r'(3|tres|cuatro|4)\s*(a|y|-)\s*6\s*meses', desc_low):
        motivo = "Exige duración mínima obligatoria de 6 meses o más"
        print(f"⏭️  Descripción descartada ({empresa}): {motivo}")
        hablar(f"Leí Acerca del empleo de {empresa} y la descarto porque {motivo}.")
        return False, desc_extraida, motivo

    # B) Ubicación fuera de Región Metropolitana / no remota
    if re.search(r'(presencial en|sede en|ubicaci[oó]n[:\s]+|residir en)\s+(valpara[ií]so|vi[nñ]a del mar|concepci[oó]n|temuco|antofagasta|puerto montt|la serena|coquimbo|rancagua|talca|arica|iquique)', desc_low):
        if not any(rm in desc_low for rm in ["santiago", "metropolitana", "las condes", "providencia", "remoto", "híbrido", "hibrido"]):
            motivo = "Presencial fuera de la Región Metropolitana"
            print(f"⏭️  Descripción descartada ({empresa}): {motivo}")
            hablar(f"Leí Acerca del empleo de {empresa} y la descarto porque es {motivo}.")
            return False, desc_extraida, motivo

    # C) Detectar pilares técnicos compatibles
    pilares = []
    if any(k in desc_low for k in ["ciberseguridad", "seguridad", "vulnerabilid", "pentest", "soc", "riesgo", "auditoría", "auditoria"]):
        pilares.append("Seguridad y vulnerabilidades")
    if any(k in desc_low for k in ["agente", "agentes", "inteligencia artificial", "prompt", "llm", "rag", "ia "]):
        pilares.append("Agentes de IA")
    if any(k in desc_low for k in ["docker", "kubernetes", "contenedores", "github", "devops", "cloud", "aws", "azure", "gcp"]):
        pilares.append("Contenedores, Cloud y GitHub")
    if any(k in desc_low for k in ["base de datos", "bases de datos", "sql", "postgres", "mysql", "sqlite"]):
        pilares.append("Bases de datos")
    if any(k in desc_low for k in ["infraestructura", "redes", "conectividad", "telecomunicaciones", "linux", "servidores", "plataformas"]):
        pilares.append("Infraestructura, redes y sistemas")
    if any(k in desc_low for k in ["soporte", "mesa de ayuda", "mesa de servicio", "hardware", "ti "]):
        pilares.append("Soporte TI")

    if not pilares and re.search(r'(desarrollo web|frontend|react|angular|vue|php|wordpress|marketing|ventas|contabilidad|comercial)', desc_low):
        motivo = "Enfocada solo en desarrollo web/área no alineada sin seguridad, infra, bases de datos, contenedores ni IA"
        print(f"⏭️  Descripción descartada ({empresa}): {motivo}")
        hablar(f"Leí la descripción de {empresa} y la descarto por estar fuera de tu perfil.")
        return False, desc_extraida, motivo

    resumen_pilares = ", ".join(pilares[:3]) if pilares else "TI / Sistemas"
    GLOBAL_BOT_CONTROL["desc_actual"] = desc_extraida
    GLOBAL_BOT_CONTROL["empresa_actual"] = empresa
    print(f"📖 Descripción leída ({len(desc_extraida)} chars) | Válida ✅ | Pilares: {resumen_pilares}")
    if GLOBAL_BOT_CONTROL.get("leer_descripcion") and desc_extraida:
        frag = re.sub(r'\s+', ' ', desc_extraida)[:700]
        hablar(f"Descripción de {empresa}: {frag}")
    else:
        hablar(f"Leí la descripción completa de {empresa}: oferta válida orientada a {resumen_pilares}.")
    return True, desc_extraida, f"Válida ({resumen_pilares})"


async def procesar_oferta_interactiva(page, oferta: dict, segundos_revision: int = 30, tabs_oferta: list | None = None) -> tuple[str, str]:
    if tabs_oferta is None:
        tabs_oferta = [page]
    elif page not in tabs_oferta:
        tabs_oferta.append(page)
    url = oferta.get("url_oferta", "")
    titulo = oferta.get("titulo", "Práctica")
    empresa = oferta.get("empresa", "Empresa")
    portal = oferta.get("portal", "Portal")
    ubicacion = oferta.get("ubicacion", "Santiago, RM")

    print(f"\n========================================================")
    print(f"🎯 [{portal}] {empresa} — {titulo}")
    print(f"📍 Ubicación: {ubicacion}")
    print(f"🔗 URL: {url}")
    print(f"========================================================")

    traer_brave_al_frente()

    ya_en_formulario_ats = (
        ("myworkdayjobs.com" in (page.url or "").lower() and "/apply" in (page.url or "").lower())
        or ("csod.com" in (page.url or "").lower() and "/application" in (page.url or "").lower())
        or ("pandape" in (page.url or "").lower() and ("/apply" in (page.url or "").lower() or "/detail/" in (page.url or "").lower()))
    )
    if not ya_en_formulario_ats:
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(2.0)
        except Exception as e:
            return "error", f"Fallo al cargar URL: {str(e)[:60]}"

    # Inyectar botón flotante de Pausar/Reanudar y X roja en esquina inferior izquierda
    await inyectar_boton_flotante_control(page)

    if not ya_en_formulario_ats:
        # 1. Comprobar si redirigió a un catálogo general o página de búsqueda
        if es_url_catalogo(page.url, portal):
            print(f"⚠️  URL redirigió al catálogo general de ofertas ({page.url}). Oferta expirada.")
            return "cerrada", "La oferta expiró o redirigió al catálogo general de ofertas"

        # Verificar si el usuario pulsó Cerrar o Pausar de inmediato
        st = await comprobar_pausa_y_parada(page)
        if st == "stop":
            return "stop", "Bot detenido por el usuario con la X roja"

        # Comprobar si ya está postulado o cerrada
        body_text = (await page.inner_text("body") or "").lower()
        if any(w in body_text for w in FRASES_YA_POSTULADO):
            return "ya_postulado", "Ya estabas postulado previamente a esta oferta"

        if any(w in body_text for w in FRASES_CERRADA) and not _parece_login_pagina(page.url, body_text):
            print("⚠️  Detectado mensaje de oferta cerrada o finalizada en el portal.")
            return "cerrada", "Oferta cerrada o expirada en el portal"

        # PASO 0.5: Expandir y leer 'Acerca del empleo' / descripción completa para validar requisitos reales
        es_valida, _desc_comp, motivo_desc = await leer_y_validar_descripcion_oferta(page, oferta)
        if not es_valida:
            return "descartada_desc", motivo_desc

        # PASO 1: Pulsar el primer botón 'Postula fácil' / 'Postularme' / 'Solicitud sencilla' / 'Solicitar'
    print("Paso 1: Buscando y pulsando botón inicial (Postula fácil / Solicitud sencilla)...")
    clic_inicial = False
    if not ya_en_formulario_ats:
        tabs_antes = list(page.context.pages)
        clic_inicial = await pulsar_primer_postula(page)
        print(f"Resultado clic inicial: {'Exitoso' if clic_inicial else 'No encontrado'}")
        await asyncio.sleep(2.5)
    else:
        tabs_antes = list(page.context.pages)
        print("♻️  Reenganchado sobre pestaña ATS ya abierta en revisión.")

    # Detectar si el clic abrió una pestaña externa nueva (ej: LinkedIn 'Solicitar' en el sitio de la empresa)
    nueva_tab = await _detectar_nueva_pestana_del_bot(tabs_antes, page)
    if nueva_tab is not None:
        print(f"🌐 Detectada nueva pestaña externa abierta: {nueva_tab.url}")
        try:
            await nueva_tab.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:
            pass
        old_page = page
        page = nueva_tab
        if page not in tabs_oferta:
            tabs_oferta.append(page)
        try:
            if old_page != page:
                await old_page.close()
                if old_page in tabs_oferta:
                    tabs_oferta.remove(old_page)
        except Exception:
            pass
        await page.bring_to_front()
        await asyncio.sleep(1.5)
        await inyectar_boton_flotante_control(page)

    # Detectar avisos ('Entendido' / 'Te redireccionaremos') o CTA externos ('Solicitar ahora')
    page = await manejar_redireccion_externa(page)
    if page not in tabs_oferta:
        tabs_oferta.append(page)

    # Comprobar si tras el clic inicial redirigió a un catálogo
    if es_url_catalogo(page.url, portal):
        print(f"⚠️  El clic inicial redirigió al catálogo general ({page.url}). Oferta cerrada.")
        return "cerrada", "El botón redirigió al catálogo general de ofertas"

    # Comprobar si tras el clic inicial el portal indica que ya estás postulado, cerrada o redirigió a publicidad/venta
    body_tras_clic = (await page.inner_text("body") or "").lower()
    if es_pagina_comercial_no_laboral(page.url, body_tras_clic):
        print(f"⚠️  Se detectó redirección comercial o no laboral ({page.url[:70]}). Cerrando sin postular.")
        return "cerrada", "Redirigió a página comercial / publicidad (no es oferta laboral)"
    if clic_inicial and ("/candidate/postapply" in page.url.lower() or "te postulaste correctamente" in body_tras_clic):
        return "ok", f"Postulación 1-clic confirmada en {portal} (sin preguntas previas)"
    if any(w in body_tras_clic for w in FRASES_YA_POSTULADO):
        return "ya_postulado", "Ya estabas postulado previamente a esta oferta (detectado tras clic inicial)"

    if any(w in body_tras_clic for w in FRASES_CERRADA) and not _parece_login_pagina(page.url, body_tras_clic):
        return "cerrada", "Oferta cerrada o expirada en el portal (detectado tras clic inicial)"

    if _parece_login_pagina(page.url, body_tras_clic):
        hablar(f"Falta iniciar sesión en {portal}. Intentando ingreso automático con Google o pausando para que inicies sesión.")

    # Comprobar si el portal externo presenta CAPTCHA antibot (Cloudflare Turnstile / reCAPTCHA / hCaptcha)
    await asyncio.sleep(0.8)
    tiene_captcha_frame = any(
        any(k in (fr.url or "").lower() for k in ["turnstile", "recaptcha", "hcaptcha", "challenges.cloudflare"])
        for fr in page.frames
    )
    diag_bloqueo = await page.evaluate(r'''() => {
        const hasCaptchaFrame = Array.from(document.querySelectorAll('iframe')).some(f => /turnstile|recaptcha|hcaptcha|challenges\.cloudflare/i.test(f.src || ''));
        return {hasCaptchaFrame};
    }''')
    if tiene_captcha_frame or diag_bloqueo.get("hasCaptchaFrame") or any(w in body_tras_clic for w in ["verify you are human", "no soy un robot"]):
        print(f"🛡️ Portal externo bloqueado por verificación antibot/CAPTCHA ({page.url[:70]}). Respetando política sin automatizar CAPTCHA.")
        return "captcha_antibot", f"Portal externo requiere verificación humana / CAPTCHA manual: {page.url}"

    # Intentar flujo automático de autenticación (Google OAuth correof1553 -> Probar otro método x2 -> Bitwarden -> Pausa interactiva)
    url_antes_auth = page.url
    page, auth_ok, auth_mot = await manejar_autenticacion_portal(page, empresa, solo_auto=False)
    if page not in tabs_oferta:
        tabs_oferta.append(page)
    if not auth_ok and auth_mot == "stop":
        return "stop", "Bot detenido por el usuario durante inicio de sesión"

    # Si venía de un muro de login y tras autenticarse redirigió al home/catálogo u otra oferta, volver a la URL original y pulsar Postular
    if _parece_login_pagina(url_antes_auth, body_tras_clic) and auth_mot in ("autenticado_ok", "autenticado_manual"):
        u_now = (page.url or "").split("?")[0].rstrip("/").lower()
        u_obj = (url or "").split("?")[0].rstrip("/").lower()
        if u_now != u_obj and not ("/apply" in u_now or "myworkdayjobs.com" in u_now or "applyct" in u_now):
            try:
                print(f"🔁 Sesión iniciada en {portal}. Volviendo a la oferta original para postular...")
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(2.0)
                await inyectar_boton_flotante_control(page)
                await pulsar_primer_postula(page)
                await asyncio.sleep(2.5)
            except Exception as e:
                print(f"Aviso regresando a la oferta tras login: {e}")

    # Comprobar tras el inicio de sesión SSO si el portal (ej. Workday) ya registra una postulación previa o oferta cerrada
    try:
        body_post_auth = (await page.inner_text("body") or "").lower()
        if any(w in body_post_auth for w in FRASES_YA_POSTULADO):
            return "ya_postulado", "Ya estabas postulado previamente a esta oferta (detectado tras login)"
        if any(w in body_post_auth for w in FRASES_CERRADA):
            return "cerrada", "Oferta cerrada o expirada tras iniciar sesión"
    except Exception:
        pass

    # Verificar parada o pausa tras clic inicial
    st = await comprobar_pausa_y_parada(page)
    if st == "stop":
        return "stop", "Bot detenido por el usuario con la X roja"
    if st == "next":
        return "siguiente", "Avanzado manualmente hacia la siguiente oferta"

    # PASO 2: Si aparece modal con 'Comenzar', pulsarlo
    await pulsar_comenzar_si_existe(page)

    # Si redirigió a KillerQuestions o formulario en Computrabajo
    if "candidate/kq" in page.url or "match" in page.url:
        print("Detectada página de KillerQuestions de Computrabajo.")
        await asyncio.sleep(1.5)

    # Asegurar que el botón flotante persista
    await inyectar_boton_flotante_control(page)

    # Verificar parada o pausa
    st = await comprobar_pausa_y_parada(page)
    if st == "stop":
        return "stop", "Bot detenido por el usuario con la X roja"
    if st == "next":
        return "siguiente", "Avanzado manualmente hacia la siguiente oferta"

    # PASO 3: Rellenar formulario (incluyendo asistente multipaso LinkedIn si aplica)
    qa_list = await avanzar_wizard_linkedin_si_existe(page, empresa, titulo)
    try:
        bt_post_wiz = (await page.inner_text("body") or "").lower()
        if any(w in bt_post_wiz for w in FRASES_YA_POSTULADO):
            return "ya_postulado", "Ya estabas postulado previamente a esta oferta (detectado al rellenar)"
    except Exception:
        pass
    try:
        tuvo_modal = await page.locator("dialog[open], div.jobs-easy-apply-modal, form#formularioPreguntasOferta, .modal.show, .offcanvas.show").count() > 0
    except Exception:
        tuvo_modal = False

    # Habilitar el segundo botón 'Postular'
    await page.evaluate('''() => {
        const btns = Array.from(document.querySelectorAll('button, input[type="submit"]')).filter(b => {
            const t = (b.innerText || b.value || "").toLowerCase();
            return t.includes('postular') || t.includes('enviar') || t.includes('responder') || t.includes('continuar');
        });
        for (let b of btns) {
            b.removeAttribute('disabled');
            b.classList.remove('boton-deshabilitado');
        }
    }''')

    # Instalar detector de clic manual del usuario
    await page.evaluate('''() => {
        window.__usuario_envio_postulacion = false;
        const btns = Array.from(document.querySelectorAll('button, a.btn, input[type="submit"]')).filter(b => {
            const t = (b.innerText || b.value || "").toLowerCase();
            return (t === 'postular' || t.includes('postular') || t.includes('enviar') || t.includes('responder') || t === 'enviar mi cv') && b.offsetParent !== null;
        });
        for (let b of btns) {
            b.addEventListener('click', () => {
                window.__usuario_envio_postulacion = true;
            });
        }
    }''')

    # Verificar parada o pausa antes de narrar
    st = await comprobar_pausa_y_parada(page)
    if st == "stop":
        return "stop", "Bot detenido por el usuario con la X roja"
    if st == "next":
        return "siguiente", "Avanzado manualmente hacia la siguiente oferta"

    # PASO 4: LEER EN VOZ ALTA CADA PREGUNTA Y SU RESPUESTA CON DI (según interruptor Leer Preguntas y Respuestas ON/OFF)
    traer_brave_al_frente()
    if qa_list:
        leer_qa_on = GLOBAL_BOT_CONTROL.get("leer_qa", True)
        if leer_qa_on:
            hablar(f"El reclutador de {empresa} solicita responder {len(qa_list)} preguntas.")
        for item in qa_list:
            # Comprobar si el usuario pausó o detuvo entre lecturas
            st = await comprobar_pausa_y_parada(page)
            if st == "stop":
                return "stop", "Bot detenido por el usuario con la X roja"
            if st == "next":
                return "siguiente", "Avanzado manualmente hacia la siguiente oferta"

            q_clean = item['pregunta']
            r_full = item['respuesta']
            # Locución concisa y clara
            r_resumen = r_full.split('.')[0] if '.' in r_full else r_full[:80]
            print(f"📢 {item['num']}: {q_clean}")
            print(f"   ↳ {r_resumen}\n")
            if GLOBAL_BOT_CONTROL.get("leer_qa", True):
                hablar(f"{item['num']}: {q_clean}.")
                hablar(f"Respuesta: {r_resumen}.")
        segundos_revision = max(30, int(GLOBAL_BOT_CONTROL.get("segundos", segundos_revision)))
        GLOBAL_BOT_CONTROL["segundos"] = segundos_revision
    elif GLOBAL_BOT_CONTROL.get("leer_qa", True):
        hablar(f"Formulario sin preguntas adicionales para {empresa}.")

    # PASO 5: RECIÉN AQUÍ COMIENZAN LOS 30 SEGUNDOS DE PAUSA DE REVISIÓN
    msg_pausa = f"Respuestas listas en pantalla. Tienes {segundos_revision} segundos para revisar. Puedes pausar con el botón flotante inferior izquierdo o cerrarlo con la X roja."
    print(f"\n📢 {msg_pausa}")
    hablar(msg_pausa)

    print(f"⏳ Pausa de revisión activa ({segundos_revision}s). [Botón flotante en esquina inferior izquierda para Pausar / X roja para Cerrar]...")
    
    tiempo_transcurrido = 0.0
    esta_pausado_prev = False
    clic_manual = False
    ultimo_qa_map = {}
    guardadas_en_vivo = {}
    pendientes_guardar = {}  # qid -> (texto, timestamp)

    def _sincronizar_ediciones_en_memoria(qa_dict, forzar=False):
        ahora = time.monotonic()
        nuevas = []
        for qid, qinfo in (qa_dict or {}).items():
            preg = (qinfo.get("pregunta") or "").strip()
            bot_ans = (qinfo.get("bot_answer") or "").strip()
            curr_ans = (qinfo.get("current_answer") or "").strip()
            if not preg or not curr_ans or curr_ans == bot_ans or len(curr_ans) < 2:
                continue
            if curr_ans == guardadas_en_vivo.get(qid):
                continue

            if not forzar:
                prev_txt, prev_ts = pendientes_guardar.get(qid, (None, 0))
                if prev_txt != curr_ans:
                    pendientes_guardar[qid] = (curr_ans, ahora)
                    continue
                if (ahora - prev_ts) < 1.2:
                    continue

            try:
                database.guardar_o_actualizar_respuesta(preg, curr_ans, categoria="editada_en_vivo")
                guardadas_en_vivo[qid] = curr_ans
                pendientes_guardar.pop(qid, None)
                nuevas.append((preg, curr_ans))
            except Exception as e:
                print(f"Aviso guardando modificación en memoria: {e}")

        if nuevas:
            print(f"\n🧠 ¡MEMORIA ACTUALIZADA EN TIEMPO REAL! Se guardaron {len(nuevas)} ediciones:")
            for mp, ma in nuevas:
                print(f"   📝 {mp[:65]} -> {ma[:65]}...")
            hablar("Anoté tu modificación en memoria; ya quedó guardada como tu respuesta actual.")

    GLOBAL_BOT_CONTROL["segundos"] = segundos_revision
    GLOBAL_BOT_CONTROL["skip_wait"] = False
    GLOBAL_BOT_CONTROL["timer_text"] = f"{segundos_revision}s"
    browser = page.context.browser if page and page.context else None

    while tiempo_transcurrido < GLOBAL_BOT_CONTROL["segundos"]:
        segundos_revision = GLOBAL_BOT_CONTROL["segundos"]
        if browser:
            await sincronizar_widgets_navegador(browser)

        try:
            estado_control = await page.evaluate('''(frases) => {
                const bt = (document.body ? document.body.innerText : '').toLowerCase();
                const explSuccess = frases.some(w => bt.includes(w));
                return {
                    clickedManual: window.__usuario_envio_postulacion || false,
                    qaMap: window.__autopostula_qa_map || {},
                    success: explSuccess
                };
            }''', FRASES_CONFIRMACION_ENVIO)
        except Exception:
            estado_control = {}
            if GLOBAL_BOT_CONTROL["paused"] or esta_pausado_prev:
                await asyncio.sleep(0.5)
                continue
            # Si la página ya navegó tras clic manual fuera de pausa
            clic_manual = True
            break

        if estado_control.get("qaMap"):
            ultimo_qa_map = estado_control.get("qaMap")
            _sincronizar_ediciones_en_memoria(ultimo_qa_map, forzar=False)

        if GLOBAL_BOT_CONTROL["stop"]:
            _sincronizar_ediciones_en_memoria(ultimo_qa_map, forzar=True)
            print(f"\n🛑 ¡Detectado clic en Detener Bot! Detención total solicitada por el usuario.")
            return "stop", "Bot detenido por el usuario desde el panel flotante"

        if GLOBAL_BOT_CONTROL["next_offer"]:
            _sincronizar_ediciones_en_memoria(ultimo_qa_map, forzar=True)
            print(f"\n⏭️  Pulsado 'Continuar siguiente': avanzando a la próxima oferta sin enviar la actual.")
            return "siguiente", "Avanzado manualmente hacia la siguiente oferta"

        if GLOBAL_BOT_CONTROL["paused"]:
            if not esta_pausado_prev:
                print(f"\n⏸️  BOT PAUSADO por el usuario desde el botón flotante.")
                hablar("Bot pausado. Tómate el tiempo necesario para revisar o editar.")
                esta_pausado_prev = True
                GLOBAL_BOT_CONTROL["timer_text"] = "PAUSA"
                if browser:
                    await sincronizar_widgets_navegador(browser)
            await asyncio.sleep(0.5)
            continue

        if estado_control.get("clickedManual") or estado_control.get("success"):
            clic_manual = True
            _sincronizar_ediciones_en_memoria(ultimo_qa_map, forzar=True)
            print(f"\n👆 ¡Detectado clic manual del usuario o confirmación anticipada!")
            break

        if GLOBAL_BOT_CONTROL["skip_wait"]:
            GLOBAL_BOT_CONTROL["skip_wait"] = False
            _sincronizar_ediciones_en_memoria(ultimo_qa_map, forzar=True)
            print(f"\n🚀 ¡Pulsado 'Enviar Postulación Ahora' desde la ventanita! Omitiendo espera restante...")
            break

        if esta_pausado_prev:
            print(f"\n▶️  BOT REANUDADO por el usuario.")
            hablar("Bot reanudado.")
            esta_pausado_prev = False

        restante = max(0, int(segundos_revision - tiempo_transcurrido))
        GLOBAL_BOT_CONTROL["timer_text"] = f"{restante}s"
        if browser:
            await sincronizar_widgets_navegador(browser)

        print(f"\r⏱️  Revisión: {restante:02d}s restantes | [⏸️ Pausar | ⚙️ Ventanita | ❌ Detener]...", end="", flush=True)
        await asyncio.sleep(0.5)
        tiempo_transcurrido += 0.5

    print("")

    # Asegurar guardado final de cualquier edición pendiente al salir del bucle
    _sincronizar_ediciones_en_memoria(ultimo_qa_map, forzar=True)

    # Comprobar parada final antes del envío
    st = await comprobar_pausa_y_parada(page)
    if st == "stop":
        return "stop", "Bot detenido por el usuario con la X roja"
    if st == "next":
        return "siguiente", "Avanzado manualmente hacia la siguiente oferta"

    # PASO 6: Pulsar el SEGUNDO 'Postular' si no hubo clic manual
    res_envio = False
    if not clic_manual:
        print(f"⏰ Tiempo de revisión cumplido ({segundos_revision}s). Pulsando el segundo botón Postular...")
        hablar("Tiempo cumplido. Enviando postulación.")
        res_envio = await pulsar_segundo_postular(page)
        print(f"Resultado clic segundo Postular: {res_envio}")
        await asyncio.sleep(3.5)
    else:
        await asyncio.sleep(2.5)

    # PASO 7: Comprobar confirmación final de éxito con espera de AJAX y verificación visual
    es_exito = False
    motivo_exito = ""

    for intento in range(8):
        await asyncio.sleep(1.0)
        body_final = (await page.inner_text("body") or "").lower()
        url_final = page.url.lower()

        if any(w in body_final for w in FRASES_CONFIRMACION_ENVIO) or ("mis-postulaciones" in url_final) or ("match" in url_final):
            es_exito = True
            motivo_exito = "Texto explícito o URL de éxito detectada"
            break

        # En Laborum, el botón cambia de 'Postularme' a 'Ver estadísticas'
        try:
            ver_stats = await page.locator("button:has-text('Ver estadísticas'), a:has-text('Ver estadísticas')").count()
            if ver_stats > 0:
                es_exito = True
                motivo_exito = "Botón 'Ver estadísticas' activo en Laborum"
                break
        except Exception:
            pass

        # Si hubo formulario de preguntas (modal o dialog LinkedIn) y se cerró tras un clic REAL de envío (no en /Detail/)
        if (qa_list or tuvo_modal) and (clic_manual or res_envio) and ("/detail/" not in url_final):
            modal_abierto = await page.evaluate('''() => {
                const form = document.querySelector("form#formularioPreguntasOferta") || document.querySelector(".modal.show") || document.querySelector(".offcanvas.show") || document.querySelector("dialog[open]") || document.querySelector("div.jobs-easy-apply-modal");
                return Boolean(form && form.offsetParent !== null);
            }''')
            if modal_abierto is False:
                es_exito = True
                motivo_exito = "Modal / Dialog completado y cerrado exitosamente"
                break

    if es_exito:
        print(f"\n========================================================")
        print(f"✅ ¡POSTULACIÓN CONFIRMADA VISIBLE EN PANTALLA!")
        print(f"🎯 Empresa: {empresa} | Portal: {portal}")
        print(f"📋 Validación: {motivo_exito}")
        print(f"👁️  Manteniendo pantalla visible 4 segundos para que verifiques el éxito...")
        print(f"========================================================\n")
        hablar(f"Postulación a {empresa} confirmada con éxito en pantalla.")
        await asyncio.sleep(4.0)
        return "ok", f"Postulación confirmada con éxito en {portal} ({motivo_exito} | {len(qa_list)} preguntas respondidas)"
    else:
        print(f"\n⚠️  Aviso: No se detectó confirmación explícita de postulación en {portal}. Se marcará para revisión.")
        return "revision", f"Revisar estado en {portal} (no se detectó confirmación explícita tras envío)"


async def _tabs_tienen_confirmacion(tabs_oferta: list) -> bool:
    """Comprueba si alguna pestaña activa de la oferta muestra confirmación explícita de envío."""
    for pg in list(tabs_oferta):
        try:
            if pg.is_closed():
                continue
            bt = (await pg.inner_text("body") or "").lower()
            if any(w in bt for w in FRASES_CONFIRMACION_ENVIO):
                return True
        except Exception:
            pass
    return False


async def _esperar_post_envio_y_cerrar(browser, tabs_oferta: list, empresa: str, espera_post: int) -> bool:
    """Espera `espera_post` segundos visibles (`OK Xs`) con soporte Pausa/Reanudar antes de cerrar la pestaña confirmada. Anuncia 'Ya postulamos a <empresa>, esperaré 35 segundos...' antes de contar y 'Cerrando' 5s antes de cerrar."""
    if espera_post > 5:
        hablar(f"Ya postulamos a {empresa}, esperaré {espera_post} segundos para cerrar la pestaña.")
    else:
        hablar(f"Postulación a {empresa} confirmada con éxito visible en pantalla.")

    post_elapsed = 0.0
    last_tick = time.monotonic()
    post_pausado_prev = False
    aviso_5s = False
    GLOBAL_BOT_CONTROL["skip_wait"] = False
    while post_elapsed < espera_post:
        await sincronizar_widgets_navegador(browser)
        now_t = time.monotonic()
        if not GLOBAL_BOT_CONTROL["paused"] and not post_pausado_prev:
            post_elapsed += max(0.0, now_t - last_tick)
        last_tick = now_t

        if GLOBAL_BOT_CONTROL["stop"]:
            print("\n🛑 Bucle detenido por completo por el usuario en verificación post-envío.")
            hablar("Bot detenido por completo a solicitud del usuario.", final=True)
            await desactivar_widgets_navegador(browser)
            return False
        if GLOBAL_BOT_CONTROL["paused"]:
            if not post_pausado_prev:
                print("\n⏸️  PAUSADO tras confirmar postulación. Revisa en pantalla y pulsa Reanudar para cerrar y pasar a la siguiente.")
                hablar("Pausado tras confirmar el envío. Presiona reanudar cuando quieras cerrar y seguir.")
                post_pausado_prev = True
                GLOBAL_BOT_CONTROL["timer_text"] = "PAUSA"
                await sincronizar_widgets_navegador(browser)
            await asyncio.sleep(0.5)
            last_tick = time.monotonic()
            continue
        if post_pausado_prev:
            print("\n▶️  Reanudado tras revisión: cerrando pestaña confirmada y pasando a la siguiente...")
            hablar("Cerrando y pasando a la siguiente oferta.")
            break
        if GLOBAL_BOT_CONTROL["next_offer"]:
            GLOBAL_BOT_CONTROL["next_offer"] = False
            print("\n⏭️  Continuando inmediatamente hacia la siguiente oferta...")
            break
        if GLOBAL_BOT_CONTROL["skip_wait"]:
            GLOBAL_BOT_CONTROL["skip_wait"] = False
            print("\n🚀 Avance inmediato solicitado: cerrando pestaña confirmada...")
            break

        rest_f = max(0.0, espera_post - post_elapsed)
        rest_post = int(rest_f + 0.999)
        if espera_post > 10 and not aviso_5s and 0 < rest_f <= 5.0:
            aviso_5s = True
            print("\n🔔 Faltan 5 segundos: cerrando pestaña...")
            hablar("Cerrando.")
            last_tick = time.monotonic()
        GLOBAL_BOT_CONTROL["timer_text"] = f"OK {rest_post}s"
        await sincronizar_widgets_navegador(browser)
        print(f"\r👁️  Verificación post-envío: {rest_post:02d}s | [⏸️ Pausar | ▶️ Reanudar = Siguiente]...", end="", flush=True)
        await asyncio.sleep(0.5)
    print("")
    for pg_o in list(tabs_oferta):
        TABS_PROTEGIDAS_MANUAL.discard(pg_o)
        try:
            if not pg_o.is_closed():
                await pg_o.close()
        except Exception:
            pass
    return True


async def ejecutar_bucle(segundos_revision: int = 30, max_ofertas: int = 100, solo_id: int = None, solo_portal: str | None = None):
    ofertas = obtener_ofertas_validas_pendientes(limite=max_ofertas, solo_id=solo_id, solo_portal=solo_portal)
    total = len(ofertas)

    if total == 0:
        msg_empty = f"No hay más ofertas pendientes en {solo_portal}." if solo_portal else "No hay más ofertas pendientes en la Región Metropolitana."
        print(f"✅ {msg_empty}")
        hablar(msg_empty, final=True)
        return

    print("==========================================================")
    print(f"🚀 INICIANDO BUCLE DE POSTULACIÓN EN PANTALLA (REGIÓN METROPOLITANA)")
    if solo_portal:
        print(f"🎯 Portal filtrado: {solo_portal}")
    print(f"📋 Total ofertas técnicas RM en cola: {total}")
    print(f"🗂️  Máx. pestañas laborales simultáneas: {GLOBAL_BOT_CONTROL['max_tabs']}")
    print(f"⏱️  Pausa de revisión tras lectura: {segundos_revision} segundos")
    print(f"🔘 Botón flotante interactivo: Esquina inferior izquierda (Pausar/Reanudar)")
    print(f"🖥️  Navegador: Brave Browser en puerto 9222 (CDP)")
    print(f"🧠 Gestor de Memoria Q&A: http://localhost:8087/memoria")
    print("==========================================================")

    # Asegurar que el servidor de Memoria y Dashboard (puerto 8087) esté activo
    try:
        import urllib.request
        urllib.request.urlopen("http://localhost:8087/api/memoria", timeout=0.8)
    except Exception:
        try:
            subprocess.Popen(
                [sys.executable, str(BASE_DIR / "web_dashboard.py")],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
        except Exception:
            pass

    hablar(f"Iniciando bucle para {total} ofertas de la Región Metropolitana. Dispones del botón flotante en la esquina inferior izquierda para pausar y reanudar.")

    async with async_playwright() as p:
        try:
            browser = await p.chromium.connect_over_cdp("http://localhost:9222")
            ctx = browser.contexts[0]
            try:
                await ctx.expose_function("__autopostula_host_cmd", _aplicar_comando_control)
            except Exception:
                pass
            try:
                await ctx.add_init_script(_WIDGET_JS)
            except Exception:
                pass
            await gestionar_limite_pestanas(browser, GLOBAL_BOT_CONTROL["max_tabs"])
            await sincronizar_widgets_navegador(browser)
        except Exception as e:
            print(f"❌ Error conectando a Brave: {e}")
            hablar("Error conectando con Brave en puerto 9222.", final=True)
            return

        exitosas = 0
        cerradas = 0

        for i, of in enumerate(ofertas, 1):
            if GLOBAL_BOT_CONTROL["paused"] or GLOBAL_BOT_CONTROL["stop"]:
                st_bucle = await comprobar_pausa_y_parada(ctx.pages[-1] if ctx.pages else None)
                if st_bucle == "stop":
                    await desactivar_widgets_navegador(browser)
                    return
            of_id = of["id"]
            titulo = of["titulo"][:45]
            empresa = of["empresa"][:25]
            portal = of["portal"]
            GLOBAL_BOT_CONTROL["next_offer"] = False
            GLOBAL_BOT_CONTROL["inv_status"] = ""
            GLOBAL_BOT_CONTROL["oferta_actual"] = of
            _registrar_historial_oferta(of)

            print(f"\n>>> [{i}/{total}] Procesando oferta #{of_id}: {empresa} - {titulo} ({portal})")
            page_reusada = None
            emp_toks = [t for t in re.findall(r'[a-z0-9]{4,}', empresa.lower()) if t not in {"chile", "empresa", "grupo", "servicios", "mercado", "mayorista"}]
            tit_toks = [t for t in re.findall(r'[a-z0-9]{4,}', titulo.lower()) if t not in {"practica", "ingenieria", "informatica", "chile", "program"}]
            for pg_cand in list(ctx.pages):
                if pg_cand.is_closed():
                    continue
                cu = (pg_cand.url or "").lower()
                emp_ok = (not emp_toks) or any(et in cu for et in emp_toks) or (len(emp_toks) >= 2 and "".join(emp_toks[-2:]) in cu)
                es_workday_app = "myworkdayjobs.com" in cu and "/apply" in cu and emp_ok and any(tk in cu for tk in tit_toks[:3])
                es_csod_app = "csod.com" in cu and "/application" in cu and emp_ok
                es_pandape_app = "pandape" in cu and ("/apply" in cu or "/detail/" in cu) and (emp_ok or "".join(re.findall(r'[a-z0-9]+', empresa.lower())[-2:]) in cu)
                if es_workday_app or es_csod_app or es_pandape_app:
                    page_reusada = pg_cand
                    break
            if page_reusada is not None:
                page = page_reusada
                BOT_OWNED_PAGES.add(page)
                await page.bring_to_front()
                await gestionar_limite_pestanas(browser, GLOBAL_BOT_CONTROL["max_tabs"], tabs_protegidas={page})
            else:
                await gestionar_limite_pestanas(browser, max(0, GLOBAL_BOT_CONTROL["max_tabs"] - 1))
                page = await ctx.new_page()
                BOT_OWNED_PAGES.add(page)
            tabs_oferta = [page]

            try:
                res_tipo, msg = await procesar_oferta_interactiva(page, of, segundos_revision=segundos_revision, tabs_oferta=tabs_oferta)
            except Exception as e:
                res_tipo, msg = "error", f"Error en oferta: {str(e)[:60]}"
            try:
                if tabs_oferta and not tabs_oferta[-1].is_closed():
                    _registrar_historial_oferta(of, tabs_oferta[-1].url)
            except Exception:
                pass

            now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()

            if res_tipo == "stop":
                print("\n🛑 Bucle detenido por completo por el usuario.")
                hablar("Bot detenido por completo a solicitud del usuario.", final=True)
                c.execute("UPDATE postulaciones SET notas = ? WHERE id = ?", (f"[Detenido por usuario] {msg}", of_id))
                conn.commit()
                conn.close()
                await desactivar_widgets_navegador(browser)
                return

            if res_tipo in ["ok", "ya_postulado"]:
                nuevo_estado = "🟢 Postulado Confirmado"
                exitosas += 1
                c.execute(
                    "UPDATE postulaciones SET estado = ?, fecha_postulacion = ?, metodo_postulacion = 'brave_cdp_pantalla', notas = ? WHERE id = ?",
                    (nuevo_estado, now_str, f"[CV Maestro Duoc] {msg}", of_id)
                )
                conn.commit()
                espera_post = max(35, int(GLOBAL_BOT_CONTROL.get("segundos", segundos_revision))) if res_tipo == "ok" else 4
                if not await _esperar_post_envio_y_cerrar(browser, tabs_oferta, empresa, espera_post):
                    conn.close()
                    return
            elif res_tipo == "descartada_desc":
                nuevo_estado = "⏭️ Descartada (Descripción no compatible)"
                c.execute("UPDATE postulaciones SET estado = ?, notas = ? WHERE id = ?", (nuevo_estado, msg, of_id))
                conn.commit()
                for pg_o in tabs_oferta:
                    try:
                        await pg_o.close()
                    except Exception:
                        pass
            elif res_tipo == "cerrada":
                nuevo_estado = "🔴 Oferta Cerrada / Expirada"
                cerradas += 1
                c.execute("UPDATE postulaciones SET estado = ?, notas = ? WHERE id = ?", (nuevo_estado, msg, of_id))
                conn.commit()
                hablar(f"La oferta de {empresa} está cerrada. Pasando a la siguiente.")
                for pg_o in tabs_oferta:
                    try:
                        await pg_o.close()
                    except Exception:
                        pass
            elif res_tipo in ["requiere_login", "captcha_antibot"]:
                nuevo_estado = "🔑 Requiere Login Manual" if res_tipo == "requiere_login" else "🛡️ Captcha / Verificación Manual"
                c.execute("UPDATE postulaciones SET estado = ?, notas = ? WHERE id = ?", (nuevo_estado, msg, of_id))
                conn.commit()
                hablar(f"El portal de {empresa} exige inicio de sesión o verificación humana; guardado en el panel para acceso manual.")
                for pg_o in tabs_oferta:
                    try:
                        await pg_o.close()
                    except Exception:
                        pass
            elif res_tipo == "siguiente":
                GLOBAL_BOT_CONTROL["next_offer"] = False
                for pg_o in tabs_oferta:
                    TABS_PROTEGIDAS_MANUAL.add(pg_o)
                c.execute("UPDATE postulaciones SET notas = ? WHERE id = ?", (f"[Siguiente manual] {msg}", of_id))
                conn.commit()
                hablar(f"Pasando a la siguiente oferta tras {empresa}.")
            else:
                c.execute("UPDATE postulaciones SET notas = ? WHERE id = ?", (f"[Revisión en pantalla] {msg}", of_id))
                conn.commit()
                for pg_o in tabs_oferta:
                    TABS_PROTEGIDAS_MANUAL.add(pg_o)
                print(f"⚠️  Oferta #{of_id} NO completó o no confirmó envío automático. PAUSANDO Y MANTENIENDO PESTAÑA ABIERTA.")
                GLOBAL_BOT_CONTROL["paused"] = True
                GLOBAL_BOT_CONTROL["timer_text"] = "SIN ENVIAR"
                traer_brave_al_frente()
                await sincronizar_widgets_navegador(browser)
                hablar(f"Atención: la postulación a {empresa} no se completó ni confirmó el envío. Queda pausada y abierta para revisarla. Cuando la envíes, esperaré 35 segundos antes de cerrarla.")

                confirmado_manual = False
                while GLOBAL_BOT_CONTROL["paused"] and not GLOBAL_BOT_CONTROL["stop"] and not GLOBAL_BOT_CONTROL["next_offer"]:
                    if await _tabs_tienen_confirmacion(tabs_oferta):
                        confirmado_manual = True
                        GLOBAL_BOT_CONTROL["paused"] = False
                        break
                    await sincronizar_widgets_navegador(browser)
                    await asyncio.sleep(0.5)

                if GLOBAL_BOT_CONTROL["stop"]:
                    conn.close()
                    await desactivar_widgets_navegador(browser)
                    return

                # Si el usuario pulsó Reanudar justo tras pulsar Enviar, esperar brevemente confirmación AJAX antes de cerrar
                if not confirmado_manual:
                    for _ in range(7):
                        if await _tabs_tienen_confirmacion(tabs_oferta):
                            confirmado_manual = True
                            break
                        await asyncio.sleep(0.5)

                if confirmado_manual:
                    c.execute(
                        "UPDATE postulaciones SET estado = ?, fecha_postulacion = ?, metodo_postulacion = 'brave_cdp_pantalla', notas = ? WHERE id = ?",
                        ("🟢 Postulado Confirmado", now_str, "[Completado manualmente tras pausa]", of_id)
                    )
                    conn.commit()
                    exitosas += 1
                    espera_post = max(35, int(GLOBAL_BOT_CONTROL.get("segundos", segundos_revision)))
                    if not await _esperar_post_envio_y_cerrar(browser, tabs_oferta, empresa, espera_post):
                        conn.close()
                        return
                else:
                    print(f"🔒 Pestaña de #{of_id} sin confirmación de envío: se mantiene abierta y protegida (no se cierra automáticamente).")

            conn.close()
            await gestionar_limite_pestanas(browser, GLOBAL_BOT_CONTROL["max_tabs"], tabs_protegidas=set(tabs_oferta) if res_tipo not in ["ok", "ya_postulado", "cerrada", "descartada_desc", "requiere_login", "captcha_antibot"] else None)
            await asyncio.sleep(2.0)

    await desactivar_widgets_navegador(browser)
    print("\n==========================================================")
    print(f"🎯 Bucle finalizado: {exitosas} confirmadas | {cerradas} cerradas de {total} procesadas.")
    print("==========================================================")
    hablar(f"Bucle de la Región Metropolitana finalizado. {exitosas} postulaciones confirmadas.", final=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AutoPostula — Bucle Interactivo en Pantalla (RM)")
    parser.add_argument("--segundos", type=int, default=30, help="Segundos de revisión tras leer las respuestas (ej. 30 a 60, o 0 para envío inmediato)")
    parser.add_argument("--max", type=int, default=100, help="Máximo de ofertas a procesar en el bucle")
    parser.add_argument("--id", type=int, default=None, help="Procesar una sola oferta específica por su ID")
    parser.add_argument("--portal", type=str, default=None, help="Filtrar solo ofertas de un portal específico (ej. Computrabajo, DuocLaboral, LinkedIn Jobs)")
    parser.add_argument("--max-tabs", type=int, default=None, help="Máximo inicial de pestañas laborales abiertas simultáneamente (ej. 1)")
    parser.add_argument("--no-focus", action="store_true", help="No enfocar la ventana de Brave (modo silencioso / segundo plano)")
    parser.add_argument("--sin-voz", action="store_true", help="No narrar con Di por voz")
    args = parser.parse_args()

    if args.max_tabs is not None:
        GLOBAL_BOT_CONTROL["max_tabs"] = max(1, min(15, args.max_tabs))
    if args.no_focus:
        GLOBAL_ENFOCAR = False
    if args.sin_voz:
        GLOBAL_CON_VOZ = False

    asyncio.run(ejecutar_bucle(segundos_revision=args.segundos, max_ofertas=args.max, solo_id=args.id, solo_portal=args.portal))
