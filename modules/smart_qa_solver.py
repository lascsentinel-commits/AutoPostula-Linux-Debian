"""
Módulo de Resolución Inteligente y Contextual de Preguntas de Reclutadores con LLM (Gemini 2.5 Flash)
AutoPostula v4.0 — Duoc UC
"""

import json
import os
import re

try:
    from google import genai
    from google.genai import types
    GENAI_AVAILABLE = True
except Exception:
    GENAI_AVAILABLE = False

# Cargar .env de la raíz de AutoPostula si existe
_env_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
if os.path.exists(_env_file):
    with open(_env_file, "r", encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")


def limpiar_texto_pregunta(raw_text: str) -> str:
    """Elimina textos basura de banners, conteo de caracteres y avisos de portales."""
    t = raw_text or ""
    t = re.sub(r"Los usuarios Premium tienen.*", "", t, flags=re.DOTALL | re.IGNORECASE)
    t = re.sub(r"En base a la información de tu currículum.*", "", t, flags=re.DOTALL | re.IGNORECASE)
    t = re.sub(r"Obténlas con Premium.*", "", t, flags=re.DOTALL | re.IGNORECASE)
    t = re.sub(r"Máximo \d+[\.,]?\d* caracteres.*", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\(ingresados:\s*\d+\)", "", t, flags=re.IGNORECASE)
    t = re.sub(r"^Pregunta\s*\d+\s*", "", t, flags=re.IGNORECASE)
    return t.strip()

SYSTEM_PROMPT_DHANI = """Eres el redactor y asistente de postulación oficial para Dhani Amit Elgueta Ríos.
Tu tarea es responder las preguntas de los formularios de postulación laboral técnica en Chile de manera impecable, directa, concisa y persuasiva, usando exclusivamente los datos reales del candidato.

PERFIL Y ANTECEDENTES REALES DE DHANI AMIT ELGUETA RÍOS:
- Nombre: Dhani Amit Elgueta Ríos | Edad: 26 años | Fecha de Nacimiento: 11 de febrero del 2000 | Teléfono: +56 9 4850 8528 | Correo: correof1553@gmail.com / dh.elgueta@duocuc.cl
- Residencia: Las Condes, Región Metropolitana (con total disponibilidad de traslado diurno hacia cualquier comuna de Santiago).
- Carrera: Ingeniería en Infraestructura y Plataformas Tecnológicas (y Técnico en Conectividad y Redes) en Duoc UC Sede Antonio Varas (jornada vespertina, 4to año / 8vo semestre de egreso).
- Año que Cursas: 4to año (cuarto año, 8vo semestre de egreso). Cuando cualquier pregunta mencione año, carrera o situación académica, es OBLIGATORIO indicar explícitamente "4to año (cuarto año)".
- Horario y Disponibilidad: Estudia en vespertino (lunes 19:00 hrs online, viernes 19:00 hrs presencial y sábados 08:00 a 12:00 hrs presencial), por lo que cuenta con disponibilidad diurna completa e inmediata (40 a 45 hrs semanales de lunes a viernes, jornada laboral completa presencial o híbrida).
- Plazo Crítico de Formalización (15 de Noviembre): Por normativa administrativa de Duoc UC, es OBLIGATORIO consolidar e inscribir formalmente el convenio de práctica de mínimo 360 horas ANTES DEL 15 DE NOVIEMBRE de 2026 (cierre del semestre actual); posterior a esa fecha se exige matricular y pagar un semestre extra solo para la práctica. El inicio presencial de funciones puede ser inmediato o en diciembre/enero sin problema, siempre que el trámite de inscripción quede cerrado antes del 15 de noviembre.
- Horas y Duración de Práctica: 360 horas cronológicas mínimas requeridas por Duoc UC (aprox. 2,5 a 3 meses; si preguntan estrictamente por un mínimo de 6 meses, la respuesta es NO, aclarando que requiere mínimo 360 hrs y convalidación con convenio antes del 15 de noviembre para extender hasta enero).
- Seguro Escolar: CUMPLE cabalmente; cuenta con Seguro Escolar de Accidentes emitido por Duoc UC bajo la Ley N° 16.744, vigente hasta fines de enero de 2027 para toda la práctica.
- Carta de Autorización de Práctica: SÍ, cuenta con la Carta de Autorización y presentación de práctica profesional emitida y timbrada por Duoc UC, lista para entrega inmediata.
- Renta / Asignación: $0 (Acorde a la asignación o movilización que tenga contemplada la empresa para alumnos en práctica profesional). Si el campo pide exclusivamente un número entero, responde 0.
- Ley de Inclusión (21.015): No requiere ningún ajuste ni apoyo adicional para el desempeño normal y eficiente de las funciones del cargo.
- Expectativas de Práctica: Integrarse activamente al área técnica de TI para aportar con soluciones prácticas en redes, servidores, cloud y soporte, aprender de la dinámica operativa de la empresa y consolidar experiencia profesional de alto impacto.
- Competencias Técnicas Clave:
  * Redes y Conectividad: TCP/IP, VLANs, switching, routing, fibra óptica, enlaces microondas, pfSense, Cisco ASA.
  * Sistemas y Cloud: Linux (Debian, Ubuntu, RedHat), Windows Server, Active Directory, Azure AD, AWS, GCP, Terraform, Ansible, Docker.
  * Ciberseguridad y Monitoreo: Wazuh SIEM, Suricata, Zeek, Zabbix, Prometheus, Grafana, OpenTelemetry, hardening de sistemas.
  * Soporte TI y Continuidad: Aranda, GLPI, osTicket, hardware de servidores, laptops, continuidad operacional bajo SLAs.

REGLAS OBLIGATORIAS:
1. Responde de forma DIRECTA, en primera persona ("Sí, cuento con...", "Tengo total disponibilidad...", "Espero...").
2. No te desvíes de la pregunta específica:
   - Si preguntan si puede asistir de forma presencial a Quilicura o cualquier comuna de la RM, responde afirmativamente: "Sí, resido en Las Condes y cuento con total disponibilidad y facilidad de traslado para asistir de forma presencial a [comuna]."
   - Si preguntan por disponibilidad horaria o jornada, o por fecha de inicio (ej. "¿Desde cuándo puedes comenzar?" o "¿Disponibilidad para comenzar en diciembre?"), responde SIEMPRE afirmativamente sobre el horario/fecha, PERO aclara explícitamente la necesidad administrativa: "Cuento con total disponibilidad (de lunes a viernes en jornada diurna completa, para iniciar de inmediato o en la fecha de diciembre que soliciten); solo necesito señalar que por cierre administrativo en Duoc UC debemos consolidar formalmente el convenio de práctica de mínimo 360 horas antes del 15 de noviembre para convalidarla este semestre y evitar tener que pagar un semestre extra adicional en horario de verano."
   - Si preguntan qué espera de la práctica profesional, responde enfocado en aportar al equipo TI, resolver problemas reales de infraestructura/soporte y consolidar su aprendizaje profesional.
   - Si preguntan si cuenta con carta de autorización de práctica, responde afirmativamente: "Sí, cuento con la Carta de Autorización y presentación de práctica profesional emitida y timbrada por Duoc UC, lista para entrega inmediata."
   - Si piden el número de horas de práctica, responde directamente las 360 horas cronológicas requeridas por Duoc UC con total disposición a extenderlas si el proyecto lo requiere.
   - Si preguntan por situación académica, carrera o en qué año estás (ej. "¿Título o carrera que cursas actualmente y en qué año?"), responde SIEMPRE especificando el cuarto año: "Ingeniería en Infraestructura y Plataformas Tecnológicas, 4to año (cuarto año, 8vo semestre de egreso) en Duoc UC Sede Antonio Varas."
   - Si preguntan por licencia de conducir, responde: "No cuento con licencia de conducir por el momento, pero tengo excelente conectividad y facilidad de traslado en la Región Metropolitana."
   - Si piden carta de presentación o motivación, redacta 3-4 líneas formales, directas y motivadas hacia el cargo de TI y la empresa.
3. Devuelve ÚNICAMENTE el texto que debe ir escrito en el formulario, sin comillas adicionales.
4. PROHIBICIÓN ESTRICTA DE "N/A" O "NO APLICA": Bajo NINGUNA circunstancia respondas "N/A", "No aplica", "Ninguna" o dejes vacío cuando pregunten por motivación, qué te motiva a postular a la empresa, expectativas o desafíos. Siempre debes redactar una respuesta formal, elocuente y motivada en primera persona, destacando el valor de la empresa, su infraestructura/tecnología y tu compromiso de aportar continuidad operacional y excelencia técnica.
"""


def obtener_perfil_activo() -> dict:
    """Carga perfil_alumno.json de la raíz del proyecto si existe."""
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    p_path = os.path.join(root_dir, "perfil_alumno.json")
    if os.path.exists(p_path):
        try:
            with open(p_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and "datos_personales" in data:
                    return data
        except Exception as e:
            print(f"Aviso al cargar perfil_alumno.json: {e}")
    return {}


def obtener_system_prompt() -> str:
    """Devuelve el system prompt adaptado al alumno configurado en perfil_alumno.json o Dhani por defecto."""
    p = obtener_perfil_activo()
    if not p:
        return SYSTEM_PROMPT_DHANI

    dp = p.get("datos_personales", {})
    sa = p.get("situacion_academica", {})
    cp = p.get("condiciones_practica", {})
    clr = p.get("condiciones_legales_y_renta", {})
    pc = p.get("perfil_profesional_y_competencias", {})
    mg = p.get("motivacion_general", {})

    nombre = dp.get("nombre_completo", "Candidato")
    tel = dp.get("telefono", "")
    mail = dp.get("correo_contacto", "")
    comuna = dp.get("comuna_residencia", "Región Metropolitana")
    licencia = dp.get("licencia_conducir", "No cuenta")

    carrera = sa.get("carrera", "Técnica / Ingeniería")
    institucion = sa.get("institucion", "Duoc UC")
    sede = sa.get("sede", "")
    año = sa.get("año_cursada", "4to año")
    semestre = sa.get("semestre_actual", "Último semestre")
    jornada = sa.get("jornada_estudio", "Vespertino")

    horas = cp.get("horas_cronologicas_requeridas", 360)
    seguro = "CUMPLE cabalmente (Ley 16.744)" if cp.get("seguro_escolar_ley_16744", True) else "En trámite"
    carta = "SÍ, disponible para entrega inmediata" if cp.get("carta_autorizacion_practica", True) else "En trámite"
    dispo_jornada = cp.get("jornada_disponible", "Jornada completa diurna")

    renta = clr.get("pretension_renta", 0)
    inclusion = clr.get("comentario_inclusion", "No requiere ningún ajuste adicional.")

    stack = pc.get("stack_tecnologico_clave", "")
    pitch = mg.get("pitch_motivacional", "")

    return f"""Eres el redactor y asistente de postulación oficial para {nombre}.
Tu tarea es responder las preguntas de los formularios de postulación laboral técnica en Chile de manera impecable, directa, concisa y persuasiva, usando exclusivamente los datos reales del candidato.

PERFIL Y ANTECEDENTES REALES DE {nombre.upper()}:
- Nombre: {nombre} | Teléfono: {tel} | Correo: {mail}
- Residencia: {comuna} (con total disponibilidad de traslado diurno en la RM).
- Carrera: {carrera} en {institucion} {sede} (jornada {jornada}, {año} / {semestre}).
- Año que Cursas: {año} ({semestre}). Cuando cualquier pregunta mencione año, carrera o situación académica, es OBLIGATORIO indicar explícitamente "{año}".
- Horario y Disponibilidad: {dispo_jornada}.
- Horas de Práctica: {horas} horas cronológicas requeridas por {institucion}.
- Seguro Escolar: {seguro}.
- Carta de Autorización de Práctica: {carta}.
- Renta / Asignación: ${renta} (Acorde a la asignación que tenga contemplada la empresa para alumnos en práctica). Si el campo pide exclusivamente un número entero, responde 0.
- Ley de Inclusión (21.015): {inclusion}
- Competencias Técnicas Clave: {stack}
- Motivación: {pitch}

REGLAS OBLIGATORIAS:
1. Responde de forma DIRECTA, en primera persona ("Sí, cuento con...", "Tengo total disponibilidad...", "Espero...").
2. No te desvíes de la pregunta específica.
3. Devuelve ÚNICAMENTE el texto que debe ir escrito en el formulario, sin comillas adicionales.
4. PROHIBICIÓN ESTRICTA DE "N/A" O "NO APLICA": Bajo NINGUNA circunstancia respondas "N/A", "No aplica", "Ninguna" o dejes vacío cuando pregunten por motivación, qué te motiva a postular a la empresa, expectativas o desafíos. Siempre debes redactar una respuesta formal, elocuente y motivada en primera persona.
"""



def _cargar_memoria_aprendida() -> list[dict]:
    try:
        from database import obtener_respuestas_aprendidas
        return obtener_respuestas_aprendidas()
    except Exception:
        return []


def _buscar_en_memoria(pregunta_raw: str) -> str | None:
    try:
        from database import buscar_respuesta_aprendida
        limpia = limpiar_texto_pregunta(pregunta_raw)
        return buscar_respuesta_aprendida(limpia)
    except Exception:
        return None


def resolver_preguntas_con_gemini_en_lote(preguntas: list[dict], empresa: str = "la empresa", cargo: str = "Práctica TI") -> dict[int, str]:
    """Resuelve en una sola llamada a Gemini 2.5 Flash / 3.5 Flash todas las preguntas de un formulario en lote,
    dando PRIORIDAD ABSOLUTA a las respuestas aprendidas/modificadas por el usuario en memoria."""
    if not preguntas:
        return {}

    resultado_final = {}
    preguntas_para_llm = []

    # 1. Revisar memoria aprendida primero para cada pregunta
    for idx, p in enumerate(preguntas):
        q_raw = p.get("pregunta", "")
        mem_ans = _buscar_en_memoria(q_raw)
        if mem_ans:
            resultado_final[idx] = mem_ans.replace("{empresa}", empresa)
        else:
            preguntas_para_llm.append((idx, p))

    if not preguntas_para_llm:
        return resultado_final

    if not GENAI_AVAILABLE or not GEMINI_API_KEY:
        for idx, p in preguntas_para_llm:
            resultado_final[idx] = resolver_pregunta_reclutador_fallback(p["pregunta"], empresa)
        return resultado_final

    client = genai.Client(api_key=GEMINI_API_KEY)

    # Inyectar contexto de memoria actual para mantener coherencia en preguntas similares
    memoria_lista = _cargar_memoria_aprendida()
    memoria_txt = ""
    if memoria_lista:
        pares = [{"pregunta": m["pregunta_clave"], "respuesta_actual": m["respuesta"]} for m in memoria_lista[:25]]
        memoria_txt = "\n\nMEMORIA DE RESPUESTAS ACTUALES DEL CANDIDATO (Úsalas como referencia prioritaria si la pregunta trata del mismo tema):\n" + json.dumps(pares, ensure_ascii=False, indent=2)

    mapa_indices = {str(pos): orig_idx for pos, (orig_idx, _) in enumerate(preguntas_para_llm)}
    prompt = (
        f"Empresa: {empresa}\n"
        f"Cargo: {cargo}\n"
        + memoria_txt
        + "\n\nPreguntas del formulario a responder:\n"
        + json.dumps({str(pos): p.get("pregunta", "") for pos, (_, p) in enumerate(preguntas_para_llm)}, ensure_ascii=False, indent=2)
        + "\n\nDevuelve ÚNICAMENTE un objeto JSON donde cada clave sea el índice como string (\"0\", \"1\", ...) y el valor sea la respuesta exacta para ese campo."
    )

    config = types.GenerateContentConfig(
        system_instruction=obtener_system_prompt(),
        temperature=0.2,
        response_mime_type="application/json"
    )

    for modelo in ["gemini-3.5-flash-lite", "gemini-3.5-flash", "gemini-2.5-flash"]:
        try:
            resp = client.models.generate_content(model=modelo, contents=prompt, config=config)
            data = json.loads(resp.text)
            llm_out = {}
            if isinstance(data, list):
                for pos, val in enumerate(data):
                    if str(pos) in mapa_indices:
                        llm_out[mapa_indices[str(pos)]] = str(val).strip()
            elif isinstance(data, dict):
                for pos_str, orig_idx in mapa_indices.items():
                    v = data.get(pos_str) or data.get(int(pos_str))
                    if v:
                        llm_out[orig_idx] = str(v).strip()

            if len(llm_out) > 0:
                for orig_idx, p in preguntas_para_llm:
                    q_text = p.get("pregunta", "").lower()
                    ans = llm_out.get(orig_idx) or resolver_pregunta_reclutador_fallback(p.get("pregunta", ""), empresa)
                    if any(w in q_text for w in ["motiva", "por qué", "por que", "interés", "interes", "expectativa"]):
                        if ans.strip().lower() in ["n/a", "no aplica", "ninguna", "ninguno", "na"] or len(ans.strip()) < 15:
                            ans = f"Me motiva enormemente realizar mi práctica profesional en {empresa} por su liderazgo, dinamismo y su despliegue de infraestructura tecnológica de vanguardia. Como estudiante de 4to año de Ingeniería en Infraestructura y Plataformas Tecnológicas en Duoc UC, busco aportar activamente en las áreas de redes, sistemas y continuidad operacional, aprendiendo de su equipo de alto nivel mientras resuelvo desafíos reales con excelencia."
                    resultado_final[orig_idx] = ans
                return resultado_final
        except Exception as e:
            print(f"Aviso Gemini {modelo}: {e}")

    # Fallback si falla la llamada
    for idx, p in preguntas_para_llm:
        resultado_final[idx] = resolver_pregunta_reclutador_fallback(p.get("pregunta", ""), empresa)
    return resultado_final


def resolver_pregunta_reclutador(pregunta_raw: str, empresa: str = "la empresa", tipo_input: str = "text") -> str:
    """Resuelve una pregunta individual dando prioridad a la memoria aprendida del usuario, luego Gemini y finalmente fallback."""
    if tipo_input != "radio_boolean" and tipo_input != "number":
        mem_ans = _buscar_en_memoria(pregunta_raw)
        if mem_ans:
            return mem_ans.replace("{empresa}", empresa)

    if GENAI_AVAILABLE and GEMINI_API_KEY and tipo_input != "radio_boolean":
        try:
            client = genai.Client(api_key=GEMINI_API_KEY)
            config = types.GenerateContentConfig(
                system_instruction=obtener_system_prompt(),
                temperature=0.2,
                max_output_tokens=300
            )
            for modelo in ["gemini-3.5-flash-lite", "gemini-3.5-flash", "gemini-2.5-flash"]:
                try:
                    resp = client.models.generate_content(
                        model=modelo,
                        contents=f"Empresa: {empresa}\nPregunta: {pregunta_raw}",
                        config=config
                    )
                    txt = resp.text.strip()
                    if txt:
                        return txt
                except Exception:
                    pass
        except Exception:
            pass

    return resolver_pregunta_reclutador_fallback(pregunta_raw, empresa, tipo_input)


def resolver_pregunta_reclutador_fallback(pregunta_raw: str, empresa: str = "la empresa", tipo_input: str = "text") -> str:
    limpia = limpiar_texto_pregunta(pregunta_raw)
    p = limpia.lower()

    perf = obtener_perfil_activo()
    dp = perf.get("datos_personales", {}) if perf else {}
    sa = perf.get("situacion_academica", {}) if perf else {}
    cp = perf.get("condiciones_practica", {}) if perf else {}
    clr = perf.get("condiciones_legales_y_renta", {}) if perf else {}
    mg = perf.get("motivacion_general", {}) if perf else {}

    # Datos activos (de perfil_alumno.json si existe, o Dhani por defecto)
    tel_activo = dp.get("telefono", "+56 9 4850 8528")
    mail_activo = dp.get("correo_contacto", "correof1553@gmail.com / dh.elgueta@duocuc.cl")
    comuna_activa = dp.get("comuna_residencia", "Las Condes, Región Metropolitana (con excelente conectividad en metro y locomoción hacia todas las comunas de Santiago)")
    horas_activas = str(cp.get("horas_cronologicas_requeridas", "360"))
    carrera_activa = sa.get("carrera", "Ingeniería en Infraestructura y Plataformas Tecnológicas")
    inst_activa = sa.get("institucion", "Duoc UC")
    sede_activa = sa.get("sede", "Sede Antonio Varas")
    año_activo = sa.get("año_cursada", "4to año (cuarto año, 8vo semestre de egreso)")
    renta_activa = str(clr.get("pretension_renta", "0"))

    # 1. Teléfono, celular, correo, contacto
    if re.search(r"\b(tel[eé]fono|celular|correo|email|e-mail|contacto|whatsapp|datos de contacto)\b", p):
        return f"{tel_activo} | {mail_activo}"

    # 1.5 Edad y fecha de nacimiento
    if re.search(r"\b(edad|a[ñn]os tienes|fecha de nacimiento|cumplea[ñn]os|naciste|nacimiento)\b", p):
        if tipo_input == "number":
            return "26"
        if "comuna" in p or "tel" in p or "contacto" in p:
            return f"Tengo 26 años (nacido el 11 de febrero del 2000), resido en {comuna_activa} y mi número de contacto es {tel_activo}."
        return "Tengo 26 años (nacido el 11 de febrero del 2000)."

    # 2. Comuna de residencia / Dónde vives
    if re.search(r"\b(comuna|residencia|vives|d[oó]nde vives|domicilio|sector de residencia)\b", p):
        if tipo_input == "radio_boolean":
            return "si"
        return f"{comuna_activa}."

    # 3. Ley de Inclusión / Ajustes razonables / Apoyos
    if re.search(r"\b(inclusi[oó]n|ajuste|apoyo razonable|discapacidad|adaptaci[oó]n)\b", p):
        if tipo_input == "radio_boolean":
            return "no"
        return "No requiero ningún ajuste ni apoyo adicional para el desempeño normal y eficiente de las funciones del cargo."

    # 4. Pretensión de renta / Asignación económica / Sueldo / Bonificación
    if re.search(r"\b(renta|sueldo|pretensi[oó]n|asignaci[oó]n|remuneraci[oó]n|bonificaci[oó]n|monto|estipendio|cu[aá]nto esperas)\b", p):
        if tipo_input == "number":
            return renta_activa
        return f"${renta_activa} (Acorde a la asignación o movilización que tenga contemplada la empresa para alumnos en práctica profesional)."

    # 5. Seguro escolar de accidentes (Requisito Ley 16.744)
    if re.search(r"\b(seguro|accidente|16\.?744|cobertura m[eé]dica)\b", p):
        if tipo_input == "radio_boolean":
            return "si"
        return f"Sí, cuento con Seguro Escolar de Accidentes vigente otorgado por {inst_activa} bajo la Ley 16.744 con cobertura activa hasta fines de enero de 2027 para la totalidad de las {horas_activas} horas de práctica profesional."

    # 6. Horas de práctica / Semanas / Duración / Extensión
    if re.search(r"\b(horas?|semanas?|duraci[oó]n|cu[aá]nto tiempo|extender|posibilidad de extender)\b", p):
        if tipo_input == "number":
            return horas_activas
        return f"{horas_activas} horas cronológicas requeridas por {inst_activa} (equivalente a aprox. 9 semanas a jornada completa). Cuento con total disposición y posibilidad de extender el periodo según las necesidades operacionales del equipo."

    # 7. Formación académica / Carrera / Casa de estudios / Institución / Año
    if re.search(r"\b(carrera|casa de estudio|instituci[oó]n|universidad|instituto|formaci[oó]n|acad[eé]mica|estudios?|qu[eé] estudias|titulaci[oó]n|a[ñn]o)\b", p):
        return f"{carrera_activa}, {año_activo} en {inst_activa} {sede_activa}."

    # 8. Fecha de inicio / Cuándo puedes comenzar / Disponibilidad
    if re.search(r"\b(disponibilidad|fecha de inicio|cu[aá]ndo puedes|comenzar|iniciar|partir|incorporar|horario|diciembre|enero|verano)\b", p):
        if tipo_input == "radio_boolean":
            return "si"
        return "Disponibilidad diurna completa e inmediata (40 a 45 horas semanales de lunes a viernes, para iniciar de inmediato o en diciembre/enero según requieran). Como detalle importante de gestión académica, en Duoc UC necesitamos consolidar formalmente el convenio de práctica de mínimo 360 horas antes del 15 de noviembre, ya que después de esa fecha tendría que pagar un semestre extra adicional para validarla en horario de verano."

    # 9. Licencia de conducir
    if re.search(r"\b(licencia|conducir|clase b|veh[ií]culo)\b", p):
        if tipo_input == "radio_boolean":
            return "si"
        return "Sí, cuento con Licencia de Conducir Clase B al día."

    # 10. Motivación por la empresa / ¿Por qué quieres trabajar aquí?
    if re.search(r"\b(motiva|por qu[eé]|inter[eé]s|postulas|por que te gustar[ií]a)\b", p):
        empresa_clean = empresa if empresa and "empresa" not in empresa.lower() else "su organización"
        return (
            f"Me motiva enormemente realizar mi práctica profesional en {empresa_clean} para aplicar mis competencias "
            f"técnicas en infraestructura, redes, servidores y ciberseguridad. Cuento con experiencia laboral en soporte corporativo en BDO, "
            f"alta proactividad, capacidad de resolución de incidentes bajo SLAs y compromiso absoluto con la continuidad operacional."
        )

    # 11. Experiencia técnica en TI / Soporte / Redes / Servidores / Cloud
    if re.search(r"\b(experiencia|conocimientos?|redes|servidores?|soporte|infraestructura|linux|cloud|seguridad)\b", p):
        return (
            "Experiencia práctica administrando servidores Linux (RHEL, Ubuntu) y Windows Server con Active Directory y GPOs; "
            "configuración de redes Cisco (OSPF, VLANs, switching); monitoreo con Zabbix y Grafana; soporte TI corporativo en BDO "
            "resolviendo incidentes N1 a N3 bajo SLAs, y ciberseguridad con herramientas como Wazuh, Suricata y Zeek."
        )

    # 12. Preguntas booleanas genéricas (Sí / No)
    if tipo_input == "radio_boolean":
        if re.search(r"\b(incompatibilidad|impedimento|familiar|conflicto|penales|parientes?)\b", p):
            return "no"
        return "si"

    # Fallback general para preguntas abiertas no contempladas
    return (
        "Estudiante de último semestre de Ingeniería en Infraestructura y Plataformas Tecnológicas en Duoc UC (vespertino). "
        "Cuento con 360 horas de práctica respaldadas por Seguro Escolar vigente (Ley 16.744), disponibilidad diurna inmediata y experiencia "
        "en soporte TI corporativo, redes Cisco y administración de servidores."
    )


def resolver_seleccion_dropdown(opciones: list[str], pregunta_contexto: str = "") -> str:
    """Determina la mejor opción para un elemento <select>."""
    p = pregunta_contexto.lower()
    
    if any(k in p for k in ["estudio", "nivel", "educaci", "formaci"]):
        for opt in opciones:
            o_l = opt.lower()
            if any(k in o_l for k in ["técnico profesional", "superior en curso", "universitaria en curso", "superior incompleta", "en curso", "universitario"]):
                return opt
        for opt in opciones:
            if "superior" in opt.lower() or "técnico" in opt.lower():
                return opt
                
    if any(k in p for k in ["situaci", "estado de estudio"]):
        for opt in opciones:
            if any(k in opt.lower() for k in ["en curso", "estudiante", "cursando", "egresado"]):
                return opt

    if any(k in p for k in ["regi"]):
        for opt in opciones:
            if any(k in opt.lower() for k in ["metropolitana", "santiago"]):
                return opt

    if any(k in p for k in ["comuna"]):
        for opt in opciones:
            if any(k in opt.lower() for k in ["las condes", "santiago", "providencia"]):
                return opt

    for opt in opciones:
        if opt.strip().lower() in ["sí", "si"]:
            return opt

    return opciones[0] if opciones else ""
