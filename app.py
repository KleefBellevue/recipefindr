"""RecipeFindr v2 - FastAPI server.

Serves the HTML/CSS/JS front end from ./public (locally; Vercel's CDN does it in production) and exposes a small JSON API:
  GET  /api/health             which features are configured (never the keys themselves)
  POST /api/detect             photo (upload or link) -> ingredient list
  POST /api/ingredients/edit   plain-English edits to the list
  POST /api/recipes            ingredient list -> recipes

API keys are read from environment variables on the server only; the browser never sees them.

Run it with:   python app.py      then open http://localhost:8000
"""
import logging
from typing import List, Literal, Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from services import config
from services.detection import DetectionError, detect_ingredients, fetch_image_url, yolo_installed
from services.llm import LLMError, clean_ingredients, edit_ingredients
from services.ratelimit import detect_limiter, edit_limiter, recipe_limiter
from services.recipes import RecipeAPIError, find_recipes

log = logging.getLogger("uvicorn.error")

app = FastAPI(
    title="RecipeFindr API",
    version="2.0",
    description="Photo-to-recipe assistant: detect ingredients from a photo, edit them, and find recipes.",
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    return response


@app.get("/api/health")
def health():
    return {
        "groq": bool(config.GROQ_API_KEY),
        "spoonacular": bool(config.SPOONACULAR_API_KEY),
        "yolo": yolo_installed(),
    }


@app.post("/api/detect")
async def detect(
    request: Request,
    file: Optional[UploadFile] = File(None),
    image_url: Optional[str] = Form(None, max_length=2048),
    mode: Literal["auto", "vision", "yolo"] = Form("auto"),
):
    detect_limiter.check(request)
    try:
        if file is not None and file.filename:
            data = await file.read()
            if len(data) > config.MAX_UPLOAD_BYTES:
                raise DetectionError("That photo is too large. Try a smaller one.")
        elif image_url:
            data = await run_in_threadpool(fetch_image_url, image_url)
        else:
            raise DetectionError("Upload a photo or paste an image link first.")
        return await run_in_threadpool(detect_ingredients, data, mode)
    except DetectionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


class EditRequest(BaseModel):
    ingredients: List[str] = Field(default_factory=list, max_length=100)
    instruction: str = Field(..., min_length=1, max_length=500)


@app.post("/api/ingredients/edit")
async def edit(req: EditRequest, request: Request):
    edit_limiter.check(request)
    try:
        updated = await run_in_threadpool(edit_ingredients, clean_ingredients(req.ingredients), req.instruction)
    except LLMError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception:
        log.exception("Ingredient edit failed")
        raise HTTPException(
            status_code=502, detail="The assistant is unavailable right now. You can still edit the list by hand."
        )
    return {"ingredients": updated}


class RecipeRequest(BaseModel):
    ingredients: List[str] = Field(..., max_length=100)
    number: int = Field(6, ge=1, le=12)
    ranking: Literal["maximize", "minimize"] = "maximize"
    ignore_pantry: bool = True
    diets: List[str] = Field(default_factory=list, max_length=10)
    max_ready_time: Optional[int] = Field(None, ge=5, le=600)


@app.post("/api/recipes")
async def recipes(req: RecipeRequest, request: Request):
    recipe_limiter.check(request)
    try:
        results = await run_in_threadpool(
            find_recipes,
            req.ingredients,
            req.number,
            req.ranking,
            req.ignore_pantry,
            req.diets,
            req.max_ready_time,
        )
    except RecipeAPIError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc))
    return {"recipes": results}


# Serve the front end locally (must come after the /api routes).
# On Vercel, files in public/ are served by the CDN instead, so we skip this.
if not config.ON_VERCEL:
    app.mount("/", StaticFiles(directory=config.STATIC_DIR, html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    print("\n  RecipeFindr is running at  http://localhost:8000  (Ctrl+C to stop)\n")
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
