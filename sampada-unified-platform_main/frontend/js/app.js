// SAMPADA AI — Master App Orchestrator v2.0
// Premium Light Theme, Left Sidebar, Cursor Glow, Scroll Reveal

import { translations } from './i18n.js';
import { farmerModule } from './farmer.js';
import { aiChatModule } from './ai_chat.js';
import { voiceModule } from './voice.js';

class SampadaApp {
  constructor() {
    this.currentLang = localStorage.getItem('sampada_lang') || 'en';
    this.currentTab  = 'dashboard';
  }

  init() {
    this.initCursorGlow();
    this.initScrollReveal();
    this.bindSidebarNav();
    this.bindMobileSidebar();
    this.bindLanguageSwitcher();
    this.bindModals();
    this.bindSmoothScroll();

    // Initialize submodules
    farmerModule.init();
    aiChatModule.init();
    voiceModule.init();

    // Deep-link hash support
    if (window.location.hash) {
      const tab = window.location.hash.replace('#', '');
      if (tab && tab !== 'home') {
        this.switchTab(tab);
      }
    }

    // Second recommendations button wiring
    const btnRecPage = document.getElementById('btn-get-rec-page');
    if (btnRecPage) {
      btnRecPage.addEventListener('click', () => {
        const season = document.getElementById('rec-season-select')?.value || 'KHARIF';
        document.getElementById('dash-season-select').value = season;
        farmerModule.generateRecommendations('rec-cards-grid');
      });
    }

    // Outcomes modal outcome button
    const btnSaveOutcome = document.getElementById('btn-save-outcome');
    if (btnSaveOutcome) {
      btnSaveOutcome.addEventListener('click', () => farmerModule.handleSaveOutcome());
    }
  }

  // ----------------------------------------------------------
  // CURSOR GLOW
  // ----------------------------------------------------------
  initCursorGlow() {
    const el = document.getElementById('cursor-glow');
    if (!el) return;

    // Only on pointer devices, not touch-only
    if (!window.matchMedia('(hover:hover)').matches) return;
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

    let targetX = 50, targetY = 50;
    let currentX = 50, currentY = 50;
    let animFrame = null;

    const update = () => {
      currentX += (targetX - currentX) * 0.08;
      currentY += (targetY - currentY) * 0.08;
      el.style.setProperty('--gx', `${currentX}%`);
      el.style.setProperty('--gy', `${currentY}%`);
      animFrame = requestAnimationFrame(update);
    };

    document.addEventListener('pointermove', (e) => {
      targetX = (e.clientX / window.innerWidth)  * 100;
      targetY = (e.clientY / window.innerHeight) * 100;
    }, { passive: true });

    update();
  }

  // ----------------------------------------------------------
  // SCROLL REVEAL
  // ----------------------------------------------------------
  initScrollReveal() {
    if (!('IntersectionObserver' in window)) return;
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      document.querySelectorAll('.reveal').forEach(el => el.classList.add('revealed'));
      return;
    }

    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry, i) => {
        if (entry.isIntersecting) {
          setTimeout(() => {
            entry.target.classList.add('revealed');
          }, i * 80);
          observer.unobserve(entry.target);
        }
      });
    }, { threshold: 0.12, rootMargin: '0px 0px -40px 0px' });

    document.querySelectorAll('.reveal').forEach(el => observer.observe(el));
  }

  // ----------------------------------------------------------
  // SIDEBAR NAVIGATION
  // ----------------------------------------------------------
  bindSidebarNav() {
    // Sidebar buttons
    document.querySelectorAll('.sidebar-nav-btn[data-tab]').forEach(btn => {
      btn.addEventListener('click', () => {
        this.switchTab(btn.dataset.tab);
        this.closeSidebar(); // auto-close on mobile
      });
    });
  }

  switchTab(tabName) {
    this.currentTab = tabName;

    // Update sidebar active state
    document.querySelectorAll('.sidebar-nav-btn[data-tab]').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.tab === tabName);
    });

    // Show/hide sections
    document.querySelectorAll('.view-section').forEach(sec => {
      const isActive = sec.id === `section-${tabName}`;
      sec.style.display = isActive ? '' : 'none';
      if (isActive) sec.classList.add('active');
      else sec.classList.remove('active');
    });

    // Update topbar title
    const titleMap = {
      dashboard:       'Overview',
      'farm-setup':    'My Farm',
      history:         'Soil & Crop History',
      recommendations: 'Crop Recommendations',
      ai:              'AI Agronomist',
      voice:           'Voice Assistant',
      weather:         'Weather & Soil',
      market:          'Market Intelligence',
      outcomes:        'Harvest & Outcomes',
    };
    const titleEl = document.getElementById('topbar-title');
    if (titleEl) titleEl.textContent = titleMap[tabName] || 'SAMPADA AI';

    // Update URL hash without scrolling
    try { history.replaceState(null, '', `#${tabName}`); } catch(e) {}

    // Scroll to top of main
    const main = document.getElementById('app-main');
    if (main) main.scrollTo({ top: 0, behavior: 'smooth' });

    // Lazy load sections
    if (tabName === 'weather') this.loadWeather();
    if (tabName === 'market')  this.loadMarket();
    if (tabName === 'outcomes') this.loadOutcomes();
    if (tabName === 'ai') {
      aiChatModule.loadChats();
      setTimeout(() => document.getElementById('chat-input-text')?.focus(), 300);
    }
  }

  // ----------------------------------------------------------
  // MOBILE SIDEBAR
  // ----------------------------------------------------------
  bindMobileSidebar() {
    const toggleBtn  = document.getElementById('sidebar-toggle');
    const backdrop   = document.getElementById('sidebar-backdrop');

    if (toggleBtn) toggleBtn.addEventListener('click', () => this.openSidebar());
    if (backdrop)  backdrop.addEventListener('click',  () => this.closeSidebar());

    // ESC key closes sidebar
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') this.closeSidebar();
    });
  }

  openSidebar() {
    document.getElementById('app-sidebar')?.classList.add('open');
    const bd = document.getElementById('sidebar-backdrop');
    if (bd) bd.style.display = 'block';
  }

  closeSidebar() {
    document.getElementById('app-sidebar')?.classList.remove('open');
    const bd = document.getElementById('sidebar-backdrop');
    if (bd) bd.style.display = 'none';
  }

  // ----------------------------------------------------------
  // LANGUAGE SWITCHER
  // ----------------------------------------------------------
  bindLanguageSwitcher() {
    const selectors = [
      document.getElementById('global-lang-select'),
      document.getElementById('public-lang-select'),
    ].filter(Boolean);

    selectors.forEach(sel => {
      sel.value = this.currentLang;
      sel.addEventListener('change', (e) => {
        this.currentLang = e.target.value;
        selectors.forEach(s => s.value = this.currentLang);
        localStorage.setItem('sampada_lang', this.currentLang);
        this.applyLanguage(this.currentLang);
      });
    });
  }

  applyLanguage(lang) {
    const dict = translations[lang] || translations.en;
    document.querySelectorAll('[data-i18n]').forEach(el => {
      const key = el.dataset.i18n;
      if (dict[key]) el.textContent = dict[key];
    });

    const voiceSel = document.getElementById('voice-lang-select');
    if (voiceSel) {
      voiceSel.value = lang === 'mr' ? 'mr-IN' : lang === 'hi' ? 'hi-IN' : 'en-IN';
    }
  }

  // ----------------------------------------------------------
  // MODALS
  // ----------------------------------------------------------
  bindModals() {
    // Open auth modal
    document.querySelectorAll('.btn-open-auth').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.preventDefault();
        const mode  = btn.dataset.authMode || 'login';
        const modal = document.getElementById('auth-modal');
        if (!modal) return;
        modal.classList.add('open');
        if (mode === 'register') {
          document.getElementById('tab-auth-register')?.click();
        } else {
          document.getElementById('tab-auth-login')?.click();
        }
      });
    });

    // Close on backdrop/close-btn click
    document.querySelectorAll('.modal-overlay').forEach(modal => {
      modal.addEventListener('click', (e) => {
        if (e.target === modal || e.target.classList.contains('btn-close-modal')) {
          modal.classList.remove('open');
        }
      });
    });

    // Close on ESC
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        document.querySelectorAll('.modal-overlay.open').forEach(m => m.classList.remove('open'));
      }
    });
  }

  // ----------------------------------------------------------
  // SMOOTH SCROLL FOR LANDING PAGE ANCHORS
  // ----------------------------------------------------------
  bindSmoothScroll() {
    document.querySelectorAll('a[href^="#"]').forEach(link => {
      link.addEventListener('click', (e) => {
        const target = document.querySelector(link.getAttribute('href'));
        if (target) {
          e.preventDefault();
          target.scrollIntoView({ behavior: 'smooth' });
        }
      });
    });
  }

  // ----------------------------------------------------------
  // ----------------------------------------------------------
  // WEATHER SECTION
  // ----------------------------------------------------------
  async loadWeather() {
    const panel = document.getElementById('weather-panel');
    if (!panel) return;

    panel.innerHTML = `<div style="padding:2rem; text-align:center; color:var(--muted-text);">Fetching live weather from Open-Meteo...</div>`;

    try {
      const farmId = farmerModule.currentFarmId;
      const url = farmId ? `/api/v1/weather?farm_id=${farmId}&days=7` : `/api/v1/weather?days=7`;
      const resp = await fetch(url, { headers: farmerModule.headers() });
      if (!resp.ok) throw new Error('Weather API returned an error');
      const d = await resp.json();
      const days = d.days || [];
      const today = days[0] || {};

      panel.innerHTML = `
        <div class="panel" style="margin-bottom:1.5rem;">
          <div class="panel-header">
            <div>
              <div class="panel-title">Weather Intelligence — ${d.place_label || 'Maharashtra'}</div>
              <div class="panel-subtitle">Lat ${d.latitude?.toFixed(2)}, Lon ${d.longitude?.toFixed(2)} · ${d.timezone || 'Asia/Kolkata'}</div>
            </div>
            <span class="chip-badge badge-verified">🌤️ Open-Meteo Live</span>
          </div>
          <div class="stats-row">
            <div class="stat-card">
              <div class="stat-label">Max Temperature</div>
              <div class="stat-value" style="color:var(--forest);">${today.temp_max_c ?? '—'}°C</div>
              <div class="stat-sub">7-Day Mean: ${d.mean_temp_max_c ? d.mean_temp_max_c + '°C' : 'Normal'}</div>
            </div>
            <div class="stat-card">
              <div class="stat-label">Min Temperature</div>
              <div class="stat-value">${today.temp_min_c ?? '—'}°C</div>
              <div class="stat-sub">Night temp</div>
            </div>
            <div class="stat-card">
              <div class="stat-label">Today's Rain</div>
              <div class="stat-value">${today.rainfall_mm ?? 0} mm</div>
              <div class="stat-sub">${(today.rainfall_mm || 0) > 0 ? 'Precipitation active' : 'No rain today'}</div>
            </div>
            <div class="stat-card">
              <div class="stat-label">7-Day Total Rain</div>
              <div class="stat-value">${d.total_rainfall_mm ?? 0} mm</div>
              <div class="stat-sub">Cumulative forecast</div>
            </div>
          </div>
        </div>
        ${days.length ? this._renderForecast(days) : ''}
      `;
    } catch (err) {
      panel.innerHTML = `<div class="panel"><p style="color:var(--muted-text); font-size:0.9rem;">Weather data temporarily unavailable. ${err.message}</p></div>`;
    }
  }

  _renderForecast(days) {
    return `
      <div class="panel">
        <div class="panel-header">
          <div class="panel-title">7-Day Daily Forecast</div>
        </div>
        <div style="display:grid; grid-template-columns: repeat(auto-fill, minmax(130px, 1fr)); gap:0.75rem;">
          ${days.map(d => `
            <div style="background:var(--ivory); border:1px solid var(--border); border-radius:var(--radius-md); padding:0.85rem; text-align:center;">
              <div style="font-size:0.75rem; color:var(--dim-text); font-weight:600;">${d.day || ''}</div>
              <div style="font-size:1.35rem; margin:0.35rem 0;">${(d.rainfall_mm || 0) > 2 ? '🌧️' : (d.rainfall_mm || 0) > 0 ? '🌦️' : '☀️'}</div>
              <div style="font-size:1rem; font-weight:700; color:var(--forest);">${d.temp_max_c ?? '—'}°C</div>
              <div style="font-size:0.75rem; color:var(--muted-text);">${d.temp_min_c ?? '—'}°C min</div>
              <div style="font-size:0.72rem; color:var(--dim-text); margin-top:0.2rem;">${d.rainfall_mm ? d.rainfall_mm + ' mm' : 'Dry'}</div>
            </div>
          `).join('')}
        </div>
      </div>
    `;
  }

  // ----------------------------------------------------------
  // MARKET SECTION
  // ----------------------------------------------------------
  async loadMarket() {
    const panel = document.getElementById('market-panel');
    if (!panel) return;

    panel.innerHTML = `<div style="padding:2rem; text-align:center; color:var(--muted-text);">Fetching APMC mandi data...</div>`;

    try {
      const resp = await fetch('/api/v1/market/prices', { headers: farmerModule.headers() });
      if (!resp.ok) throw new Error('Market API returned an error');
      const d = await resp.json();
      const prices = d.prices || d.commodities || [];

      if (!prices.length) throw new Error('No price data returned');

      panel.innerHTML = `
        <div class="panel">
          <div class="panel-header">
            <div>
              <div class="panel-title">APMC Maharashtra Mandi Prices</div>
              <div class="panel-subtitle">Live commodity arrivals — ${d.date || 'Today'}</div>
            </div>
            <span class="chip-badge badge-verified">📊 Live</span>
          </div>
          <div style="display:grid; grid-template-columns:repeat(auto-fill, minmax(200px, 1fr)); gap:1rem; margin-top:1rem;">
            ${prices.map(p => `
              <div class="mandi-card">
                <div class="mandi-crop">${p.crop || p.commodity} ${p.local_name ? '· ' + p.local_name : ''}</div>
                <div class="mandi-price">₹${Number(p.modal_price || p.price || 0).toLocaleString('en-IN')}</div>
                <div class="mandi-msp">${p.msp ? 'MSP: ₹' + Number(p.msp).toLocaleString('en-IN') + '/Q' : p.market || ''}</div>
              </div>
            `).join('')}
          </div>
        </div>
      `;
    } catch (err) {
      // Fallback to informative static display
      panel.innerHTML = `
        <div class="panel">
          <div class="panel-header">
            <div class="panel-title">Maharashtra APMC Indicative Prices</div>
            <div class="panel-subtitle">Live market API temporarily unavailable. Showing representative MSP data.</div>
          </div>
          <div style="display:grid; grid-template-columns:repeat(auto-fill, minmax(200px, 1fr)); gap:1rem; margin-top:1rem;">
            ${[
              ['Soybean · सोयाबीन','₹4,890','MSP: ₹4,600/Q'],
              ['Cotton · कापूस','₹7,450','MSP: ₹7,122/Q'],
              ['Onion · कांदा','₹2,250','Market Active'],
              ['Tur / Arhar · तूर','₹9,800','MSP: ₹7,550/Q'],
              ['Wheat · गहू','₹2,275','MSP: ₹2,275/Q'],
              ['Sugarcane · ऊस','₹3,200','FRP: ₹3,150/Q'],
            ].map(([crop,price,msp]) => `
              <div class="mandi-card">
                <div class="mandi-crop">${crop}</div>
                <div class="mandi-price">${price}</div>
                <div class="mandi-msp">${msp}</div>
              </div>
            `).join('')}
          </div>
          <p style="margin-top:1rem; font-size:0.78rem; color:var(--dim-text);">⚠️ Prices are representative MSP values. Sign in and connect to the live API for real-time mandi data.</p>
        </div>
      `;
    }
  }

  // ----------------------------------------------------------
  // OUTCOMES SECTION
  // ----------------------------------------------------------
  async loadOutcomes() {
    const panel = document.getElementById('outcomes-panel');
    if (!panel || !farmerModule.token) return;

    const farmId = farmerModule.currentFarmId;
    if (!farmId) {
      panel.innerHTML = `<div class="panel"><p class="text-muted">Select a farm plot first to view outcomes.</p></div>`;
      return;
    }

    panel.innerHTML = `<div style="padding:2rem; text-align:center; color:var(--muted-text);">Loading outcomes...</div>`;

    try {
      const resp = await fetch(`/api/v1/outcomes?farm_id=${farmId}`, { headers: farmerModule.headers() });
      const outcomes = resp.ok ? await resp.json() : [];

      if (!outcomes.length) {
        panel.innerHTML = `
          <div class="panel" style="text-align:center; padding:3rem;">
            <div style="font-size:2rem; margin-bottom:0.75rem;">🏆</div>
            <h3 style="font-weight:700; margin-bottom:0.5rem; color:var(--dark-text);">No harvest outcomes recorded yet</h3>
            <p class="text-muted" style="font-size:0.88rem; max-width:420px; margin:0 auto 1.5rem;">Recording harvest data helps SAMPADA build yield benchmarks and improves future recommendations.</p>
            <button class="btn-primary" onclick="document.getElementById('modal-add-outcome').classList.add('open')">
              ➕ Record First Harvest
            </button>
          </div>
        `;
        return;
      }

      panel.innerHTML = `
        <div class="timeline-container">
          ${outcomes.map(out => `
            <div class="timeline-card">
              <div class="timeline-marker marker-outcome"></div>
              <div class="timeline-header">
                <div>
                  <span class="chip-badge badge-verified">🏷️ Harvest Outcome</span>
                  <span class="chip-badge badge-time" style="margin-left:0.3rem;">📅 ${out.season || ''} ${out.year || ''}</span>
                  <h4 style="font-size:1.05rem; font-weight:800; margin-top:0.35rem; color:var(--dark-text);">${(out.crop_id||'').toUpperCase()}</h4>
                </div>
                ${out.profit_inr ? `<div style="font-size:1.1rem; font-weight:800; color:var(--forest);">₹${Number(out.profit_inr).toLocaleString('en-IN')} Net Profit</div>` : ''}
              </div>
              <div class="timeline-body-grid">
                <div><span class="k">Yield:</span> <strong>${out.yield_quintal || '—'} Q</strong></div>
                <div><span class="k">Cost:</span> <strong>₹${out.cost_inr ? Number(out.cost_inr).toLocaleString('en-IN') : '—'}</strong></div>
                <div><span class="k">Sale Price:</span> <strong>₹${out.sell_price_inr_q ? Number(out.sell_price_inr_q).toLocaleString('en-IN') : '—'}/Q</strong></div>
                <div><span class="k">Market:</span> <strong>${out.market || '—'}</strong></div>
              </div>
              ${out.notes ? `<p style="font-size:0.8rem; color:var(--muted-text); margin-top:0.4rem;">Notes: ${out.notes}</p>` : ''}
            </div>
          `).join('')}
        </div>
      `;
    } catch (err) {
      panel.innerHTML = `<div class="panel"><p style="color:var(--red);">Error loading outcomes: ${err.message}</p></div>`;
    }
  }
}

document.addEventListener('DOMContentLoaded', () => {
  const app = new SampadaApp();
  app.init();
  window.sampadaApp = app;
});
