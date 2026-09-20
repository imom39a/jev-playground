import { createHash } from "node:crypto";
import { readFileSync, statSync } from "node:fs";
import { createServer } from "node:http";
import { extname, join, normalize } from "node:path";
import { fileURLToPath } from "node:url";

const projectDir = fileURLToPath(new URL(".", import.meta.url));
const publicDir = join(projectDir, "public");

loadDotEnv(join(projectDir, ".env"));

const PORT = Number(process.env.PORT || 4173);
const TYPESAFE_API_KEY = process.env.TYPESAFE_API_KEY || process.env.JEV_API_KEY || "";
const OPENROUTER_API_KEY =
  process.env.OPENROUTER_API_KEY || process.env.openouterkey || process.env.OPENROUTER_KEY || "";
const JEV_MODEL = process.env.JEV_MODEL || "jev-latest";
const OPENROUTER_MODEL = process.env.OPENROUTER_MODEL || "openai/gpt-5.6-luna";
const DEFAULT_INTENT =
  "Take me home on a calm route and stop at a quiet coffee shop with outdoor seating. I can spare ten minutes; light rain is fine if the seating is covered.";
const APP_USER_AGENT = "VibePlannerLocalDemo/0.2";
const planCache = new Map();
const geocodeCache = new Map();
let lastNominatimRequestAt = 0;
let openRouterModelsCache = null;

const ROUTE_SCORE_LEVELS = [
  "Conflicts with the request or misses its important constraints",
  "Weak fit; satisfies few preferences and has meaningful drawbacks",
  "Mixed fit; satisfies some preferences but compromises others",
  "Good fit; satisfies most preferences with only a minor compromise",
  "Excellent fit; directly satisfies the request and its constraints",
];

const WALKING_COMFORT_LEVELS = [
  "Uncomfortable for this weather and trip context",
  "Mostly uncomfortable with notable exposure or inconvenience",
  "Acceptable but includes a meaningful weather or access compromise",
  "Comfortable with only minor exposure or inconvenience",
  "Very comfortable for the stated weather and access needs",
];

function loadDotEnv(path) {
  let source;
  try {
    source = readFileSync(path, "utf8");
  } catch {
    return;
  }

  for (const rawLine of source.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) continue;
    const match = line.match(/^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$/);
    if (!match || process.env[match[1]] !== undefined) continue;
    let value = match[2].trim();
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    process.env[match[1]] = value;
  }
}

function jsonResponse(res, status, body) {
  res.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "cache-control": "no-store",
  });
  res.end(JSON.stringify(body));
}

async function readJson(req) {
  const chunks = [];
  let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size > 128 * 1024) throw new Error("Request body is too large.");
    chunks.push(chunk);
  }
  return JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}");
}

function cleanText(value, maxLength, label) {
  const cleaned = typeof value === "string" ? value.trim().slice(0, maxLength) : "";
  if (!cleaned) throw new Error(`${label} is required.`);
  return cleaned;
}

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function fetchJson(url, options = {}, timeoutMs = 25_000) {
  const response = await fetch(url, { ...options, signal: AbortSignal.timeout(timeoutMs) });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message = payload?.error?.message || payload?.message || `HTTP ${response.status}`;
    throw new Error(message);
  }
  return payload;
}

async function postJson(url, headers, body, timeoutMs = 45_000) {
  return fetchJson(
    url,
    {
      method: "POST",
      headers: { "content-type": "application/json", ...headers },
      body: JSON.stringify(body),
    },
    timeoutMs,
  );
}

async function geocodePlace(query) {
  const key = query.toLocaleLowerCase();
  if (geocodeCache.has(key)) return geocodeCache.get(key);

  const elapsed = Date.now() - lastNominatimRequestAt;
  if (elapsed < 1_050) await wait(1_050 - elapsed);
  lastNominatimRequestAt = Date.now();

  const params = new URLSearchParams({ q: query, format: "jsonv2", limit: "1", addressdetails: "1" });
  const results = await fetchJson(`https://nominatim.openstreetmap.org/search?${params}`, {
    headers: { "user-agent": APP_USER_AGENT, "accept-language": "en" },
  });
  const first = Array.isArray(results) ? results[0] : null;
  if (!first) throw new Error(`Could not find “${query}”. Try a more specific address.`);

  const place = {
    label: first.display_name,
    lat: Number(first.lat),
    lng: Number(first.lon),
  };
  geocodeCache.set(key, place);
  return place;
}

function weatherSummary(code) {
  if (code === 0) return "Clear";
  if ([1, 2, 3].includes(code)) return "Partly cloudy";
  if ([45, 48].includes(code)) return "Foggy";
  if ([51, 53, 55, 56, 57].includes(code)) return "Drizzle";
  if ([61, 63, 65, 66, 67, 80, 81, 82].includes(code)) return "Rain";
  if ([71, 73, 75, 77, 85, 86].includes(code)) return "Snow";
  if ([95, 96, 99].includes(code)) return "Thunderstorms";
  return "Current conditions available";
}

async function fetchWeather(origin, destination) {
  const lat = (origin.lat + destination.lat) / 2;
  const lng = (origin.lng + destination.lng) / 2;
  const params = new URLSearchParams({
    latitude: String(lat),
    longitude: String(lng),
    current: "temperature_2m,precipitation,weather_code,wind_speed_10m",
    temperature_unit: "fahrenheit",
    wind_speed_unit: "mph",
  });

  try {
    const payload = await fetchJson(`https://api.open-meteo.com/v1/forecast?${params}`);
    const current = payload.current || {};
    return {
      summary: weatherSummary(Number(current.weather_code)),
      temperature_f: Number.isFinite(current.temperature_2m) ? current.temperature_2m : null,
      precipitation: Number.isFinite(current.precipitation)
        ? `${current.precipitation} ${payload.current_units?.precipitation || "mm"}`
        : "not available",
      wind_mph: Number.isFinite(current.wind_speed_10m) ? current.wind_speed_10m : null,
    };
  } catch {
    return { summary: "Weather unavailable", temperature_f: null, precipitation: "not available", wind_mph: null };
  }
}

function uniqueStreetNames(route) {
  const names = route.legs
    ?.flatMap((leg) => leg.steps || [])
    .map((step) => step.name?.trim())
    .filter(Boolean);
  return [...new Set(names || [])].slice(0, 3);
}

async function fetchRoutes(origin, destination) {
  const coordinates = `${origin.lng},${origin.lat};${destination.lng},${destination.lat}`;
  const params = new URLSearchParams({ alternatives: "3", steps: "true", geometries: "geojson", overview: "full" });
  const payload = await fetchJson(`https://router.project-osrm.org/route/v1/driving/${coordinates}?${params}`);
  if (payload.code !== "Ok" || !payload.routes?.length) throw new Error("No driving route was found between those places.");

  const fastestMinutes = payload.routes[0].duration / 60;
  return payload.routes.slice(0, 3).map((route, index) => {
    const names = uniqueStreetNames(route);
    const durationMin = Math.max(1, Math.round(route.duration / 60));
    const extraMin = Math.max(0, Math.round(durationMin - fastestMinutes));
    return {
      id: `route_${String.fromCharCode(97 + index)}`,
      label: names.length ? names.join(" → ") : `Route ${index + 1}`,
      duration_min: durationMin,
      distance_mi: Math.round((route.distance / 1609.344) * 10) / 10,
      traffic: "Live traffic is not included in this demo route feed.",
      character:
        index === 0
          ? "Fastest route returned by the router."
          : `Alternative route; about ${extraMin || 1} minute${extraMin === 1 ? "" : "s"} longer than the fastest route.`,
      weather_exposure: "Driving route; curbside comfort depends on the selected stop and current weather.",
      nearby_stop_ids: [],
      path: route.geometry.coordinates.map(([lng, lat]) => ({ lat, lng })),
    };
  });
}

function haversineKm(a, b) {
  const radians = (degrees) => (degrees * Math.PI) / 180;
  const dLat = radians(b.lat - a.lat);
  const dLng = radians(b.lng - a.lng);
  const lat1 = radians(a.lat);
  const lat2 = radians(b.lat);
  const sinLat = Math.sin(dLat / 2);
  const sinLng = Math.sin(dLng / 2);
  const h = sinLat * sinLat + Math.cos(lat1) * Math.cos(lat2) * sinLng * sinLng;
  return 6371 * 2 * Math.atan2(Math.sqrt(h), Math.sqrt(1 - h));
}

function distanceToPath(location, path) {
  let best = Infinity;
  const stride = Math.max(1, Math.floor(path.length / 200));
  for (let index = 0; index < path.length; index += stride) {
    best = Math.min(best, haversineKm(location, path[index]));
  }
  return best;
}

function stopFacts(tags) {
  const facts = [];
  if (tags.internet_access === "wlan" || tags.internet_access === "yes") facts.push("Wi-Fi listed");
  if (tags.takeaway === "yes" || tags.takeaway === "only") facts.push("takeaway available");
  if (tags.opening_hours) facts.push("opening hours recorded");
  return facts.length ? `${facts.join(", ")}. Ambience is not recorded.` : "Ambience is not recorded in OpenStreetMap.";
}

function outdoorSeating(tags) {
  if (tags.outdoor_seating === "no") return "none listed";
  if (tags.outdoor_seating === "yes") {
    return tags.covered === "yes" ? "outdoor seating listed as covered" : "outdoor seating listed; cover is not recorded";
  }
  return "not recorded";
}

async function fetchCoffeeStops(routes, origin, destination) {
  const mid = { lat: (origin.lat + destination.lat) / 2, lng: (origin.lng + destination.lng) / 2 };
  const routeDistanceKm = haversineKm(origin, destination);
  const radius = Math.min(8_000, Math.max(2_500, Math.round(routeDistanceKm * 450)));
  const query = `[out:json][timeout:20];(nwr["amenity"="cafe"](around:${radius},${mid.lat},${mid.lng});nwr["shop"="coffee"](around:${radius},${mid.lat},${mid.lng}););out center tags 40;`;

  let elements = [];
  try {
    const payload = await fetchJson(
      "https://overpass-api.de/api/interpreter",
      {
        method: "POST",
        headers: { "content-type": "application/x-www-form-urlencoded;charset=UTF-8", "user-agent": APP_USER_AGENT },
        body: new URLSearchParams({ data: query }),
      },
      30_000,
    );
    elements = payload.elements || [];
  } catch {
    elements = [];
  }

  const named = elements
    .map((element) => ({
      name: element.tags?.name || element.tags?.brand || "Unnamed coffee shop",
      tags: element.tags || {},
      location: { lat: Number(element.lat ?? element.center?.lat), lng: Number(element.lon ?? element.center?.lon) },
    }))
    .filter((stop) => Number.isFinite(stop.location.lat) && Number.isFinite(stop.location.lng));

  const ranked = named
    .map((stop) => ({ ...stop, nearestKm: Math.min(...routes.map((route) => distanceToPath(stop.location, route.path))) }))
    .sort((a, b) => a.nearestKm - b.nearestKm || a.name.localeCompare(b.name))
    .filter((stop, index, all) => all.findIndex((entry) => entry.name === stop.name) === index)
    .slice(0, 4);

  return ranked.map((stop, index) => {
    const distances = routes.map((route) => ({ route, km: distanceToPath(stop.location, route.path) }));
    const nearestDistance = Math.min(...distances.map((entry) => entry.km));
    const routeIds = distances
      .filter((entry) => entry.km <= Math.max(0.8, nearestDistance + 0.25))
      .map((entry) => entry.route.id);
    return {
      id: `stop_${index + 1}`,
      name: stop.name,
      detour_min: Math.max(2, Math.round((nearestDistance / 25) * 120)),
      atmosphere: stopFacts(stop.tags),
      outdoor_seating: outdoorSeating(stop.tags),
      route_ids: routeIds.length ? routeIds : [distances.sort((a, b) => a.km - b.km)[0].route.id],
      location: stop.location,
    };
  });
}

function fallbackStops(routes) {
  const route = routes[0];
  const fractions = [0.35, 0.55, 0.72];
  return fractions.map((fraction, index) => ({
    id: `stop_${index + 1}`,
    name: `Coffee candidate ${index + 1}`,
    detour_min: 3 + index * 3,
    atmosphere: "Live café details were unavailable; ambience is unknown.",
    outdoor_seating: "not recorded",
    route_ids: [route.id],
    location: route.path[Math.min(route.path.length - 1, Math.floor(route.path.length * fraction))],
  }));
}

async function buildPlan(input) {
  const originQuery = cleanText(input.origin, 240, "Start");
  const destinationQuery = cleanText(input.destination, 240, "Destination");
  const intent = cleanText(input.intent || DEFAULT_INTENT, 800, "Trip request");
  const cacheKey = JSON.stringify([originQuery.toLocaleLowerCase(), destinationQuery.toLocaleLowerCase(), intent]);
  if (planCache.has(cacheKey)) return planCache.get(cacheKey);

  const origin = await geocodePlace(originQuery);
  const destination = await geocodePlace(destinationQuery);
  const [routes, weather] = await Promise.all([fetchRoutes(origin, destination), fetchWeather(origin, destination)]);
  let stops = await fetchCoffeeStops(routes, origin, destination);
  if (!stops.length) stops = fallbackStops(routes);

  for (const route of routes) {
    route.nearby_stop_ids = stops.filter((stop) => stop.route_ids.includes(route.id)).map((stop) => stop.id);
  }

  const state = {
    scenario: "live_vibe_route",
    request: { intent },
    trip: {
      origin: origin.label,
      destination: destination.label,
      departure_local: new Date().toISOString(),
      travel_mode: "DRIVE",
    },
    weather,
    routes: routes.map(({ path, ...route }) => route),
    stops: stops.map(({ location, ...stop }) => stop),
    data_notes: {
      routing: "OSRM driving routes; live traffic is not included.",
      places: "Nearby café facts from OpenStreetMap; missing ambience or seating data is kept unknown.",
      weather: "Current midpoint conditions from Open-Meteo when available.",
    },
  };
  const plan = {
    state,
    map: {
      origin,
      destination,
      routes: routes.map(({ id, path }) => ({ id, path })),
      stops: stops.map(({ id, location }) => ({ id, location })),
    },
    state_digest: digestState(state),
  };
  planCache.set(cacheKey, plan);
  if (planCache.size > 20) planCache.delete(planCache.keys().next().value);
  return plan;
}

function normalizeState(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Route data is required.");
  const serialized = JSON.stringify(value);
  if (serialized.length > 64_000) throw new Error("Route data is too large.");
  const state = JSON.parse(serialized);
  if (typeof state.request?.intent !== "string" || !state.request.intent.trim()) throw new Error("Trip request is required.");
  if (!Array.isArray(state.routes) || state.routes.length < 1 || state.routes.length > 3) {
    throw new Error("Route data must contain one to three candidates.");
  }
  if (!Array.isArray(state.stops) || state.stops.length < 1 || state.stops.length > 4) {
    throw new Error("Route data must contain one to four coffee candidates.");
  }
  const routeIds = state.routes.map((route) => route.id);
  const stopIds = state.stops.map((stop) => stop.id);
  if (new Set(routeIds).size !== routeIds.length || new Set(stopIds).size !== stopIds.length) {
    throw new Error("Candidate IDs must be unique.");
  }
  return state;
}

function digestState(state) {
  return createHash("sha256").update(JSON.stringify(state)).digest("hex").slice(0, 12);
}

function routeCriterion(route) {
  return `${route.label}: ${route.duration_min} min, ${route.distance_mi} mi; ${route.traffic}; ${route.character}; ${route.weather_exposure}; nearby stops ${route.nearby_stop_ids.join(", ") || "none"}.`;
}

function stopCriterion(stop) {
  return `${stop.name}: ${stop.detour_min}-minute estimated detour; ${stop.atmosphere}; outdoor seating: ${stop.outdoor_seating}; available on ${stop.route_ids.join(", ")}.`;
}

function buildJevQuestions(state) {
  const routeCriteria = Object.fromEntries(state.routes.map((route) => [route.id, routeCriterion(route)]));
  routeCriteria.none = "None of the candidate routes is a reasonable match for the request.";
  const stopCriteria = Object.fromEntries(state.stops.map((stop) => [stop.id, stopCriterion(stop)]));
  stopCriteria.none = "None of the candidate stops satisfies the request and constraints.";

  const questions = {
    best_route: {
      type: "choice",
      instructions:
        "Which candidate in `routes` best satisfies `request.intent`, considering `weather`, travel time, route facts, and the stops available along that route?",
      criteria: routeCriteria,
    },
    best_stop: {
      type: "choice",
      instructions:
        "Which candidate in `stops` best satisfies `request.intent`, including any seating, weather, ambience, and detour constraints? Do not assume facts marked unknown or not recorded.",
      criteria: stopCriteria,
    },
    clarification_needed: {
      type: "noul",
      instructions:
        "Is `request.intent` too ambiguous or missing essential information to choose a route and coffee stop from the supplied candidates?",
      criteria: {
        true: "An essential preference or constraint is missing, so a useful choice cannot be made.",
        false: "The request and supplied state are sufficient to make a useful choice.",
      },
    },
  };

  for (const route of state.routes) {
    questions[`${route.id}_preference_fit`] = {
      type: "score",
      instructions: `How well does route \`${route.id}\` fit \`request.intent\`, considering all supplied facts in that route, \`weather\`, and its available stops?`,
      criteria: ROUTE_SCORE_LEVELS,
    };
    questions[`${route.id}_walking_comfort`] = {
      type: "score",
      instructions: `How comfortable is curbside and stop access for route \`${route.id}\` in \`weather\`, considering the request and only the supplied facts?`,
      criteria: WALKING_COMFORT_LEVELS,
    };
  }

  for (const stop of state.stops) {
    questions[`${stop.id}_matches`] = {
      type: "noul",
      instructions: `Does stop \`${stop.id}\` satisfy the coffee-stop preferences and hard constraints in \`request.intent\`, using only its supplied facts?`,
      criteria: {
        true: "The stop satisfies the stated preferences and hard constraints.",
        false: "The stop conflicts with a stated requirement, or a required fact is explicitly unknown.",
      },
    };
  }
  return questions;
}

function decisionSchema(state) {
  const routeIds = state.routes.map((route) => route.id);
  const stopIds = state.stops.map((stop) => stop.id);
  return {
    type: "object",
    properties: {
      best_route: { type: "string", enum: [...routeIds, "none"] },
      best_stop: { type: "string", enum: [...stopIds, "none"] },
      route_scores: {
        type: "object",
        properties: Object.fromEntries(
          routeIds.map((id) => [
            id,
            {
              type: "object",
              properties: {
                preference_fit: { type: "number", minimum: 0, maximum: 4 },
                walking_comfort: { type: "number", minimum: 0, maximum: 4 },
              },
              required: ["preference_fit", "walking_comfort"],
              additionalProperties: false,
            },
          ]),
        ),
        required: routeIds,
        additionalProperties: false,
      },
      stop_matches: {
        type: "object",
        properties: Object.fromEntries(stopIds.map((id) => [id, { type: "boolean" }])),
        required: stopIds,
        additionalProperties: false,
      },
      clarification_needed: { type: "boolean" },
    },
    required: ["best_route", "best_stop", "route_scores", "stop_matches", "clarification_needed"],
    additionalProperties: false,
  };
}

function assertDecision(decision, state) {
  const routeIds = new Set([...state.routes.map((route) => route.id), "none"]);
  const stopIds = new Set([...state.stops.map((stop) => stop.id), "none"]);
  if (!decision || !routeIds.has(decision.best_route) || !stopIds.has(decision.best_stop)) {
    throw new Error("Model output did not contain a valid route and stop choice.");
  }
  for (const route of state.routes) {
    const scores = decision.route_scores?.[route.id];
    for (const value of [scores?.preference_fit, scores?.walking_comfort]) {
      if (typeof value !== "number" || !Number.isFinite(value) || value < 0 || value > 4) {
        throw new Error(`Model output contained an invalid score for ${route.id}.`);
      }
    }
  }
  for (const stop of state.stops) {
    if (typeof decision.stop_matches?.[stop.id] !== "boolean") {
      throw new Error(`Model output contained an invalid match flag for ${stop.id}.`);
    }
  }
  if (typeof decision.clarification_needed !== "boolean") throw new Error("Model output contained an invalid clarification flag.");
  return decision;
}

async function judgeWithJev(state) {
  if (!TYPESAFE_API_KEY) throw new Error("Missing TYPESAFE_API_KEY (or JEV_API_KEY) in .env.");
  const questions = buildJevQuestions(state);
  const started = performance.now();
  const payload = await postJson(
    "https://api.typesafe.ai/v1/systemone",
    { authorization: `Bearer ${TYPESAFE_API_KEY}` },
    { state, model: JEV_MODEL, questions },
  );

  const answers = payload.answers || {};
  const decision = {
    best_route: answers.best_route?.choice,
    best_stop: answers.best_stop?.choice,
    route_scores: Object.fromEntries(
      state.routes.map((route) => [
        route.id,
        {
          preference_fit: answers[`${route.id}_preference_fit`]?.score,
          walking_comfort: answers[`${route.id}_walking_comfort`]?.score,
        },
      ]),
    ),
    stop_matches: Object.fromEntries(
      state.stops.map((stop) => [stop.id, (answers[`${stop.id}_matches`]?.noul ?? 0) >= 0.5]),
    ),
    clarification_needed: (answers.clarification_needed?.noul ?? 0) >= 0.5,
  };
  assertDecision(decision, state);

  return {
    engine: "jev",
    model: payload.model || JEV_MODEL,
    latency_ms: Math.round((performance.now() - started) * 10) / 10,
    schema_valid: true,
    question_count: Object.keys(questions).length,
    decision,
    usage: {
      input_tokens: payload.usage?.input_tokens ?? null,
      output_tokens: payload.usage?.output_tokens ?? null,
      total_tokens:
        typeof payload.usage?.input_tokens === "number" && typeof payload.usage?.output_tokens === "number"
          ? payload.usage.input_tokens + payload.usage.output_tokens
          : null,
    },
    evidence: {
      choice_confidence: { best_route: answers.best_route?.confidence ?? null, best_stop: answers.best_stop?.confidence ?? null },
      score_confidence: Object.fromEntries(
        state.routes.flatMap((route) => [
          [`${route.id}_preference_fit`, answers[`${route.id}_preference_fit`]?.confidence ?? null],
          [`${route.id}_walking_comfort`, answers[`${route.id}_walking_comfort`]?.confidence ?? null],
        ]),
      ),
      stop_probabilities: Object.fromEntries(
        state.stops.map((stop) => [stop.id, answers[`${stop.id}_matches`]?.noul ?? null]),
      ),
      clarification_probability: answers.clarification_needed?.noul ?? null,
    },
  };
}

function allowedOpenRouterModel(model) {
  const id = model.id?.toLocaleLowerCase() || "";
  const name = model.name?.toLocaleLowerCase() || "";
  return (
    id.startsWith("openai/") ||
    id.startsWith("deepseek/") ||
    id.startsWith("moonshotai/") ||
    id.startsWith("x-ai/") ||
    id.includes("/kimi") ||
    id.includes("/grok") ||
    name.includes("kimi") ||
    name.includes("grok")
  );
}

async function listOpenRouterModels() {
  if (!OPENROUTER_API_KEY) throw new Error("Missing OPENROUTER_API_KEY (or existing openouterkey) in .env.");
  if (openRouterModelsCache) return openRouterModelsCache;
  const payload = await fetchJson(
    "https://openrouter.ai/api/v1/models?sort=most-popular",
    { headers: { authorization: `Bearer ${OPENROUTER_API_KEY}` } },
    15_000,
  );
  openRouterModelsCache = (payload.data || [])
    .filter((model) => typeof model.id === "string" && allowedOpenRouterModel(model))
    .map((model) => ({ id: model.id, name: typeof model.name === "string" ? model.name : model.id }));
  if (!openRouterModelsCache.length) throw new Error("OpenRouter returned no matching OpenAI, DeepSeek, Kimi, or Grok models.");
  return openRouterModelsCache;
}

function extractOpenRouterText(payload) {
  const content = payload.choices?.[0]?.message?.content;
  if (typeof content === "string") return content;
  if (Array.isArray(content)) return content.map((part) => (typeof part === "string" ? part : part?.text || "")).join("");
  return "";
}

function parseDecisionJson(text) {
  const trimmed = text.trim();
  try {
    return JSON.parse(trimmed);
  } catch (directError) {
    const unfenced = trimmed.replace(/^```(?:json)?\s*/i, "").replace(/\s*```$/, "");
    try {
      return JSON.parse(unfenced);
    } catch {
      const objectStart = unfenced.indexOf("{");
      const objectEnd = unfenced.lastIndexOf("}");
      if (objectStart >= 0 && objectEnd > objectStart) return JSON.parse(unfenced.slice(objectStart, objectEnd + 1));
      throw directError;
    }
  }
}

async function judgeWithOpenRouter(state, requestedModel) {
  const models = await listOpenRouterModels();
  const configuredDefault = models.some((entry) => entry.id === OPENROUTER_MODEL) ? OPENROUTER_MODEL : models[0].id;
  const model = typeof requestedModel === "string" && requestedModel.trim() ? requestedModel.trim() : configuredDefault;
  if (!models.some((entry) => entry.id === model)) throw new Error("Choose an available OpenAI, DeepSeek, Kimi, or Grok model.");

  const started = performance.now();
  const payload = await postJson(
    "https://openrouter.ai/api/v1/chat/completions",
    {
      authorization: `Bearer ${OPENROUTER_API_KEY}`,
      "HTTP-Referer": `http://127.0.0.1:${PORT}`,
      "X-OpenRouter-Title": "Vibe Planner",
    },
    {
      model,
      messages: [
        {
          role: "system",
          content: `You are a route-and-stop decision engine. Evaluate only the supplied state. Return every requested field. Preference-fit and walking-comfort scores use numbers from 0 to 4. Do not invent facts; unknown place facts remain unknown. Return only one JSON object matching this schema exactly: ${JSON.stringify(decisionSchema(state))}`,
        },
        { role: "user", content: JSON.stringify(state) },
      ],
    },
    90_000,
  );
  const outputText = extractOpenRouterText(payload);
  if (!outputText) throw new Error("OpenRouter returned no decision text.");
  const decision = assertDecision(parseDecisionJson(outputText), state);
  return {
    engine: "llm",
    provider: "openrouter",
    model: payload.model || model,
    latency_ms: Math.round((performance.now() - started) * 10) / 10,
    schema_valid: true,
    decision,
    usage: {
      input_tokens: payload.usage?.prompt_tokens ?? null,
      output_tokens: payload.usage?.completion_tokens ?? null,
      total_tokens: payload.usage?.total_tokens ?? null,
    },
  };
}

function contentType(path) {
  return (
    {
      ".html": "text/html; charset=utf-8",
      ".css": "text/css; charset=utf-8",
      ".js": "text/javascript; charset=utf-8",
      ".json": "application/json; charset=utf-8",
      ".svg": "image/svg+xml",
      ".ico": "image/x-icon",
    }[extname(path)] || "application/octet-stream"
  );
}

function serveStatic(req, res, pathname) {
  const requested = pathname === "/" ? "index.html" : pathname.replace(/^\/+/, "");
  const safePath = normalize(requested).replace(/^(\.\.[/\\])+/, "");
  const path = join(publicDir, safePath);
  if (!path.startsWith(publicDir)) return jsonResponse(res, 404, { error: "Not found" });
  try {
    if (!statSync(path).isFile()) throw new Error("not a file");
    const body = readFileSync(path);
    res.writeHead(200, {
      "content-type": contentType(path),
      "cache-control": path.endsWith(".html") ? "no-store" : "public, max-age=60",
    });
    res.end(body);
  } catch {
    jsonResponse(res, 404, { error: "Not found" });
  }
}

const server = createServer(async (req, res) => {
  const url = new URL(req.url || "/", `http://${req.headers.host || "localhost"}`);
  try {
    if (req.method === "GET" && url.pathname === "/api/health") return jsonResponse(res, 200, { ok: true });
    if (req.method === "GET" && url.pathname === "/api/config") {
      const models = OPENROUTER_API_KEY ? await listOpenRouterModels() : [];
      return jsonResponse(res, 200, {
        credentials: { jev: Boolean(TYPESAFE_API_KEY), llm: Boolean(OPENROUTER_API_KEY) },
        models: {
          jev: JEV_MODEL,
          llm: models.some((model) => model.id === OPENROUTER_MODEL) ? OPENROUTER_MODEL : models[0]?.id || null,
          llm_provider: "OpenRouter",
          available: models,
        },
      });
    }
    if (req.method === "GET" && url.pathname === "/api/scenario") {
      return jsonResponse(res, 200, {
        origin: "Ferry Building, San Francisco",
        destination: "Dolores Park, San Francisco",
        intent: DEFAULT_INTENT,
      });
    }
    if (req.method === "POST" && url.pathname === "/api/plan") {
      return jsonResponse(res, 200, await buildPlan(await readJson(req)));
    }
    if (req.method === "POST" && (url.pathname === "/api/judge/jev" || url.pathname === "/api/judge/llm")) {
      const body = await readJson(req);
      const state = normalizeState(body.state);
      const result =
        url.pathname === "/api/judge/jev" ? await judgeWithJev(state) : await judgeWithOpenRouter(state, body.model);
      return jsonResponse(res, 200, { ...result, state_digest: digestState(state) });
    }
    if (req.method !== "GET" && req.method !== "HEAD") return jsonResponse(res, 405, { error: "Method not allowed" });
    serveStatic(req, res, url.pathname);
  } catch (error) {
    jsonResponse(res, 500, { error: error instanceof Error ? error.message : "Unexpected server error." });
  }
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(`Vibe Planner running at http://127.0.0.1:${PORT}`);
  console.log(`Engines: Jev ${TYPESAFE_API_KEY ? "ready" : "missing"}, OpenRouter ${OPENROUTER_API_KEY ? "ready" : "missing"}`);
});
