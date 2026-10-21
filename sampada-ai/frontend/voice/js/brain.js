// The brain behind the voice loop.
//
// Two paths, and the choice is made by whether an LLM key exists:
//
//  1. A streaming LLM. Same shape as the reference implementation - stream the
//     Responses API and cut the text into sentences as they complete, so speech
//     starts before the answer is finished.
//  2. The backend's own data. No key, no model, no invented numbers: the answer
//     is assembled from /crops, /weather, /market/prices and /recommendations,
//     including their honest-unavailable states. This is what the project's
//     data-governance rules require, and it is the default until a key is set.
//
// The LLM path is told to keep to short spoken sentences and to route factual
// questions about prices, weather and recommendations to the backend rather
// than answering from memory, but that is a prompt, not a guarantee. Nothing
// the LLM says about a number is cited.

import { SentenceBuffer, splitLong } from './sentences.js';

const SYSTEM_PROMPT = `You are the voice of SAMPADA AI, a decision-support assistant for smallholder farmers in Maharashtra. You are being listened to, not read.

Rules:
- Answer in the same language the farmer used. Default to Marathi.
- Two or three short sentences. Never a list, never a heading, never markdown.
- End every sentence cleanly so it can be spoken as soon as it is written.
- You give estimates with a confidence level, never guarantees. Say so when it matters.
- For prices, weather, soil and crop recommendations, do NOT answer from memory. Say you will check, and let the app's own data be used.
- If you do not know, say you do not know.`;

async function* streamLlm(userText, config, signal) {
  const res = await fetch(config.llmUrl, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${config.llmKey}`,
    },
    body: JSON.stringify({
      model: config.llmModel,
      instructions: SYSTEM_PROMPT,
      input: userText,
      stream: true,
    }),
    signal,
  });

  if (!res.ok) {
    const detail = await res.text().catch(() => '');
    throw new Error(`LLM ${res.status}: ${detail.slice(0, 200)}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  const buffer = new SentenceBuffer();

  let carry = '';
  let full = '';

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;

    carry += decoder.decode(value, { stream: true });
    const events = carry.split('\n\n');
    carry = events.pop() ?? '';

    for (const event of events) {
      const payload = event
        .split('\n')
        .filter((line) => line.startsWith('data: '))
        .map((line) => line.slice(6))
        .join('\n');

      if (!payload || payload === '[DONE]') continue;

      let parsed;
      try {
        parsed = JSON.parse(payload);
      } catch {
        continue;
      }

      const delta = textDelta(parsed);
      if (!delta) continue;

      full += delta;
      for (const sentence of buffer.push(delta)) {
        yield { text: full, sentence, final: false };
      }
    }
  }

  for (const sentence of buffer.flush()) {
    for (const part of splitLong(sentence)) {
      yield { text: full, sentence: part, final: true };
    }
  }
}

// The Responses API streams typed events; older chat-completions deployments
// stream a single shape. Accepting both keeps the key pluggable.
function textDelta(event) {
  if (event.type === 'response.output_text.delta') return event.delta || '';
  if (event.type === 'response.text.delta') return event.delta || '';
  if (event.choices) return event.choices[0]?.delta?.content || '';
  return '';
}

export async function* answerStream(userText, context) {
  const { config, backend, signal } = context;

  if (config.llmKey) {
    yield* streamLlm(userText, config, signal);
    return;
  }

  for await (const sentence of backend.answer(userText, config)) {
    yield { text: sentence, sentence, final: true };
  }
}
