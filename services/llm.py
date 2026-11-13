"""Groq LLM helpers plus the Ingredient Validator agent."""
import ast
import json
import re

from . import config

_client = None


class LLMError(Exception):
    pass


def get_client():
    """Return a shared Groq client, or raise if no API key is configured."""
    global _client
    if not config.GROQ_API_KEY:
        raise LLMError("AI features aren't configured on the server (missing GROQ_API_KEY).")
    if _client is None:
        from groq import Groq

        _client = Groq(api_key=config.GROQ_API_KEY)
    return _client


def parse_ingredient_list(text: str) -> list[str]:
    """Pull a list of ingredients out of an LLM reply.

    Accepts {"ingredients": [...]}, a bare JSON/Python list, or one item per line.
    """
    text = (text or "").strip()
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()  # reasoning models
    text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text).strip()

    candidates = [text, *re.findall(r"\{.*\}", text, re.S), *re.findall(r"\[.*\]", text, re.S)]
    for candidate in candidates:
        for loader in (json.loads, ast.literal_eval):
            try:
                value = loader(candidate)
            except Exception:
                continue
            if isinstance(value, dict):
                value = value.get("ingredients", [])
            if isinstance(value, list):
                return [str(item) for item in value if item]

    lines = [line.strip().strip("-*•'\",.") for line in text.splitlines()]
    return [line for line in lines if line and not line.startswith("#")]


def clean_ingredients(items) -> list[str]:
    """Lowercase, trim, and de-duplicate while keeping the original order."""
    seen, out = set(), []
    for item in items or []:
        name = re.sub(r"\s+", " ", str(item)).strip().strip(" .,-*•'\"").lower()
        if name and len(name) <= 60 and name not in seen:
            seen.add(name)
            out.append(name)
    return out


VALIDATOR_PROMPT = """You are the Ingredient Validator for RecipeFindr, a cooking assistant.
You receive the user's current ingredient list and a request to change it.
Apply the request exactly: remove, add, rename, or keep items as asked.
If the request just confirms the list (e.g. "looks good"), return it unchanged.
Use short, generic grocery names (e.g. "eggs", "cheddar cheese").
Respond with ONLY a JSON object: {"ingredients": ["item", "item", ...]}"""


def edit_ingredients(current: list[str], instruction: str) -> list[str]:
    """Let the user edit their list in plain English, e.g. 'remove banana, add rice and eggs'."""
    client = get_client()
    messages = [
        {"role": "system", "content": VALIDATOR_PROMPT},
        {"role": "user", "content": f"Current ingredients: {json.dumps(current)}\nUser request: {instruction}"},
    ]
    try:
        response = client.chat.completions.create(
            model=config.GROQ_TEXT_MODEL, temperature=0, messages=messages,
            response_format={"type": "json_object"},
        )
    except Exception as exc:
        if "decommissioned" in str(exc) or "model_not_found" in str(exc):
            raise
        # some models don't support JSON mode; the prompt still asks for JSON
        response = client.chat.completions.create(model=config.GROQ_TEXT_MODEL, temperature=0, messages=messages)
    return clean_ingredients(parse_ingredient_list(response.choices[0].message.content))
