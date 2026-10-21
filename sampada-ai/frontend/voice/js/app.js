// Marathi voice-to-voice assistant.
//
// Pipeline: mic → STT (Bhashini ASR) → brain (LLM, else the backend's own
// data) → sentence splitter → TTS (Bhashini) → speaker, with barge-in at every
// stage. Every answer is spoken in Marathi by default.
//
// Without a Bhashini key it falls back to the browser's speech recognition and
// synthesis so the loop is still testable; the header shows which provider is
// actually live rather than implying Bhashini is wired up when it is not.

import { loadConfig, saveConfig, clearConfig, capabilityReport } from './config.js';
import { startRecording, bhashiniTranscribe, hasBrowserRecognition } from './stt.js';
import { Speaker } from './speaker.js';
import { answerStream } from './brain.js';
import { GroundedBackend } from './grounded.js';
import { SentenceBuffer } from './sentences.js';

const BARGE_IN = {
  rmsThreshold: 0.25,
  consecutiveFrames: 10,
};

const els = {};
let config = loadConfig();
let speaker = null;
let backend = null;
let listening = false;
let speaking = false;
let micStream = null;
let micContext = null;
let micAnalyser = null;
let micRaf = 0;
let loudFrames = 0;
let activeController = null;
let turnId = 0;
let browserRecognition = null;

function byId(id) {
  return document.getElementById(id);
}

function setStatus(text, kind = 'idle') {
  els.status.textContent = text;
  els.status.dataset.kind = kind;
}

function log(role, text) {
  const row = document.createElement('div');
  row.className = `turn ${role}`;
  row.textContent = text;
  els.log.appendChild(row);
  els.log.scrollTop = els.log.scrollHeight;
}

function refreshCapabilities() {
  const caps = capabilityReport(config);
  const parts = [
    `ऐकणे: ${caps.stt === 'bhashini' ? 'Bhashini ASR' : 'browser fallback'}`,
    `बोलणे: ${caps.tts === 'bhashini' ? 'Bhashini TTS' : 'browser fallback'}`,
    `विचार: ${caps.brain === 'llm' ? 'LLM' : 'backend data (grounded)'}`,
  ];
  els.caps.textContent = parts.join(' · ');
  els.caps.className = caps.brain === 'llm' ? 'caps ok' : 'caps warn';
  return caps;
}

function saveSettings() {
  config = saveConfig({
    apiBase: els.apiBase.value.trim(),
    bhashiniKey: els.bhashiniKey.value.trim(),
    llmKey: els.llmKey.value.trim(),
    llmModel: els.llmModel.value.trim(),
    ttsVoice: els.ttsVoice.value.trim(),
    lang: els.lang.value,
  });
  refreshCapabilities();
  setStatus('Settings saved.');
}

function fillSettings() {
  els.apiBase.value = config.apiBase;
  els.bhashiniKey.value = config.bhashiniKey;
  els.llmKey.value = config.llmKey;
  els.llmModel.value = config.llmModel;
  els.ttsVoice.value = config.ttsVoice;
  els.lang.value = config.lang;
}

// --- one turn of the loop ---------------------------------------------------

async function runTurn(transcript) {
  const text = transcript.trim();
  if (!text) return;

  log('farmer', text);
  speaker.interrupt();

  const id = ++turnId;
  activeController = new AbortController();
  const buffer = new SentenceBuffer();
  let spoken = '';

  setStatus('विचार करत आहे…', 'thinking');

  try {
    for await (const chunk of answerStream(text, {
      config,
      backend,
      signal: activeController.signal,
    })) {
      if (id !== turnId) return;

      const sentences = chunk.final
        ? [...buffer.push(chunk.sentence), ...buffer.flush()]
        : buffer.push(chunk.sentence);
      for (const part of sentences) {
        if (id !== turnId) return;
        spoken = part;
        log('assistant', spoken);
        await speaker.speak(spoken);
      }
    }

    // The grounded path knows why a source was empty. Showing that in the log
    // is what makes "no prices today" checkable instead of a shrug.
    if (backend.lastUnavailable) {
      log('system', `स्रोत उपलब्ध नाही: ${backend.lastUnavailable}`);
    }
  } catch (error) {
    if (error.name !== 'AbortError') {
      log('system', `काहीतरी चुकले: ${error.message}`);
      setStatus('Error', 'error');
    }
  } finally {
    if (id === turnId) {
      setStatus(speaker.isSpeaking() ? 'बोलत आहे…' : 'ऐका…', speaker.isSpeaking() ? 'speaking' : 'idle');
    }
  }
}

async function transcribeWithBhashini() {
  setStatus('ऐकत आहे…', 'listening');

  const handle = await startRecording({
    onLevel: (level) => {
      els.meter.style.width = `${Math.min(100, level * 300)}%`;
    },
    maxSeconds: 15,
  });

  listening = true;
  setStatus('बोला… (पुन्हा थांबवण्यासाठी पुन्हा दाबा)', 'listening');

  const stop = () => {
    if (!listening) return;
    listening = false;
    els.micBtn.classList.remove('rec');
    handle.stop();
  };

  els.micBtn.addEventListener('click', stop, { once: true });

  const wav = await handle.audio;
  els.meter.style.width = '0%';

  setStatus('लेखन घेत आहे…', 'thinking');
  const text = await bhashiniTranscribe(wav, config, config.lang);
  if (!text) {
    setStatus('काही ऐकले नाही.', 'idle');
    return;
  }
  await runTurn(text);
}

function startBrowserRecognition() {
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  const rec = new Recognition();
  rec.lang = config.lang === 'mr' ? 'mr-IN' : config.lang === 'hi' ? 'hi-IN' : 'en-IN';
  rec.continuous = false;
  rec.interimResults = false;

  rec.onresult = (event) => {
    const text = event.results[event.results.length - 1][0].transcript;
    runTurn(text);
  };
  rec.onerror = (event) => {
    setStatus(`ऐकता आले नाही: ${event.error}`, 'error');
  };
  rec.onend = () => {
    listening = false;
    els.micBtn.classList.remove('rec');
    setStatus('ऐका…', 'idle');
  };

  rec.start();
  listening = true;
  els.micBtn.classList.add('rec');
  setStatus('ऐकत आहे…', 'listening');
  return rec;
}

async function toggleMic() {
  if (listening) {
    browserRecognition?.stop();
    return;
  }

  if (config.bhashiniKey) {
    await transcribeWithBhashini();
  } else if (hasBrowserRecognition()) {
    browserRecognition = startBrowserRecognition();
  } else {
    setStatus('या ब्राउझरमध्ये भाषा ओळखण्याची सुविधा नाही. Bhashini key टाका.', 'error');
  }
}

// --- barge-in ---------------------------------------------------------------

// The mic level is watched continuously while the agent talks. Sustained loud
// audio is a farmer cutting in, so playback stops and the mic opens.
async function startBargeInMonitor() {
  micStream = await navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
  });

  micContext = new AudioContext();
  micAnalyser = micContext.createAnalyser();
  micAnalyser.fftSize = 2048;
  micAnalyser.smoothingTimeConstant = 0.3;
  micContext.createMediaStreamSource(micStream).connect(micAnalyser);

  if (micContext.state === 'suspended') await micContext.resume();

  const samples = new Uint8Array(micAnalyser.fftSize);

  const tick = () => {
    const level = rms(micAnalyser, samples);

    if (speaker.isSpeaking() && level >= BARGE_IN.rmsThreshold) {
      loudFrames += 1;
      if (loudFrames >= BARGE_IN.consecutiveFrames) {
        interrupt();
        loudFrames = 0;
      }
    } else {
      loudFrames = 0;
    }

    els.level.textContent = level.toFixed(2);
    micRaf = requestAnimationFrame(tick);
  };

  micRaf = requestAnimationFrame(tick);
}

function rms(analyser, samples) {
  analyser.getByteTimeDomainData(samples);
  let sum = 0;
  for (let i = 0; i < samples.length; i++) {
    const n = (samples[i] - 128) / 128;
    sum += n * n;
  }
  return Math.sqrt(sum / samples.length);
}

function interrupt() {
  turnId += 1;
  activeController?.abort();
  activeController = null;
  speaker.interrupt();
  setStatus('आपण बोलाला तयार आहात.', 'idle');
}

// --- boot -------------------------------------------------------------------

async function main() {
  for (const [name, id] of Object.entries({
    status: 'status',
    caps: 'caps',
    log: 'log',
    micBtn: 'mic',
    speakBtn: 'speak',
    sendBtn: 'send',
    text: 'text',
    level: 'level',
    meter: 'meter',
    lang: 'lang',
    apiBase: 'api-base',
    bhashiniKey: 'bhashini-key',
    llmKey: 'llm-key',
    llmModel: 'llm-model',
    ttsVoice: 'tts-voice',
    settings: 'settings',
    toggleSettings: 'toggle-settings',
    save: 'save-settings',
    clear: 'clear-settings',
  })) {
    els[name] = byId(id);
  }

  backend = new GroundedBackend(config.apiBase);
  speaker = new Speaker(() => config, config.lang);
  speaker.onStateChange = () => {
    els.speakBtn.classList.toggle('on', speaker.isSpeaking());
  };

  fillSettings();
  refreshCapabilities();

  els.micBtn.addEventListener('click', toggleMic);
  els.speakBtn.addEventListener('click', interrupt);
  els.sendBtn.addEventListener('click', () => runTurn(els.text.value));
  els.text.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') runTurn(els.text.value);
  });
  els.save.addEventListener('click', saveSettings);
  els.clear.addEventListener('click', () => {
    config = clearConfig();
    fillSettings();
    refreshCapabilities();
    setStatus('Settings cleared.');
  });
  els.toggleSettings?.addEventListener('click', () => {
    document.body.classList.toggle('show-settings');
  });

  try {
    await startBargeInMonitor();
    setStatus('ऐका… (बोलण्यासाठी मायक वापरा)');
  } catch (error) {
    setStatus(`Microphone unavailable: ${error.message}`, 'error');
  }
}

document.addEventListener('DOMContentLoaded', main);
