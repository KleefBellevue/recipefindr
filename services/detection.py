"""Vision Analyzer agent: turns a photo into a list of ingredients.

Two methods:
  * "vision" - a Groq vision LLM looks at the photo (recognizes far more foods, reads labels)
  * "yolo"   - the original YOLOv8 object detector (runs locally, but only knows ~10 foods)
"""
import base64
import importlib.util
import io
import ipaddress
import logging
import socket
from urllib.parse import urljoin, urlparse

import requests
from PIL import Image, ImageOps, UnidentifiedImageError

from . import config
from .llm import LLMError, clean_ingredients, get_client, parse_ingredient_list

# Optional: lets the server read iPhone .HEIC photos.
try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:
    pass


log = logging.getLogger("uvicorn.error")  # shows up in the server terminal


class DetectionError(Exception):
    pass


# The food classes YOLOv8 (COCO dataset) can recognize.
COCO_FOOD = {"banana", "apple", "sandwich", "orange", "broccoli", "carrot", "hot dog", "pizza", "donut", "cake"}

VISION_PROMPT = """You are the Vision Analyzer for RecipeFindr, a cooking assistant.
List every distinct food ingredient you can see in this photo of a fridge, pantry, or counter.
- Use short, generic grocery names that work in a recipe search (e.g. "eggs", "milk", "cheddar cheese", "spinach").
- Read packaging labels when they are legible.
- Skip non-food things (shelves, containers, appliances) unless you can tell what food is inside.
- Do not guess at items you cannot see.
Respond with ONLY a JSON object: {"ingredients": ["item", "item", ...]}
If you see no food, respond with {"ingredients": []}"""

_yolo_model = None


def yolo_installed() -> bool:
    return importlib.util.find_spec("ultralytics") is not None


def load_image(data: bytes) -> Image.Image:
    try:
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img)  # respect phone rotation
        return img.convert("RGB")
    except (UnidentifiedImageError, OSError) as exc:
        raise DetectionError("That file doesn't look like an image we can read. Try a JPG or PNG.") from exc


def _require_public_host(url: str) -> None:
    """Block links to private/internal addresses (prevents server-side request forgery)."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise DetectionError("Image links must start with http:// or https://")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(parsed.hostname, port, proto=socket.IPPROTO_TCP)}
    except (socket.gaierror, UnicodeError) as exc:
        raise DetectionError("Couldn't find that website. Check the link.") from exc
    if not addresses or any(not ipaddress.ip_address(a.split("%")[0]).is_global for a in addresses):
        raise DetectionError("That link isn't allowed. Use a public image link.")


def fetch_image_url(url: str) -> bytes:
    """Download an image from a link (the old version's only input method)."""
    url = (url or "").strip()
    try:
        for _ in range(4):  # follow up to 3 redirects, re-checking each hop
            _require_public_host(url)
            with requests.get(
                url, stream=True, timeout=15, allow_redirects=False, headers={"User-Agent": "RecipeFindr/2.0"}
            ) as resp:
                if resp.is_redirect:
                    url = urljoin(url, resp.headers.get("location", ""))
                    continue
                resp.raise_for_status()
                chunks, total = [], 0
                for chunk in resp.iter_content(64 * 1024):
                    total += len(chunk)
                    if total > config.MAX_UPLOAD_BYTES:
                        raise DetectionError("That image is too large.")
                    chunks.append(chunk)
                return b"".join(chunks)
    except requests.RequestException as exc:
        raise DetectionError("Couldn't download that image. Check that the link opens an image.") from exc
    raise DetectionError("That link redirects too many times.")


def detect_with_yolo(img: Image.Image) -> list[str]:
    if not yolo_installed():
        raise DetectionError("YOLO isn't installed. Run: pip install -r requirements-yolo.txt")
    global _yolo_model
    if _yolo_model is None:
        from ultralytics import YOLO

        _yolo_model = YOLO(config.YOLO_MODEL)  # downloads the weights the first time
    result = _yolo_model(img, verbose=False, conf=config.YOLO_CONFIDENCE)[0]
    labels = {result.names[int(c)] for c in result.boxes.cls.tolist()}
    return sorted(label for label in labels if label in COCO_FOOD)


def _to_data_url(img: Image.Image) -> str:
    small = img.copy()
    small.thumbnail((1280, 1280))  # keeps the request well under Groq's image size limit
    buf = io.BytesIO()
    small.save(buf, format="JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def detect_with_vision(img: Image.Image) -> list[str]:
    client = get_client()
    response = client.chat.completions.create(
        model=config.GROQ_VISION_MODEL,
        temperature=0.2,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": VISION_PROMPT},
                    {"type": "image_url", "image_url": {"url": _to_data_url(img)}},
                ],
            }
        ],
    )
    return clean_ingredients(parse_ingredient_list(response.choices[0].message.content))


LABELS = {"vision": "AI vision", "yolo": "YOLO"}


def detect_ingredients(data: bytes, mode: str = "auto") -> dict:
    img = load_image(data)
    mode = (mode or "auto").lower()

    if mode == "auto":
        order = ["vision", "yolo"] if config.GROQ_API_KEY else ["yolo"]
    elif mode in LABELS:
        order = [mode]
    else:
        raise DetectionError(f"Unknown detection mode: {mode}")

    notes, errors = [], []
    for method in order:
        try:
            items = detect_with_vision(img) if method == "vision" else detect_with_yolo(img)
        except (DetectionError, LLMError) as exc:  # our own, user-safe messages
            log.warning("%s detection failed: %s", LABELS[method], exc)
            errors.append(f"{LABELS[method]}: {exc}")
            notes.append(f"{LABELS[method]} didn't work, so we tried the next method.")
        except Exception:  # upstream API/network errors: details go to the server log only
            log.exception("%s detection failed", LABELS[method])
            errors.append(f"{LABELS[method]} is unavailable right now.")
            notes.append(f"{LABELS[method]} didn't work, so we tried the next method.")
        else:
            if method == "yolo":
                notes.append(
                    "YOLO only recognizes a handful of foods (apples, bananas, oranges, broccoli, carrots...). "
                    "Add anything it missed below."
                )
            return {"ingredients": items, "method": method, "notes": notes}

    # Every method failed: report all of them, not just the last one.
    raise DetectionError("Couldn't detect ingredients. " + " | ".join(errors))
