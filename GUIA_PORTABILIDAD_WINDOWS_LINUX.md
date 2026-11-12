# Guía Técnica Cruzada: AutoPostula Linux Debian ↔ Windows 11

> **Repositorios hermanos:**
> - 🐧 **Edición Linux Debian / Ubuntu:** [lascsentinel-commits/AutoPostula-Linux-Debian](https://github.com/lascsentinel-commits/AutoPostula-Linux-Debian)
> - 🪟 **Edición Windows 11:** [lascsentinel-commits/AutoPostula](https://github.com/lascsentinel-commits/AutoPostula)

Esta guía documenta **todas las funciones, botones, endpoints y flujos nuevos** incorporados en `AutoPostula`, junto con sus adaptaciones entre **Linux Debian/Ubuntu** y **Windows 11**, para que cualquier usuario o agente de IA pueda mantener ambos repositorios sincronizados.

---

## 1. Panel Web NOC (`web_dashboard.py`, puerto `8087`)

1. **Servidor HTTP Multihilo (`ThreadingHTTPServer` + Timeout + PNA):**
   - Evita que sockets TCP pre-conectados de Brave/Chrome bloqueen el dashboard o las llamadas del Agente Investigador (`timeout(15)` en `setup()` + cabeceras `Access-Control-Allow-Private-Network: true`).
   - Funciona idéntico en **Windows 11** y **Linux**.

2. **Indicador Vivo `#bot-live-badge` + `/api/bot-status`:**
   - Muestra en tiempo real si `bucle_pantalla_interactivo.py` está activo (`🟢 Bot Activo`) o inactivo (`⚪ Bot Inactivo · N válidas TI en cola`).
   - En Linux consulta `pgrep -f`; en Windows usa `PowerShell Get-CimInstance Win32_Process`.
   - `POST /api/start-interactive-loop` y `POST /api/start-headless-batch` validan antes que haya ofertas válidas (`empty_queue: True` si hay `0`), avisando por toast y por voz en lugar de salir en silencio.

3. **Visor «🔍 Revisar Válidas (14 Portales)» (`validas14Dialog` + `GET /api/ofertas-14-portales`):**
   - **Dónde aparece:**
     - Botón principal en la barra superior (`#btn-revisar-14`, junto a los botones del bot).
     - Cuadro resumen con botón `🔍 Ver 14` dentro de la tarjeta KPI **`Portales Reales Activos (14)`**.
     - Acceso directo desde el widget flotante de Brave (`#autopostula-modal-validas14`) abriendo `http://localhost:8087/#validas14`.
   - **Qué muestra y permite hacer:**
     - **Número total en grande (`#v14-big-count`)** de la categoría seleccionada y resumen de todas las categorías.
     - **Pestañas para separar:**
       1. `✅ Válidas Disponibles` *(cola exacta del bot automático)*
       2. `🟢 Postulados` *(confirmadas en navegador)*
       3. `🟡 Pausadas / CAPTCHA / Login` *(requieren intervención humana o ClaveÚnica)*
       4. `🔴 Ofertas Cerradas` *(expiradas o 404)*
       5. `📦 Otras en Cola IA` *(ofertas filtradas fuera de cola automática)*
     - **Lista desplegable/seleccionable (`<select id="v14-select">`):** al elegir una oferta muestra el panel derecho con todos sus datos (empresa, cargo, portal, fechas, ubicación, sueldo/asignación, notas, descripción completa, respuestas IA guardadas).
     - **Acciones directas por oferta:**
       - `🌐 Abrir Oferta en Navegador` (`url_oferta` directa si existe).
       - `🤖 Postular #ID en Brave` (`POST /api/start-single-offer` -> lanza `--id <ID> --segundos 30` en Linux o Windows).
       - `✉️ Correo directo` (`mailto:` si tiene `email_reclutador`).

4. **Corrección en `get_stats()` (`Postuladas Confirmadas` vs `Cerradas`):**
   - Antes `LIKE '%Oferta%'` sumaba por error `🔴 Oferta Cerrada / Expirada` dentro de `Abiertas / Confirmadas`. Ahora se reportan por separado (`postuladas_activas` y `cerradas_total`).

5. **Lanzamiento y Cierre Multiplataforma:**
   - `_launch_bot_process(extra_args)`: en Linux usa `bot_pantalla.sh` (o `python3`), en Windows usa `sys.executable` directamente con `CREATE_NEW_PROCESS_GROUP` (sin `DISPLAY` ni `.Xauthority`).
   - `_stop_bot_processes()`: en Linux `pkill -SIGTERM -f`, en Windows `taskkill /PID ... /T /F`.
   - `_open_url_browser(url)`: usa `brave-browser` en Linux si existe o `webbrowser.open(url)` en Windows/genérico.

---

## 2. Widget Flotante y Automatización en Pantalla (`bucle_pantalla_interactivo.py`, Widget `v4.9`)

1. **Watchdog de Latido (`10s`) y Limpieza al Salir:**
   - Python envía latido cada `2s`; si el navegador deja de recibir latido por `> 10s`, el widget autoelimina `#autopostula-root-container` para nunca quedarse congelado diciendo `En Marcha`.
   - Si la cola está vacía (`total == 0`) o recibe `SIGINT`/`SIGTERM`, limpia todos los widgets abiertos en Brave/Chrome/Edge.

2. **Botones del Modal Flotante (`v4.9`):**
   - **`⏸️ PAUSAR / ▶️ REANUDAR BOT`** y presets `[15s] [30s] [60s] [2m]` + límite de pestañas (`- / +`).
   - **`🔊 Leer Preguntas y Respuestas: ON/OFF`** (`#autopostula-modal-readqa`): lee por voz cada pregunta detectada (inputs, radios, selects) y su respuesta antes de enviar.
   - **`🚀 Enviar Postulación Ahora`**: omite la espera restante.
   - **`⬅️ Link anterior`** y **`⏭️ Siguiente oferta`**: salto inmediato incluso dentro de pausa `SIN ENVIAR`, verificación final `PASO 7` o espera de login.
   - **`🕵️ Agente investigador`**: envía nota + snapshot del DOM (`errors`, `buttons`, `inputs`, `bodySnippet`) al endpoint `POST /api/agente-investigador` inmediatamente desde Python (evitando bloqueos de CSP/Mixed-Content en sitios HTTPS).
   - **`🧠 Ver / Editar Memoria (Q&A)`** y **`🔍 Revisar Válidas (14 Portales)`**: abren `/memoria` y `/#validas14` respectivamente.
   - **`🛑 Detener Bot por Completo`**: detención total y limpieza de widgets.

3. **Espera Post-Envío de `35s` + Aviso `"Cerrando"` 5s Antes:**
   - [`_esperar_post_envio_y_cerrar`](./bucle_pantalla_interactivo.py): tras confirmar envío, anuncia *"Ya postulamos a {empresa}, esperaré 35 segundos para cerrar la pestaña"*, cuenta 35s en tiempo real (`OK Xs`), dice *"Cerrando"* faltando 5s y cierra la pestaña sin perder protección en pausa.

4. **Compatibilidad Windows 11 en Voz, Foco y Rutas:**
   - **Voz (`hablar`):** en Linux usa `/home/dhamit/.local/bin/Di` (`edge-tts` + VLC); en Windows 11 usa `bin\Di.cmd` / `bin\di.py` si existe o `System.Speech` de PowerShell como respaldo nativo.
   - **Foco de ventana:** `wmctrl -a Brave` en Linux; `(New-Object -ComObject WScript.Shell).AppActivate('Brave')` en Windows.
   - **CV PDF:** configurable vía variable de entorno `CV_MASTER_PDF` o archivo local `cv.pdf` junto al proyecto.
   - **Agente Investigador en Windows (`_disparar_investigacion`):** busca `agy` en `PATH` (`shutil.which("agy")`). Si el usuario en Windows usa otro CLI/IDE de agente, puede apuntar la variable o adaptar esa línea en `web_dashboard.py`.

---

## 3. Portales Mapeados y Detección de Estados

- **Detección de avisos cerrados / 404 (`FRASES_CERRADA` + `_tabs_tienen_cerrada`):** detecta avisos finalizados, páginas 404 (`No podemos encontrar esta página`, `esta página no existe`, `Ver el aviso finalizado`) incluso durante la pausa `SIN ENVIAR`, sin confundirlas con muros de login (`_parece_login_pagina`).
- **FirstJob:** manejo de `/usuarios/ingresar` (sin confundirlo con catálogo), Google OAuth automático, detección de `"¡Felicidades! Postulaste correctamente, te deseamos mucho éxito."` y `"Ya estás postulando."`.
- **Laborum Chile:** `salarioPretendido = 0` (sin confundir con RUT), botón `Postulación rápida`, detección de `Postulado el DD/MM/YYYY` / `Ver estadísticas`, y flujo automatizado de actualización de CV PDF vía Jobi (`Actualizar mi CV` -> subir PDF -> `Reemplazar contenido`).
- **Computrabajo:** confirmación en `/candidate/postapply` (`Te postulaste correctamente`).
- **Workday / Pandape / Get on Board / LinkedIn:** flujo multipaso Workday (`Pasos 2..5` + exclusión `beecatcher`), Pandape (`/ApplyCT/`), Get on Board (`Ver tu postulación`), y respeto estricto de CAPTCHAs/Turnstile (`🛡️ Captcha / Verificación Manual`).
