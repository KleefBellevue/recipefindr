"""Central configuration. Values come from the .env file in the project root."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT_DIR / "public"  # Vercel serves public/ from its CDN automatically

# Vercel sets VERCEL=1 in its environment.
ON_VERCEL = os.getenv("VERCEL") == "1"

load_dotenv(ROOT_DIR / ".env")

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
SPOONACULAR_API_KEY = os.getenv("SPOONACULAR_API_KEY", "").strip()

# Groq retires models often (llama3-70b, llama-3.3-70b and llama-4-scout are all gone).
# Current list: https://console.groq.com/docs/models  -- override in .env when one is retired.
GROQ_TEXT_MODEL = os.getenv("GROQ_TEXT_MODEL", "openai/gpt-oss-120b")
GROQ_VISION_MODEL = os.getenv("GROQ_VISION_MODEL", "qwen/qwen3.8-27b")

YOLO_MODEL = os.getenv("YOLO_MODEL", "yolov8m.pt")
YOLO_CONFIDENCE = float(os.getenv("YOLO_CONFIDENCE", "0.25"))

MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_MB", "15")) * 1024 * 1024
