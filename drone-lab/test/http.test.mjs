import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createServer } from 'node:net';
import { once } from 'node:events';

test('local HTTP flow serves upstream, classifies offline, and exposes no physical control', async t => {
  const probe = createServer(); probe.listen(0, '127.0.0.1'); await once(probe, 'listening');
  const port = probe.address().port; await new Promise(resolve => probe.close(resolve));
  const origin = `http://127.0.0.1:${port}`;
  const child = spawn(process.execPath, ['server/index.mjs'], {
    cwd: new URL('../', import.meta.url),
    env: { PATH: process.env.PATH, HOME: process.env.HOME, DRONE_LAB_PORT: String(port), TYPESAFE_API_KEY: 'fixture-do-not-expose', OPENROUTER_API_KEY: 'another-fixture-secret' },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  t.after(async () => { child.kill(); if (child.exitCode === null) await once(child, 'exit'); });
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('Test server did not start')), 5000);
    child.stdout.on('data', data => { if (data.toString().includes('Jev Flight Lab:')) { clearTimeout(timer); resolve(); } });
    child.on('exit', code => { clearTimeout(timer); reject(new Error(`Test server exited: ${code}`)); });
  });
  const configText = await (await fetch(`${origin}/api/config`)).text();
  assert.doesNotMatch(configText, /fixture|secret/);
  assert.equal(JSON.parse(configText).physicalFlightEnabled, false);
  const simulator = await (await fetch(`${origin}/sim/`)).text();
  assert.match(simulator, /sim-bridge\.mjs/); assert.match(simulator, /Blockly/);
  const payload = { request: 'move forward', provider: 'offline', state: { source: 'drone-commander', capturedAt: Date.now(), revision: 7, x: 0, y: 10, z: 0, heading: 0, flying: true } };
  const options = { method: 'POST', headers: { origin, 'content-type': 'application/json', 'x-flight-lab': '1' }, body: JSON.stringify(payload) };
  const response = await fetch(`${origin}/api/decide`, options);
  assert.equal(response.status, 200);
  const decision = await response.json(); assert.equal(decision.action, 'forward'); assert.equal(decision.revision, 7);
  assert.equal((await fetch(`${origin}/api/decide`, { ...options, headers: { ...options.headers, origin: 'https://example.com' } })).status, 403);
  assert.equal((await fetch(`${origin}/api/hardware/execute`, options)).status, 404);
  assert.equal((await fetch(`${origin}/sim/.git/config`)).status, 403);
  assert.equal((await fetch(`${origin}/.env`)).status, 403);
  assert.equal((await fetch(`${origin}/api/decide`, { ...options, body: JSON.stringify({ ...payload, state: { ...payload.state, source: 'physical-drone' } }) })).status, 400);
});
