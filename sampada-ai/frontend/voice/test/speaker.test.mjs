import { Speaker } from '../js/speaker.js';

const results = [];
function check(name, condition, detail = '') {
  results.push({ name, pass: Boolean(condition), detail });
}

// A fake provider stands in for the network so queueing, draining and
// interruption can be tested deterministically.
function fakeSpeaker() {
  const synth = [];
  const started = [];
  const disposed = [];
  let resolveNext = null;

  const speaker = new Speaker(() => ({ bhashiniKey: 'test' }));
  speaker.prepare = async (sentence) => {
    synth.push(sentence);
    return {
      kind: 'url',
      url: 'blob:' + sentence,
      dispose: () => disposed.push(sentence),
    };
  };
  speaker.play = (prepared) =>
    new Promise((resolve) => {
      started.push(prepared.url);
      resolveNext = resolve;
    });

  return {
    speaker,
    synth,
    started,
    disposed,
    finish: () => resolveNext?.(),
    tick: () => new Promise((r) => setTimeout(r, 0)),
  };
}

// --- sentences are played in order, one after another -------------------------
{
  const h = fakeSpeaker();
  h.speaker.speak('पहिली.');
  h.speaker.speak('दुसरी.');
  h.speaker.speak('तीसरी.');
  await h.tick();
  await h.tick();

  check('all three synthesised', h.synth.length === 3, h.synth.join('|'));
  check('only the first is playing', h.started.length === 1, h.started.join('|'));
  check('queue holds the rest', h.speaker.queue.length === 2);

  h.finish();
  await h.tick();
  await h.tick();
  check('second starts after first ends', h.started[1] === 'blob:दुसरी.', h.started.join('|'));

  h.finish();
  await h.tick();
  h.finish();
  await h.tick();
  await h.tick();
  check('drains fully', h.speaker.queue.length === 0 && !h.speaker.isSpeaking(),
    'q=' + h.speaker.queue.length + ' speaking=' + h.speaker.isSpeaking());
  check('every prepared item disposed', h.disposed.length === 3, h.disposed.join('|'));
}

// --- interrupt drops the queue and bumps the session -------------------------
{
  const h = fakeSpeaker();
  h.speaker.speak('पहिली.');
  h.speaker.speak('दुसरी.');
  await h.tick();
  await h.tick();

  const sessionBefore = h.speaker.session;
  h.speaker.interrupt();

  check('session advanced', h.speaker.session === sessionBefore + 1);
  check('queue emptied', h.speaker.queue.length === 0);
  check('not speaking any more', !h.speaker.isSpeaking());
  check('queued audio released', h.disposed.includes('दुसरी.'), h.disposed.join('|'));
}

// --- a barge-in during synthesis discards the late arrival --------------------
// This is the race the session counter exists for: synthesis resolves after the
// farmer has already interrupted, and that audio must never be played.
{
  let releaseSynth;
  const speaker = new Speaker(() => ({ bhashiniKey: 'test' }));
  speaker.prepare = () =>
    new Promise((resolve) => {
      releaseSynth = () => resolve({ kind: 'url', url: 'blob:late', dispose: () => {} });
    });
  speaker.play = () => new Promise(() => {});

  const pending = speaker.speak('उशीर वाक्य.');
  speaker.interrupt();
  releaseSynth();
  await pending;
  await new Promise((r) => setTimeout(r, 0));

  check('late audio never queued', speaker.queue.length === 0);
  check('late audio never played', !speaker.isSpeaking());
}

// --- two speakers never interleave (one drain loop) --------------------------
{
  const h = fakeSpeaker();
  h.speaker.speak('अ.');
  h.speaker.speak('ब.');
  await h.tick();
  await h.tick();
  check('single drain loop', h.started.length === 1, h.started.join('|'));
}

// --- empty input is ignored ---------------------------------------------------
{
  const h = fakeSpeaker();
  await h.speaker.speak('   ');
  await h.speaker.speak(null);
  check('whitespace not synthesised', h.synth.length === 0);
}

// --- report -------------------------------------------------------------------
let failed = 0;
for (const r of results) {
  if (!r.pass) failed += 1;
  console.log(`${r.pass ? 'PASS' : 'FAIL'}  ${r.name}${r.pass || !r.detail ? '' : '  -> ' + r.detail}`);
}
console.log(`\n${results.length - failed}/${results.length} passed`);
process.exit(failed ? 1 : 0);
