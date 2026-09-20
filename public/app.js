const $ = (selector) => document.querySelector(selector);

const state = {
  config: null,
  scenario: null,
  plan: null,
  maps: new Map(),
  running: false,
};

const palette = ["#246a4b", "#8d5b25", "#8651a4"];

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: { "content-type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `Request failed (${response.status})`);
  return payload;
}

function setStatus(message, error = false) {
  const target = $("#run-status");
  target.textContent = message;
  target.dataset.error = error ? "true" : "false";
}

function renderConfig() {
  const select = $("#llm-model-select");
  const models = state.config?.models?.available || [];
  select.replaceChildren();
  if (!models.length) {
    select.append(new Option("No OpenRouter models available", ""));
    select.disabled = true;
  } else {
    for (const model of models) select.append(new Option(model.name || model.id, model.id));
    select.disabled = false;
    if (state.config.models.llm) select.value = state.config.models.llm;
  }
  const credentials = state.config?.credentials || {};
  $("#service-status").textContent = `JEV ${credentials.jev ? "ready" : "missing"} · OpenRouter ${credentials.llm ? "ready" : "missing"}`;
}

function renderScenario() {
  const scenario = state.scenario;
  if (!scenario) return;
  $("#origin").value = scenario.origin;
  $("#destination").value = scenario.destination;
  $("#intent").value = scenario.intent;
}

function routeName(id) {
  return state.plan?.state?.routes?.find((route) => route.id === id)?.label || id || "No route";
}

function stopName(id) {
  return state.plan?.state?.stops?.find((stop) => stop.id === id)?.name || id || "No stop";
}

function renderShared() {
  const plan = state.plan;
  if (!plan) return;
  const stateData = plan.state;
  $("#state-fingerprint").textContent = plan.state_digest || "unavailable";
  $("#shared-data").innerHTML = `
    <p><strong>${escapeHtml(stateData.trip.origin)}</strong> → <strong>${escapeHtml(stateData.trip.destination)}</strong></p>
    <p>${escapeHtml(stateData.request.intent)}</p>
    <table><thead><tr><th>Route</th><th>Time</th><th>Distance</th><th>Stops</th></tr></thead>
      <tbody>${stateData.routes.map((route) => `<tr><td>${escapeHtml(route.label)}</td><td>${route.duration_min} min</td><td>${route.distance_mi} mi</td><td>${escapeHtml(route.nearby_stop_ids.join(", ") || "none")}</td></tr>`).join("")}</tbody>
    </table>`;
}

function resetMap(containerId) {
  const previous = state.maps.get(containerId);
  if (previous) previous.remove();
  state.maps.delete(containerId);
}

function renderMap(containerId, decision) {
  const container = $(`#${containerId}`);
  const mapData = state.plan?.map;
  if (!container || !mapData) return;
  resetMap(containerId);
  if (!window.L) {
    container.innerHTML = `<p>Map library unavailable. ${mapData.routes.length} route candidates and ${mapData.stops.length} stop candidates are ready.</p>`;
    return;
  }
  const origin = mapData.origin;
  const destination = mapData.destination;
  const map = window.L.map(container).setView([origin.lat, origin.lng], 12);
  window.L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "© OpenStreetMap contributors",
    maxZoom: 19,
  }).addTo(map);
  const bounds = [];
  for (const [index, route] of mapData.routes.entries()) {
    const points = route.path.map((point) => [point.lat, point.lng]);
    bounds.push(...points);
    window.L.polyline(points, {
      color: route.id === decision?.best_route ? "#d33d35" : palette[index % palette.length],
      weight: route.id === decision?.best_route ? 6 : 3,
      opacity: route.id === decision?.best_route ? 0.95 : 0.55,
    }).addTo(map).bindTooltip(routeName(route.id));
  }
  const marker = (point, label) => window.L.marker([point.lat, point.lng]).addTo(map).bindPopup(label);
  marker(origin, "Start");
  marker(destination, "Destination");
  for (const stop of mapData.stops) marker(stop.location, stopName(stop.id));
  if (bounds.length) map.fitBounds(bounds, { padding: [20, 20] });
  state.maps.set(containerId, map);
}

function renderResult(prefix, result) {
  const decision = result.decision || {};
  $(`#${prefix}-model`).textContent = `${result.model || "unknown model"} · ${result.latency_ms ?? "?"} ms`;
  $(`#${prefix}-status`).textContent = result.schema_valid ? "Schema-valid decision" : "Decision rejected";
  $(`#${prefix}-choice`).innerHTML = `
    <strong>Route:</strong> ${escapeHtml(routeName(decision.best_route))}<br>
    <strong>Stop:</strong> ${escapeHtml(stopName(decision.best_stop))}<br>
    <strong>Clarification needed:</strong> ${decision.clarification_needed ? "yes" : "no"}`;
  $(`#${prefix}-result`).textContent = JSON.stringify(result, null, 2);
  renderMap(`${prefix}-map`, decision);
}

async function runJudges() {
  if (!state.plan || state.running) return;
  state.running = true;
  $("#race-button").disabled = true;
  setStatus("Comparing both engines on the shared route state…");
  const selectedModel = $("#llm-model-select").value || undefined;
  try {
    const [jev, llm] = await Promise.all([
      request("/api/judge/jev", { method: "POST", body: JSON.stringify({ state: state.plan.state }) }),
      request("/api/judge/llm", { method: "POST", body: JSON.stringify({ state: state.plan.state, model: selectedModel }) }),
    ]);
    renderResult("jev", jev);
    renderResult("llm", llm);
    setStatus(`Both decisions completed · shared state ${state.plan.state_digest}`);
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    state.running = false;
    $("#race-button").disabled = false;
  }
}

async function loadPlan(event) {
  event.preventDefault();
  if (state.running) return;
  const button = $("#plan-button");
  button.disabled = true;
  setStatus("Loading routes, weather, and nearby cafés…");
  try {
    state.plan = await request("/api/plan", {
      method: "POST",
      body: JSON.stringify({
        origin: $("#origin").value,
        destination: $("#destination").value,
        intent: $("#intent").value,
      }),
    });
    renderShared();
    $("#race-button").disabled = false;
    setStatus(`Route state ready · fingerprint ${state.plan.state_digest}`);
  } catch (error) {
    setStatus(error.message, true);
    $("#race-button").disabled = true;
  } finally {
    button.disabled = false;
  }
}

$("#plan-form").addEventListener("submit", loadPlan);
$("#race-button").addEventListener("click", runJudges);
$("#random-mood").addEventListener("click", () => {
  const moods = [
    "Take me home on a calm route and stop at a quiet coffee shop with outdoor seating.",
    "Find a scenic route with a relaxed café stop and minimal walking.",
    "Choose the fastest route and a comfortable coffee stop with a short detour.",
    "Prefer a covered café stop because rain is possible; avoid long exposed walks.",
  ];
  $("#intent").value = moods[Math.floor(Math.random() * moods.length)];
});

Promise.all([request("/api/config"), request("/api/scenario")])
  .then(([config, scenario]) => {
    state.config = config;
    state.scenario = scenario;
    renderConfig();
    renderScenario();
  })
  .catch((error) => {
    $("#service-status").textContent = error.message;
    setStatus("Could not load local configuration.", true);
  });
