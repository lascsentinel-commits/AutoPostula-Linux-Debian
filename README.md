# 🐧 AutoPostula · Edición Linux Base Debian / Ubuntu

> **💻 Sistema Operativo Compatible:** **Linux Base Debian** (Debian 12+, Ubuntu 22.04/24.04+, Xubuntu, Linux Mint, Kali Linux) con entorno gráfico X11/Wayland.  
> **🪟 ¿Buscas la versión para Windows 11?** Mantuvimos intacto el repositorio separado de Windows en: [lascsentinel-commits/AutoPostula](https://github.com/lascsentinel-commits/AutoPostula).

Sistema de postulación asistida con IA sobre pantalla real para ofertas de **Prácticas Profesionales y Empleos Técnicos** en los principales portales laborales de Chile (**FirstJob, Trabajando.cl, Computrabajo, Laborum, ChileTrabajos, DuocLaboral, LinkedIn, Prácticas Chile**).

### ✨ Capacidades Destacadas (Edición Linux Debian)
- 👁️ **Visualización en Vivo (Brave CDP puerto `9222`):** Ves cada pestaña abierta y cada respuesta escrita en pantalla real sin bloqueos de Cloudflare.
- 🧠 **Memoria Activa de Respuestas en Tiempo Real (`/memoria`):** Cualquier edición que hagas en pantalla se guarda automáticamente tras ~1.2s incluso en pausa, con panel web CRUD en `http://localhost:8087/memoria` para crear, modificar o borrar preguntas y respuestas.
- 🔊 **Voz Neural Nativa `Di` (`edge-tts` + Cola FIFO en VLC):** Narración en vivo de cada pregunta y respuesta con prioridad `--final` (`bin/habla.sh` + `bin/play_vlc_managed.sh`).
- ⏱️ **Ventanita Flotante de Control:**
  - `[ ⏸️ Pausar Bot ]` / `[ ▶️ Reanudar Bot ]`
  - Selectores de espera dinámicos `[ 15s ]`, `[ 30s ]`, `[ 60s ]`, `[ 2m ]`
  - `[ 🚀 Enviar Postulación Ahora ]` y `[ 🧠 Ver / Editar Memoria (Q&A) ]`
  - `[ 🛑 Detener Bot por Completo ]`
- 🛡️ **Verificación Estricta:** Prohibición total de cerrar pestañas si no existe confirmación visual de éxito en pantalla.

---

## ⚡ Instalación Rápida en Debian / Ubuntu (1 Comando)

```bash
git clone https://github.com/lascsentinel-commits/AutoPostula-Linux-Debian.git
cd AutoPostula-Linux-Debian
chmod +x instalar_debian.sh
./instalar_debian.sh
```

---

## 🪟 Nota sobre Windows 11

AutoPostula funciona de forma nativa en **Windows 11**:

### Opción A: 1-Clic con Script por Lotes (Recomendado)
1. Clona el repositorio en Windows:
   ```cmd
   git clone https://github.com/lascsentinel-commits/AutoPostula.git
   cd AutoPostula
   ```
2. Haz doble clic sobre **`iniciar_windows.bat`** (o ejecuta `.\iniciar_windows.ps1` en PowerShell).
3. El lanzador configurará el entorno virtual `venv`, instalará dependencias, levantará Brave o Chrome en el puerto 9222 y arrancará el bot.

### Opción B: WSL2 (Ubuntu en Windows 11)
Si prefieres Linux dentro de Windows 11, puedes usar WSL2 con WSLg:
```bash
wsl
cd AutoPostula
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python3 bucle_pantalla_interactivo.py
```

### Opción C: Contenedor Docker
```bash
docker compose up --build
```

---

## 🐧 Ejecución en Linux

### 1. Clonar el Repositorio
```bash
git clone https://github.com/lascsentinel-commits/AutoPostula.git
cd AutoPostula
```

### 2. Instalar Dependencias
```bash
pip install playwright google-genai
playwright install chromium
```

### 3. Configurar API Key de Gemini
Obtén tu clave gratuita en [Google AI Studio](https://aistudio.google.com/):
```bash
cp .env.example .env
# Edita .env y añade tu clave:
# GEMINI_API_KEY=tu_clave_de_gemini
```

---

## 🖥️ Cómo Ejecutar en Pantalla

### Paso 1: Abrir Brave con Depuración Remota (Puerto 9222)
Asegúrate de que Brave esté cerrado y lánzalo con el puerto de depuración:
```bash
brave --remote-debugging-port=9222 &
```
*(Inicia sesión manualmente en tus portales preferidos: FirstJob, Trabajando.cl, Computrabajo, etc.)*

### Paso 2: Lanzar el Bucle Interactivo con Pausa de Revisión
```bash
./bot_pantalla.sh --segundos 30
```
- El bot tomará las ofertas pendientes, abrirá cada una en una pestaña, rellenará las preguntas y te dará 30 segundos de revisión antes de enviar.
- Si necesitas más tiempo, haz clic en **`[ ⏸️ Pausar ]`** en el widget flotante inferior izquierdo.

---

## 📋 Adaptar para otro Alumno (Plantilla de Perfil)

Revisa el archivo **[`CUESTIONARIO_ALUMNO_PRACTICAS.md`](./CUESTIONARIO_ALUMNO_PRACTICAS.md)** y completa la plantilla **[`perfil_alumno_template.json`](./perfil_alumno_template.json)** con tus datos:
1. **Datos Personales:** Nombre, RUT, teléfono, comuna, correo de contacto.
2. **Situación Académica:** Carrera, institución, sede, año de carrera (ej. *4to año*), semestre.
3. **Condiciones de Práctica:** Horas cronológicas requeridas (ej. 360 hrs), seguro escolar Ley 16.744, carta de autorización al día.
4. **Competencias y Motivación:** Áreas de interés y stack tecnológico para personalizar las respuestas abiertas.

---

## 🌐 Portales Soportados
- **FirstJob** (`firstjob.me`)
- **Trabajando.cl** (`trabajando.cl`)
- **Computrabajo** (`computrabajo.cl`)
- **Laborum** (`laborum.cl`)
- **ChileTrabajos** (`chiletrabajos.cl`)
- **Prácticas Chile / Servicio Civil** (`practicaschile.cl`)
