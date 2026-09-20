import { ACTIONS, LIMITS, gateDecision } from '/shared/contract.mjs';
const $ = id => document.getElementById(id);
const frame = $('sim'), channel = crypto.randomUUID();
const pending = new Map(), history = [];
let telemetry = { ready: false }, lastTelemetry = 0, config = null, editor = false, deciding = false, epoch = 0, toastTimer;
const send = (operation, data, id) => frame.contentWindow.postMessage({ namespace: 'flight-lab', channel, operation, data, id }, location.origin);
function rpc(operation, data = {}) {
  const id = crypto.randomUUID();
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { pending.delete(id); reject(new Error('Simulator did not respond. Try resetting or reloading the page.')); }, 11000);
    pending.set(id, { resolve, reject, timer }); send(operation, data, id);
  });
}
function notify(message, error = false) {
  $('toast').textContent = message; $('toast').classList.toggle('error', error); $('toast').hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { $('toast').hidden = true; }, 6500);
}
function log(message, detail = null) {
  const time = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
  history.unshift({ at: new Date().toISOString(), message, detail });
  if (history.length > 200) history.pop();
  const li = document.createElement('li'), stamp = document.createElement('time'), text = document.createElement('span');
  stamp.textContent = time; text.textContent = message; li.append(stamp, text); $('log').prepend(li);
  while ($('log').children.length > 12) $('log').lastChild.remove();
}
function renderTelemetry() {
  const online = telemetry.ready && Date.now() - lastTelemetry < 3000;
  $('loading').hidden = Boolean(online);
  $('connection-dot').classList.toggle('online', Boolean(online));
  $('send').disabled = !online || deciding || telemetry.busy;
  $('scene').disabled = !online;
  for (const button of document.querySelectorAll('[data-action]')) button.disabled = !online || deciding || telemetry.linkLost || telemetry.busy || (button.dataset.action === 'takeoff' ? telemetry.flying : !telemetry.flying);
  if (!online) return;
  $('flight-state').textContent = telemetry.linkLost ? 'Link lost' : telemetry.collision ? 'Recovering' : telemetry.busy ? 'Moving' : telemetry.flying ? 'Hovering' : 'On ground';
  $('altitude').replaceChildren(document.createTextNode(`${telemetry.y.toFixed(1)} `));
  const unit = document.createElement('small'); unit.textContent = 'u'; $('altitude').append(unit);
  $('position').textContent = `${telemetry.x.toFixed(1)} / ${telemetry.z.toFixed(1)}`;
  $('heading').textContent = `${(((telemetry.heading % 360) + 360) % 360).toFixed(0)}°`;
  $('link-loss').checked = telemetry.linkLost;
}
window.addEventListener('message', event => {
  if (event.source !== frame.contentWindow || event.origin !== location.origin || event.data?.namespace !== 'flight-lab' || event.data.channel !== channel) return;
  const message = event.data;
  if (message.kind === 'telemetry') {
    const wasReady = telemetry.ready;
    telemetry = message.state; lastTelemetry = Date.now(); renderTelemetry();
    if (!wasReady && telemetry.ready) log('Drone Commander connected. Simulation ready.');
  } else if (message.kind === 'event') { epoch++; log(message.message); notify(message.message); }
  else if (message.kind === 'reply') {
    const item = pending.get(message.id); if (!item) return;
    clearTimeout(item.timer); pending.delete(message.id);
    if (message.error) item.reject(new Error(message.error)); else item.resolve(message.result);
  }
});
frame.addEventListener('load', () => { telemetry = { ready: false }; send('init'); renderTelemetry(); });
setInterval(() => { send(lastTelemetry ? 'heartbeat' : 'init'); renderTelemetry(); }, 700);
send('init');

async function post(path, payload) {
  const response = await fetch(path, { method: 'POST', headers: { 'content-type': 'application/json', 'x-flight-lab': '1' }, body: JSON.stringify(payload), signal: AbortSignal.timeout(45000) });
  const result = await response.json(); if (!response.ok) throw new Error(result.error || 'Request failed.');
  return result;
}
function showDecision(result) {
  $('latency').textContent = `${result.latencyMs ?? 0} ms`;
  $('decision-action').textContent = ACTIONS[result.action] || 'No action';
  $('decision-detail').textContent = `${result.model || 'Direct control'} · ${result.amount} step${result.needsClarification ? ` · ${result.reasons?.join(', ') || 'clarification required'}` : ''}`;
  $('probabilities').replaceChildren();
  if (result.probabilities) {
    for (const [action, probability] of Object.entries(result.probabilities).sort((a, b) => b[1] - a[1]).slice(0, 3)) {
      const row = document.createElement('div'); row.className = 'probability';
      const label = document.createElement('span'), track = document.createElement('i'), fill = document.createElement('b'), value = document.createElement('small');
      label.textContent = action.replaceAll('_', ' '); fill.style.width = `${probability * 100}%`; value.textContent = `${Math.round(probability * 100)}%`;
      track.append(fill); row.append(label, track, value); $('probabilities').append(row);
    }
  }
}
async function runCommand() {
  if (deciding || !telemetry.ready) return;
  const currentEpoch = ++epoch;
  const request = $('command').value.trim(); if (!request) return;
  deciding = true; $('send').textContent = 'Classifying…'; renderTelemetry();
  try {
    const before = await rpc('state');
    const result = await post('/api/decide', { request, provider: $('provider').value, state: before });
    if (epoch !== currentEpoch) throw new Error('A newer control action cancelled this decision.');
    if (Date.now() - result.createdAt > LIMITS.maxDecisionAgeMs || Date.now() - before.capturedAt > LIMITS.maxStateAgeMs) throw new Error('Decision arrived too late. Please try again.');
    showDecision(result); log(`${result.provider}: ${ACTIONS[result.action]} · ${result.latencyMs} ms`, { request, result });
    const current = await rpc('state'); gateDecision(result, current, before.revision);
    $('send').textContent = 'Executing…'; await rpc('execute', { decision: result, revision: before.revision });
    log(`Completed: ${ACTIONS[result.action]}.`);
  } catch (error) { notify(error.message, true); log(`Not executed: ${error.message}`); }
  finally { deciding = false; $('send').innerHTML = 'Run command <span>↗</span>'; renderTelemetry(); }
}
$('command-form').addEventListener('submit', event => { event.preventDefault(); runCommand(); });
$('command').addEventListener('keydown', event => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); runCommand(); } });
document.querySelectorAll('[data-prompt]').forEach(button => button.addEventListener('click', () => { $('command').value = button.dataset.prompt; $('command').focus(); }));
document.querySelectorAll('[data-action]').forEach(button => button.addEventListener('click', async () => {
  epoch++;
  const decision = { action: button.dataset.action, amount: 'normal', needsClarification: false };
  try {
    const current = await rpc('state'); gateDecision(decision, current, current.revision);
    showDecision(decision); log(`Direct: ${ACTIONS[decision.action]}.`);
    await rpc('execute', { decision, revision: current.revision });
  } catch (error) { notify(error.message, true); log(error.message); }
}));
async function hold() {
  epoch++;
  try { await rpc('hold'); log('Hold requested. Pending AI decisions cancelled.'); notify('Holding position.'); }
  catch (error) { notify(error.message, true); }
}
$('hold').addEventListener('click', hold);
document.addEventListener('keydown', event => { if (event.key === 'Escape') { event.preventDefault(); hold(); } });
$('reset').addEventListener('click', async () => {
  epoch++; try { await rpc('reset'); log('Simulation reset to origin.'); notify('Simulator reset.'); } catch (error) { notify(error.message, true); }
});
$('editor').addEventListener('click', async () => {
  editor = !editor; await rpc('editor', { enabled: editor }).catch(error => notify(error.message, true));
  $('editor').textContent = editor ? 'Hide Blockly' : 'Show Blockly';
});
$('scene').addEventListener('change', async () => {
  epoch++;
  try { await rpc('scene', { file: $('scene').value }); log(`Scene: ${$('scene').selectedOptions[0].textContent}.`); }
  catch (error) { notify(error.message, true); }
});
$('link-loss').addEventListener('change', async () => {
  epoch++;
  try { await rpc('link', { lost: $('link-loss').checked }); log($('link-loss').checked ? 'Injected link loss. Flight commands blocked.' : 'Control link restored.'); }
  catch (error) { notify(error.message, true); }
});
$('collision').addEventListener('click', async () => {
  epoch++; await rpc('collision').catch(error => notify(error.message, true));
});
$('analyze').addEventListener('click', async () => {
  $('analyze').disabled = true; $('analyze').textContent = 'Reading camera…';
  try {
    const captured = await rpc('capture');
    $('vision-frame').src = captured.image; $('vision-result').hidden = false;
    $('vision-description').textContent = 'Vision model is reading this frame…'; $('vision-classification').textContent = '';
    const result = await post('/api/observe', { image: captured.image });
    $('vision-description').textContent = result.description;
    $('vision-classification').textContent = result.classification ? `Jev: ${result.classification.scene.replaceAll('_', ' ')} · visible person probability ${Math.round(result.classification.personProbability * 100)}% · ${result.latencyMs} ms` : 'Jev classification unavailable without a key.';
    log('Camera frame interpreted. No flight action taken.', result);
  } catch (error) { $('vision-description').textContent = error.message; notify(error.message, true); }
  finally { $('analyze').disabled = !config?.llm; $('analyze').textContent = 'Analyze camera frame ↗'; }
});
$('export').addEventListener('click', () => {
  const url = URL.createObjectURL(new Blob([JSON.stringify({ simulator: config?.upstream, exportedAt: new Date().toISOString(), events: history }, null, 2)], { type: 'application/json' }));
  const a = document.createElement('a'); a.href = url; a.download = `flight-lab-${Date.now()}.json`; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
});
$('hardware').addEventListener('click', async () => {
  $('hardware-status').textContent = 'Checking…';
  try { const response = await fetch('/api/hardware'); const status = await response.json(); $('hardware-status').textContent = status.connected ? 'Bridge reachable · flight disabled' : 'Bridge offline · flight disabled'; notify(status.message); }
  catch { $('hardware-status').textContent = 'Bridge unavailable'; }
});
function providerNote() {
  const provider = $('provider').value;
  $('engine-status').textContent = provider === 'offline' ? 'LOCAL' : 'LIVE API';
  $('provider-note').textContent = provider === 'offline' ? 'Exact example phrases work without API keys.' : provider === 'jev' ? 'Jev classifies one command. Local code checks and executes it.' : 'LLM returns the same bounded command contract.';
}
$('provider').addEventListener('change', providerNote);
try {
  config = await (await fetch('/api/config')).json();
  const jev = $('provider').querySelector('[value="jev"]'), llm = $('provider').querySelector('[value="llm"]');
  jev.disabled = !config.jev; jev.textContent = config.jev ? `Jev · ${config.jevModel}` : 'Jev · key not configured';
  llm.disabled = !config.llm; llm.textContent = config.llm ? `LLM · ${config.llmModel}` : 'LLM · key not configured';
  $('provider').value = config.jev ? 'jev' : 'offline'; providerNote();
  $('analyze').disabled = !config.llm;
  if (!config.llm) $('vision-note').textContent = 'Set the parent OpenRouter key to enable camera interpretation.';
} catch { notify('Could not read server configuration. Reload the page.', true); }

const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
if (!Recognition) { $('mic').disabled = true; $('mic').title = 'Speech recognition is unavailable in this browser. Type your command instead.'; }
else {
  const recognition = new Recognition(); recognition.lang = 'en-US'; recognition.interimResults = false; recognition.continuous = false;
  $('mic').addEventListener('click', () => { try { recognition.start(); $('mic').classList.add('listening'); notify('Listening. Dictation fills the box; press Run when ready.'); } catch (error) { notify(error.message, true); } });
  recognition.onresult = event => { $('command').value = event.results[0][0].transcript; $('command').focus(); };
  recognition.onerror = event => notify(`Dictation unavailable (${event.error}). You can type instead.`, true);
  recognition.onend = () => $('mic').classList.remove('listening');
}
