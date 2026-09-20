import { LIMITS, gateDecision, movementFor } from '/shared/contract.mjs';

// Adapter for the pinned Drone Commander globals. Flight, scenes, camera and
// collisions remain upstream; the lab only validates and dispatches commands.
let channel = null, revision = 0, linkLost = false, initialized = false, terrainRequested = false;
let lastHeartbeat = Date.now();
let localBusy = false;
let changingScene = false, previousTerrain = null;
// Upstream resets immediately after asynchronous terrain loads, before the next
// render updates transforms. Refresh matrices before its existing height queries.
const originalIntersections = collisionRaycaster.intersectObjects.bind(collisionRaycaster);
collisionRaycaster.intersectObjects = (...args) => { if (typeof scene !== 'undefined' && scene) scene.updateMatrixWorld(true); return originalIntersections(...args); };
const emit = (kind, data) => { if (channel) parent.postMessage({ namespace: 'flight-lab', channel, kind, ...data }, location.origin); };
const ready = () => !changingScene && typeof drone !== 'undefined' && Boolean(drone?.mesh) && groundMeshes.length > 0;
function selectScene(file) {
  changingScene = true;
  previousTerrain = scene.children.find(object => object.userData.ground);
  loadScenario(file);
  document.getElementById('scenarioSelect').value = file;
}
function state() {
  if (!ready()) return { ready: false };
  return { ready: true, source: 'drone-commander', capturedAt: Date.now(), revision,
    x: drone.mesh.position.x, y: drone.altitude, z: drone.mesh.position.z, heading: drone.direction,
    flying: drone.flying, busy: Boolean(localBusy || activeCommand || run), collision: collisionEmergencyActive, linkLost };
}
function hold() {
  revision++;
  run = false; cancelDroneCommands(); localBusy = false;
  if (drone?.mesh) { drone.mesh.rotation.x = 0; drone.mesh.rotation.z = 0; }
  updateStatus();
}
function setEditor(enabled) {
  document.getElementById('leftPanel').style.display = enabled ? '' : 'none';
  document.getElementById('divider').style.display = enabled ? '' : 'none';
  document.getElementById('rightPanel').style.width = enabled ? '' : '100%';
  document.getElementById('statusPanel').style.display = enabled ? '' : 'none';
  document.getElementById('viewerToolbar').style.display = enabled ? '' : 'none';
  updateViewerCanvases();
  if (!enabled) fitViewport();
}
function fitViewport() {
  if (document.getElementById('leftPanel').style.display !== 'none') return;
  const container = document.getElementById('webglContainer');
  const width = container.clientWidth, height = container.clientHeight;
  if (!width || !height) return;
  renderer.setSize(width, height);
  renderer.domElement.style.cssText = 'position:absolute;inset:0;width:100%;height:100%';
  camera.aspect = width / height; camera.updateProjectionMatrix();
  const preview = document.getElementById('droneCameraPanel');
  preview.style.width = `${Math.min(210, width * .28)}px`; preview.style.height = `${Math.min(210, width * .28) * .75}px`; preview.style.left = '8px';
  updateDroneCameraCanvas();
}
function capture() {
  const width = 480, height = 360;
  const target = new THREE.WebGLRenderTarget(width, height);
  const previousTarget = renderer.getRenderTarget(), previousAspect = droneCamera.aspect;
  const viewport = renderer.getViewport(new THREE.Vector4());
  const scissor = renderer.getScissor(new THREE.Vector4()), scissorTest = renderer.getScissorTest();
  const pixels = new Uint8Array(width * height * 4);
  try {
    updateDroneCameraPose(); droneCamera.aspect = width / height; droneCamera.updateProjectionMatrix();
    renderer.setRenderTarget(target); renderer.setScissorTest(false); renderer.setViewport(0, 0, width, height);
    renderer.render(scene, droneCamera); renderer.readRenderTargetPixels(target, 0, 0, width, height, pixels);
  } finally {
    renderer.setRenderTarget(previousTarget); renderer.setViewport(viewport); renderer.setScissor(scissor); renderer.setScissorTest(scissorTest);
    droneCamera.aspect = previousAspect; droneCamera.updateProjectionMatrix(); target.dispose();
  }
  const canvas = document.createElement('canvas'); canvas.width = width; canvas.height = height;
  const context = canvas.getContext('2d'); const data = context.createImageData(width, height);
  for (let row = 0; row < height; row++) data.data.set(pixels.subarray((height - row - 1) * width * 4, (height - row) * width * 4), row * width * 4);
  context.putImageData(data, 0, 0);
  return canvas.toDataURL('image/jpeg', .8);
}
async function execute(decision, expectedRevision) {
  gateDecision(decision, state(), expectedRevision);
  if (decision.action === 'stop') { hold(); return state(); }
  if (decision.action === 'land') hold();
  const { offset, angle } = movementFor(decision);
  if (offset) {
    const relative = resolveRelativeOffset(...offset, drone.direction);
    const x = drone.mesh.position.x + relative.x, z = drone.mesh.position.z + relative.z;
    const altitude = drone.altitude + relative.y;
    if (altitude > LIMITS.maxAltitude || Math.hypot(x, z) > LIMITS.maxRadius) throw new Error('This step would cross the lab flight boundary.');
    if (altitude < getSafeAltitudeAt(x, z)) throw new Error('This step would intersect terrain or an obstacle. Use Land to descend to the surface.');
  }
  revision++;
  const executionRevision = revision;
  localBusy = true;
  const timeout = setTimeout(() => { if (revision === executionRevision) { hold(); emit('event', { message: 'Movement timeout: holding position.' }); } }, 8000);
  try {
    drone.speed = .5;
    if (offset) await drone.moveBy(...offset);
    else if (decision.action === 'takeoff') await drone.takeOff();
    else if (decision.action === 'land') await drone.land();
    else if (decision.action === 'return_home') await drone.returnToBase();
    else if (decision.action.startsWith('turn_')) await drone.changeAngle(angle);
    if (revision !== executionRevision) throw new Error('Command interrupted by a hold, reset, or collision.');
  } finally {
    clearTimeout(timeout);
    if (revision === executionRevision) { localBusy = false; revision++; }
  }
  return state();
}
window.addEventListener('message', async event => {
  if (event.source !== parent || event.origin !== location.origin || event.data?.namespace !== 'flight-lab') return;
  const message = event.data;
  if (message.operation === 'init' && typeof message.channel === 'string') { channel = message.channel; lastHeartbeat = Date.now(); return; }
  if (!channel || message.channel !== channel) return;
  if (message.operation === 'heartbeat') { lastHeartbeat = Date.now(); return; }
  if (!ready()) { emit('reply', { id: message.id, error: 'Simulator is still loading its models and terrain.' }); return; }
  try {
    let result;
    switch (message.operation) {
      case 'execute': result = await execute(message.data.decision, message.data.revision); break;
      case 'hold': hold(); result = state(); break;
      case 'reset': hold(); stopDroneSound(); resetScene(); linkLost = false; result = state(); break;
      case 'capture': result = { image: capture(), state: state() }; break;
      case 'editor': setEditor(Boolean(message.data.enabled)); result = {}; break;
      case 'scene': {
        if (!['campo.json', 'citta.json', 'metropoli.json', 'isola.json'].includes(message.data.file)) throw new Error('Unknown scene.');
        hold(); stopDroneSound(); resetScene(); selectScene(message.data.file);
        result = {}; break;
      }
      case 'link': linkLost = Boolean(message.data.lost); hold(); result = state(); break;
      case 'collision': if (!drone.flying) throw new Error('Take off before testing collision recovery.'); triggerCollisionEmergency(); result = state(); break;
      case 'state': result = state(); break;
      default: throw new Error('Unknown simulator operation.');
    }
    emit('reply', { id: message.id, result });
  } catch (error) { emit('reply', { id: message.id, error: error.message }); }
});

setInterval(() => {
  if (!terrainRequested && typeof drone !== 'undefined' && drone?.mesh) {
    terrainRequested = true;
    selectScene('campo.json');
  }
  if (changingScene && scene.children.some(object => object.userData.ground && object !== previousTerrain)) { changingScene = false; revision++; }
  if (!ready()) { emit('telemetry', { state: { ready: false } }); return; }
  if (!initialized) {
    initialized = true;
    // The bundled Three.js renders the camera target correctly at pixel ratio 1.
    applyDarkTheme(true, false); applyGraphicsProfile('balanced'); applyCameraPreviewVisibility(true, false); setEditor(false);
    const collisionHandler = window.handleDroneCollisionEmergency;
    window.handleDroneCollisionEmergency = () => { revision++; localBusy = false; collisionHandler(); emit('event', { message: 'Upstream collision recovery: command cancelled and simulated emergency landing.' }); };
    const style = document.createElement('style');
    style.textContent = 'html,body,#container{height:100%;margin:0}#rightPanel{border:0}#webglContainer{flex:1;min-height:0;aspect-ratio:auto;background:#18231c}';
    document.head.append(style);
    new ResizeObserver(fitViewport).observe(document.getElementById('webglContainer'));
    fitViewport();
    for (const id of ['runBtn', 'stopBtn', 'scenarioSelect', 'x', 'z', 'altitude', 'direction']) {
      document.getElementById(id).addEventListener(id.endsWith('Btn') ? 'click' : 'change', () => { revision++; }, true);
    }
    document.addEventListener('keydown', event => { if (event.key === 'Escape') { hold(); emit('event', { message: 'Hold requested with Escape.' }); } });
  }
  if (Date.now() - lastHeartbeat > 2500 && (activeCommand || run)) { hold(); emit('event', { message: 'Control panel disconnected: simulation paused in place.' }); }
  const current = state();
  emit('telemetry', { state: current });
}, 200);
