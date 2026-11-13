// RecipeFindr front end. Replaces the old WORKFLOW_MEMORY state machine with
// a simple 3-step UI: 1) photo  2) check ingredients  3) recipes.
import { api } from "./api.js";

const $ = (id) => document.getElementById(id);

const state = {
  source: "upload", // "upload" | "url"
  file: null,
  previewUrl: null,
  ingredients: [],
  health: null,
};

/* ---------------- helpers ---------------- */

let toastTimer;
function toast(message, isError = false) {
  const el = $("toast");
  el.textContent = message;
  el.classList.toggle("error", isError);
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.hidden = true), isError ? 7000 : 3500);
}

async function withLoading(button, label, task) {
  const original = button.textContent;
  button.disabled = true;
  button.classList.add("loading");
  button.textContent = label;
  try {
    return await task();
  } catch (err) {
    toast(err.message, true);
  } finally {
    button.disabled = false;
    button.classList.remove("loading");
    button.textContent = original;
    updateDetectButton();
  }
}

function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text != null) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function normalize(list) {
  const seen = new Set();
  return list
    .map((s) => s.trim().toLowerCase())
    .filter((s) => s && !seen.has(s) && seen.add(s));
}

/* ---------------- steps ---------------- */

function showStep(n) {
  document.querySelectorAll("[data-panel]").forEach((p) => (p.hidden = Number(p.dataset.panel) !== n));
  document.querySelectorAll(".stepper li").forEach((li) => {
    const s = Number(li.dataset.step);
    li.classList.toggle("done", s < n);
    if (s === n) li.setAttribute("aria-current", "step");
    else li.removeAttribute("aria-current");
  });
  window.scrollTo({ top: 0, behavior: "smooth" });
}

/* ---------------- step 1: photo ---------------- */

function setSource(source) {
  state.source = source;
  $("tab-upload").setAttribute("aria-selected", source === "upload");
  $("tab-url").setAttribute("aria-selected", source === "url");
  $("pane-upload").hidden = source !== "upload";
  $("pane-url").hidden = source !== "url";
  updateDetectButton();
}

function updateDetectButton() {
  const ready = state.source === "upload" ? !!state.file : /^https?:\/\/\S+/i.test($("url-input").value.trim());
  $("detect-btn").disabled = !ready;
}

/** Shrink big phone photos in the browser so uploads are fast and stay under
 *  Vercel's 4.5 MB request limit (falls back to the original file). */
async function shrinkImage(file, maxDim = 1600) {
  try {
    const bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
    const scale = Math.min(1, maxDim / Math.max(bitmap.width, bitmap.height));
    if (scale === 1 && file.size < 3 * 1024 * 1024 && /jpe?g|png|webp/.test(file.type)) return file;
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(bitmap.width * scale);
    canvas.height = Math.round(bitmap.height * scale);
    canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.88));
    return blob ? new File([blob], "photo.jpg", { type: "image/jpeg" }) : file;
  } catch {
    return file; // e.g. HEIC in Chrome: let the server handle it
  }
}

function setFile(file) {
  if (!file) return;
  if (file.type && !file.type.startsWith("image/")) {
    toast("That doesn't look like an image file.", true);
    return;
  }
  if (state.previewUrl) URL.revokeObjectURL(state.previewUrl);
  state.file = file;
  state.previewUrl = URL.createObjectURL(file);

  const img = $("preview-img");
  img.onerror = () => {
    img.hidden = true; // browser can't preview it (e.g. HEIC) but the server can still read it
  };
  img.hidden = false;
  img.src = state.previewUrl;
  $("file-name").textContent = file.name || "Pasted image";
  $("dz-empty").hidden = true;
  $("dz-preview").hidden = false;
  $("dropzone").classList.add("has-file");
  setSource("upload");
}

function clearFile() {
  if (state.previewUrl) URL.revokeObjectURL(state.previewUrl);
  state.file = null;
  state.previewUrl = null;
  $("file-input").value = "";
  $("camera-input").value = "";
  $("dz-empty").hidden = false;
  $("dz-preview").hidden = true;
  $("dropzone").classList.remove("has-file");
  updateDetectButton();
}

async function detect() {
  await withLoading($("detect-btn"), "Looking in your fridge…", async () => {
    const mode = $("mode-select").value;
    const payload =
      state.source === "upload"
        ? { file: await shrinkImage(state.file), mode }
        : { imageUrl: $("url-input").value.trim(), mode };

    const result = await api.detect(payload);
    state.ingredients = normalize(result.ingredients || []);

    const methodName = result.method === "vision" ? "AI vision" : "YOLOv8";
    const n = state.ingredients.length;
    $("detect-summary").textContent = n
      ? `${methodName} found ${n} ingredient${n === 1 ? "" : "s"}. Remove anything wrong and add what we missed.`
      : `${methodName} didn't spot any food. Add your ingredients below.`;

    const notes = $("notes");
    notes.replaceChildren(...(result.notes || []).map((t) => el("li", t)));
    notes.hidden = !(result.notes && result.notes.length);

    const thumb = $("thumb");
    thumb.src = state.source === "upload" ? state.previewUrl : payload.imageUrl;
    thumb.hidden = false;
    thumb.onerror = () => (thumb.hidden = true);

    renderChips();
    showStep(2);
  });
}

/* ---------------- step 2: ingredients ---------------- */

function renderChips() {
  const list = $("chips");
  list.replaceChildren(
    ...state.ingredients.map((name) => {
      const li = el("li", null, "chip");
      li.append(el("span", name));
      const remove = el("button", "×");
      remove.type = "button";
      remove.setAttribute("aria-label", `Remove ${name}`);
      remove.addEventListener("click", () => {
        state.ingredients = state.ingredients.filter((i) => i !== name);
        renderChips();
      });
      li.append(remove);
      return li;
    })
  );
  $("chips-empty").hidden = state.ingredients.length > 0;
  $("recipes-btn").disabled = state.ingredients.length === 0;
}

function addIngredients(event) {
  event.preventDefault();
  const input = $("add-input");
  const items = input.value.split(/[,;\n]/);
  state.ingredients = normalize([...state.ingredients, ...items]);
  input.value = "";
  renderChips();
  input.focus();
}

async function askAssistant(event) {
  event.preventDefault();
  const input = $("assistant-input");
  const instruction = input.value.trim();
  if (!instruction) return;
  if (state.health && !state.health.groq) {
    toast("The assistant isn't available right now. You can still add or remove items by hand.", true);
    return;
  }
  const button = event.submitter || $("assistant-form").querySelector("button");
  await withLoading(button, "Updating…", async () => {
    const result = await api.editIngredients(state.ingredients, instruction);
    state.ingredients = normalize(result.ingredients || []);
    input.value = "";
    renderChips();
    toast("List updated.");
  });
}

function preferences() {
  const time = $("time-select").value;
  return {
    number: Number($("count-select").value),
    ranking: document.querySelector('input[name="ranking"]:checked').value,
    ignore_pantry: $("pantry-check").checked,
    diets: [...document.querySelectorAll('input[name="diet"]:checked')].map((c) => c.value),
    max_ready_time: time ? Number(time) : null,
  };
}

async function findRecipes() {
  if (!state.ingredients.length) {
    toast("Add at least one ingredient first.", true);
    return;
  }
  await withLoading($("recipes-btn"), "Finding recipes…", async () => {
    const { recipes } = await api.recipes({ ingredients: state.ingredients, ...preferences() });
    renderRecipes(recipes || []);
    showStep(3);
  });
}

/* ---------------- step 3: recipes ---------------- */

function renderRecipes(recipes) {
  const grid = $("recipes");
  const template = $("recipe-template");

  if (!recipes.length) {
    $("recipes-summary").textContent = "";
    grid.replaceChildren(
      el("p", "No recipes matched. Try removing a diet filter, allowing more cooking time, or adding a few staples.", "empty")
    );
    return;
  }

  $("recipes-summary").textContent = `Using: ${state.ingredients.join(", ")}`;

  grid.replaceChildren(
    ...recipes.map((r) => {
      const card = template.content.firstElementChild.cloneNode(true);
      const img = card.querySelector(".recipe-img");
      if (r.image) img.src = r.image;
      else img.remove();

      card.querySelector(".recipe-title").textContent = r.title;

      const meta = [];
      if (r.readyInMinutes) meta.push(`⏱ ${r.readyInMinutes} min`);
      if (r.servings) meta.push(`🍽 ${r.servings} servings`);
      if (r.diets && r.diets.length) meta.push(r.diets.join(" · "));
      card.querySelector(".recipe-meta").textContent = meta.join("  ·  ");

      const match = card.querySelector(".recipe-match");
      const used = r.used.length;
      const missing = r.missing.length;
      match.append(
        el("strong", `Uses ${used} of your ingredient${used === 1 ? "" : "s"}`),
        document.createTextNode(missing ? ` · needs ${missing} more` : " · nothing extra to buy!")
      );

      const toChip = (i) => {
        const li = el("li", i.name);
        if (i.amount) li.title = i.amount;
        return li;
      };
      card.querySelector(".mini-chips.used").append(...r.used.map(toChip));
      card.querySelector(".mini-chips.missing").append(...r.missing.map(toChip));

      const steps = card.querySelector(".recipe-steps");
      if (r.steps && r.steps.length) {
        steps.querySelector("ol").append(...r.steps.map((s) => el("li", s)));
      } else {
        steps.remove();
      }

      const link = card.querySelector(".recipe-link");
      if (r.sourceUrl) link.href = r.sourceUrl;
      else link.remove();

      return card;
    })
  );
}

function restart() {
  clearFile();
  $("url-input").value = "";
  setSource("upload");
  state.ingredients = [];
  renderChips();
  showStep(1);
}

/* ---------------- server status ---------------- */

async function checkHealth() {
  const status = $("status");
  try {
    state.health = await api.health();
    const missing = [];
    if (!state.health.spoonacular) missing.push("Spoonacular key");
    if (!state.health.groq && !state.health.yolo) missing.push("Groq key or YOLO");
    if (missing.length) {
      status.textContent = `Missing: ${missing.join(", ")}`;
      status.className = "status bad";
    } else {
      status.textContent = "Server ready";
      status.className = "status ok";
    }
    if (!state.health.yolo) $("mode-select").querySelector('[value="yolo"]').disabled = true;
    if (!state.health.groq) $("mode-select").querySelector('[value="vision"]').disabled = true;
  } catch (err) {
    status.textContent = "Server offline";
    status.className = "status bad";
  }
}

/* ---------------- wire up ---------------- */

function init() {
  $("tab-upload").addEventListener("click", () => setSource("upload"));
  $("tab-url").addEventListener("click", () => setSource("url"));
  $("url-input").addEventListener("input", updateDetectButton);
  $("url-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !$("detect-btn").disabled) detect();
  });

  $("browse-btn").addEventListener("click", () => $("file-input").click());
  $("camera-btn").addEventListener("click", () => $("camera-input").click());
  $("file-input").addEventListener("change", (e) => setFile(e.target.files[0]));
  $("camera-input").addEventListener("change", (e) => setFile(e.target.files[0]));
  $("clear-file").addEventListener("click", clearFile);

  const dz = $("dropzone");
  ["dragenter", "dragover"].forEach((type) =>
    dz.addEventListener(type, (e) => {
      e.preventDefault();
      dz.classList.add("dragging");
    })
  );
  ["dragleave", "drop"].forEach((type) =>
    dz.addEventListener(type, (e) => {
      e.preventDefault();
      dz.classList.remove("dragging");
    })
  );
  dz.addEventListener("drop", (e) => setFile(e.dataTransfer.files[0]));

  document.addEventListener("paste", (e) => {
    if ($("step1-title").closest("[data-panel]").hidden) return;
    const item = [...(e.clipboardData?.items || [])].find((i) => i.type.startsWith("image/"));
    if (item) setFile(item.getAsFile());
  });

  $("detect-btn").addEventListener("click", detect);
  $("skip-btn").addEventListener("click", () => {
    $("detect-summary").textContent = "Type what you have and we'll find recipes for it.";
    $("notes").hidden = true;
    $("thumb").hidden = true;
    renderChips();
    showStep(2);
    $("add-input").focus();
  });

  $("add-form").addEventListener("submit", addIngredients);
  $("assistant-form").addEventListener("submit", askAssistant);
  $("back-1").addEventListener("click", () => showStep(1));
  $("recipes-btn").addEventListener("click", findRecipes);
  $("back-2").addEventListener("click", () => showStep(2));
  $("restart-btn").addEventListener("click", restart);

  showStep(1);
  checkHealth();
}

init();
