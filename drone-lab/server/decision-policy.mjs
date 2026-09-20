import { ACTIONS, AMOUNTS, validateDecision } from '../shared/contract.mjs';

// Demo thresholds, intentionally beside the rubrics. These are uncalibrated;
// passing them never authorizes physical flight or bypasses deterministic gates.
export const POLICY = Object.freeze({ minActionConfidence: .65, minAmountConfidence: .5, maxClarificationProbability: .35 });
// Observation-only classification; these answers have no flight threshold.
export const VISION_QUESTIONS = Object.freeze({
  scene: { type: 'choice', instructions: 'Classify the environment explicitly described in `description`. The description may be mistaken. Do not infer flight safety.', criteria: { open_terrain: 'Mostly grass, fields or open land.', built_up: 'Predominantly buildings, streets or other built structures.', water: 'Predominantly water.', unknown: 'Insufficient or conflicting evidence.' } },
  person: { type: 'noul', instructions: 'Does `description` explicitly report a visible person? Uncertain or absent evidence should not count as confirmation.' },
});
export const QUESTIONS = Object.freeze({
  action: {
    type: 'choice',
    instructions: 'Choose the single action explicitly requested by `request` for this simulated drone. Direction words refer to the drone, not the camera. Turning rotates the heading; sliding moves sideways. Do not invent a sequence. Unsupported tasks (follow a person, navigate to an object, flips), multi-step requests, questions about capabilities, negated flight requests, or ambiguous instructions must choose no_match. stop means hold position, never cut motors.',
    criteria: {
      takeoff: 'Launch from the ground.', land: 'Descend and land.', stop: 'Stop movement and hold current position.',
      forward: 'Move forward.', backward: 'Move backward.', left: 'Move or slide sideways left.', right: 'Move or slide sideways right.',
      up: 'Increase altitude.', down: 'Decrease altitude.', turn_left: 'Rotate/yaw left.', turn_right: 'Rotate/yaw right.',
      return_home: 'Return to the starting location.', no_match: 'Unsupported, ambiguous, negated, informational, or multiple actions.',
    },
  },
  amount: {
    type: 'choice', instructions: 'Assuming `request` specifies a supported movement, select its qualitative size. Never infer precise real-world distances from simulator units. Small means a little/slightly; normal is the unspecified default; large is farther/a lot. This answer is ignored for takeoff, land, stop and return home.',
    criteria: { small: 'A little, slightly, a tiny adjustment.', normal: 'Ordinary/default movement; no size modifier.', large: 'A larger/farther movement.' },
  },
  clarification: {
    type: 'noul', instructions: 'Does the user request require clarification? Answer near 0 for one supported command, including "move forward", "turn left", "take off", "land", "stop", "go up", and "return home". An omitted distance, angle, speed or duration is NOT ambiguity: the controller has a normal step default. "A little" and "a lot" are supported size modifiers. Answer near 1 only when the request actually contains multiple actions, an explicit numerical distance/angle/duration, a named or ambiguous destination, unsupported follow/tracking/flip tasks, a negation, or a question about capabilities. Supported commands use drone-relative directions. Do not add imagined requirements or judge whether a supported command is currently executable; deterministic code checks flight state.',
  },
});

function choiceAnswer(answer, options) {
  if (answer?.type !== 'choice' || !options.includes(answer.choice) || !Number.isFinite(answer.confidence) || answer.confidence < 0 || answer.confidence > 1) throw new Error('Jev returned an invalid choice answer.');
  const p = answer.probabilities;
  if (!p || Object.keys(p).length !== options.length || !options.every(k => Number.isFinite(p[k]) && p[k] >= 0 && p[k] <= 1) || Math.abs(Object.values(p).reduce((a, b) => a + b, 0) - 1) > .03) throw new Error('Jev returned an invalid probability distribution.');
  if (options.some(k => p[k] > p[answer.choice] + .0001)) throw new Error('Jev selection does not match its distribution.');
  return answer;
}
export function parseJev(payload) {
  const action = choiceAnswer(payload?.answers?.action, Object.keys(ACTIONS));
  const amount = choiceAnswer(payload?.answers?.amount, AMOUNTS);
  const clarification = payload?.answers?.clarification;
  if (clarification?.type !== 'noul' || !Number.isFinite(clarification.noul) || clarification.noul < 0 || clarification.noul > 1) throw new Error('Jev returned an invalid clarification answer.');
  const consumesAmount = !['takeoff', 'land', 'stop', 'return_home', 'no_match'].includes(action.choice);
  const reasons = [];
  if (action.confidence < POLICY.minActionConfidence) reasons.push('Uncertain action');
  if (consumesAmount && amount.confidence < POLICY.minAmountConfidence) reasons.push('Uncertain step size');
  if (clarification.noul > POLICY.maxClarificationProbability) reasons.push('Request needs clarification');
  if (action.choice === 'no_match') reasons.push('Unsupported command');
  return {
    ...validateDecision({ action: action.choice, amount: amount.choice, needsClarification: reasons.length > 0 }),
    confidence: action.confidence, amountConfidence: amount.confidence, probabilities: action.probabilities, clarificationProbability: clarification.noul, reasons,
  };
}

// An explicit phrase table is an offline test fixture, not an AI imitation.
export function offlineDecision(request) {
  const text = request.trim().toLowerCase().replace(/[.!]$/, '');
  const phrases = {
    'take off': 'takeoff', 'takeoff': 'takeoff', 'land': 'land', 'stop': 'stop', 'hold position': 'stop',
    'move forward': 'forward', 'forward': 'forward', 'move backward': 'backward', 'backward': 'backward',
    'slide left': 'left', 'slide right': 'right', 'turn left': 'turn_left', 'turn right': 'turn_right',
    'go up': 'up', 'go down': 'down', 'return home': 'return_home',
  };
  const small = text.endsWith(' a little');
  const normalized = small ? text.slice(0, -9) : text;
  const action = phrases[normalized] || 'no_match';
  return { action, amount: small ? 'small' : 'normal', needsClarification: action === 'no_match', confidence: null, probabilities: null };
}
