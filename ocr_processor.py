import requests
import base64
import re
import logging

logger = logging.getLogger(__name__)


def extract_text_google_vision(image_bytes, api_key):
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
            return "", f"HTTP ошибка: {response.status_code}"

        result = response.json()

        if 'error' in result:
            error = result['error']
            return "", f"API ошибка {error.get('code')}: {error.get('message')}"

        try:
            text = result['responses'][0]['fullTextAnnotation']['text']
            logger.info(f"Vision API распознал текст ({len(text)} символов)")
            return text, None
        except (KeyError, IndexError):
            try:
                annotations = result['responses'][0]['textAnnotations']
                if annotations:
                    text = annotations[0]['description']
                    return text, None
            except (KeyError, IndexError):
                pass
            return "", "Текст не найден на изображении"

    except requests.Timeout:
        return "", "Таймаут запроса к API"
    except requests.ConnectionError:
        return "", "Ошибка подключения к API"
    except Exception as e:
        return "", f"Неожиданная ошибка: {str(e)}"


def parse_sim_cards(text):
    results = []

    if not text:
        return results

    text = text.strip()

    patterns = {
        'iccid': re.compile(r'(89\d{16,18}(?:-\d)?)', re.IGNORECASE),
        'puk': re.compile(r'PUK\s*:?\s*(\d{8})', re.IGNORECASE),
        'pin': re.compile(r'PIN\s*:?\s*(\d{4})', re.IGNORECASE)
    }

    iccid_matches = [(m.start(), m.group(1)) for m in patterns['iccid'].finditer(text)]
    puk_matches = [(m.start(), m.group(1)) for m in patterns['puk'].finditer(text)]

    logger.info(f"Найдено: ICCID={len(iccid_matches)}, PUK={len(puk_matches)}")

    used_puks = set()

    for iccid_pos, iccid_val in iccid_matches:
        best_puk = None
        best_puk_dist = float('inf')
        best_puk_idx = -1

        for idx, (puk_pos, puk_val) in enumerate(puk_matches):
            if idx in used_puks:
                continue
            dist = abs(puk_pos - iccid_pos)
            if dist < best_puk_dist:
                best_puk_dist = dist
                best_puk = puk_val
                best_puk_idx = idx

        if best_puk:
            used_puks.add(best_puk_idx)
            results.append({
                'iccid': iccid_val,
                'puk': best_puk
            })

    return results


def format_results(results):
    if not results:
        return "❌ Данные не найдены. Попробуйте сделать более чёткое фото."

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


def process_image(image_bytes, api_key):
    raw_text, error = extract_text_google_vision(image_bytes, api_key)

    if error:
        return [], raw_text, error

    results = parse_sim_cards(raw_text)
    return results, raw_text, None
