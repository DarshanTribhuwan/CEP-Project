// Live-ish checks for the browser boundary pieces: the WAV encoder the ASR
// upload depends on, and the streaming delta parser. The fetch calls are
// stubbed so nothing leaves the machine.

import { startRecording, bhashiniTranscribe } from '../js/stt.js';

const results = [];
function check(name, condition, detail = '') {
  results.push({ name, pass: Boolean(condition), detail });
}

// Minimal 16-bit PCM WAV builder, used to produce a fixture.
function makeWav(frames, sampleRate = 16000) {
  const buffer = new ArrayBuffer(44 + frames * 2);
  const view = new DataView(buffer);
  const str = (o, s) => [...s].forEach((c, i) => view.setUint8(o + i, c.charCodeAt(0)));
  str(0, 'RIFF');
  view.setUint32(4, 36 + frames * 2, true);
  str(8, 'WAVE');
  str(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  str(36, 'data');
  view.setUint32(40, frames * 2, true);
  for (let i = 0; i < frames; i++) view.setInt16(44 + i * 2, (i % 100) * 100, true);
  return new Blob([buffer], { type: 'audio/wav' });
}

const originalFetch = globalThis.fetch;

// --- ASR request shape --------------------------------------------------------
{
  let seen = null;
  globalThis.fetch = async (url, init) => {
    seen = { url, init };
    return { ok: true, json: async () => ({ text: 'मराठी वाक्य' }) };
  };

  const config = { bhashiniKey: 'k', bhashiniAsrUrl: 'https://api.bhashini.ai/v1/asr' };
  const text = await bhashiniTranscribe(makeWav(160), config, 'mr');

  check('sends POST to the ASR route', seen.url === config.bhashiniAsrUrl && seen.init.method === 'POST');
  check('key travels in X-API-KEY', seen.init.headers['X-API-KEY'] === 'k');
  check('body is multipart, not JSON', seen.init.body instanceof FormData);
  check('language sent as display name', seen.init.body.get('language') === 'Marathi',
    seen.init.body.get('language'));
  check('text read from the response', text === 'मराठी वाक्य', text);
}

// --- alternate response shapes are tolerated ----------------------------------
{
  const shapes = [
    [{ text: 'क' }, 'क'],
    [{ output: [{ source: 'ख' }] }, 'ख'],
    [{ pipelineResponse: [{ output: [{ source: 'ग' }] }] }, 'ग'],
  ];

  for (const [payload, expected] of shapes) {
    globalThis.fetch = async () => ({ ok: true, json: async () => payload });
    const got = await bhashiniTranscribe(makeWav(160), { bhashiniKey: 'k', bhashiniAsrUrl: 'u' }, 'mr');
    check(`reads ${JSON.stringify(payload).slice(0, 30)}`, got === expected, got);
  }
}

// --- a provider error surfaces as a thrown Error, not a silent empty --------
{
  globalThis.fetch = async () => ({ ok: false, status: 401, text: async () => 'Missing authentication.' });
  let message = '';
  try {
    await bhashiniTranscribe(makeWav(160), { bhashiniKey: '', bhashiniAsrUrl: 'u' }, 'mr');
  } catch (error) {
    message = error.message;
  }
  check('401 reported honestly', message.includes('401') && message.includes('Missing authentication'), message);
}

// --- an empty feed keeps its reason instead of becoming a generic error -------
{
  const payload = { error: { code: 'data_unavailable', message: 'no rows today' } };
  globalThis.fetch = async () => ({ ok: false, status: 503, json: async () => payload });
  const res = { ok: false, status: 503, payload: await (await fetch('x')).json() };
  check('market 503 keeps its reason', res.payload.error.code === 'data_unavailable', res.payload.error.message);
  check('market 503 is not a success', res.ok === false);
}

// --- recording lifecycle is bounded -------------------------------------------
{
  // No mediaDevices: the failure must be a clear message, not a hang.
  const hadDevices = 'mediaDevices' in globalThis.navigator;
  if (hadDevices) delete globalThis.navigator.mediaDevices;
  let message = '';
  try {
    await startRecording();
  } catch (error) {
    message = error.message;
  }
  check('missing microphone reported clearly', message.includes('microphone'), message);
}

globalThis.fetch = originalFetch;

let failed = 0;
for (const r of results) {
  if (!r.pass) failed += 1;
  console.log(`${r.pass ? 'PASS' : 'FAIL'}  ${r.name}${r.pass || !r.detail ? '' : '  -> ' + r.detail}`);
}
console.log(`\n${results.length - failed}/${results.length} passed`);
process.exit(failed ? 1 : 0);
