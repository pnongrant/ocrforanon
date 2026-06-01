# ocr_processor.py
import requests
import base64
import re
import logging
from typing import List, Dict, Tuple

logger = logging.getLogger(__name__)


# -------------------- OCR --------------------

def extract_text_google_vision(image_bytes, api_key):
    """
    Возвращает:
      full_text: str
      vision_response: dict (полный JSON от Google Vision)
      error: str | None
    """
    try:
        encoded = base64.b64encode(image_bytes).decode('utf-8')
        url = f"https://vision.googleapis.com/v1/images:annotate?key={api_key}"

        payload = {
            "requests": [{
                "image": {"content": encoded},
                "features": [{"type": "TEXT_DETECTION", "maxResults": 1}],
                "imageContext": {"languageHints": ["en"]}
            }]
        }

        response = requests.post(url, json=payload, timeout=30)

        if response.status_code != 200:
            return "", None, f"HTTP ошибка: {response.status_code}"

        result = response.json()

        if 'error' in result:
            error = result['error']
            return "", result, f"API ошибка {error.get('code')}: {error.get('message')}"

        full_text = ""
        try:
            full_text = result['responses'][0]['fullTextAnnotation']['text']
        except (KeyError, IndexError):
            try:
                annotations = result['responses'][0]['textAnnotations']
                if annotations:
                    full_text = annotations[0]['description']
            except (KeyError, IndexError):
                pass

        if not full_text:
            return "", result, "Текст не найден"

        return full_text, result, None

    except requests.Timeout:
        return "", None, "Таймаут запроса"
    except Exception as e:
        return "", None, str(e)


# -------------------- Helpers --------------------

def _normalize_iccid(raw: str) -> str:
    s = re.sub(r'[\s\-]', '', raw)
    s = re.sub(r'\D', '', s)
    return s


def _bbox_center(vertices: List[Dict]) -> Tuple[float, float]:
    xs = [v.get('x', 0) for v in vertices]
    ys = [v.get('y', 0) for v in vertices]
    return (sum(xs) / max(len(xs), 1), sum(ys) / max(len(ys), 1))


def _bbox_bounds(vertices: List[Dict]) -> Tuple[int, int, int, int]:
    xs = [v.get('x', 0) for v in vertices]
    ys = [v.get('y', 0) for v in vertices]
    return min(xs), min(ys), max(xs), max(ys)


def _collect_tokens_with_boxes(vision_response: Dict) -> List[Dict]:
    """
    Возвращает список токенов:
    { "text": str, "x": float, "y": float, "min_x": int, "min_y": int, "max_x": int, "max_y": int }
    """
    out = []
    try:
        pages = vision_response["responses"][0]["fullTextAnnotation"]["pages"]
    except (KeyError, IndexError, TypeError):
        return out

    for page in pages:
        for block in page.get("blocks", []):
            for para in block.get("paragraphs", []):
                for word in para.get("words", []):
                    letters = [s.get("text", "") for s in word.get("symbols", [])]
                    wtext = "".join(letters).strip()
                    if not wtext:
                        continue
                    box = word.get("boundingBox", {}).get("vertices", [])
                    if not box:
                        continue
                    cx, cy = _bbox_center(box)
                    min_x, min_y, max_x, max_y = _bbox_bounds(box)
                    out.append({
                        "text": wtext,
                        "x": cx,
                        "y": cy,
                        "min_x": min_x,
                        "min_y": min_y,
                        "max_x": max_x,
                        "max_y": max_y
                    })
    return out


# -------------------- Coordinate parser --------------------

def parse_sim_cards_by_coordinates(vision_response: Dict) -> List[Dict]:
    """
    Основной парсер:
    1) Ищем ICCID-токены
    2) Ищем токены 'PUK' и ближайшие к ним 8-значные числа (справа/рядом)
    3) Привязываем ICCID к ближайшему PUK-кандидату с ограничением по Y
    """
    tokens = _collect_tokens_with_boxes(vision_response)
    if not tokens:
        return []

    # ICCID-кандидаты
    iccid_items = []
    for t in tokens:
        cleaned = _normalize_iccid(t["text"])
        if cleaned.startswith("89") and 18 <= len(cleaned) <= 20:
            iccid_items.append({
                "iccid": cleaned,
                "x": t["x"],
                "y": t["y"],
                "min_x": t["min_x"],
                "max_x": t["max_x"],
                "min_y": t["min_y"],
                "max_y": t["max_y"]
            })

    if not iccid_items:
        return []

    # Все 8-значные числовые токены
    num8 = []
    for t in tokens:
        txt = re.sub(r'\D', '', t["text"])
        if re.fullmatch(r'\d{8}', txt):
            num8.append({
                "value": txt,
                "x": t["x"],
                "y": t["y"],
                "min_x": t["min_x"],
                "max_x": t["max_x"],
                "min_y": t["min_y"],
                "max_y": t["max_y"]
            })

    # Метки PUK
    puk_labels = []
    for t in tokens:
        normalized = t["text"].upper().replace(" ", "")
        if normalized in ("PUK", "PUK1", "P.U.K", "PUK:"):
            puk_labels.append(t)

    # Привязка PUK-метка -> 8 цифр
    puk_items = []
    used_num_idx = set()

    for lbl in puk_labels:
        best_idx = None
        best_score = float('inf')

        for idx, n in enumerate(num8):
            if idx in used_num_idx:
                continue

            dx = n["x"] - lbl["x"]
            dy = abs(n["y"] - lbl["y"])

            if dx < -120:
                continue
            if dy > 70:
                continue

            score = abs(dx) + (dy * 2.0)
            if score < best_score:
                best_score = score
                best_idx = idx

        if best_idx is not None:
            used_num_idx.add(best_idx)
            n = num8[best_idx]
            puk_items.append({
                "puk": n["value"],
                "x": n["x"],
                "y": n["y"],
                "min_x": n["min_x"],
                "max_x": n["max_x"],
                "min_y": n["min_y"],
                "max_y": n["max_y"]
            })

    # Fallback: если мало PUK по меткам, добавим остальные 8-значные (кроме начинающихся на 89)
    if len(puk_items) < max(1, len(iccid_items) // 2):
        for n in num8:
            if n["value"].startswith("89"):
                continue
            if any(p["puk"] == n["value"] and abs(p["x"] - n["x"]) < 2 and abs(p["y"] - n["y"]) < 2 for p in puk_items):
                continue
            puk_items.append({
                "puk": n["value"],
                "x": n["x"],
                "y": n["y"],
                "min_x": n["min_x"],
                "max_x": n["max_x"],
                "min_y": n["min_y"],
                "max_y": n["max_y"]
            })

    # ICCID -> PUK по геометрии
    results = []
    used_puks = set()

    for ic in sorted(iccid_items, key=lambda z: (z["y"], z["x"])):
        best_j = None
        best_score = float('inf')

        for j, pk in enumerate(puk_items):
            if j in used_puks:
                continue

            dx = pk["x"] - ic["x"]
            dy = abs(pk["y"] - ic["y"])

            if dx < -180:
                continue
            if dy > 110:
                continue

            score = abs(dx) + (dy * 2.5)
            if score < best_score:
                best_score = score
                best_j = j

        if best_j is not None:
            used_puks.add(best_j)
            results.append({
                "iccid": ic["iccid"],
                "puk": puk_items[best_j]["puk"]
            })

    # дедуп
    uniq = []
    seen = set()
    for r in results:
        key = (r["iccid"], r["puk"])
        if key not in seen:
            seen.add(key)
            uniq.append(r)

    return uniq


# -------------------- Fallback line parser --------------------

def parse_sim_cards_by_lines(text: str) -> List[Dict]:
    results = []
    if not text:
        return results

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    iccid_re = re.compile(r'(89[\d\-\s]{16,24})')
    puk_label_re = re.compile(r'(?:P\s*U\s*K|PUK1?)\s*[:\-]?\s*(\d{8})', re.IGNORECASE)
    puk_plain_re = re.compile(r'\b(\d{8})\b')

    used_line_for_puk = set()

    for i, line in enumerate(lines):
        iccid_m = iccid_re.search(line)
        if not iccid_m:
            continue

        iccid = _normalize_iccid(iccid_m.group(1))
        if not (iccid.startswith("89") and 18 <= len(iccid) <= 20):
            continue

        puk = None
        puk_line_idx = None

        m = puk_label_re.search(line)
        if m:
            puk = m.group(1)
            puk_line_idx = i

        if not puk:
            for j in range(i + 1, min(i + 3, len(lines))):
                if j in used_line_for_puk:
                    continue
                m = puk_label_re.search(lines[j])
                if m:
                    puk = m.group(1)
                    puk_line_idx = j
                    break

        if not puk:
            tail = line[iccid_m.end():]
            m = puk_plain_re.search(tail)
            if m:
                candidate = m.group(1)
                if not candidate.startswith("89"):
                    puk = candidate
                    puk_line_idx = i

        if puk:
            if puk_line_idx is not None:
                used_line_for_puk.add(puk_line_idx)
            results.append({"iccid": iccid, "puk": puk})

    return results


# -------------------- Output formatters --------------------

def format_results(results):
    if not results:
        return "❌ Данные не найдены. Попробуйте более чёткое фото."

    output = f"✅ Найдено SIM: {len(results)}\n\n"
    for card in results:
        output += f"`{card['iccid']}` `{card['puk']}`\n"
    return output


def format_csv(results):
    if not results:
        return ""
    lines = ["ICCID,PUK"]
    for card in results:
        lines.append(f"{card['iccid']},{card['puk']}")
    return "\n".join(lines)


# -------------------- Main --------------------

def process_image(image_bytes, api_key):
    raw_text, vision_response, error = extract_text_google_vision(image_bytes, api_key)
    if error:
        return [], raw_text, error

    # 1) Координаты
    results = parse_sim_cards_by_coordinates(vision_response)

    # 2) Fallback: построчный парсер
    if not results:
        results = parse_sim_cards_by_lines(raw_text)

    return results, raw_text, None
