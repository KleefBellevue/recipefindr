"""Recipe Generator agent: finds real recipes on Spoonacular for the user's ingredients.

Unlike v1, the instructions come straight from Spoonacular (no LLM-invented steps),
and results can be filtered by diet and cooking time.
"""
import logging
import re
from functools import lru_cache

import requests

from . import config

log = logging.getLogger("uvicorn.error")

BASE_URL = "https://api.spoonacular.com"

# UI value -> boolean field on Spoonacular's recipe information
DIET_FLAGS = {
    "vegetarian": "vegetarian",
    "vegan": "vegan",
    "gluten-free": "glutenFree",
    "dairy-free": "dairyFree",
}


class RecipeAPIError(Exception):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def _get(path: str, params: dict):
    if not config.SPOONACULAR_API_KEY:
        raise RecipeAPIError("Recipe search isn't configured on the server (missing SPOONACULAR_API_KEY).", 500)
    try:
        # Key goes in a header, not the URL, so it can never show up in logged URLs or error messages.
        resp = requests.get(
            BASE_URL + path, params=params, headers={"x-api-key": config.SPOONACULAR_API_KEY}, timeout=20
        )
    except requests.RequestException as exc:
        log.warning("Spoonacular request failed: %s", exc.__class__.__name__)
        raise RecipeAPIError("Couldn't reach the recipe service. Please try again.") from exc

    if resp.status_code == 401:
        log.error("Spoonacular rejected the API key (401)")
        raise RecipeAPIError("Recipe search is unavailable right now.")
    if resp.status_code == 402:
        raise RecipeAPIError("RecipeFindr has hit today's free recipe-search limit. Please try again tomorrow.", 429)
    if not resp.ok:
        log.error("Spoonacular error %s: %s", resp.status_code, resp.text[:200])
        raise RecipeAPIError("Recipe search is unavailable right now.")
    return resp.json()


def _steps(info: dict) -> list[str]:
    steps = [
        step["step"].strip()
        for block in info.get("analyzedInstructions") or []
        for step in block.get("steps", [])
        if step.get("step")
    ]
    if not steps and info.get("instructions"):
        text = re.sub(r"<[^>]+>", " ", info["instructions"])
        steps = [s.strip() for s in re.split(r"\n+|(?<=[.!?])\s+(?=[A-Z])", text) if s.strip()]
    return steps


def _ingredient(item: dict) -> dict:
    return {"name": item.get("name", ""), "amount": item.get("original", "")}


def _passes(info: dict, diets: tuple, max_ready_time) -> bool:
    if any(not info.get(DIET_FLAGS[d]) for d in diets if d in DIET_FLAGS):
        return False
    if max_ready_time and (info.get("readyInMinutes") or 10_000) > max_ready_time:
        return False
    return True


@lru_cache(maxsize=64)  # saves your daily Spoonacular quota when you repeat a search
def _search(ingredients: tuple, number: int, ranking: str, ignore_pantry: bool, diets: tuple, max_ready_time):
    filtering = bool(diets or max_ready_time)
    pool = min(number * 4, 40) if filtering else number

    matches = _get(
        "/recipes/findByIngredients",
        {
            "ingredients": ",".join(ingredients),
            "number": pool,
            "ranking": 1 if ranking == "maximize" else 2,
            "ignorePantry": str(ignore_pantry).lower(),
        },
    )
    if not matches:
        return []

    details = _get("/recipes/informationBulk", {"ids": ",".join(str(m["id"]) for m in matches)})
    by_id = {d["id"]: d for d in details}

    results = []
    for match in matches:
        info = by_id.get(match["id"], {})
        if filtering and not _passes(info, diets, max_ready_time):
            continue
        results.append(
            {
                "id": match["id"],
                "title": match.get("title") or info.get("title", "Untitled recipe"),
                "image": match.get("image") or info.get("image"),
                "readyInMinutes": info.get("readyInMinutes"),
                "servings": info.get("servings"),
                "sourceUrl": info.get("sourceUrl") or info.get("spoonacularSourceUrl"),
                "used": [_ingredient(i) for i in match.get("usedIngredients", [])],
                "missing": [_ingredient(i) for i in match.get("missedIngredients", [])],
                "diets": [label for label, flag in DIET_FLAGS.items() if info.get(flag)],
                "steps": _steps(info),
            }
        )
        if len(results) >= number:
            break
    return results


def find_recipes(
    ingredients: list[str],
    number: int = 6,
    ranking: str = "maximize",
    ignore_pantry: bool = True,
    diets: list[str] | None = None,
    max_ready_time: int | None = None,
) -> list[dict]:
    cleaned = tuple(sorted({i.strip().lower() for i in ingredients if i.strip()}))
    if not cleaned:
        raise RecipeAPIError("Add at least one ingredient first.", 400)
    return _search(cleaned, number, ranking, ignore_pantry, tuple(sorted(set(diets or []))), max_ready_time)
