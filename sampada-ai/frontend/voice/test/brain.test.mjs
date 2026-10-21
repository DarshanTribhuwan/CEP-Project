// The brain's two paths: a streaming LLM, and the backend-grounded fallback.
// The LLM is exercised with a scripted fetch so the streaming parser can be
// checked without a key or a network call.

import { answerStream } from '../js/brain.js';

const results = [];
function check(name, condition, detail = '') {
  results.push({ name, pass: Boolean(condition), detail });
}

const originalFetch = globalThis.fetch;

function sseStream(chunks) {
  return {
    ok: true,
    body: {
      getReader() {
        let i = 0;
        return {
          read: async () =>
            i < chunks.length
              ? { value: new TextEncoder().encode(chunks[i++]), done: false }
              : { value: undefined, done: true },
        };
      },
    },
  };
}

function frame(delta) {
  return `data: ${JSON.stringify({ type: 'response.output_text.delta', delta })}\n\n`;
}

const config = { llmKey: 'k', llmUrl: 'https://llm.test/v1/responses', llmModel: 'm' };
const noBrain = { config: { llmKey: '' }, backend: { async *answer() {} } };

// --- streamed deltas become speakable sentences ------------------------------
{
  globalThis.fetch = async () =>
    sseStream([
      frame('नमस्कार। '),
      frame('तुमचे स्वागत आहे. '),
      frame('सोयाबीन चांगली ठरते.'),
      'data: [DONE]\n\n',
    ]);

  const spoken = [];
  let full = '';
  for await (const chunk of answerStream('काय पेरणी?', { config, backend: null, signal: undefined })) {
    spoken.push(chunk.sentence);
    full = chunk.text;
  }

  // The terminator is kept on the sentence: a trailing danda or full stop gives
  // the synthesiser a natural pause, and stripping it would leave the audio
  // ending abruptly.
  check('sentences cut from the stream', JSON.stringify(spoken) === JSON.stringify([
    'नमस्कार।',
    'तुमचे स्वागत आहे.',
    'सोयाबीन चांगली ठरते.',
  ]), JSON.stringify(spoken));

  check('running text accumulates', full === 'नमस्कार। तुमचे स्वागत आहे. सोयाबीन चांगली ठरते.', full);
  // `final` marks the sentence produced by the end-of-stream flush, which is
  // the one that cannot grow any further. Sentences cut mid-stream are not
  // final: more text may still extend them.
  const finals = [];
  globalThis.fetch = async () => sseStream([frame('पहिली वाक्य. '), frame('शेवटचे अपूर्ण')]);
  for await (const chunk of answerStream('x', { config, backend: null, signal: undefined })) {
    finals.push([chunk.sentence, chunk.final]);
  }
  check('only the flushed tail is marked final',
    JSON.stringify(finals) === JSON.stringify([['पहिली वाक्य.', false], ['शेवटचे अपूर्ण', true]]),
    JSON.stringify(finals));
}

// --- a sentence split across many deltas is held until it is complete --------
{
  globalThis.fetch = async () =>
    sseStream([
      frame('नम'),
      frame('स्का'),
      frame('र। '),
      frame('छान'),
      frame(' दिवस.'),
    ]);

  const spoken = [];
  for await (const chunk of answerStream('x', { config, backend: null, signal: undefined })) {
    spoken.push(chunk.sentence);
  }
  check('partial word never spoken', JSON.stringify(spoken) === JSON.stringify(['नमस्कार।', 'छान दिवस.']),
    JSON.stringify(spoken));
}

// --- an unterminated tail is still spoken at end of stream --------------------
{
  globalThis.fetch = async () => sseStream([frame('शेवटचे वाक्य पूर्ण नाही')]);

  const spoken = [];
  for await (const chunk of answerStream('x', { config, backend: null, signal: undefined })) {
    spoken.push(chunk.sentence);
  }
  check('tail released on flush', JSON.stringify(spoken) === JSON.stringify(['शेवटचे वाक्य पूर्ण नाही']),
    JSON.stringify(spoken));
}

// --- unrelated and malformed events are ignored, not fatal --------------------
{
  globalThis.fetch = async () =>
    sseStream([
      'event: ping\n\n',
      'data: {not json}\n\n',
      frame('ठीक आहे. '),
      'data: [DONE]\n\n',
    ]);

  const spoken = [];
  for await (const chunk of answerStream('x', { config, backend: null, signal: undefined })) {
    spoken.push(chunk.sentence);
  }
  check('noise ignored', JSON.stringify(spoken) === JSON.stringify(['ठीक आहे.']), JSON.stringify(spoken));
}

// --- an HTTP error is reported, never spoken as if it were an answer ----------
{
  globalThis.fetch = async () => ({ ok: false, status: 429, text: async () => 'rate limited' });
  let message = '';
  try {
    for await (const _ of answerStream('x', { config, backend: null, signal: undefined })) { /* drain */ }
  } catch (error) {
    message = error.message;
  }
  check('LLM error surfaces', message.includes('429') && message.includes('rate limited'), message);
}

// --- no key means the backend answers, and nothing else ----------------------
{
  let called = false;
  const backend = {
    async *answer() {
      called = true;
      yield 'सोयाबीन शिफारस केली आहे.';
    },
  };

  const spoken = [];
  for await (const chunk of answerStream('काय पेरणी?', { config: noBrain.config, backend })) {
    spoken.push(chunk.sentence);
  }
  check('falls back to the backend', called && JSON.stringify(spoken) === JSON.stringify(['सोयाबीन शिफारस केली आहे.']),
    JSON.stringify(spoken));
}

globalThis.fetch = originalFetch;

let failed = 0;
for (const r of results) {
  if (!r.pass) failed += 1;
  console.log(`${r.pass ? 'PASS' : 'FAIL'}  ${r.name}${r.pass || !r.detail ? '' : '  -> ' + r.detail}`);
}
console.log(`\n${results.length - failed}/${results.length} passed`);
process.exit(failed ? 1 : 0);
