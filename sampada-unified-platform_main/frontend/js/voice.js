// SAMPADA AI — Multilingual Voice Assistant (Marathi, Hindi, English)
// Browser-Native Speech: Web Speech API (SpeechRecognition & SpeechSynthesis)
// Zero External Audio Latency, Pure Client-Side Speech Processing

export const voiceModule = {
  isListening: false,
  isSpeaking: false,
  recognition: null,
  currentLang: 'mr-IN',
  lastResponseText: '',
  availableVoices: [],
  synthUtterances: [],

  init() {
    this.setupSpeechRecognition();
    this.loadVoices();
    this.bindEvents();
  },

  loadVoices() {
    if (!('speechSynthesis' in window)) return;
    const updateVoices = () => {
      this.availableVoices = window.speechSynthesis.getVoices();
    };
    updateVoices();
    if (window.speechSynthesis.onvoiceschanged !== undefined) {
      window.speechSynthesis.onvoiceschanged = updateVoices;
    }
  },

  setupSpeechRecognition() {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (SpeechRecognition) {
      try {
        this.recognition = new SpeechRecognition();
        this.recognition.continuous = false;
        this.recognition.interimResults = false;
        this.recognition.lang = this.currentLang;

        this.recognition.onstart = () => {
          this.isListening = true;
          this.updateUiState('recording', 'ऐकत आहे... कृपया आपला प्रश्न बोला 🎤');
        };

        this.recognition.onresult = (event) => {
          this.isListening = false;
          const transcript = event.results[0][0].transcript;
          if (transcript && transcript.trim()) {
            this.handleVoiceInput(transcript.trim());
          } else {
            this.updateUiState('idle', 'काही ऐकू आले नाही. पुन्हा माईक दाबा किंवा खाली टाइप करा.');
          }
        };

        this.recognition.onerror = (event) => {
          console.warn('Speech recognition error:', event.error);
          this.isListening = false;
          if (event.error === 'not-allowed' || event.error === 'service-not-allowed') {
            this.updateUiState('idle', '⚠️ माईक परवानगी नाकारली आहे. कृपया ब्राउझरमध्ये माईक परवानगी द्या, किंवा खाली टाइप करा.');
          } else if (event.error === 'no-speech') {
            this.updateUiState('idle', 'आवाज ऐकू आला नाही. कृपया पुन्हा बोला किंवा खाली टाइप करा.');
          } else {
            this.updateUiState('idle', 'आवाज ओळखण्यात अडचण आली. कृपया खाली टाइप करा किंवा पुन्हा प्रयत्न करा.');
          }
        };

        this.recognition.onend = () => {
          this.isListening = false;
          if (!this.isSpeaking) {
            this.updateUiState('idle', 'बोलण्यासाठी माईक दाबा · Tap Mic to Speak');
          }
        };
      } catch (e) {
        console.warn('SpeechRecognition initialization error:', e);
        this.recognition = null;
      }
    }
  },

  bindEvents() {
    // Mic Button
    const micBtn = document.getElementById('btn-voice-mic');
    if (micBtn) {
      micBtn.addEventListener('click', () => this.toggleListening());
    }

    // Direct Text Fallback
    const btnSendText = document.getElementById('btn-voice-send-text');
    const inputFallback = document.getElementById('voice-text-fallback');
    if (btnSendText && inputFallback) {
      const sendFromInput = () => {
        const val = inputFallback.value.trim();
        if (val) {
          inputFallback.value = '';
          this.handleVoiceInput(val);
        }
      };
      btnSendText.addEventListener('click', sendFromInput);
      inputFallback.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') sendFromInput();
      });
    }

    // Audio Playback Controls
    const replayBtn = document.getElementById('btn-replay-voice');
    if (replayBtn) {
      replayBtn.addEventListener('click', () => {
        if (this.lastResponseText) this.speakText(this.lastResponseText);
      });
    }

    const stopBtn = document.getElementById('btn-stop-voice');
    if (stopBtn) {
      stopBtn.addEventListener('click', () => this.stopSpeaking());
    }

    // Sample Voice Tester
    const testSampleBtn = document.getElementById('btn-test-voice-sample');
    if (testSampleBtn) {
      testSampleBtn.addEventListener('click', () => {
        const sampleMsg = this.currentLang.startsWith('mr')
          ? 'नमस्कार शेतकरी बंधूंनो, संपदा कृषी सल्लागार प्रणालीमध्ये आपले स्वागत आहे. मी आपल्या सेवेसाठी तयार आहे.'
          : this.currentLang.startsWith('hi')
          ? 'नमस्कार किसान भाइयों, संपदा कृषि सलाहकार प्रणाली में आपका स्वागत है। मैं आपकी सहायता के लिए तैयार हूँ।'
          : 'Welcome to SAMPADA agricultural intelligence. I am ready to assist you.';
        this.speakText(sampleMsg);
      });
    }

    // Quick Prompt Chips
    document.querySelectorAll('.prompt-chip').forEach(chip => {
      chip.addEventListener('click', (e) => {
        e.preventDefault();
        const text = chip.textContent.replace(/^[🎤🌾💧📊🌱🧅\s]+/, '').trim();
        this.handleVoiceInput(text);
      });
    });

    // Language Toggle
    const voiceLang = document.getElementById('voice-lang-select');
    if (voiceLang) {
      voiceLang.addEventListener('change', (e) => {
        this.currentLang = e.target.value;
        if (this.recognition) this.recognition.lang = this.currentLang;
        this.updateUiTitles();
      });
    }
  },

  updateUiTitles() {
    const heading = document.getElementById('voice-heading');
    const subheading = document.getElementById('voice-subheading');
    if (!heading || !subheading) return;

    if (this.currentLang.startsWith('mr')) {
      heading.textContent = 'मराठी आवाज कृषी सल्लागार';
      subheading.textContent = 'माईकवर टॅप करा आणि मराठीत कोणताही शेतीविषयक प्रश्न विचारा';
    } else if (this.currentLang.startsWith('hi')) {
      heading.textContent = 'हिन्दी आवाज़ कृषि सलाहकार';
      subheading.textContent = 'माइक बटन दबाएं और हिंदी में कोई भी कृषि प्रश्न पूछें';
    } else {
      heading.textContent = 'Multilingual Voice Advisory';
      subheading.textContent = 'Tap the microphone and ask any farming query';
    }
  },

  toggleListening() {
    if (this.isListening) {
      this.stopListening();
      return;
    }

    // Stop speaking if playing
    this.stopSpeaking();

    if (!this.recognition) {
      alert("Speech recognition is supported natively in Google Chrome and Microsoft Edge. Please use Chrome, or type your question in the text box below.");
      return;
    }

    try {
      this.recognition.lang = this.currentLang;
      this.recognition.start();
    } catch (err) {
      console.warn('Native speech recognition start error:', err);
      this.updateUiState('idle', 'कृपया पुन्हा माईक दाबा.');
    }
  },

  stopListening() {
    if (this.recognition && this.isListening) {
      try { this.recognition.stop(); } catch(e) {}
    }
    this.isListening = false;
    this.updateUiState('idle', 'बोलण्यासाठी माईक दाबा · Tap Mic to Speak');
  },

  // ------------------------------------------------------------------
  // HANDLE INPUT & CALL LLM
  // ------------------------------------------------------------------
  async handleVoiceInput(userSpeech) {
    const transcriptBox = document.getElementById('voice-transcript-content');
    const langLabel = this.currentLang.startsWith('mr') ? 'Marathi' : this.currentLang.startsWith('hi') ? 'Hindi' : 'English';

    if (transcriptBox) {
      transcriptBox.innerHTML = `
        <div style="color:var(--forest); font-weight:700; margin-bottom:0.4rem;">
          🗣️ ${this.currentLang.startsWith('mr') ? 'शेतकरी' : this.currentLang.startsWith('hi') ? 'किसान' : 'Farmer'}:
        </div>
        <div style="font-size:1.15rem; font-weight:600; color:var(--dark-text); margin-bottom:1rem; padding:0.5rem 0.75rem; background:var(--ivory); border-radius:var(--radius-sm); border-left:3px solid var(--forest);">
          "${userSpeech}"
        </div>
        <div style="color:var(--gold); font-size:0.9rem; font-weight:600;">
          ⏳ ${this.currentLang.startsWith('mr') ? 'कृषी सल्ला तयार होत आहे...' : this.currentLang.startsWith('hi') ? 'कृषि सलाह तैयार हो रही है...' : 'Formulating agronomy advice...'}
        </div>
      `;
    }

    this.updateUiState('thinking', 'कृषी सल्ला तयार होत आहे... (Formulating advice)');

    const farmerId = localStorage.getItem('sampada_farmer_id') || null;
    const voiceSessionId = 'voice-' + (farmerId ? farmerId + '-' : '') + Math.floor(Date.now() / (1000 * 60 * 30)); // 30-min window

    try {
      const resp = await fetch('/api/v1/ai/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          chat_id: voiceSessionId,
          prompt: `${userSpeech} (Please provide a concise, direct, spoken answer strictly in ${langLabel} (${this.currentLang.startsWith('mr') ? 'मराठी' : this.currentLang.startsWith('hi') ? 'हिन्दी' : 'English'}). Write entirely in Devanagari script without English words, English letters, or symbols. Limit answer to 2-3 short, clear sentences. Use 'ते' for ranges like '30 ते 35 रुपये'.)`,
          farmer_id: farmerId ? parseInt(farmerId, 10) : null,
          enable_web_search: true
        })
      });

      const data = await resp.json();
      const reply = data.content || (this.currentLang.startsWith('mr') ? 'सध्या माहिती उपलब्ध नाही.' : 'Information is currently unavailable.');
      this.lastResponseText = reply;

      if (transcriptBox) {
        transcriptBox.innerHTML = `
          <div style="color:var(--muted-text); font-weight:600; margin-bottom:0.3rem;">
            🗣️ शेतकरी: "${userSpeech}"
          </div>
          <hr style="border:none; border-top:1px solid var(--border); margin:0.8rem 0;">
          <div style="color:var(--forest); font-weight:700; margin-bottom:0.4rem; display:flex; align-items:center; gap:0.5rem;">
            <span>🌾 SAMPADA AI सल्ला:</span>
            <span class="chip-badge badge-verified" style="font-size:0.7rem;">Spoken Audio Ready</span>
          </div>
          <div style="font-size:1.05rem; line-height:1.65; color:var(--dark-text);">
            ${reply}
          </div>
        `;
      }

      const replayBtn = document.getElementById('btn-replay-voice');
      const stopBtn = document.getElementById('btn-stop-voice');
      if (replayBtn) replayBtn.style.display = 'inline-block';
      if (stopBtn) stopBtn.style.display = 'inline-block';

      // Read aloud automatically
      this.speakText(reply);

    } catch (err) {
      if (transcriptBox) {
        transcriptBox.innerHTML = `<div style="color:var(--red)">सल्ला मिळवण्यात अडचण आली: ${err.message}</div>`;
      }
      this.updateUiState('idle', 'त्रुटी आली. पुन्हा प्रयत्न करा.');
    }
  },

  // ------------------------------------------------------------------
  // SPEECH SYNTHESIS (READ ALOUD)
  // ------------------------------------------------------------------
  speakText(text) {
    if (!('speechSynthesis' in window)) {
      console.warn('Speech synthesis not supported.');
      return;
    }

    // Stop any existing speech
    this.stopSpeaking();

    const rangeWord = this.currentLang.startsWith('mr') ? ' ते ' : this.currentLang.startsWith('hi') ? ' से ' : ' to ';

    // Clean text: strip markdown tables, code blocks, bold/italics, bullet characters
    let clean = text
      .replace(/\|[^\n]+\|/g, '')     // tables
      .replace(/```[\s\S]*?```/g, '') // code
      .replace(/[*#_\[\]\(\)>`]/g, '') // markdown punctuation
      .replace(/[\u{1F300}-\u{1F9FF}]/gu, '') // emojis
      .replace(/[\u202f\u00a0]/g, ' ') // replace narrow no-break and no-break spaces with normal spaces
      .replace(/(\d+)\s*[\u2010-\u2015\-–—]\s*(\d+)/g, `$1${rangeWord}$2`) // replace numeric ranges like 30-35 with '30 ते 35'
      .replace(/\.{2,}/g, '.') // replace ellipsis '...' or '..' with single dot to avoid 'dot dot' speech
      .replace(/;/g, ',') // replace semicolons with comma
      .replace(/₹/g, ' रुपये ')
      .replace(/\b(?:rs|rs\.|inr)\b/gi, ' रुपये ')
      .replace(/\b(?:kg|kgs)\b/gi, ' किलो ')
      .replace(/\b(?:quintal|qtl)\b/gi, ' क्विंटल ')
      .replace(/%/g, ' टक्के ')
      .replace(/\s+/g, ' ')
      .trim();

    if (!clean) return;

    // Split into sentences so Chrome never times out on long utterances
    const sentences = clean.match(/[^.!?।\n]+[.!?।\n]+|\S+/g) || [clean];

    // Pick ONLY matching Devanagari voice for Marathi/Hindi
    // CRITICAL: NEVER pick en-IN for Marathi, because English voices spell out Devanagari as ASCII!
    const voices = window.speechSynthesis.getVoices();
    let selectedVoice = null;
    let targetLang = this.currentLang;

    if (this.currentLang.startsWith('mr')) {
      // 1. First look for native Marathi voice
      selectedVoice = voices.find(v => v.lang === 'mr-IN' || v.lang.startsWith('mr') || v.name.toLowerCase().includes('marathi'));

      // 2. Second look for Hindi Devanagari voice (shares identical alphabet & sounds natural for Marathi)
      if (!selectedVoice) {
        selectedVoice = voices.find(v => v.lang === 'hi-IN' || v.lang.startsWith('hi') || v.name.toLowerCase().includes('hindi') || v.name.toLowerCase().includes('lekha'));
      }

      // If we matched a Hindi voice or native engine, set language tag to Devanagari
      targetLang = (selectedVoice && selectedVoice.lang.startsWith('hi')) ? 'hi-IN' : 'mr-IN';

    } else if (this.currentLang.startsWith('hi')) {
      selectedVoice = voices.find(v => v.lang === 'hi-IN' || v.lang.startsWith('hi') || v.name.toLowerCase().includes('hindi') || v.name.toLowerCase().includes('lekha'));
      targetLang = 'hi-IN';
    } else {
      selectedVoice = voices.find(v => v.lang.startsWith('en-IN')) || voices.find(v => v.lang.startsWith('en'));
      targetLang = 'en-IN';
    }

    this.isSpeaking = true;
    this.updateUiState('speaking', '🔊 आवाज वाचून दाखवत आहे... (Speaking...)');

    // Queue sentences sequentially
    sentences.forEach((sentence, index) => {
      const sentenceText = sentence.trim();
      if (!sentenceText) return;

      const utterance = new SpeechSynthesisUtterance(sentenceText);
      utterance.lang = targetLang;

      // Only assign voice if it matches the target script (NEVER assign English voice to Devanagari text)
      if (selectedVoice) {
        if (this.currentLang.startsWith('mr') || this.currentLang.startsWith('hi')) {
          if (selectedVoice.lang.startsWith('mr') || selectedVoice.lang.startsWith('hi')) {
            utterance.voice = selectedVoice;
          }
        } else {
          utterance.voice = selectedVoice;
        }
      }

      utterance.rate = 0.92;
      utterance.pitch = 1.0;

      if (index === sentences.length - 1) {
        utterance.onend = () => {
          this.isSpeaking = false;
          this.updateUiState('idle', 'बोलण्यासाठी माईक दाबा · Tap Mic to Speak');
        };
      }
      utterance.onerror = (e) => {
        console.warn('Utterance error:', e);
        this.isSpeaking = false;
        this.updateUiState('idle', 'बोलण्यासाठी माईक दाबा · Tap Mic to Speak');
      };

      this.synthUtterances.push(utterance);
      window.speechSynthesis.speak(utterance);
    });

    // Chrome bug workaround: keep synthesis active
    window.speechSynthesis.resume();
  },

  stopSpeaking() {
    if ('speechSynthesis' in window) {
      window.speechSynthesis.cancel();
    }
    this.synthUtterances = [];
    this.isSpeaking = false;
    const stopBtn = document.getElementById('btn-stop-voice');
    if (stopBtn) stopBtn.style.display = 'none';
  },

  updateUiState(state, message) {
    const micBtn = document.getElementById('btn-voice-mic');
    const statusPill = document.getElementById('voice-status-pill');
    const waveform = document.getElementById('voice-waveform');
    const ring1 = document.getElementById('voice-ring-1');
    const ring2 = document.getElementById('voice-ring-2');

    if (statusPill) {
      statusPill.textContent = message;
      statusPill.className = 'voice-status-pill';
      if (state === 'recording') statusPill.classList.add('recording');
      if (state === 'speaking')  statusPill.classList.add('speaking');
    }

    if (micBtn) {
      if (state === 'recording') {
        micBtn.classList.add('listening');
        micBtn.textContent = '⏹️';
      } else {
        micBtn.classList.remove('listening');
        micBtn.textContent = '🎙️';
      }
    }

    if (waveform) {
      waveform.style.display = (state === 'recording' || state === 'speaking') ? 'flex' : 'none';
    }

    if (ring1 && ring2) {
      const active = state === 'recording' || state === 'speaking';
      ring1.style.animationPlayState = active ? 'running' : 'paused';
      ring2.style.animationPlayState = active ? 'running' : 'paused';
    }
  }
};
