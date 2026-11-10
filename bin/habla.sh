#!/bin/bash
# ============================================================================
# Di - Dual Engine TTS (Edge TTS + Piper)
# Por defecto usa Edge TTS (nube, 324 voces, 75 idiomas)
# Usar -p | --piper para Piper (local, 41 voces)
# ============================================================================
DIR_PIPER="$HOME/piper"
AUDIO_TEMP="/tmp/lasc_sentinel_reporte.wav"
DEFAULT_VOICE_FILE="$DIR_PIPER/.default_voice"
VELOCIDAD_DEFAULT="1.65"

export PATH="$HOME/.local/bin:$PATH"
USER_UID="$(id -u)"
if [ -z "$XDG_RUNTIME_DIR" ] && [ -d "/run/user/$USER_UID" ]; then
    export XDG_RUNTIME_DIR="/run/user/$USER_UID"
fi
if [ -z "$DBUS_SESSION_BUS_ADDRESS" ] && [ -S "/run/user/$USER_UID/bus" ]; then
    export DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$USER_UID/bus"
fi
if [ -z "$PULSE_SERVER" ] && [ -S "/run/user/$USER_UID/pulse/native" ]; then
    export PULSE_SERVER="unix:/run/user/$USER_UID/pulse/native"
fi

ENGINE="edge"
VOICE_ARG=""
EDGE_VOICE=""
PIPER_MODEL=""
TEXT=""
GUARDAR_PERMANENTE=false
VELOCIDAD="$VELOCIDAD_DEFAULT"
INPUT_MODE="auto"
FINAL_MODE=false
SPEAKER="${DI_SPEAKER:-Antigravity}"
if [ "${DI_FINAL:-}" = "1" ] || [ "${DI_FINAL:-}" = "true" ]; then
    FINAL_MODE=true
fi
DI_INVOKED_AT="$(date +%s.%N)"

# Lee el portapapeles sin pasar su contenido por el parser del shell.
# Así se conservan de forma segura !, comillas, $, backticks y Markdown.
read_clipboard() {
    local value=""

    if command -v wl-paste >/dev/null 2>&1; then
        value=$(timeout 3s wl-paste --no-newline 2>/dev/null || true)
    fi
    if [ -z "$value" ] && command -v xclip >/dev/null 2>&1; then
        value=$(timeout 3s xclip -o -selection clipboard 2>/dev/null || true)
    fi
    if [ -z "$value" ] && command -v xsel >/dev/null 2>&1; then
        value=$(timeout 3s xsel --clipboard --output 2>/dev/null || true)
    fi

    printf '%s' "$value"
}

# Detectar SSH/headless
# Si hay SSH_CLIENT (Termius/conexión remota), modo sin ventana
[ -n "$SSH_CLIENT" ] && HEADLESS=true || HEADLESS=false

# ============================================================================
# Reproducir audio: VLC si hay GUI, aplay si es headless/SSH
# ============================================================================
play_audio() {
    local file="$1"
    if [ "$HEADLESS" = false ] && [ -x "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/play_vlc_managed.sh" ]; then
        local mode_flag="--progress"
        if [ "$FINAL_MODE" = true ]; then
            mode_flag="--final"
        fi
        DISPLAY="${DISPLAY:-:0.0}" "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/play_vlc_managed.sh" "$mode_flag" --invoked-at "$DI_INVOKED_AT" --speaker "$SPEAKER" "$file" "$TEXT" >/dev/null 2>&1
    elif command -v mpv >/dev/null 2>&1; then
        mpv --no-video --volume=100 "$file" > /dev/null 2>&1
    elif command -v paplay >/dev/null 2>&1; then
        paplay "$file" > /dev/null 2>&1
    elif command -v aplay >/dev/null 2>&1; then
        aplay "$file" > /dev/null 2>&1
    fi
}

# ============================================================================
# Edge TTS default voice per locale (primera voz femenina disponible)
# ============================================================================
declare -A EDGE_DEFAULT
EDGE_DEFAULT=(
  [af-ZA]="af-ZA-AdriNeural" [am-ET]="am-ET-AmehaNeural"
  [ar-AE]="ar-AE-FatimaNeural" [ar-BH]="ar-BH-LailaNeural" [ar-DZ]="ar-DZ-AminaNeural"
  [ar-EG]="ar-EG-SalmaNeural" [ar-IQ]="ar-IQ-RanaNeural" [ar-JO]="ar-JO-SanaNeural"
  [ar-KW]="ar-KW-NouraNeural" [ar-LB]="ar-LB-LaylaNeural" [ar-LY]="ar-LY-ImanNeural"
  [ar-MA]="ar-MA-MounaNeural" [ar-OM]="ar-OM-AyshaNeural" [ar-QA]="ar-QA-AmalNeural"
  [ar-SA]="ar-SA-ZariyahNeural" [ar-SY]="ar-SY-AmanyNeural" [ar-TN]="ar-TN-ReemNeural"
  [ar-YE]="ar-YE-MaryamNeural"
  [az-AZ]="az-AZ-BanuNeural" [bg-BG]="bg-BG-KalinaNeural"
  [bn-BD]="bn-BD-NabanitaNeural" [bn-IN]="bn-IN-TanishaaNeural"
  [bs-BA]="bs-BA-VesnaNeural" [ca-ES]="ca-ES-JoanaNeural"
  [cs-CZ]="cs-CZ-VlastaNeural" [cy-GB]="cy-GB-NiaNeural"
  [da-DK]="da-DK-ChristelNeural" [de-AT]="de-AT-IngridNeural"
  [de-CH]="de-CH-LeniNeural" [de-DE]="de-DE-KatjaNeural"
  [el-GR]="el-GR-AthinaNeural"
  [en-AU]="en-AU-NatashaNeural" [en-CA]="en-CA-ClaraNeural"
  [en-GB]="en-GB-SoniaNeural" [en-HK]="en-HK-YanNeural"
  [en-IE]="en-IE-EmilyNeural" [en-IN]="en-IN-NeerjaNeural"
  [en-KE]="en-KE-AsiliaNeural" [en-NG]="en-NG-EzinneNeural"
  [en-NZ]="en-NZ-MollyNeural" [en-PH]="en-PH-RosaNeural"
  [en-SG]="en-SG-LunaNeural" [en-TZ]="en-TZ-ImaniNeural"
  [en-US]="en-US-AriaNeural" [en-ZA]="en-ZA-LeahNeural"
  [es-AR]="es-AR-ElenaNeural" [es-BO]="es-BO-SofiaNeural"
  [es-CL]="es-CL-CatalinaNeural" [es-CO]="es-CO-SalomeNeural"
  [es-CR]="es-CR-MariaNeural" [es-CU]="es-CU-BelkysNeural"
  [es-DO]="es-DO-RamonaNeural" [es-EC]="es-EC-AndreaNeural"
  [es-ES]="es-ES-ElviraNeural" [es-GQ]="es-GQ-TeresaNeural"
  [es-GT]="es-GT-MartaNeural" [es-HN]="es-HN-KarlaNeural"
  [es-MX]="es-MX-DaliaNeural" [es-NI]="es-NI-YolandaNeural"
  [es-PA]="es-PA-MargaritaNeural" [es-PE]="es-PE-CamilaNeural"
  [es-PR]="es-PR-KarinaNeural" [es-PY]="es-PY-TaniaNeural"
  [es-SV]="es-SV-LorenaNeural" [es-US]="es-US-PalomaNeural"
  [es-UY]="es-UY-ValentinaNeural" [es-VE]="es-VE-PaolaNeural"
  [et-EE]="et-EE-AnuNeural" [fa-IR]="fa-IR-DilaraNeural"
  [fi-FI]="fi-FI-NooraNeural" [fil-PH]="fil-PH-BlessicaNeural"
  [fr-BE]="fr-BE-CharlineNeural" [fr-CA]="fr-CA-SylvieNeural"
  [fr-CH]="fr-CH-ArianeNeural" [fr-FR]="fr-FR-DeniseNeural"
  [ga-IE]="ga-IE-OrlaNeural" [gl-ES]="gl-ES-SabelaNeural"
  [gu-IN]="gu-IN-DhwaniNeural" [he-IL]="he-IL-HilaNeural"
  [hi-IN]="hi-IN-SwaraNeural" [hr-HR]="hr-HR-GabrijelaNeural"
  [hu-HU]="hu-HU-NoemiNeural" [id-ID]="id-ID-GadisNeural"
  [is-IS]="is-IS-GudrunNeural" [it-IT]="it-IT-ElsaNeural"
  [ja-JP]="ja-JP-NanamiNeural" [jv-ID]="jv-ID-SitiNeural"
  [ka-GE]="ka-GE-EkaNeural" [kk-KZ]="kk-KZ-AigulNeural"
  [km-KH]="km-KH-SreymomNeural" [kn-IN]="kn-IN-SapnaNeural"
  [ko-KR]="ko-KR-SunHiNeural" [lo-LA]="lo-LA-KeomanyNeural"
  [lt-LT]="lt-LT-OnaNeural" [lv-LV]="lv-LV-EveritaNeural"
  [mk-MK]="mk-MK-MarijaNeural" [ml-IN]="ml-IN-SobhanaNeural"
  [mn-MN]="mn-MN-YesuiNeural" [mr-IN]="mr-IN-AarohiNeural"
  [ms-MY]="ms-MY-YasminNeural" [mt-MT]="mt-MT-GraceNeural"
  [my-MM]="my-MM-NilarNeural" [nb-NO]="nb-NO-PernilleNeural"
  [ne-NP]="ne-NP-HemkalaNeural" [nl-BE]="nl-BE-DenaNeural"
  [nl-NL]="nl-NL-FennaNeural" [pl-PL]="pl-PL-ZofiaNeural"
  [ps-AF]="ps-AF-LatifaNeural" [pt-BR]="pt-BR-FranciscaNeural"
  [pt-PT]="pt-PT-RaquelNeural" [ro-RO]="ro-RO-AlinaNeural"
  [ru-RU]="ru-RU-SvetlanaNeural" [si-LK]="si-LK-ThiliniNeural"
  [sk-SK]="sk-SK-ViktoriaNeural" [sl-SI]="sl-SI-PetraNeural"
  [so-SO]="so-SO-UbaxNeural" [sq-AL]="sq-AL-AnilaNeural"
  [sr-RS]="sr-RS-SophieNeural" [su-ID]="su-ID-TutiNeural"
  [sv-SE]="sv-SE-SofieNeural" [sw-KE]="sw-KE-ZuriNeural"
  [sw-TZ]="sw-TZ-RehemaNeural" [ta-IN]="ta-IN-PallaviNeural"
  [ta-LK]="ta-LK-SaranyaNeural" [ta-MY]="ta-MY-KaniNeural"
  [ta-SG]="ta-SG-VenbaNeural" [te-IN]="te-IN-ShrutiNeural"
  [th-TH]="th-TH-PremwadeeNeural" [tr-TR]="tr-TR-EmelNeural"
  [uk-UA]="uk-UA-PolinaNeural" [ur-IN]="ur-IN-GulNeural"
  [ur-PK]="ur-PK-UzmaNeural" [uz-UZ]="uz-UZ-MadinaNeural"
  [vi-VN]="vi-VN-HoaiMyNeural"
  [zh-CN]="zh-CN-XiaoxiaoNeural" [zh-HK]="zh-HK-HiuGaaiNeural"
  [zh-TW]="zh-TW-HsiaoChenNeural"
  [zu-ZA]="zu-ZA-ThandoNeural"
)

# ============================================================================
# Piper model name by locale (para fallback Edge→Piper)
# ============================================================================
declare -A PIPER_BY_LOCALE
PIPER_BY_LOCALE=(
  [ar-JO]="ar_JO-kareem-medium" [ca-ES]="ca_ES-upc-medium"
  [cs-CZ]="cs_CZ-jirka-medium" [cy-GB]="cy_GB-gwawr-medium"
  [da-DK]="da_DK-talesyntese-medium" [de-DE]="de_DE-thorsten-medium"
  [el-GR]="el_GR-rapunzelina-medium" [en-GB]="en_GB-alba-medium"
  [en-US]="en_US-amy-medium" [es-ES]="es_ES-sharvard-medium"
  [es-MX]="es_MX-claude-high" [es-AR]="es_AR-daniela-high"
  [fa-IR]="fa_IR-rana-medium" [fi-FI]="fi_FI-harri-medium"
  [fr-FR]="fr_FR-siwis-medium" [hi-IN]="hi_IN-san-medium"
  [hu-HU]="hu_HU-anna-medium" [is-IS]="is_IS-ugla-medium"
  [it-IT]="it_IT-riccardo-medium" [ka-GE]="ka_GE-natia-medium"
  [kk-KZ]="kk_KZ-iseke-medium" [lb-LU]="lb_LU-marylu-medium"
  [lv-LV]="lv_LV-everita-medium" [ml-IN]="ml_IN-akhila-medium"
  [ne-NP]="ne_NP-hemkanta-medium" [nl-NL]="nl_NL-mls-medium"
  [no-NO]="no_NO-talesyntese-medium" [pl-PL]="pl_PL-mls-medium"
  [pt-PT]="pt_PT-tugao-medium" [pt-BR]="pt_BR-edson-medium"
  [ro-RO]="ro_RO-mihai-medium" [ru-RU]="ru_RU-irina-medium"
  [sk-SK]="sk_SK-lili-medium" [sl-SI]="sl_SI-artur-medium"
  [sr-RS]="sr_RS-serbian-medium" [sv-SE]="sv_SE-talesyntese-medium"
  [sw-CD]="sw_CD-femispeaker-medium" [tr-TR]="tr_TR-dfki-medium"
  [uk-UA]="uk_UA-ukrainian-medium" [vi-VN]="vi_VT-ngoc-medium"
  [zh-CN]="zh_CN-huayan-medium"
)

# ============================================================================
# Extract locale from a voice name (handles both Piper and Edge formats)
# ============================================================================
extract_locale() {
    local name="$1"
    # Piper: en_US-amy-medium → en-US
    # Edge: en-US-AriaNeural → en-US
    # Locale-only: en-US → en-US
    echo "$name" | sed 's/_.*$//; s/-[A-Za-z].*$//' | sed 's/_/-/'
}

# ============================================================================
# Resolve Edge TTS voice name from any input
# ============================================================================
resolve_edge_voice() {
    local input="$1"
    case "${input,,}" in
        colombia-mujer|colombia|colombiana|salome) echo "es-CO-SalomeNeural"; return ;;
        colombia-hombre|gonzalo)                  echo "es-CO-GonzaloNeural"; return ;;
        chile-mujer|chile|chilena|catalina)       echo "es-CL-CatalinaNeural"; return ;;
        chile-hombre|lorenzo)                     echo "es-CL-LorenzoNeural"; return ;;
        mexico-mujer|mexico|mexicana|dalia)       echo "es-MX-DaliaNeural"; return ;;
        mexico-hombre|jorge)                      echo "es-MX-JorgeNeural"; return ;;
        argentina-mujer|argentina|elena)          echo "es-AR-ElenaNeural"; return ;;
        argentina-hombre|tomas)                   echo "es-AR-TomasNeural"; return ;;
        espana-mujer|espana|espanola|elvira)      echo "es-ES-ElviraNeural"; return ;;
        espana-hombre|alvaro)                     echo "es-ES-AlvaroNeural"; return ;;
        usa-mujer|usa|eeuu|ingles|jenny)          echo "en-US-JennyNeural"; return ;;
        usa-hombre|guy)                           echo "en-US-GuyNeural"; return ;;
        italia|italiano|elsa)                     echo "it-IT-ElsaNeural"; return ;;
        china|chino|xiaoxiao)                     echo "zh-CN-XiaoxiaoNeural"; return ;;
    esac

    # Si ya es un nombre completo de Edge TTS
    if echo "$input" | grep -qE '^[a-z]{2}-[A-Z]{2}-[A-Za-z]+Neural$'; then
        echo "$input"
        return
    fi
    local locale
    locale=$(extract_locale "$input")
    local voice="${EDGE_DEFAULT[$locale]}"
    if [ -n "$voice" ]; then
        echo "$voice"
    else
        echo "es-CO-SalomeNeural"  # fallback predeterminado Colombia
    fi
}

# ============================================================================
# Parse arguments
# ============================================================================
PARSED_VOICE=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --codex)
            VOICE_ARG="chile-mujer"
            PARSED_VOICE=true
            SPEAKER="Codex"
            shift
            ;;
        --antigravity)
            VOICE_ARG="colombia-mujer"
            PARSED_VOICE=true
            SPEAKER="Antigravity"
            shift
            ;;
        --speaker)
            SPEAKER="$2"
            shift 2
            ;;
        -v|--voz|--voice) VOICE_ARG="$2"; PARSED_VOICE=true; shift 2 ;;
        -f|--final) FINAL_MODE=true; shift ;;
        --progress) FINAL_MODE=false; shift ;;
        -p|--piper) ENGINE="piper"; shift ;;
        -c|--clipboard) INPUT_MODE="clipboard"; shift ;;
        --paste|--stdin|-)
            INPUT_MODE="stdin"
            shift
            ;;
        --save) GUARDAR_PERMANENTE=true; shift ;;
        --headless) HEADLESS=true; shift ;;
        --gui) HEADLESS=false; shift ;;
        --list-edge) edge-tts --list-voices; exit 0 ;;
        --list-piper)
            echo "=== Voces Piper instaladas ==="
            ls "$DIR_PIPER"/*.onnx 2>/dev/null | sed 's/.*\///; s/\.onnx$//' || echo "No hay modelos Piper en $DIR_PIPER"
            exit 0 ;;
        -h|--help)
            echo "Di - Texto a Voz (Edge TTS + Piper)"
            echo ""
            echo "USO:"
            echo "  Di [voz] [opciones] <texto>"
            echo ""
            echo "OPCIONES:"
            echo "  -f, --final       Respuesta final: detiene de inmediato audios de avance en curso y habla de inmediato"
            echo "  --codex           Identidad Codex con voz chilena Catalina"
            echo "  --antigravity     Identidad Antigravity con voz colombiana Salome"
            echo "  -p, --piper       Usar motor local Piper (en vez de Edge TTS)"
            echo "  -c, --clipboard   Leer el texto completo desde el portapapeles"
            echo "  --paste, --stdin  Pegar texto después de iniciar Di; terminar con Ctrl+D"
            echo "  --save            Guardar audio permanentemente (solo Piper)"
            echo "  --headless        Forzar modo sin ventana (paplay, útil por SSH)"
            echo "  --gui             Forzar modo ventana (VLC)"
            echo "  --list-edge       Listar las 324 voces Edge TTS disponibles"
            echo "  --list-piper      Listar modelos Piper instalados"
            echo "  -h, --help        Mostrar esta ayuda"
            echo ""
            echo "MODO HEADLESS (SSH/Termius):"
            echo "  Se detecta automáticamente si hay SSH_CLIENT."
            echo "  Usa paplay para reproducir sin ventana gráfica."
            echo "  Forzar: Di --headless \"texto\" | Di --gui \"texto\""
            echo ""
            echo "EJEMPLOS:"
            echo '  Di "Hola mundo"                    # Edge TTS (default)'
            echo '  Di                                   # Lee todo el portapapeles (recomendado)'
            echo '  Di --clipboard                       # Igual, de forma explícita'
            echo '  Di --paste                           # Pega texto y termina con Ctrl+D'
            echo '  Di en-US "Hello world"             # Edge TTS inglés EEUU'
            echo '  Di es-CL "Hola Chile"              # Edge TTS chileno'
            echo '  Di --codex "Respuesta de Codex"    # Voz chilena + cola compartida'
            echo '  Di --antigravity "Respuesta"       # Voz colombiana + cola compartida'
            echo '  Dius "Good morning"                # Edge TTS (alias existente)'
            echo '  Di -p "Hola"                       # Piper voz default'
            echo '  Dius -p "Hello"                    # Piper voz EEUU'
            echo '  Di "1.5 Hola"                      # Velocidad 1.5x para Piper'
            echo '  Di --list-edge | less              # Explorar voces'
            echo ""
            echo "VOCES DISPONIBLES (75 idiomas):"
            for loc in "${!EDGE_DEFAULT[@]}"; do
                printf "  %-10s → %s\n" "$loc" "${EDGE_DEFAULT[$loc]}"
            done | sort
            exit 0
            ;;
        *)
            # Detectar si es argumento de voz (alias amigable o código de idioma)
            alias_arg="${1,,}"
            case "$alias_arg" in
                colombia*|chile*|mexico*|mexicana*|chilena*|colombiana*|argentina*|espana*|espanola*|usa*|eeuu*|italia*|italiano*|china*|chino*|salome|gonzalo|catalina|lorenzo|dalia|jorge|elena|tomas|elvira|alvaro|jenny|guy|elsa|xiaoxiao)
                    if [ "$PARSED_VOICE" = false ]; then
                        VOICE_ARG="$1"
                        PARSED_VOICE=true
                        shift
                        continue
                    fi
                    ;;
            esac
            if [ "$PARSED_VOICE" = false ] && (echo "$1" | grep -qE '^[a-z]{2}[_-][A-Z]{2}'); then
                VOICE_ARG="$1"
                PARSED_VOICE=true
                shift
            else
                break
            fi
            ;;
    esac
done

if [ "$INPUT_MODE" = "clipboard" ]; then
    TEXT=$(read_clipboard)
elif [ "$INPUT_MODE" = "stdin" ]; then
    if [ -t 0 ]; then
        printf '%s\n' "Pega el texto completo. Cuando termines, presiona Ctrl+D:" >&2
    fi
    TEXT=$(cat)
# Si el argumento es un archivo existente, leer su contenido
elif [ -n "$1" ] && [ -f "$1" ]; then
    TEXT=$(cat "$1")
elif [ -n "$*" ] && [ -f "$*" ]; then
    TEXT=$(cat "$*")
else
    TEXT="$*"
fi

# Si no hay texto en argumentos:
if [ -z "$TEXT" ]; then
    if [ ! -t 0 ]; then
        # Viene por tuberia/pipe (ej: echo "hola" | Di)
        TEXT=$(cat)
    else
        # Lee directamente lo que tengas copiado en el portapapeles (Ctrl+C)
        TEXT=$(read_clipboard)
    fi
fi

if [ -z "$TEXT" ]; then
    echo "[Di] No encontré texto. Copia una respuesta y ejecuta solamente: di" >&2
    exit 1
fi

# Serializa la síntesis de todos los clientes de Di. Esto preserva el orden de llegada
# entre Codex, Antigravity y terminal, y evita que compartan archivos temporales a la vez.
mkdir -p /tmp/di_vlc_queue
exec 9>/tmp/di_vlc_queue/producer.lock
flock -x 9

# Si es respuesta final, cortar de inmediato cualquier audio de avance/desarrollo en curso
# del MISMO agente. Nunca interrumpe al otro agente: la cola global les da turnos.
if [ "$FINAL_MODE" = true ] && [ "$HEADLESS" = false ] && [ -x "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/play_vlc_managed.sh" ]; then
    DISPLAY="${DISPLAY:-:0.0}" "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/play_vlc_managed.sh" --interrupt-progress "$DI_INVOKED_AT" "$SPEAKER" >/dev/null 2>&1 || true
fi

# Purificar y limpiar texto con el script inteligente
if [ -f "$HOME/purificar_texto.py" ]; then
    TEXT=$(printf '%s' "$TEXT" | python3.13 "$HOME/purificar_texto.py")
fi

# ============================================================================
# Resolve the voice name
# ============================================================================
if [ -n "$VOICE_ARG" ]; then
    if [ "$ENGINE" = "edge" ]; then
        EDGE_VOICE=$(resolve_edge_voice "$VOICE_ARG")
    else
        # Piper: check if the voice file exists, else fallback
        local_locale=$(extract_locale "$VOICE_ARG")
        PIPER_MODEL="${PIPER_BY_LOCALE[$local_locale]}"
        if [ -z "$PIPER_MODEL" ] || [ ! -f "$DIR_PIPER/$PIPER_MODEL.onnx" ]; then
            # Try the raw name
            PIPER_MODEL="$VOICE_ARG"
        fi
    fi
else
    # No voice specified - use default (Preferencia: Colombia -> Chile -> Mexico -> España -> Argentina)
    if [ "$ENGINE" = "edge" ]; then
        EDGE_VOICE="es-CO-SalomeNeural"
    else
        if [ -f "$DEFAULT_VOICE_FILE" ]; then
            PIPER_MODEL=$(cat "$DEFAULT_VOICE_FILE")
        else
            PIPER_MODEL=$(ls "$DIR_PIPER"/es_*.onnx "$DIR_PIPER"/*.onnx 2>/dev/null | head -1 | sed 's/.*\///; s/\.onnx$//')
        fi
    fi
fi

# ============================================================================
# MODO DRAMATICO: Pausas reales de silencio y velocidades localizadas
# ============================================================================
DRAMATIC_SCRIPT="$HOME/Documentos/MYCreIAtedAPPs/EcosystemServiceTech/scripts/sintetizar_dramatico.py"
if [[ "$TEXT" =~ \[(pausa|silencio|lento|rapido|vel) || "$TEXT" =~ \.{4,} ]] && [ -x "$DRAMATIC_SCRIPT" ]; then
    OUTPUT_WAV="/tmp/tts_dram_$$.wav"
    P_MOD="${PIPER_MODEL:-es_MX-claude-high.onnx}"
    [[ "$P_MOD" != *.onnx ]] && P_MOD="${P_MOD}.onnx"
    python3 "$DRAMATIC_SCRIPT" \
        --text "$TEXT" \
        --engine "$ENGINE" \
        --voice "$EDGE_VOICE" \
        --piper-bin "$DIR_PIPER/piper/piper/piper" \
        --piper-model "$DIR_PIPER/$P_MOD" \
        --output "$OUTPUT_WAV" > /dev/null 2>&1

    if [ -f "$OUTPUT_WAV" ] && [ -s "$OUTPUT_WAV" ]; then
        play_audio "$OUTPUT_WAV"
        exit 0
    fi
fi

# ============================================================================
# EDGE TTS ENGINE
# ============================================================================
if [ "$ENGINE" = "edge" ]; then
    RATE_ARG=""
    if [[ "$TEXT" =~ ^([0-9]\.[0-9]+)\ (.*)$ ]]; then
        VEL="${BASH_REMATCH[1]}"
        TEXT="${BASH_REMATCH[2]}"
        PERCENT=$(awk "BEGIN {printf \"%+d\", ($VEL - 1.0) * 100}")
        RATE_ARG="+$PERCENT%"
    fi

    # Reproduccion completa por VLC
    # (Bypass de streaming mpv desactivado a favor de VLC con ventana y temporizador)

    OUTPUT_WAV="/tmp/tts_edge_$$.wav"
    INPUT_TXT="/tmp/tts_edge_in_$$.txt"
    printf '%s' "$TEXT" > "$INPUT_TXT"
    # Cadena de voces en orden de preferencia
    FALLBACK_VOICES=("$EDGE_VOICE" "es-CO-SalomeNeural" "es-CL-CatalinaNeural" "es-MX-DaliaNeural" "es-ES-ElviraNeural" "es-AR-ElenaNeural")
    SUCCESS=false

    for v in "${FALLBACK_VOICES[@]}"; do
        [ -z "$v" ] && continue
        edge-tts --voice "$v" --file "$INPUT_TXT" $RATE_ARG --write-media "$OUTPUT_WAV" > /dev/null 2>&1
        if [ -f "$OUTPUT_WAV" ] && [ -s "$OUTPUT_WAV" ]; then
            SUCCESS=true
            break
        fi
    done
    rm -f "$INPUT_TXT" 

    if [ "$SUCCESS" = true ]; then
        cp -f "$OUTPUT_WAV" "/tmp/di_latest_response.wav" 2>/dev/null
        printf '%s' "$TEXT" > "/tmp/di_latest_text.txt" 2>/dev/null
        play_audio "/tmp/di_latest_response.wav"
        rm -f "$OUTPUT_WAV"
        exit 0
    else
        # Fallback a Piper si Edge falla
        echo "[Di] Edge TTS falló, usando Piper..."
        if [ -z "$PIPER_MODEL" ]; then
            PIPER_MODEL=$(ls "$DIR_PIPER"/es_*.onnx "$DIR_PIPER"/*.onnx 2>/dev/null | head -1 | sed 's/.*\///; s/\.onnx$//')
        fi
        ENGINE="piper"
    fi
fi

# ============================================================================
# PIPER ENGINE
# ============================================================================
if [ "$ENGINE" = "piper" ]; then
    if [ -f "$DIR_PIPER/piper/piper" ]; then
        PIPER_CMD="$DIR_PIPER/piper/piper"
    elif [ -f "$DIR_PIPER/piper" ]; then
        PIPER_CMD="$DIR_PIPER/piper"
    else
        echo "Error: No se encuentra Piper en ~/piper"
        exit 1
    fi

    MODELO="$DIR_PIPER/$PIPER_MODEL.onnx"
    if [ ! -f "$MODELO" ]; then
        echo "Error: Modelo no encontrado: $MODELO"
        echo "Voces Piper disponibles:"
        ls "$DIR_PIPER"/*.onnx 2>/dev/null | sed 's/.*\///; s/\.onnx$//'
        exit 1
    fi

    # Velocidad en caliente
    if [[ "$TEXT" =~ ^([0-9]\.[0-9]+)\ (.*)$ ]]; then
        VELOCIDAD="${BASH_REMATCH[1]}"
        TEXT="${BASH_REMATCH[2]}"
    fi

    # --save
    if [[ "$TEXT" =~ ^--save,?\ *(.*)$ ]]; then
        GUARDAR_PERMANENTE=true
        TEXT="${BASH_REMATCH[1]}"
    fi

[ -z "$TEXT" ] && exit 0

# Sanitizar texto para lectura de voz natural
TEXT=$(echo "$TEXT" | sed -E "s/\\(([^)]+)\\)/, \\1, /g")
TEXT=$(echo "$TEXT" | sed -E "s/\\*\\*([^*]+)\\*\\*/\\1/g; s/\\*([^*]+)\\*/\\1/g")
TEXT=$(echo "$TEXT" | sed -E "s/^[[:space:]]*#+[[:space:]]*//g")
TEXT=$(echo "$TEXT" | sed -E "s/^[[:space:]]*[•\\*-][[:space:]]+/ /g")
TEXT=$(echo "$TEXT" | tr "\"" " " | tr "'" " ")

    DRAMATIC_SCRIPT="$HOME/Documentos/MYCreIAtedAPPs/EcosystemServiceTech/scripts/sintetizar_dramatico.py"
    if [[ "$TEXT" =~ \[(pausa|silencio|lento|rapido) || "$TEXT" =~ \.{4,} ]] && [ -x "$DRAMATIC_SCRIPT" ]; then
        python3 "$DRAMATIC_SCRIPT" --text "$TEXT" --model "$MODELO" --output "$AUDIO_TEMP" --speed "$VELOCIDAD" --piper-bin "$PIPER_CMD" > /dev/null 2>&1
    else
        echo "$TEXT" | "$PIPER_CMD" --model "$MODELO" --output_file "$AUDIO_TEMP" --length_scale "$VELOCIDAD" > /dev/null 2>&1
    fi

    if [ -f "$AUDIO_TEMP" ]; then
        if [ "$GUARDAR_PERMANENTE" = true ]; then
            FECHA_KEY=$(date +"%Y%m%d_%H%M%S")
            AUDIO_FINAL="$HOME/piper/guardados/reporte_${FECHA_KEY}.wav"
            mkdir -p "$HOME/piper/guardados"
            mv "$AUDIO_TEMP" "$AUDIO_FINAL"
            echo -e "\n💾 Audio guardado: $AUDIO_FINAL"
            if [ "$HEADLESS" = true ]; then
                paplay "$AUDIO_FINAL" > /dev/null 2>&1
            else
                vlc "$AUDIO_FINAL" > /dev/null 2>&1 &
            fi
        else
            play_audio "$AUDIO_TEMP"
        fi
    fi
fi

exit 0
