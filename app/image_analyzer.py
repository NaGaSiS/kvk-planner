"""
image_analyzer.py
Módulo de análisis de imágenes del juego KingShot mediante Groq Vision API (GRATIS).
Modelo: qwen/qwen3.6-27b  (con capacidad de visión)
Extrae datos de aceleradores (speedups) y recursos (mochila) de capturas de pantalla.

API gratuita: https://console.groq.com  (sin tarjeta de crédito)
"""

import base64
import json
import logging
import re

logger = logging.getLogger(__name__)

GROQ_MODEL = "qwen/qwen3.8-27b"

# System prompt that suppresses Qwen's <think> reasoning blocks.
# /no_think is a Qwen3 directive; the plain-language instruction is a fallback.
SYSTEM_PROMPT = (
    "/no_think "
    "You are a JSON extractor for a mobile game assistant. "
    "Output ONLY a raw JSON object. No markdown, no explanation, no reasoning."
)


def _parse_time_to_minutes(time_str: str) -> int:
    """
    Convierte '10 día(s)4 h44 min' → minutos totales.
    Soporta '1d 5h 30m', '3h 20m', '45m', etc.
    """
    if not time_str:
        return 0

    total = 0
    s = time_str.lower().strip()
    for old, new in [
        ("día(s)", "d"), ("día", "d"), ("dias", "d"), ("days", "d"), ("day", "d"),
        ("horas", "h"), ("hora", "h"), ("hours", "h"), ("hour", "h"),
        ("minutos", "m"), ("minuto", "m"), ("minutes", "m"), ("minute", "m"), ("min", "m"),
        ("(s)", ""), (",", ""), (".", ""), ("(es)", ""),
    ]:
        s = s.replace(old, new)
    s = s.strip()

    m = re.search(r"(\d+(?:\.\d+)?)\s*d", s)
    if m:
        total += int(float(m.group(1))) * 1440
    m = re.search(r"(\d+(?:\.\d+)?)\s*h", s)
    if m:
        total += int(float(m.group(1))) * 60
    m = re.search(r"(\d+(?:\.\d+)?)\s*m(?!i)", s)
    if m:
        total += int(float(m.group(1)))

    if total == 0:
        m = re.search(r"(\d+)", s)
        if m:
            total = int(m.group(1))

    return total


def _parse_quantity(qty_str: str) -> int:
    """Convierte '1,042' o '1K' a entero."""
    if not qty_str:
        return 0
    s = str(qty_str).replace(",", "").replace(".", "").strip()
    if s.upper().endswith("K"):
        try:
            return int(float(s[:-1]) * 1000)
        except ValueError:
            return 0
    try:
        return int(s)
    except ValueError:
        return 0


def _find_last_json_object(text: str) -> dict:
    """
    Finds the LAST JSON object in a text string.
    Qwen models put <think>...</think> first, then the actual JSON at the end.
    We skip the thinking section and grab the last { ... } block.
    """
    # Find all positions of '{' in the text
    positions = [i for i, ch in enumerate(text) if ch == "{"]

    # Try each '{' from last to first, looking for a valid JSON object
    for start in reversed(positions):
        depth = 0
        in_string = False
        escape_next = False
        for i, ch in enumerate(text[start:], start=start):
            if escape_next:
                escape_next = False
                continue
            if ch == "\\" and in_string:
                escape_next = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start : i + 1]
                    try:
                        return json.loads(candidate)
                    except json.JSONDecodeError:
                        break  # Try the next '{' position

    raise ValueError(f"No valid JSON object found in response ({len(text)} chars)")


def _call_groq_vision(image_bytes: bytes, prompt: str, api_key: str, max_retries: int = 3) -> str:
    """Llama a la API de Groq con imagen en base64, devuelve texto completo.
    Usa qwen3.8-27b con /no_think para obtener JSON limpio sin bloques de razonamiento.
    Reintenta automáticamente si el modelo devuelve respuesta vacía.
    """
    import groq

    client = groq.Groq(api_key=api_key)
    image_b64 = base64.b64encode(image_bytes).decode("utf-8")

    mime_type = "image/png" if image_bytes[:4] == b"\x89PNG" else "image/jpeg"
    data_url = f"data:{mime_type};base64,{image_b64}"

    last_error = None
    for attempt in range(max_retries):
        try:
            completion = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": SYSTEM_PROMPT,
                    },
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": data_url}},
                        ],
                    },
                ],
                temperature=0,
                max_tokens=300,  # JSON output is ~100 tokens; 300 gives plenty of headroom
            )
            text = completion.choices[0].message.content
            if text and text.strip():
                return text
            logger.warning("Groq returned empty response on attempt %d, retrying...", attempt + 1)
            last_error = ValueError("Empty response from model")
        except Exception as e:
            last_error = e
            logger.warning("Groq call failed on attempt %d: %s", attempt + 1, e)

    raise last_error or ValueError("All retries failed")


def analyze_speedups_image(image_bytes: bytes, api_key: str) -> dict:
    """
    Analiza la pestaña 'Acelerar' de la mochila KingShot.
    Devuelve tiempos en minutos por categoría.
    """
    if not api_key:
        return {
            "success": False,
            "error": "API key de Groq no configurada. Obtén una gratis en console.groq.com",
            "general": 0, "construction": 0, "training": 0, "research": 0, "healing": 0,
            "confidence": 0,
        }

    prompt = (
        "This is a screenshot from the mobile game KingShot showing speedup items.\n"
        "CRITICAL INSTRUCTION: There are two possible screens the user might upload:\n"
        "1. SUMMARY SCREEN: A list format with text like 'Acelerador General' and total times like '10 dia(s) 4 h 44 min'.\n"
        "2. GRID SCREEN: The backpack view with a grid of many small blue arrow icons and numbers like 1,518.\n\n"
        "IF YOU SEE THE GRID SCREEN (#2):\n"
        "You CANNOT calculate the totals accurately from the grid. You MUST reject it and return EXACTLY this JSON:\n"
        '{"error": "wrong_screen"}\n\n'
        "IF YOU SEE THE SUMMARY SCREEN (#1):\n"
        "Extract the TOTAL TIME for each category directly.\n"
        "- general_speedup (Acelerador General)\n"
        "- construction_speedup (Acelerador de construcción)\n"
        "- training_speedup (Acelerador de Entrenamiento)\n"
        "- research_speedup (Acelerador de Investigación)\n"
        "- healing_speedup (Acelerador de Curación)\n\n"
        "Return EXACTLY this JSON format (replace values with real ones, use '0m' if missing):\n"
        '{"general_speedup":"10d 4h 44m","construction_speedup":"1d 18h 5m",'
        '"training_speedup":"9d 16h 31m","research_speedup":"9d 2h 32m",'
        '"healing_speedup":"0m","confidence":0.95}'
    )

    try:
        raw = _call_groq_vision(image_bytes, prompt, api_key)
        data = _find_last_json_object(raw)
        
        if "error" in data and data["error"] == "wrong_screen":
            return {
                "success": False,
                "error": "wrong_screen_error",
                "general": 0, "construction": 0, "training": 0, "research": 0, "healing": 0,
                "confidence": 0,
            }
        return {
            "success": True,
            "general": _parse_time_to_minutes(data.get("general_speedup", "0")),
            "construction": _parse_time_to_minutes(data.get("construction_speedup", "0")),
            "training": _parse_time_to_minutes(data.get("training_speedup", "0")),
            "research": _parse_time_to_minutes(data.get("research_speedup", "0")),
            "healing": _parse_time_to_minutes(data.get("healing_speedup", "0")),
            "confidence": float(data.get("confidence", 0.8)),
        }
    except Exception as e:
        logger.exception("Error analizando imagen de aceleradores con Groq")
        return {
            "success": False,
            "error": f"Error al analizar: {e}",
            "general": 0, "construction": 0, "training": 0, "research": 0, "healing": 0,
            "confidence": 0,
        }


def analyze_backpack_image(image_bytes: bytes, api_key: str) -> dict:
    """
    Analiza la pestaña 'Recursos' de la mochila KingShot.
    Detecta TrueGold, TrueGold Dust, TrueGold Template (Tempered Truegold).
    """
    if not api_key:
        return {
            "success": False,
            "error": "API key de Groq no configurada. Obtén una gratis en console.groq.com",
            "truegold": 0, "truegold_dust": 0, "truegold_template": 0, "confidence": 0,
        }

    prompt = (
        "This is an image of KingShot game resources. The user might have uploaded the full backpack or just a cropped image of specific items.\n"
        "Identify the quantities of the following specific items by their VISUAL appearance. If an item is NOT in the image, you MUST set its value to 0. Do NOT invent numbers.\n\n"
        "1. **truegold** (Truegold): A SOLID SHINY GOLD NUGGET cube with smooth metallic golden facets.\n"
        "2. **truegold_template** (Tempered Truegold): A GLOWING YELLOW translucent cube encased in a brown metal frame with spiked corners.\n"
        "3. **truegold_dust** (Truegold Dust): A dark tray filled with YELLOW GOLDEN POWDER/SAND.\n"
        "4. (Ignore) Lesser Truegold: A dark brown/grey rocky cube with orange glowing cracks.\n\n"
        "Read the white number shown on the bottom right of each matched icon.\n"
        "Return EXACTLY this JSON:\n"
        '{"truegold":7820,"truegold_template":505,"truegold_dust":1042,"confidence":0.95}\n'
        "Use 0 for items not present."
    )

    try:
        raw = _call_groq_vision(image_bytes, prompt, api_key)
        data = _find_last_json_object(raw)
        return {
            "success": True,
            "truegold": _parse_quantity(str(data.get("truegold", 0))),
            "truegold_dust": _parse_quantity(str(data.get("truegold_dust", 0))),
            "truegold_template": _parse_quantity(str(data.get("truegold_template", 0))),
            "confidence": float(data.get("confidence", 0.8)),
        }
    except Exception as e:
        logger.exception("Error analizando imagen de mochila con Groq")
        return {
            "success": False,
            "error": f"Error al analizar: {e}",
            "truegold": 0, "truegold_dust": 0, "truegold_template": 0, "confidence": 0,
        }
