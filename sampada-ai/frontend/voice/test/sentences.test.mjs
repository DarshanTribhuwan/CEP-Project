import { SentenceBuffer, splitLong } from '../js/sentences.js';

const results = [];
function check(name, condition, detail = '') {
  results.push({ name, pass: Boolean(condition), detail });
}

function sentencesOf(text, final = false) {
  const buffer = new SentenceBuffer();
  return [...buffer.push(text), ...(final ? buffer.flush() : [])];
}

// --- Marathi danda is the real terminator -----------------------------------
check(
  'breaks on danda',
  JSON.stringify(sentencesOf('नमस्कार। कसे आहात? भरत आहे!')) ===
    JSON.stringify(['नमस्कार।', 'कसे आहात?', 'भरत आहे!']),
  JSON.stringify(sentencesOf('नमस्कार। कसे आहात? भरत आहे!')),
);

// --- incomplete trailing text is held back ----------------------------------
{
  const buffer = new SentenceBuffer();
  const first = buffer.push('नमस्कार। मी');
  check(
    'holds incomplete tail',
    JSON.stringify(first) === JSON.stringify(['नमस्कार।']) && buffer.buffer === ' मी',
    JSON.stringify(first) + ' tail=' + JSON.stringify(buffer.buffer),
  );
  const rest = buffer.push(' ठीक आहे.');
  check(
    'releases once terminated',
    JSON.stringify(rest) === JSON.stringify(['मी ठीक आहे.']),
    JSON.stringify(rest),
  );
}

// --- decimals and abbreviations are not sentence ends ------------------------
check(
  'keeps decimals intact',
  JSON.stringify(sentencesOf('बियाणे 55.5 किलो प्रति हेक्टर.')) ===
    JSON.stringify(['बियाणे 55.5 किलो प्रति हेक्टर.']),
  JSON.stringify(sentencesOf('बियाणे 55.5 किलो प्रति हेक्टर.')),
);

// A terminator with no following space is held, not guessed at: cutting there
// would split mid-word, and the digits guard above already covers the case that
// actually matters (decimals). flush() at end of stream still releases it.
{
  const buffer = new SentenceBuffer();
  const held = buffer.push('नाही.पुढे वाचा');
  check('does not cut mid-word', held.length === 0, JSON.stringify(held));
  check(
    'mid-word text survives flush',
    JSON.stringify(buffer.flush()) === JSON.stringify(['नाही.पुढे वाचा']),
    JSON.stringify(buffer.flush()),
  );
}

// --- no characters are lost or reordered --------------------------------------
{
  const text = 'पावसाने चांगले वाटत आहे. पुढील तीन दिवस पाऊस अपेक्षित आहे! नातंबी तर ठीक आहे? शेवटी काही नाही';
  const buffer = new SentenceBuffer();
  const emitted = [...buffer.push(text), ...buffer.flush()];
  check(
    'reconstructs the original text',
    emitted.join(' ').replace(/\s+/g, ' ').trim() === text.replace(/\s+/g, ' ').trim(),
    emitted.join(' '),
  );
}

// --- streaming matches batch output ------------------------------------------
{
  const stream = new SentenceBuffer();
  const streamed = [];
  const text = 'पावसाने चांगले वाटत आहे. पुढील तीन दिवस पाऊस अपेक्षित आहे! नातंबी तर ठीक आहे?';
  for (const ch of text) streamed.push(...stream.push(ch));
  streamed.push(...stream.flush());

  const batch = sentencesOf(text, true);
  check('char-by-char equals whole-string', JSON.stringify(streamed) === JSON.stringify(batch),
    JSON.stringify(streamed));
}

// --- empty and whitespace ----------------------------------------------------
check('ignores empty input', sentencesOf('').length === 0 && sentencesOf('   ').length === 0);
check('flush of empty buffer yields nothing', new SentenceBuffer().flush().length === 0);

// --- splitLong ---------------------------------------------------------------
{
  const long = 'शिफारस '.repeat(80);
  const parts = splitLong(long, 100);
  check('splitLong respects limit', parts.every((p) => p.length <= 100), 'max=' + Math.max(...parts.map((p) => p.length)));
  check('splitLong loses nothing', parts.join(' ') === long.trim().split(/\s+/).join(' '),
    'joined length ' + parts.join(' ').length + ' vs ' + long.length);
}
check('splitLong leaves short text alone', JSON.stringify(splitLong('हा मजकूर आहे', 240)) === JSON.stringify(['हा मजकूर आहे']));

// --- report ------------------------------------------------------------------
let failed = 0;
for (const r of results) {
  if (!r.pass) failed += 1;
  console.log(`${r.pass ? 'PASS' : 'FAIL'}  ${r.name}${r.pass || !r.detail ? '' : '  -> ' + r.detail}`);
}
console.log(`\n${results.length - failed}/${results.length} passed`);
process.exit(failed ? 1 : 0);
