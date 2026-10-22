// Speech in (STT).
//
// Bhashini ASR is the target: it is the only option here that reliably handles
// Marathi, and it needs an uploaded file rather than a running recogniser. The
// browser's SpeechRecognition is kept as a fallback so the loop can be tested
// with no key - Chrome's Marathi support is poor, which is a fact the UI states
// rather than hides.
//
// Bhashini's /v1/asr expects multipart with `file` and a `language` field; the
// language is a display name ("Marathi"), not the ISO code used by the pipeline
// API. Both spellings are accepted by the service, and the display name is what
// its own OpenAPI schema documents for this route.

const ASR_LANGUAGES = {
  mr: 'Marathi',
  hi: 'Hindi',
  en: 'English',
};

export function hasBrowserRecognition() {
  return Boolean(window.SpeechRecognition || window.webkitSpeechRecognition);
}

export async function bhashiniTranscribe(blob, config, lang = 'mr') {
  const form = new FormData();
  form.append('file', blob, 'utterance.wav');
  form.append('language', ASR_LANGUAGES[lang] || 'Marathi');

  const res = await fetch(config.bhashiniAsrUrl, {
    method: 'POST',
    headers: { 'X-API-KEY': config.bhashiniKey },
    body: form,
  });

  if (!res.ok) {
    const detail = await res.text().catch(() => '');
    throw new Error(`Bhashini ASR ${res.status}: ${detail.slice(0, 200)}`);
  }

  const payload = await res.json();
  return (
    payload.text ||
    payload.output?.[0]?.source ||
    payload.pipelineResponse?.[0]?.output?.[0]?.source ||
    ''
  );
}

// Records a single utterance from the mic.
//
// The returned handle stops the recording; `audio` settles with the decoded
// 16 kHz mono wav. Capture is bounded (maxSeconds) because an unbounded
// recording of a silent room produces a huge upload and a confusing error from
// the ASR service.
export async function startRecording({ onLevel, maxSeconds = 15 } = {}) {
  const devices = typeof navigator !== 'undefined' ? navigator.mediaDevices : null;
  if (!devices?.getUserMedia) {
    throw new Error('microphone unavailable: this browser exposes no getUserMedia');
  }

  let stream;
  try {
    stream = await devices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });
  } catch (error) {
    throw new Error(`microphone blocked: ${error.message}`);
  }

  const mimeType = MediaRecorder.isTypeSupported('audio/webm') ? 'audio/webm' : '';
  const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
  const chunks = [];

  let audioContext = null;
  let raf = 0;
  if (onLevel) {
    audioContext = new AudioContext();
    const source = audioContext.createMediaStreamSource(stream);
    const analyser = audioContext.createAnalyser();
    analyser.fftSize = 1024;
    source.connect(analyser);
    const samples = new Uint8Array(analyser.fftSize);
    const tick = () => {
      analyser.getByteTimeDomainData(samples);
      onLevel(rms(samples));
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
  }

  let settle;
  const audio = new Promise((resolve, reject) => {
    settle = { resolve, reject };
  });

  recorder.ondataavailable = (event) => {
    if (event.data.size) chunks.push(event.data);
  };

  recorder.onstop = async () => {
    cancelAnimationFrame(raf);
    if (audioContext) await audioContext.close().catch(() => {});
    stream.getTracks().forEach((track) => track.stop());

    const blob = new Blob(chunks, { type: recorder.mimeType || 'audio/webm' });
    if (!chunks.length) {
      settle.reject(new Error('nothing was recorded'));
      return;
    }

    try {
      settle.resolve(await toWav16k(blob));
    } catch (error) {
      settle.reject(new Error(`could not decode microphone audio: ${error.message}`));
    }
  };

  recorder.onerror = (event) => {
    settle.reject(new Error(event.error?.message || 'recorder failed'));
  };

  const timer = setTimeout(() => handle.stop(), maxSeconds * 1000);
  recorder.start();

  const handle = {
    stop() {
      clearTimeout(timer);
      if (recorder.state !== 'inactive') recorder.stop();
    },
    cancel() {
      clearTimeout(timer);
      if (recorder.state !== 'inactive') {
        recorder.onstop = null;
        recorder.stop();
      }
      stream.getTracks().forEach((track) => track.stop());
    },
    audio,
  };

  return handle;
}

function rms(samples) {
  let sum = 0;
  for (let i = 0; i < samples.length; i++) {
    const n = (samples[i] - 128) / 128;
    sum += n * n;
  }
  return Math.sqrt(sum / samples.length);
}

// Decodes any browser-supported container and writes a 16 kHz mono 16-bit WAV,
// which is what every Bhashini ASR model is trained on.
async function toWav16k(blob) {
  const bytes = await blob.arrayBuffer();
  const audioContext = new AudioContext();
  let decoded;
  try {
    decoded = await audioContext.decodeAudioData(bytes);
  } finally {
    audioContext.close();
  }

  const targetRate = 16000;
  const frames = Math.max(1, Math.round(decoded.duration * targetRate));
  const offline = new OfflineAudioContext(1, frames, targetRate);
  const source = offline.createBufferSource();
  source.buffer = decoded;
  source.connect(offline.destination);
  source.start();

  const rendered = await offline.startRendering();
  return encodeWav(rendered.getChannelData(0), targetRate);
}

function encodeWav(samples, sampleRate) {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);

  const writeString = (offset, text) => {
    for (let i = 0; i < text.length; i++) view.setUint8(offset + i, text.charCodeAt(i));
  };

  writeString(0, 'RIFF');
  view.setUint32(4, 36 + samples.length * 2, true);
  writeString(8, 'WAVE');
  writeString(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeString(36, 'data');
  view.setUint32(40, samples.length * 2, true);

  for (let i = 0; i < samples.length; i++) {
    const clamped = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(44 + i * 2, clamped * 0x7fff, true);
  }

  return new Blob([buffer], { type: 'audio/wav' });
}
