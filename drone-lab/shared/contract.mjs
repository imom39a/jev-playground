export const ACTIONS = Object.freeze({
  takeoff: 'Take off', land: 'Land', stop: 'Hold position', forward: 'Forward', backward: 'Backward',
  left: 'Slide left', right: 'Slide right', up: 'Ascend', down: 'Descend',
  turn_left: 'Turn left', turn_right: 'Turn right', return_home: 'Return to origin', no_match: 'Clarify request',
});
export const AMOUNTS = Object.freeze(['small', 'normal', 'large']);
export const LIMITS = Object.freeze({ maxAltitude: 35, maxRadius: 80, maxStateAgeMs: 8000, maxDecisionAgeMs: 10000 });
export function validateDecision(value) {
  if (!value || !Object.hasOwn(ACTIONS, value.action) || !AMOUNTS.includes(value.amount) || typeof value.needsClarification !== 'boolean') throw new Error('Invalid decision returned; no command will run.');
  return { action: value.action, amount: value.amount, needsClarification: value.needsClarification };
}
export function validateState(value, now = Date.now()) {
  if (!value || value.source !== 'drone-commander' || !Number.isFinite(value.capturedAt) || now - value.capturedAt > LIMITS.maxStateAgeMs || value.capturedAt > now + 1000) throw new Error('Simulator state is missing or stale.');
  if (!['x', 'y', 'z', 'heading'].every(k => Number.isFinite(value[k])) || typeof value.flying !== 'boolean' || !Number.isInteger(value.revision) || value.revision < 0) throw new Error('Invalid simulator state.');
  return { source: value.source, capturedAt: value.capturedAt, revision: value.revision, x: value.x, y: value.y, z: value.z, heading: value.heading, flying: value.flying, busy: Boolean(value.busy), linkLost: Boolean(value.linkLost), collision: Boolean(value.collision) };
}
export function gateDecision(decision, state, expectedRevision) {
  validateDecision(decision);
  if (state.revision !== expectedRevision) throw new Error('State changed while deciding. Send the command again.');
  if (decision.needsClarification || decision.action === 'no_match') throw new Error('Please give one clear, supported command. Nothing moved.');
  if (state.linkLost && decision.action !== 'stop') throw new Error('Simulated link is lost. Restore it first.');
  if (state.collision) throw new Error('Collision recovery is active. Wait for landing.');
  if (state.busy && !['stop', 'land'].includes(decision.action)) throw new Error('Wait for the current movement to finish.');
  if (decision.action === 'takeoff' && state.flying) throw new Error('The drone is already airborne.');
  if (!state.flying && !['takeoff', 'stop'].includes(decision.action)) throw new Error('Take off before sending flight commands.');
  return decision;
}
export function movementFor(decision) {
  validateDecision(decision);
  const i = AMOUNTS.indexOf(decision.amount);
  const distance = [2, 5, 10][i], altitude = [2, 4, 6][i], angle = [15, 30, 45][i];
  const moves = {
    forward: [0, 0, distance], backward: [0, 0, -distance], left: [-distance, 0, 0], right: [distance, 0, 0],
    up: [0, altitude, 0], down: [0, -altitude, 0],
  };
  return { offset: moves[decision.action] || null, angle: decision.action === 'turn_left' ? -angle : angle };
}
