// Speech out (TTS).
//
// Two providers behind one interface so the rest of the pipeline never asks
// which one is configured: Bhashini when a key is present, the browser's own
// speechSynthesis otherwise. The browser fallback is not equivalent - it cannot
// be queued the same way and its Marathi voice depends on the OS - but it keeps
// the loop testable with no key at all, and the UI says which one is live.

// Guarded so this module can be imported in a non-browser context (the unit
// tests replace the transport and never touch the real synthesiser).
const synth = typeof window !== 'undefined' ? window.speechSynthesis || null : null;

function pickVoice(lang) {
  if (!synth) return null;
  const voices = synth.getVoices();
  const wanted = lang === 'mr' ? 'mr' : lang;
  return (
    voices.find((v) => v.lang?.toLowerCase().startsWith(wanted)) ||
    voices.find((v) => v.lang?.toLowerCase().startsWith('mr')) ||
    voices.find((v) => v.lang?.toLowerCase().startsWith('hi')) ||
    voices.find((v) => v.lang?.toLowerCase().startsWith('en')) ||
    voices[0] ||
    null
  );
}

export function hasBrowserSpeech() {
  return Boolean(synth);
}

export async function bhashiniSpeak(text, config, signal) {
  const res = await fetch(config.bhashiniTtsUrl, {
    method: 'POST',
    headers: {
      'X-API-KEY': config.bhashiniKey,
      'Content-Type': 'application/json',
      Accept: 'audio/mpeg',
    },
    body: JSON.stringify({
      text,
      language: config.ttsLanguage,
      voiceName: config.ttsVoice,
    }),
    signal,
  });

  if (!res.ok) {
    const detail = await res.text().catch(() => '');
    throw new Error(`Bhashini TTS ${res.status}: ${detail.slice(0, 200)}`);
  }

  return res.blob();
}

export function browserSpeak(text, lang) {
  return new Promise((resolve, reject) => {
    if (!synth) {
      reject(new Error('This browser has no speech synthesis'));
      return;
    }

    synth.cancel();

    const utter = new SpeechSynthesisUtterance(text);
    const voice = pickVoice(lang);
    if (voice) {
      utter.voice = voice;
      utter.lang = voice.lang;
    } else {
      utter.lang = lang === 'mr' ? 'mr-IN' : lang === 'hi' ? 'hi-IN' : 'en-IN';
    }

    utter.onend = () => resolve();
    utter.onerror = (event) => {
      // A cancel is the expected path when the farmer barges in.
      if (event.error === 'canceled' || event.error === 'interrupted') resolve();
      else reject(new Error(`browser TTS failed: ${event.error}`));
    };

    synth.speak(utter);
  });
}

export function browserStop() {
  synth?.cancel();
}
