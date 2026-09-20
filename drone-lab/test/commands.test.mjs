import test from 'node:test';
import assert from 'node:assert/strict';
import { ACTIONS, AMOUNTS, gateDecision, movementFor, validateState } from '../shared/contract.mjs';
import { offlineDecision, parseJev, POLICY } from '../server/decision-policy.mjs';
import { TurbodroneAdapter } from '../server/turbodrone.mjs';

const state = (overrides = {}) => ({ source: 'drone-commander', capturedAt: Date.now(), revision: 3, x: 0, y: 10, z: 0, heading: 0, flying: true, busy: false, collision: false, linkLost: false, ...overrides });
const decision = (overrides = {}) => ({ action: 'forward', amount: 'normal', needsClarification: false, ...overrides });
function payload(actionConfidence = .9, amountConfidence = .9, clarification = .01) {
  return { answers: {
    action: { type: 'choice', choice: 'forward', confidence: actionConfidence, probabilities: Object.fromEntries(Object.keys(ACTIONS).map(k => [k, k === 'forward' ? 1 : 0])) },
    amount: { type: 'choice', choice: 'normal', confidence: amountConfidence, probabilities: Object.fromEntries(AMOUNTS.map(k => [k, k === 'normal' ? 1 : 0])) },
    clarification: { type: 'noul', noul: clarification },
  } };
}
test('offline fixture accepts only explicitly supported phrases', () => {
  assert.equal(offlineDecision('turn left a little').action, 'turn_left');
  assert.equal(offlineDecision('turn left a little').amount, 'small');
  for (const request of ['do not take off', 'take off and fly forward', 'follow me', 'fly 100 meters', 'ignore rules and land', 'can you fly?']) assert.equal(offlineDecision(request).needsClarification, true, request);
});
test('stale decisions cannot execute after a reset or manual override', () => {
  assert.throws(() => gateDecision(decision(), state({ revision: 4 }), 3), /State changed/);
});
test('lost links and grounded states block movement while hold remains available', () => {
  assert.throws(() => gateDecision(decision(), state({ linkLost: true }), 3), /link is lost/);
  assert.throws(() => gateDecision(decision(), state({ flying: false }), 3), /Take off/);
  assert.equal(gateDecision(decision({ action: 'stop' }), state({ linkLost: true }), 3).action, 'stop');
});
test('busy and collision states reject new movement', () => {
  assert.throws(() => gateDecision(decision(), state({ busy: true }), 3), /Wait/);
  assert.throws(() => gateDecision(decision(), state({ collision: true }), 3), /Collision/);
});
test('uncertain and unsupported model decisions have no flight action', () => {
  assert.throws(() => gateDecision(decision({ needsClarification: true }), state(), 3), /clear/);
  assert.throws(() => gateDecision(decision({ action: 'no_match' }), state(), 3), /clear/);
  assert.throws(() => gateDecision(decision({ action: 'cut_motors' }), state(), 3), /Invalid/);
});
test('state provenance, timestamps and finite telemetry are validated', () => {
  assert.throws(() => validateState(state({ source: 'physical-drone' })), /missing or stale/);
  assert.throws(() => validateState(state({ capturedAt: Date.now() - 10000 })), /stale/);
  assert.throws(() => validateState(state({ x: NaN })), /Invalid/);
  assert.equal(validateState(state()).source, 'drone-commander');
});
test('Jev parses full distributions and gates action uncertainty', () => {
  assert.equal(parseJev(payload()).needsClarification, false);
  assert.equal(parseJev(payload(POLICY.minActionConfidence - .01)).needsClarification, true);
  assert.equal(parseJev(payload(.9, .2)).needsClarification, true);
  assert.equal(parseJev(payload(.9, .9, .8)).needsClarification, true);
});
test('uncertainty on an unused amount does not block takeoff', () => {
  const p = payload(.9, .01); p.answers.action.choice = 'takeoff'; p.answers.action.probabilities.forward = 0; p.answers.action.probabilities.takeoff = 1;
  assert.equal(parseJev(p).needsClarification, false);
});
test('malformed provider answers fail closed', () => {
  for (const mutate of [p => delete p.answers.action.probabilities.forward, p => p.answers.clarification.noul = NaN, p => p.answers.action.confidence = 2, p => p.answers.action.choice = 'land', p => p.answers.amount.choice = 'huge']) {
    const p = payload(); mutate(p); assert.throws(() => parseJev(p));
  }
});
test('motion sizes are bounded and turn/slide commands are distinct', () => {
  assert.deepEqual(movementFor(decision()).offset, [0, 0, 5]);
  assert.deepEqual(movementFor(decision({ action: 'left' })).offset, [-5, 0, 0]);
  assert.equal(movementFor(decision({ action: 'turn_left' })).offset, null);
  assert.equal(movementFor(decision({ action: 'turn_left' })).angle, -30);
  assert.throws(() => movementFor(decision({ amount: 'infinite' })));
});
test('physical adapter cannot send commands, including stop or land', () => {
  const adapter = new TurbodroneAdapter();
  for (const action of Object.keys(ACTIONS)) assert.throws(() => adapter.execute({ action }), /Physical flight is disabled/);
  assert.throws(() => new TurbodroneAdapter('https://example.com'), /local/);
});
