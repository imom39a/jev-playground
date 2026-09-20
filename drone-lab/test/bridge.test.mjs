import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { LIMITS, gateDecision, movementFor } from '../shared/contract.mjs';

function harness() {
  const listeners = {}, messages = [], calls = [];
  const parent = { postMessage: message => messages.push(message) };
  const mesh = { position: { x: 0, y: 10, z: 0 }, rotation: { x: 0, y: 0, z: 0 } };
  const drone = { mesh, altitude: 10, direction: 0, flying: true,
    moveBy: async (...args) => calls.push(['moveBy', ...args]),
    changeAngle: async angle => calls.push(['changeAngle', angle]),
    land: async () => { drone.flying = false; calls.push(['land']); },
    takeOff: async () => { drone.flying = true; calls.push(['takeOff']); },
    returnToBase: async () => calls.push(['returnToBase']),
  };
  const context = {
    LIMITS, gateDecision, movementFor, parent, drone, location: { origin: 'http://127.0.0.1:4180' },
    window: { addEventListener: (type, fn) => { listeners[type] = fn; } },
    groundMeshes: [{}], activeCommand: null, run: false, collisionEmergencyActive: false,
    cancelDroneCommands: () => calls.push(['cancel']), updateStatus() {},
    collisionRaycaster: { intersectObjects: () => [] },
    resolveRelativeOffset: (x, y, z) => ({ x, y, z }), getSafeAltitudeAt: () => 1.4,
    setInterval() {}, setTimeout, clearTimeout, console,
    scene: { children: [{ userData: { ground: true } }] },
    document: { getElementById: () => ({}) },
    stopDroneSound() {}, resetScene() {}, loadScenario() {},
  };
  const source = readFileSync(new URL('../public/sim-bridge.mjs', import.meta.url), 'utf8').replace(/^import .*\n/, '');
  vm.runInNewContext(source, context);
  const send = (operation, data = {}, extras = {}) => listeners.message({ source: parent, origin: context.location.origin, data: { namespace: 'flight-lab', channel: 'test', operation, id: String(messages.length), data }, ...extras });
  return { send, calls, messages, drone };
}
const move = { action: 'forward', amount: 'normal', needsClarification: false };
test('bridge dispatches a bounded action through the upstream command API', async () => {
  const h = harness(); await h.send('init'); await h.send('execute', { decision: move, revision: 0 });
  assert.deepEqual(h.calls, [['moveBy', 0, 0, 5]]);
  assert.equal(h.messages.at(-1).result.revision, 2);
});
test('hold invalidates a decision captured before it', async () => {
  const h = harness(); await h.send('init'); await h.send('hold'); await h.send('execute', { decision: move, revision: 0 });
  assert.match(h.messages.at(-1).error, /State changed/);
  assert.equal(h.calls.some(c => c[0] === 'moveBy'), false);
});
test('link-loss injection cancels execution and blocks later commands', async () => {
  const h = harness(); await h.send('init'); await h.send('link', { lost: true }); await h.send('execute', { decision: move, revision: 1 });
  assert.match(h.messages.at(-1).error, /link is lost/);
  assert.deepEqual(h.calls, [['cancel']]);
});
test('bridge enforces altitude limits before calling upstream', async () => {
  const h = harness(); h.drone.altitude = 34; await h.send('init');
  await h.send('execute', { decision: { ...move, action: 'up' }, revision: 0 });
  assert.match(h.messages.at(-1).error, /boundary/); assert.deepEqual(h.calls, []);
});
test('foreign origins cannot initialize or control the bridge', async () => {
  const h = harness(); await h.send('init', {}, { origin: 'https://example.com' });
  await h.send('execute', { decision: move, revision: 0 });
  assert.deepEqual(h.calls, []); assert.deepEqual(h.messages, []);
});
test('in-flight promise cannot resume after hold', async () => {
  const h = harness(); let complete;
  h.drone.moveBy = () => new Promise(resolve => { complete = resolve; });
  await h.send('init'); const running = h.send('execute', { decision: move, revision: 0 });
  await h.send('hold'); complete(); await running;
  assert.match(h.messages.at(-1).error, /interrupted/);
});
test('a scene switch blocks commands until new terrain has arrived', async () => {
  const h = harness(); await h.send('init'); await h.send('scene', { file: 'isola.json' });
  await h.send('execute', { decision: move, revision: 1 });
  assert.match(h.messages.at(-1).error, /still loading/);
  assert.equal(h.calls.some(c => c[0] === 'moveBy'), false);
});
