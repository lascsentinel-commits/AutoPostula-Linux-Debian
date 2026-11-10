#!/usr/bin/env python3
"""
AutoPostula — Bot Auditor y Postulador de Pestañas Abiertas en Brave
Revisa todas las pestañas laborales abiertas en Chrome/Brave CDP (puerto 9222):
- Omite pestañas personales (Campus Virtual Duoc, Mis Postulaciones, Perfil LinkedIn, Memoria local).
- Cierra pestañas cerradas, spam comercial, duplicadas o ya postuladas.
- Intenta completar y enviar la postulación en las pestañas activas.
- Si no puede postular en una pestaña, la cierra y registra la causa técnica exacta.
- Deja como máximo las últimas 4 pestañas a las que postuló (o menos si hubo <4 postulables),
  y genera un análisis completo por consola, JSON y voz (Di / Edge-TTS) si no alcanzó 4 postulaciones.
"""

import argparse
import asyncio
import json
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path

os.environ.pop("PLAYWRIGHT_NODEJS_PATH", None)

from playwright.async_api import async_playwright

import bucle_pantalla_interactivo as bp

BASE_DIR = Path(__file__).resolve().parent
REPORT_JSON = BASE_DIR / "data" / "ultimo_analisis_pestanas.json"

KEEP_PERSONAL_PATTERNS = [
    "mis-postulaciones",
    "campusvirtual.duoc.cl",
    "localhost:8087",
    "/postulantes/curriculum",
    "linkedin.com/in/",
    "github.com",
]


def es_pestana_personal(url: str) -> bool:
    u = (url or "").lower()
    if not u.startswith("http"):
        return True
    return any(p in u for p in KEEP_PERSONAL_PATTERNS)


def actualizar_db_por_url(url: str, estado: str, notas: str):
    if not url:
        return
    u_base = url.split("?")[0].rstrip("/")
    try:
        conn = sqlite3.connect(bp.DB_PATH)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT id FROM postulaciones WHERE url_oferta LIKE ? OR ? LIKE '%' || id || '%' LIMIT 1",
            (f"%{u_base[-35:]}%", url)
        ).fetchone()
        if row:
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
            if "Postulado" in estado:
                conn.execute(
                    "UPDATE postulaciones SET estado = ?, fecha_postulacion = ?, metodo_postulacion = 'auditor_pestanas_cdp', notas = ? WHERE id = ?",
                    (estado, now_str, notas, row["id"])
                )
            else:
                conn.execute("UPDATE postulaciones SET estado = ?, notas = ? WHERE id = ?", (estado, notas, row["id"]))
            conn.commit()
        conn.close()
    except Exception:
        pass


def diagnosticar_bloqueo(url: str, title: str, diag: dict) -> tuple[str, str]:
    """Determina la causa técnica exacta por la cual no se pudo completar la postulación."""
    bt = (diag.get("body_low") or "")
    u = (url or "").lower()

    if bp.es_pagina_comercial_no_laboral(u, bt):
        return "pagina_comercial_spam", "El enlace abrió una página comercial/publicitaria (no es formulario laboral)."

    if any(w in bt for w in bp.FRASES_CERRADA):
        return "oferta_cerrada", "La vacante indica en pantalla que ya cerró o no acepta solicitudes."

    if "el empleo se ha movido a en curso dentro de clics en la solicitud" in bt and not diag.get("has_modal"):
        return "pestana_origen_linkedin", "Pestaña intermedia de LinkedIn tras abrir sitio externo ('Clics en la solicitud')."

    if diag.get("has_captcha") or any(w in bt for w in ["recaptcha", "no soy un robot", "cloudflare", "verify you are human", "hcaptcha"]):
        return "captcha_antibot", "Bloqueada por CAPTCHA o verificación humana antibot (Turnstile / reCAPTCHA)."

    if diag.get("has_password") or any(w in bt for w in ["iniciar sesión", "inicio de sesión", "crear cuenta", "registrarse", "acceder a tu solicitud", "sign in", "create account"]):
        if not diag.get("has_send_btn"):
            return "requiere_login_o_cuenta", f"El portal externo ({u.split('/')[2]}) exige iniciar sesión o crear cuenta propia antes de mostrar el formulario."

    if "hiringroom.com" in u and diag.get("has_password"):
        return "hiringroom_pide_sesion", "HiringRoom mostró modal de acceso/soporte con contraseña en lugar de postulación directa sin sesión."

    if not diag.get("has_send_btn") and diag.get("editable_inputs", 0) == 0:
        return "sin_formulario_ni_envio", "No expone formulario editable ni botón de envío directo sin pasos externos de registro."

    if diag.get("missing_required", 0) > 0:
        return "campos_obligatorios_pendientes", "Quedaron campos obligatorios o validaciones del portal sin completar."

    return "sin_confirmacion_envio", "Se intentó enviar pero el portal no mostró mensaje explícito de postulación exitosa."


async def _cerrar_pestana_seguro(ctx, pg):
    """Cierra una pestaña sin apagar Brave si es la última abierta (la redirige al panel local)."""
    try:
        if len(ctx.pages) <= 1:
            await pg.goto("http://localhost:8087/", wait_until="domcontentloaded", timeout=8000)
        else:
            await pg.close()
    except Exception:
        pass


async def auditar_y_postular_pestanas(max_mantener: int = 4, auto_enviar: bool = True, con_voz: bool = True):
    bp.GLOBAL_CON_VOZ = con_voz
    resultados = []
    postuladas_ok = []
    vistas_urls = set()

    if con_voz:
        bp.hablar("Iniciando auditoría de pestañas abiertas en Brave para postular donde sea posible y cerrar el resto.")

    async with async_playwright() as p:
        try:
            browser = await p.chromium.connect_over_cdp("http://localhost:9222")
            ctx = browser.contexts[0]
            try:
                await ctx.expose_function("__autopostula_host_cmd", bp._aplicar_comando_control)
            except Exception:
                pass
            try:
                await ctx.add_init_script(bp._WIDGET_JS)
            except Exception:
                pass
            await bp.sincronizar_widgets_navegador(browser)
        except Exception as e:
            print(f"❌ No se pudo conectar a Brave CDP: {e}")
            return

        cand_pages = [pg for pg in list(ctx.pages) if not es_pestana_personal(pg.url)]
        print(f"\n🔎 Total pestañas laborales/externas abiertas a revisar: {len(cand_pages)}")

        for idx, pg in enumerate(cand_pages, 1):
            if pg.is_closed():
                continue
            if await bp.comprobar_pausa_y_parada(pg) == "stop":
                break
            await bp.inyectar_boton_flotante_control(pg)
            url_orig = pg.url
            try:
                title = await pg.title()
            except Exception:
                title = url_orig[:50]

            print(f"\n--- [{idx}/{len(cand_pages)}] Revisando: {title[:60]} | {url_orig[:80]} ---")
            try:
                await pg.bring_to_front()
                await asyncio.sleep(0.8)
            except Exception:
                pass

            u_base = url_orig.split("?")[0].rstrip("/").lower()
            if u_base in vistas_urls:
                print("♻️  Pestaña duplicada detectada. Cerrando...")
                resultados.append({"url": url_orig, "titulo": title, "estado": "cerrada_duplicada", "motivo": "Pestaña duplicada de otra ya revisada."})
                await _cerrar_pestana_seguro(ctx, pg)
                continue
            vistas_urls.add(u_base)

            try:
                bt_init = (await pg.inner_text("body") or "").lower()
            except Exception:
                bt_init = ""

            # 1. Ya confirmada previamente en pantalla
            frases_done = [
                "solicitud enviada", "postulación enviada", "postulación exitosa",
                "solicitud completada", "ha presentado solicitud exitosamente", "tu postulación fue recibida",
                "gracias por postular", "has postulado con éxito", "ya te has postulado", "ya has postulado"
            ]
            try:
                ver_stats = await pg.locator("button:has-text('Ver estadísticas'), a:has-text('Ver estadísticas')").count() > 0
            except Exception:
                ver_stats = False

            if any(w in bt_init for w in frases_done) or ver_stats:
                print("✅ Ya figuraba confirmada en pantalla. Actualizando DB y cerrando...")
                actualizar_db_por_url(url_orig, "🟢 Postulado Confirmado", "[Auditor pestañas] Confirmación detectada en pantalla")
                resultados.append({"url": url_orig, "titulo": title, "estado": "ya_confirmada_cerrada", "motivo": "Ya estaba confirmada con éxito en pantalla."})
                await _cerrar_pestana_seguro(ctx, pg)
                continue

            # 2. Cerrada, spam comercial o pestaña intermedia LinkedIn sin modal
            diag_pre = await pg.evaluate(r'''() => {
                const bt = (document.body?.innerText || '').toLowerCase();
                const hasModal = document.querySelectorAll('dialog[open], div.jobs-easy-apply-modal, div[role="dialog"] textarea').length > 0;
                const hasPass = document.querySelectorAll('input[type="password"]:not([hidden])').length > 0;
                return { body_low: bt.slice(0, 4000), has_modal: hasModal, has_password: hasPass };
            }''')
            cod_pre, msg_pre = diagnosticar_bloqueo(url_orig, title, {**diag_pre, "has_send_btn": True, "editable_inputs": 1})
            if cod_pre in ["pagina_comercial_spam", "oferta_cerrada", "pestana_origen_linkedin"]:
                print(f"🧹 Cerrando ({cod_pre}): {msg_pre}")
                if cod_pre == "oferta_cerrada":
                    actualizar_db_por_url(url_orig, "🔴 Oferta Cerrada / Expirada", msg_pre)
                resultados.append({"url": url_orig, "titulo": title, "estado": "no_postulable_cerrada", "codigo": cod_pre, "motivo": msg_pre})
                await _cerrar_pestana_seguro(ctx, pg)
                continue

            # 3. Intentar completar formulario o pulsar CTA inicial ('Postularme' / 'Postular' / 'Solicitar')
            active_pg = pg
            tabs_antes = list(ctx.pages)
            try:
                # Si no hay inputs editables de preguntas aún y tampoco hay modal abierto, probar botón Postular/Postularme
                editable_cnt = await active_pg.locator("textarea:visible, #form-postulation-questions input:visible, dialog[open] input:visible").count()
                if editable_cnt == 0:
                    await bp.pulsar_primer_postula(active_pg)
                    await asyncio.sleep(2.0)
                    tabs_despues = list(ctx.pages)
                    if len(tabs_despues) > len(tabs_antes):
                        nueva = [t for t in tabs_despues if t not in tabs_antes][-1]
                        try:
                            await nueva.wait_for_load_state("domcontentloaded", timeout=12000)
                        except Exception:
                            pass
                        try:
                            await active_pg.close()
                        except Exception:
                            pass
                        active_pg = nueva
                        await active_pg.bring_to_front()
                        await asyncio.sleep(1.5)
                    active_pg = await bp.manejar_redireccion_externa(active_pg)
                active_pg, _auth_ok, _auth_mot = await bp.manejar_autenticacion_portal(active_pg, title[:35], solo_auto=True)
            except Exception:
                pass

            # Verificar si tras el clic cayó en login / Turnstile / HiringRoom / spam / cerrada
            try:
                diag_mid = await active_pg.evaluate(r'''() => {
                    const bt = (document.body?.innerText || '').toLowerCase();
                    const hasModal = document.querySelectorAll('dialog[open], div.jobs-easy-apply-modal, div[data-test-modal], #form-postulation-questions, form#formularioPreguntasOferta').length > 0;
                    const hasPass = Array.from(document.querySelectorAll('input[type="password"]')).some(e => e.offsetParent !== null);
                    const hasCaptcha = Array.from(document.querySelectorAll('iframe')).some(f => /turnstile|recaptcha|hcaptcha|challenges\.cloudflare/i.test(f.src || ''));
                    const editable = Array.from(document.querySelectorAll('textarea, input[type="text"]:not([id*="search"]):not([id*="react-select"]), input[type="email"], input[type="file"]')).filter(e => (e.offsetParent !== null || e.type === 'file') && !e.readOnly && !e.disabled).length;
                    const sendBtns = Array.from(document.querySelectorAll('button, input[type="submit"]')).filter(e => {
                        if (e.offsetParent === null) return false;
                        if (e.closest('#supportModal, .modal:not(.show), [aria-hidden="true"], header, nav, footer')) return false;
                        const t = (e.innerText || e.value || '').trim().toLowerCase();
                        return t === 'postular' || t.includes('enviar solicitud') || t.includes('enviar postulación') || t === 'enviar' || t === 'postularme' || t.includes('finalizar postulación') || t === 'continuar' || t === 'siguiente' || t.includes('guardar y continuar');
                    }).length;
                    return { body_low: bt.slice(0, 5000), has_modal: hasModal, has_password: hasPass, has_captcha: hasCaptcha, editable_inputs: editable, has_send_btn: sendBtns > 0 };
                }''')
            except Exception:
                diag_mid = {"body_low": "", "has_modal": False, "has_password": False, "has_captcha": False, "editable_inputs": 0, "has_send_btn": False}

            # Si aún no hay botón de envío real ni formulario desbloqueado (por login wall / Workday / HiringRoom Turnstile / captcha)
            if diag_mid.get("has_captcha") or diag_mid["has_password"] or (not diag_mid["has_send_btn"] and diag_mid["editable_inputs"] == 0):
                cod, mot = diagnosticar_bloqueo(active_pg.url, title, diag_mid)
                print(f"❌ No postulable ({cod}): {mot} -> Cerrando pestaña.")
                resultados.append({"url": active_pg.url, "titulo": title, "estado": "no_postulable_cerrada", "codigo": cod, "motivo": mot})
                await _cerrar_pestana_seguro(ctx, active_pg)
                continue

            # Rellenar datos personales/CV y preguntas de la oferta
            empresa_guess = (title.split("|")[0].split("-")[0].strip() or "la empresa")[:35]
            try:
                qa_list = await bp.avanzar_wizard_linkedin_si_existe(active_pg, empresa_guess, title)
            except Exception as e:
                qa_list = []
                print(f"Aviso completando formulario: {e}")

            enviada = False
            if auto_enviar:
                try:
                    clic_envio = await bp.pulsar_segundo_postular(active_pg)
                    if clic_envio:
                        await asyncio.sleep(4.5)
                        bt_post = (await active_pg.inner_text("body") or "").lower()
                        ver_stats_post = await active_pg.locator("button:has-text('Ver estadísticas'), a:has-text('Ver estadísticas')").count() > 0
                        dlg_cerrado = (diag_mid["has_modal"] and await active_pg.locator("dialog[open], div.jobs-easy-apply-modal, form#formularioPreguntasOferta").count() == 0)
                        if any(w in bt_post for w in frases_done) or ver_stats_post or dlg_cerrado:
                            enviada = True
                except Exception:
                    pass

            if enviada:
                print(f"🟢 ¡POSTULACIÓN EXITOSA EN PESTAÑA ABIERTA!: {title[:55]}")
                actualizar_db_por_url(active_pg.url, "🟢 Postulado Confirmado", f"[Auditor pestañas] Enviado y confirmado ({len(qa_list)} preguntas)")
                postuladas_ok.append({"pg": active_pg, "url": active_pg.url, "titulo": title})
                resultados.append({"url": active_pg.url, "titulo": title, "estado": "postulada_ok", "motivo": "Postulada y confirmada desde pestaña abierta."})
            else:
                try:
                    bt_end = (await active_pg.inner_text("body") or "").lower()[:4000]
                except Exception:
                    bt_end = ""
                cod, mot = diagnosticar_bloqueo(active_pg.url, title, {**diag_mid, "body_low": bt_end})
                es_en_curso = (
                    cod in ["campos_obligatorios_pendientes", "sin_confirmacion_envio"]
                    and (diag_mid.get("editable_inputs", 0) > 0 or "myworkdayjobs" in active_pg.url.lower() or "pandape" in active_pg.url.lower())
                )
                if es_en_curso and len(postuladas_ok) < max_mantener:
                    print(f"📝 Formulario en curso con datos pendientes ({cod}): se MANTIENE abierta para completar/revisar.")
                    postuladas_ok.append({"pg": active_pg, "url": active_pg.url, "titulo": title})
                    resultados.append({"url": active_pg.url, "titulo": title, "estado": "incompleta_conservada", "codigo": cod, "motivo": mot})
                else:
                    print(f"⚠️ No confirmó envío ({cod}): {mot} -> Cerrando pestaña.")
                    resultados.append({"url": active_pg.url, "titulo": title, "estado": "no_postulable_cerrada", "codigo": cod, "motivo": mot})
                    await _cerrar_pestana_seguro(ctx, active_pg)

        # Dejar abiertas como máximo las últimas `max_mantener` (4) pestañas a las que postuló
        if len(postuladas_ok) > max_mantener:
            sobrantes = postuladas_ok[:-max_mantener]
            for item in sobrantes:
                try:
                    await item["pg"].close()
                except Exception:
                    pass
            postuladas_ok = postuladas_ok[-max_mantener:]

        # Resumen y diagnóstico de por qué no se pudo postular hasta 4 (si aplica)
        resumen = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "total_revisadas": len(resultados),
            "postuladas_exitosas": len(postuladas_ok),
            "urls_conservadas": [x["url"] for x in postuladas_ok],
            "detalle": resultados,
        }
        REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
        REPORT_JSON.write_text(json.dumps(resumen, ensure_ascii=False, indent=2), encoding="utf-8")

        print("\n==========================================================")
        print(f"📊 AUDITORÍA FINALIZADA: {len(resultados)} pestañas revisadas | {len(postuladas_ok)}/{max_mantener} postuladas mantenidas")
        if len(postuladas_ok) < max_mantener:
            print("🔍 ANÁLISIS DE POR QUÉ NO SE PUDO POSTULAR A LAS 4:")
            fallos = [r for r in resultados if r["estado"] == "no_postulable_cerrada"]
            for r in fallos:
                print(f"   • [{r.get('codigo')}] {r['titulo'][:45]} -> {r['motivo']}")
        print("==========================================================")

        if con_voz:
            if len(postuladas_ok) < max_mantener:
                causas = ", ".join(sorted({r.get("codigo", "bloqueo") for r in resultados if r["estado"] == "no_postulable_cerrada"})) or "sin nuevas postulaciones"
                bp.hablar(f"Auditoría terminada. Se revisaron {len(resultados)} pestañas, se postuló a {len(postuladas_ok)} y se cerraron las no válidas por: {causas}.", final=True)
            else:
                bp.hablar(f"Auditoría terminada. Postulé a {len(postuladas_ok)} pestañas y dejé abiertas las últimas {max_mantener}.", final=True)

        await browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AutoPostula — Auditor y Postulador de Pestañas Abiertas")
    parser.add_argument("--mantener", type=int, default=4, help="Máximo de pestañas postuladas a conservar (por defecto 4)")
    parser.add_argument("--solo-analizar", action="store_true", help="No pulsar Enviar automáticamente")
    parser.add_argument("--sin-voz", action="store_true", help="Desactivar locución Di")
    args = parser.parse_args()

    asyncio.run(auditar_y_postular_pestanas(
        max_mantener=args.mantener,
        auto_enviar=not args.solo_analizar,
        con_voz=not args.sin_voz,
    ))

