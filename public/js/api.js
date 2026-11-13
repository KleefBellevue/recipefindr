// Thin wrapper around the RecipeFindr backend (app.py).

async function request(path, options = {}) {
  let res;
  try {
    res = await fetch(path, options);
  } catch {
    throw new Error("Can't reach the RecipeFindr server. Is `python app.py` still running?");
  }

  let body = null;
  try {
    body = await res.json();
  } catch {
    /* non-JSON response */
  }

  if (!res.ok) {
    if (res.status === 413) throw new Error("That photo is too large to upload. Try a smaller one or a screenshot of it.");
    let detail = body && body.detail;
    if (Array.isArray(detail)) detail = detail.map((d) => d.msg).join("; "); // FastAPI validation errors
    throw new Error(detail || `Request failed (${res.status})`);
  }
  return body;
}

function postJSON(path, data) {
  return request(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

export const api = {
  health: () => request("/api/health"),

  /** Send either an uploaded File or an image URL; returns { ingredients, method, notes }. */
  detect({ file, imageUrl, mode }) {
    const form = new FormData();
    if (file) form.append("file", file, file.name || "photo.jpg");
    if (imageUrl) form.append("image_url", imageUrl);
    form.append("mode", mode || "auto");
    return request("/api/detect", { method: "POST", body: form });
  },

  editIngredients: (ingredients, instruction) =>
    postJSON("/api/ingredients/edit", { ingredients, instruction }),

  recipes: (payload) => postJSON("/api/recipes", payload),
};
