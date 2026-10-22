// Voice layer configuration.
//
// Keys live here, in the browser, because this build is a prototype: the
// Bhashini and LLM keys are entered in the Settings panel and kept in
// localStorage. That is a deliberate trade-off for iteration speed and it is
// NOT how this ships - a real deployment keeps keys server-side and this
// module becomes a thin client of the backend's /api/v1/voice routes.

const STORAGE_KEY = 'sampada.voice.config.v1';

export const DEFAULTS = {
  apiBase: 'http://127.0.0.1:8000/api/v1',
  bhashiniKey: '',
  bhashiniAsrUrl: 'https://api.bhashini.ai/v1/asr',
  bhashiniTtsUrl: 'https://api.bhashini.ai/v1/synthesize',
  ttsLanguage: 'Marathi',
  ttsVoice: 'Female1',
  llmKey: '',
  llmUrl: 'https://api.openai.com/v1/responses',
  llmModel: 'gpt-5.6-luna',
  lang: 'mr',
};

export function loadConfig() {
  let stored = {};
  try {
    stored = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}');
  } catch {
    stored = {};
  }
  return { ...DEFAULTS, ...stored };
}

export function saveConfig(patch) {
  const next = { ...loadConfig(), ...patch };
  localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  return next;
}

export function clearConfig() {
  localStorage.removeItem(STORAGE_KEY);
  return loadConfig();
}

// What the UI needs to show an honest status line rather than pretending a
// provider is wired up. The voice loop degrades: no Bhashini key means the
// browser's own speech recognition instead, no LLM key means answers come
// from the backend's cited data instead of a model.
export function capabilityReport(config) {
  return {
    stt: config.bhashiniKey ? 'bhashini' : 'browser',
    tts: config.bhashiniKey ? 'bhashini' : 'browser',
    brain: config.llmKey ? 'llm' : 'backend',
  };
}
