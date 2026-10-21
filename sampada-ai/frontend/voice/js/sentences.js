// Sentence segmentation for streaming text.
//
// The LLM emits tokens; speech needs sentences. Feeding half a sentence to TTS
// produces audio that cuts words off, so text is buffered until a terminator
// appears and only then is it handed to the synthesiser.
//
// Marathi text uses the danda (।) far more than the full stop, and both it and
// '.' can appear inside abbreviations or decimals, so the splitter is
// conservative: it only breaks on a terminator followed by whitespace, a
// closing bracket, or end of buffer.

const HARD_STOPS = '।.!?';
const SOFT_STOPS = ',;:—';

export class SentenceBuffer {
  constructor() {
    this.buffer = '';
  }

  push(delta) {
    this.buffer += delta;
    return this.drain(false);
  }

  flush() {
    return this.drain(true);
  }

  drain(final) {
    const out = [];
    let rest = this.buffer;

    while (rest.length) {
      const cut = this.findCut(rest, final);
      if (cut === -1) break;

      const sentence = rest.slice(0, cut).trim();
      rest = rest.slice(cut);
      if (sentence) out.push(sentence);
    }

    this.buffer = rest;
    return out;
  }

  // Returns the index just past the terminator, or -1 when more text is needed.
  findCut(text, final) {
    for (let i = 0; i < text.length; i++) {
      const ch = text[i];
      if (!HARD_STOPS.includes(ch)) continue;

      // A decimal point or a version number is not a sentence end.
      if (ch === '.' && /\d/.test(text[i - 1] || '') && /\d/.test(text[i + 1] || '')) {
        continue;
      }

      const next = text[i + 1];
      if (next !== undefined && !/[\s)\]}।.!?]/.test(next)) continue;

      return i + 1;
    }

    if (final && text.trim()) return text.length;
    return -1;
  }

  // Used when a stream ends mid-sentence: long tails are better spoken in one
  // go than dropped.
  isLong() {
    return this.buffer.trim().length > 240;
  }
}

export function splitLong(sentence, limit = 240) {
  if (sentence.length <= limit) return [sentence];

  const parts = [];
  let rest = sentence;
  while (rest.length > limit) {
    const window = rest.slice(0, limit);
    const cut = window.lastIndexOf(' ');
    const at = cut > limit * 0.5 ? cut : limit;
    parts.push(rest.slice(0, at).trim());
    rest = rest.slice(at).trim();
  }
  if (rest) parts.push(rest);
  return parts;
}

export { SOFT_STOPS };
