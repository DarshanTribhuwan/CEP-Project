// ChatGPT-style Smart Agronomist AI Interface (Groq LLM + DDG + Charts + PDF parser)
export const aiChatModule = {
  currentChatId: null,
  chatList: [],
  soilCardContext: "",
  userLocation: "Maharashtra, India",

  init() {
    this.bindEvents();
    this.loadChats();
  },

  bindEvents() {
    const newChatBtn = document.getElementById('btn-new-chat');
    if (newChatBtn) newChatBtn.addEventListener('click', () => this.createNewChat());

    const sendBtn = document.getElementById('btn-chat-send');
    if (sendBtn) sendBtn.addEventListener('click', () => this.sendUserMessage());

    const chatInput = document.getElementById('chat-input-text');
    if (chatInput) {
      chatInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault();
          this.sendUserMessage();
        }
      });
    }

    // Soil card file upload
    const attachBtn = document.getElementById('btn-attach-soil');
    const fileInput = document.getElementById('soil-card-file-input');
    if (attachBtn && fileInput) {
      attachBtn.addEventListener('click', () => fileInput.click());
      fileInput.addEventListener('change', (e) => this.handleFileUpload(e));
    }
  },

  async loadChats() {
    const listContainer = document.getElementById('chat-history-list');
    try {
      const resp = await fetch('/api/v1/ai/chats');
      if (resp.ok) {
        this.chatList = await resp.json();
        this.renderChatList();
        if (this.chatList.length > 0 && !this.currentChatId) {
          this.switchChat(this.chatList[0].id);
        } else if (!this.currentChatId) {
          this.createNewChat();
        }
      }
    } catch (err) {
      console.warn("Could not fetch chat sessions", err);
    }
  },

  renderChatList() {
    const listContainer = document.getElementById('chat-history-list');
    if (!listContainer) return;

    listContainer.innerHTML = this.chatList.map(c => `
      <div class="chat-session-item ${c.id === this.currentChatId ? 'active' : ''}" data-chat-id="${c.id}">
        <span class="chat-session-title">💬 ${c.title || 'Agronomy Chat'}</span>
        <button class="delete-chat-btn" data-chat-id="${c.id}" style="background:transparent;border:none;color:var(--text-dim);cursor:pointer;">✕</button>
      </div>
    `).join('');

    listContainer.querySelectorAll('.chat-session-item').forEach(el => {
      el.addEventListener('click', (e) => {
        if (e.target.classList.contains('delete-chat-btn')) {
          this.deleteChat(e.target.dataset.chatId);
          return;
        }
        this.switchChat(el.dataset.chatId);
      });
    });
  },

  async createNewChat() {
    const farmerId = localStorage.getItem('sampada_farmer_id') || null;
    try {
      const resp = await fetch('/api/v1/ai/chats', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: 'New Agri Chat', farmer_id: farmerId })
      });
      if (resp.ok) {
        const data = await resp.json();
        this.currentChatId = data.id;
        await this.loadChats();
        this.switchChat(data.id);
      }
    } catch (err) {
      console.error("Create chat error", err);
    }
  },

  async switchChat(chatId) {
    this.currentChatId = chatId;
    this.renderChatList();
    const messagesBox = document.getElementById('chat-messages-box');
    if (!messagesBox) return;

    messagesBox.innerHTML = '<div style="color:var(--text-muted);text-align:center;padding:2rem;">Loading chat history...</div>';

    try {
      const resp = await fetch(`/api/v1/ai/chats/${chatId}`);
      if (resp.ok) {
        const data = await resp.json();
        messagesBox.innerHTML = '';
        data.messages.forEach(msg => {
          this.appendMessageBubble(msg.role, msg.content, msg.charts, msg.metrics);
        });
        messagesBox.scrollTop = messagesBox.scrollHeight;
      }
    } catch (err) {
      messagesBox.innerHTML = '<div style="color:var(--accent-red)">Error loading chat history.</div>';
    }
  },

  async deleteChat(chatId) {
    try {
      await fetch(`/api/v1/ai/chats/${chatId}`, { method: 'DELETE' });
      this.chatList = this.chatList.filter(c => c.id !== chatId);
      if (this.currentChatId === chatId) {
        this.currentChatId = this.chatList[0]?.id || null;
      }
      this.renderChatList();
      if (this.currentChatId) {
        this.switchChat(this.currentChatId);
      } else {
        this.createNewChat();
      }
    } catch (err) {
      console.warn("Delete chat failed", err);
    }
  },

  async handleFileUpload(e) {
    const file = e.target.files[0];
    if (!file) return;

    const formData = new FormData();
    formData.append('file', file);

    const uploadStatus = document.getElementById('chat-upload-status');
    if (uploadStatus) uploadStatus.textContent = `Analyzing ${file.name}...`;

    try {
      const resp = await fetch('/api/v1/ai/upload-soil', {
        method: 'POST',
        body: formData
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.detail || "Upload failed");

      this.soilCardContext = data.extracted_text;
      if (uploadStatus) {
        uploadStatus.textContent = `✓ Soil Card Attached (${file.name})`;
        uploadStatus.style.color = "var(--primary-light)";
      }

      // Add a system announcement bubble into chat
      this.appendMessageBubble('assistant', `I have reviewed your Soil Health Card (${file.name}). Key nutrients detected: ${JSON.stringify(data.nutrients || {})}. I will consider these in my agronomy recommendations.`);
    } catch (err) {
      alert("Error parsing soil health card: " + err.message);
      if (uploadStatus) uploadStatus.textContent = "";
    }
  },

  async sendUserMessage() {
    const input = document.getElementById('chat-input-text');
    const prompt = input?.value.trim();
    if (!prompt) return;

    input.value = '';
    const farmerId = localStorage.getItem('sampada_farmer_id') || null;
    const enableSearch = document.getElementById('toggle-web-search')?.checked ?? true;

    // Render user message immediately
    this.appendMessageBubble('user', prompt);

    // Placeholder for assistant thinking
    const assistantBubble = this.appendMessageBubble('assistant', 'Thinking and consulting agricultural datasets...');

    try {
      const resp = await fetch('/api/v1/ai/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          chat_id: this.currentChatId,
          prompt: prompt,
          farmer_id: farmerId ? parseInt(farmerId, 10) : null,
          enable_web_search: enableSearch,
          soil_card_text: this.soilCardContext
        })
      });

      const data = await resp.json();
      if (!resp.ok) throw new Error(data.detail || "Communication failed");

      // Replace assistant bubble with real response and charts
      assistantBubble.innerHTML = '';
      this.renderAssistantContent(assistantBubble, data.content, data.charts, data.metrics);

      // Refresh chat list to update title
      this.loadChats();
    } catch (err) {
      assistantBubble.innerHTML = `<span style="color:var(--accent-red)">⚠️ Agronomist AI Error: ${err.message}</span>`;
    }
  },

  appendMessageBubble(role, content, charts = [], metrics = []) {
    const container = document.getElementById('chat-messages-box');
    if (!container) return null;

    const bubble = document.createElement('div');
    bubble.className = `chat-bubble ${role}`;

    if (role === 'assistant') {
      this.renderAssistantContent(bubble, content, charts, metrics);
    } else {
      bubble.textContent = content;
    }

    container.appendChild(bubble);
    container.scrollTop = container.scrollHeight;
    return bubble;
  },

  renderAssistantContent(bubbleEl, text, charts = [], metrics = []) {
    // Format paragraph breaks
    const formatted = text.split('\n\n').map(p => `<p>${this.escapeHtml(p)}</p>`).join('');
    bubbleEl.innerHTML = formatted;

    // Render Confidence Badges
    if (metrics && metrics.length > 0) {
      metrics.forEach(m => {
        const badge = document.createElement('div');
        badge.className = 'chat-confidence-badge';
        badge.innerHTML = `🛡️ ${m.label || 'Confidence'}: <strong>${m.value || '90%'}</strong>`;
        bubbleEl.appendChild(badge);
      });
    }

    // Render Charts using Chart.js
    if (charts && charts.length > 0 && window.Chart) {
      charts.forEach(c => {
        const chartBox = document.createElement('div');
        chartBox.className = 'chat-chart-box';

        const chartTitle = document.createElement('div');
        chartTitle.style.fontWeight = '700';
        chartTitle.style.marginBottom = '0.5rem';
        chartTitle.style.color = 'var(--primary-light)';
        chartTitle.textContent = c.title || 'Suitability / Analysis Chart';
        chartBox.appendChild(chartTitle);

        const canvas = document.createElement('canvas');
        canvas.style.maxHeight = '220px';
        chartBox.appendChild(canvas);
        bubbleEl.appendChild(chartBox);

        const labels = Object.keys(c.data || {});
        const values = Object.values(c.data || {});

        new window.Chart(canvas.getContext('2d'), {
          type: 'bar',
          data: {
            labels: labels,
            datasets: [{
              label: c.title || 'Suitability Score',
              data: values,
              backgroundColor: ['#10b981', '#34d399', '#f59e0b', '#0ea5e9'],
              borderRadius: 6
            }]
          },
          options: {
            responsive: true,
            plugins: {
              legend: { display: false }
            },
            scales: {
              y: { beginAtZero: true, grid: { color: 'rgba(255,255,255,0.06)' } },
              x: { grid: { display: false } }
            }
          }
        });
      });
    }
  },

  escapeHtml(str) {
    return (str || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }
};
