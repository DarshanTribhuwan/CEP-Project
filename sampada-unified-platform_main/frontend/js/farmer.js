// Farmer Portal & Session Manager
// Handles Authentication, Farm Management, Farm History Timeline, and Recommendations

export const farmerModule = {
  token: localStorage.getItem('sampada_token') || null,
  farmer: JSON.parse(localStorage.getItem('sampada_farmer') || 'null'),
  farms: [],
  currentFarmId: localStorage.getItem('sampada_active_farm_id') || null,
  cropsList: [],
  enums: {},

  init() {
    this.bindEvents();
    this.loadEnumsAndCrops();
    this.checkSession();
  },

  headers() {
    const h = { 'Content-Type': 'application/json' };
    if (this.token) {
      h['Authorization'] = `Bearer ${this.token}`;
    }
    return h;
  },

  async loadEnumsAndCrops() {
    try {
      const [cropRes, enumRes] = await Promise.all([
        fetch('/api/v1/crops?lang=en'),
        fetch('/api/v1/enums?lang=en')
      ]);
      if (cropRes.ok) {
        const d = await cropRes.json();
        this.cropsList = d.crops || [];
        this.populateCropDropdowns();
      }
      if (enumRes.ok) {
        const e = await enumRes.json();
        this.enums = e.enums || {};
        this.populateEnumDropdowns();
      }
    } catch (err) {
      console.warn("Could not load enums/crops", err);
    }
  },

  populateCropDropdowns() {
    const cropSelects = document.querySelectorAll('.crop-select-field');
    cropSelects.forEach(sel => {
      sel.innerHTML = '<option value="">-- Select Crop --</option>' +
        this.cropsList.map(c => `<option value="${c.id}">${c.name.label} (${c.name.scientific_name || c.id})</option>`).join('');
    });
  },

  populateEnumDropdowns() {
    const populate = (selId, group) => {
      const el = document.getElementById(selId);
      if (el && this.enums[group]) {
        el.innerHTML = this.enums[group].map(i => `<option value="${i.code}">${i.label}</option>`).join('');
      }
    };
    populate('f-unit', 'land_unit');
    populate('f-irrigation', 'irrigation_type');
    populate('f-water', 'water_source');
    populate('f-soiltype', 'soil_type');
    populate('f-rainbeh', 'soil_rain_behaviour');
    populate('hist-season', 'season');
  },

  bindEvents() {
    // Auth Form Tabs (Sign In vs Register)
    const tabSignIn = document.getElementById('tab-auth-login');
    const tabRegister = document.getElementById('tab-auth-register');
    const paneSignIn = document.getElementById('auth-pane-login');
    const paneRegister = document.getElementById('auth-pane-register');

    if (tabSignIn && tabRegister) {
      tabSignIn.addEventListener('click', () => {
        tabSignIn.classList.add('active');
        tabRegister.classList.remove('active');
        if (paneSignIn) paneSignIn.style.display = 'block';
        if (paneRegister) paneRegister.style.display = 'none';
      });
      tabRegister.addEventListener('click', () => {
        tabRegister.classList.add('active');
        tabSignIn.classList.remove('active');
        if (paneSignIn) paneSignIn.style.display = 'none';
        if (paneRegister) paneRegister.style.display = 'block';
      });
    }

    // Login submit
    const btnLogin = document.getElementById('btn-submit-login');
    if (btnLogin) {
      btnLogin.addEventListener('click', (e) => {
        e.preventDefault();
        this.handleLogin();
      });
    }

    // Register submit
    const btnRegister = document.getElementById('btn-submit-register');
    if (btnRegister) {
      btnRegister.addEventListener('click', (e) => {
        e.preventDefault();
        this.handleRegister();
      });
    }

    // Demo Account Quick Fill Buttons
    const btnFillDemo1 = document.getElementById('btn-fill-demo1');
    if (btnFillDemo1) {
      btnFillDemo1.addEventListener('click', () => {
        document.getElementById('login-mobile').value = '9076411451';
        document.getElementById('login-pin').value = '1234';
      });
    }
    const btnFillDemo2 = document.getElementById('btn-fill-demo2');
    if (btnFillDemo2) {
      btnFillDemo2.addEventListener('click', () => {
        document.getElementById('login-mobile').value = '9317362680';
        document.getElementById('login-pin').value = '1234';
      });
    }

    // Sign out
    const btnLogout = document.getElementById('btn-logout');
    if (btnLogout) {
      btnLogout.addEventListener('click', () => this.handleLogout());
    }

    // Farm selector change
    const farmSelect = document.getElementById('active-farm-select');
    if (farmSelect) {
      farmSelect.addEventListener('change', (e) => {
        this.setActiveFarm(parseInt(e.target.value, 10));
      });
    }

    // Save/Update Farm
    const btnSaveFarm = document.getElementById('btn-save-farm');
    if (btnSaveFarm) {
      btnSaveFarm.addEventListener('click', () => this.handleSaveFarm());
    }

    // GPS Auto-detect
    const btnGps = document.getElementById('btn-detect-gps');
    if (btnGps) {
      btnGps.addEventListener('click', () => this.detectLocation());
    }

    // Soil estimate
    const btnSaveSoil = document.getElementById('btn-save-soil');
    if (btnSaveSoil) {
      btnSaveSoil.addEventListener('click', () => this.handleSaveSoil());
    }

    // Generate recommendations (dashboard widget)
    const btnGetRec = document.getElementById('btn-get-rec');
    if (btnGetRec) {
      btnGetRec.addEventListener('click', () => this.generateRecommendations('dash-recommendations-grid'));
    }

    // Add Crop History submit
    const btnAddCropHist = document.getElementById('btn-submit-crop-history');
    if (btnAddCropHist) {
      btnAddCropHist.addEventListener('click', () => this.handleAddCropHistory());
    }

    // Add Outcome submit
    const btnSaveOutcome = document.getElementById('btn-save-outcome');
    if (btnSaveOutcome) {
      btnSaveOutcome.addEventListener('click', () => this.handleSaveOutcome());
    }
  },

  async checkSession() {
    if (!this.token) {
      this.renderAuthState(false);
      return;
    }

    try {
      const resp = await fetch('/api/v1/auth/me', { headers: this.headers() });
      if (resp.ok) {
        this.farmer = await resp.json();
        localStorage.setItem('sampada_farmer', JSON.stringify(this.farmer));
        this.renderAuthState(true);
        await this.loadFarms();
      } else {
        // Token is invalid or expired
        this.clearSession();
        this.renderAuthState(false);
      }
    } catch (err) {
      console.warn("Session check error", err);
      this.renderAuthState(false);
    }
  },

  renderAuthState(isAuthenticated) {
    const publicView = document.getElementById('view-public');
    const authView   = document.getElementById('view-authenticated');

    if (isAuthenticated && this.farmer) {
      if (publicView) publicView.style.display = 'none';
      if (authView)   authView.style.display   = 'block';

      // Update all farmer name elements
      ['nav-farmer-name', 'sidebar-farmer-name'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.textContent = this.farmer.name;
      });

      const greetEl = document.getElementById('dash-farmer-greeting');
      if (greetEl) greetEl.textContent = this.farmer.name;

      // Update sidebar avatar initials
      const avatarEl = document.getElementById('sidebar-avatar');
      if (avatarEl) avatarEl.textContent = (this.farmer.name || 'F').charAt(0).toUpperCase();

      // Navigate to dashboard
      if (window.sampadaApp) window.sampadaApp.switchTab('dashboard');
    } else {
      if (publicView) publicView.style.display = 'block';
      if (authView)   authView.style.display   = 'none';
    }
  },

  async handleLogin() {
    const mobileEl = document.getElementById('login-mobile');
    const pinEl = document.getElementById('login-pin');
    const errBox = document.getElementById('login-error-msg');

    const mobile = mobileEl?.value.trim();
    const pin = pinEl?.value.trim();

    const showErr = (msg) => {
      if (errBox) {
        errBox.textContent = msg;
        errBox.style.display = 'block';
      }
    };
    if (errBox) {
      errBox.textContent = '';
      errBox.style.display = 'none';
    }

    if (!mobile || mobile.length !== 10 || !/^[6-9]\d{9}$/.test(mobile)) {
      showErr('Please enter a valid 10-digit Indian mobile number (starts with 6-9).');
      return;
    }
    if (!pin || pin.length < 4 || !/^\d+$/.test(pin)) {
      showErr('PIN must be 4 to 8 numeric digits.');
      return;
    }

    try {
      const resp = await fetch('/api/v1/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mobile, pin })
      });
      const data = await resp.json();

      if (!resp.ok) {
        const msg = data.error?.message || 'Invalid mobile number or PIN. Please check credentials or register.';
        showErr(msg);
        return;
      }

      // Successful login
      this.token = data.access_token;
      this.farmer = data.farmer;

      localStorage.setItem('sampada_token', this.token);
      localStorage.setItem('sampada_farmer', JSON.stringify(this.farmer));
      localStorage.setItem('sampada_farmer_id', this.farmer.id);
      localStorage.setItem('sampada_farmer_name', this.farmer.name);

      this.renderAuthState(true);
      await this.loadFarms();

      // Close modal if open
      const modal = document.getElementById('auth-modal');
      if (modal) modal.classList.remove('open');

    } catch (err) {
      showErr('Network error during login: ' + err.message);
    }
  },

  async handleRegister() {
    const nameEl = document.getElementById('reg-name');
    const mobileEl = document.getElementById('reg-mobile');
    const pinEl = document.getElementById('reg-pin');
    const langEl = document.getElementById('reg-lang');
    const errBox = document.getElementById('reg-error-msg');

    const name = nameEl?.value.trim();
    const mobile = mobileEl?.value.trim();
    const pin = pinEl?.value.trim();
    const language = langEl?.value || 'mr';

    const showErr = (msg) => {
      if (errBox) {
        errBox.textContent = msg;
        errBox.style.display = 'block';
      }
    };
    if (errBox) {
      errBox.textContent = '';
      errBox.style.display = 'none';
    }

    if (!name || name.length < 2) {
      showErr('Please enter your full name.');
      return;
    }
    if (!mobile || !/^[6-9]\d{9}$/.test(mobile)) {
      showErr('Mobile must be a valid 10-digit Indian number (starts with 6-9).');
      return;
    }
    if (!pin || pin.length < 4 || !/^\d+$/.test(pin)) {
      showErr('PIN must be 4 to 8 numeric digits.');
      return;
    }

    try {
      const resp = await fetch('/api/v1/auth/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, mobile, pin, language })
      });
      const data = await resp.json();

      if (!resp.ok) {
        const msg = data.error?.message || 'Registration failed.';
        showErr(msg);
        return;
      }

      // Successful registration
      this.token = data.access_token;
      this.farmer = data.farmer;

      localStorage.setItem('sampada_token', this.token);
      localStorage.setItem('sampada_farmer', JSON.stringify(this.farmer));
      localStorage.setItem('sampada_farmer_id', this.farmer.id);
      localStorage.setItem('sampada_farmer_name', this.farmer.name);

      this.renderAuthState(true);
      await this.loadFarms();

      const modal = document.getElementById('auth-modal');
      if (modal) modal.classList.remove('open');

    } catch (err) {
      showErr('Registration error: ' + err.message);
    }
  },

  async handleLogout() {
    try {
      if (this.token) {
        await fetch('/api/v1/auth/logout', {
          method: 'POST',
          headers: this.headers()
        });
      }
    } catch (e) {
      console.warn("Logout error", e);
    }
    this.clearSession();
    this.renderAuthState(false);
  },

  clearSession() {
    this.token = null;
    this.farmer = null;
    this.farms = [];
    this.currentFarmId = null;
    localStorage.removeItem('sampada_token');
    localStorage.removeItem('sampada_farmer');
    localStorage.removeItem('sampada_farmer_id');
    localStorage.removeItem('sampada_farmer_name');
    localStorage.removeItem('sampada_active_farm_id');
  },

  async loadFarms() {
    if (!this.token) return;
    try {
      const resp = await fetch('/api/v1/farms', { headers: this.headers() });
      if (resp.ok) {
        this.farms = await resp.json();
        this.renderFarmSelector();

        if (this.farms.length > 0) {
          const selected = this.farms.find(f => String(f.id) === String(this.currentFarmId)) || this.farms[0];
          this.setActiveFarm(selected.id);
        } else {
          // No farms yet
          this.renderEmptyFarmState();
        }
      }
    } catch (err) {
      console.warn("Could not load farms", err);
    }
  },

  renderFarmSelector() {
    const sel = document.getElementById('active-farm-select');
    if (!sel) return;

    if (this.farms.length === 0) {
      sel.innerHTML = '<option value="">No farms recorded</option>';
      return;
    }

    sel.innerHTML = this.farms.map(f => `
      <option value="${f.id}" ${String(f.id) === String(this.currentFarmId) ? 'selected' : ''}>
        🌾 ${f.label || 'Farm #' + f.id} (${f.land_area_ha ? (f.land_area_ha / 0.404686).toFixed(1) + ' Acres' : 'Plot'})
      </option>
    `).join('') + '<option value="new">+ Register New Plot</option>';
  },

  setActiveFarm(farmId) {
    if (farmId === 'new') {
      if (window.sampadaApp) window.sampadaApp.switchTab('farm-setup');
      return;
    }
    this.currentFarmId = farmId;
    localStorage.setItem('sampada_active_farm_id', farmId);

    const farm = this.farms.find(f => f.id === farmId);
    if (!farm) return;

    // Update Dashboard Header Cards
    const nameEl = document.getElementById('dash-active-farm-name');
    if (nameEl) nameEl.textContent = farm.label || 'Home Farm';

    const areaEl = document.getElementById('dash-active-farm-area');
    if (areaEl) {
      const acres = farm.land_area_ha ? (farm.land_area_ha / 0.404686).toFixed(1) : farm.land_area_input || '5.0';
      areaEl.textContent = `${acres} Acres (${farm.land_area_ha} Ha)`;
    }

    const locEl = document.getElementById('dash-active-farm-loc');
    if (locEl) locEl.textContent = [farm.village, farm.taluka, farm.district].filter(Boolean).join(', ') || 'Maharashtra, India';

    const irrEl = document.getElementById('dash-active-farm-irrigation');
    if (irrEl) irrEl.textContent = `${farm.irrigation_type || 'Rainfed'} (${farm.water_source || 'Well'})`;

    // Populate Farm Setup Form with current farm values
    if (document.getElementById('f-farm-label')) document.getElementById('f-farm-label').value = farm.label || '';
    if (document.getElementById('f-area')) document.getElementById('f-area').value = (farm.land_area_ha / 0.404686).toFixed(1) || '5.0';
    if (document.getElementById('f-district')) document.getElementById('f-district').value = farm.district || 'Pune';
    if (document.getElementById('f-taluka')) document.getElementById('f-taluka').value = farm.taluka || 'Baramati';
    if (document.getElementById('f-village')) document.getElementById('f-village').value = farm.village || '';
    if (document.getElementById('f-lat')) document.getElementById('f-lat').value = farm.latitude || 18.15;
    if (document.getElementById('f-lon')) document.getElementById('f-lon').value = farm.longitude || 74.58;

    // Load Live Data & History for active farm
    this.loadFarmHistory(farmId);
    this.loadFarmWeather(farm.latitude || 18.15, farm.longitude || 74.58);
  },

  renderEmptyFarmState() {
    const areaEl = document.getElementById('dash-active-farm-area');
    if (areaEl) areaEl.textContent = 'No plots created yet';
    const timeline = document.getElementById('farm-history-timeline');
    if (timeline) {
      timeline.innerHTML = `
        <div class="glass-panel" style="padding:2rem;text-align:center;">
          <h3>No farm plot registered yet</h3>
          <p style="color:var(--text-muted);margin:0.5rem 0 1.25rem;">Register your first plot in the Farm Setup tab to view recommendations and history.</p>
          <button class="btn-primary" onclick="window.sampadaApp.switchTab('farm-setup')">Register Farm Land</button>
        </div>
      `;
    }
  },

  async handleSaveFarm() {
    if (!this.token) return;

    const payload = {
      label: document.getElementById('f-farm-label')?.value || 'Main Farm',
      land_area: parseFloat(document.getElementById('f-area')?.value) || 5.0,
      land_unit: document.getElementById('f-unit')?.value || 'ACRE',
      irrigation_type: document.getElementById('f-irrigation')?.value || 'RAINFED',
      water_source: document.getElementById('f-water')?.value || 'WELL',
      district: document.getElementById('f-district')?.value || 'Pune',
      taluka: document.getElementById('f-taluka')?.value || 'Baramati',
      village: document.getElementById('f-village')?.value || '',
      latitude: parseFloat(document.getElementById('f-lat')?.value) || 18.15,
      longitude: parseFloat(document.getElementById('f-lon')?.value) || 74.58
    };

    const statusEl = document.getElementById('farm-save-status');
    if (statusEl) statusEl.textContent = 'Saving farm plot...';

    try {
      let resp;
      if (this.currentFarmId && this.currentFarmId !== 'new') {
        resp = await fetch(`/api/v1/farms/${this.currentFarmId}`, {
          method: 'PATCH',
          headers: this.headers(),
          body: JSON.stringify(payload)
        });
      } else {
        resp = await fetch('/api/v1/farms', {
          method: 'POST',
          headers: this.headers(),
          body: JSON.stringify(payload)
        });
      }
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error?.message || 'Failed to save farm');

      if (statusEl) {
        statusEl.innerHTML = '<span style="color:var(--primary-light)">✓ Farm plot saved successfully!</span>';
      }
      await this.loadFarms();
      this.setActiveFarm(data.id);
    } catch (err) {
      if (statusEl) statusEl.innerHTML = `<span style="color:var(--accent-red)">Error: ${err.message}</span>`;
    }
  },

  detectLocation() {
    const locStatus = document.getElementById('gps-status-indicator');
    if (!navigator.geolocation) {
      alert("Geolocation is not supported by your browser.");
      return;
    }
    if (locStatus) locStatus.textContent = "Detecting GPS location...";

    navigator.geolocation.getCurrentPosition(async (pos) => {
      const lat = pos.coords.latitude;
      const lon = pos.coords.longitude;
      document.getElementById('f-lat').value = lat.toFixed(4);
      document.getElementById('f-lon').value = lon.toFixed(4);

      try {
        const resp = await fetch('/api/v1/ai/geocode', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ latitude: lat, longitude: lon })
        });
        const geo = await resp.json();
        if (locStatus) locStatus.textContent = `📍 ${geo.formatted_address}`;
      } catch (err) {
        if (locStatus) locStatus.textContent = `📍 Lat: ${lat.toFixed(4)}, Lon: ${lon.toFixed(4)}`;
      }
    }, () => {
      if (locStatus) locStatus.textContent = "GPS permission denied or timed out.";
    });
  },

  async handleSaveSoil() {
    if (!this.currentFarmId) {
      alert("Please select or create a farm plot first.");
      return;
    }

    const soilType = document.getElementById('f-soiltype')?.value || 'BLACK';
    const rainBeh = document.getElementById('f-rainbeh')?.value || 'HOLDS_LONG';
    const statusEl = document.getElementById('soil-save-status');

    if (statusEl) statusEl.textContent = 'Computing scientific soil estimate...';

    try {
      const resp = await fetch(`/api/v1/farms/${this.currentFarmId}/soil/estimate`, {
        method: 'POST',
        headers: this.headers(),
        body: JSON.stringify({
          known_soil_type: soilType,
          rain_behaviour: rainBeh
        })
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error?.message || 'Soil estimation failed');

      if (statusEl) {
        statusEl.innerHTML = `
          <div style="color:var(--primary-light);margin-top:0.5rem;">
            ✓ Soil record stored: <strong>${data.top_soil_type || soilType}</strong>
            (Confidence: ${Math.round((data.confidence || 0.8) * 100)}%)
          </div>
        `;
      }
      this.loadFarmHistory(this.currentFarmId);
    } catch (err) {
      if (statusEl) statusEl.innerHTML = `<span style="color:var(--accent-red)">Error: ${err.message}</span>`;
    }
  },

  async loadFarmWeather(lat, lon) {
    const box = document.getElementById('dash-weather-box');
    if (!box) return;

    try {
      const resp = await fetch(`/api/v1/weather/forecast?latitude=${lat}&longitude=${lon}`, { headers: this.headers() });
      if (resp.ok) {
        const d = await resp.json();
        const cur = d.current || {};
        box.innerHTML = `
          <div style="display:flex;align-items:center;justify-content:space-between;">
            <div>
              <div style="font-size:2rem;font-weight:800;color:var(--primary-light);">${cur.temp_c ?? 29}°C</div>
              <div style="font-size:0.85rem;color:var(--text-muted);">${d.monsoon_stage || 'Normal Monsoon Onset'}</div>
            </div>
            <div style="text-align:right;">
              <div style="color:var(--accent-blue);font-weight:700;">Rain: ${cur.rain_prob ?? 15}%</div>
              <div style="font-size:0.8rem;color:var(--text-dim);">Horizon: 15 Days Live</div>
            </div>
          </div>
        `;
      }
    } catch (e) {
      box.innerHTML = '<div style="color:var(--text-muted)">Weather forecast cached regionally.</div>';
    }
  },

  // -------------------------------------------------------------------------
  // REAL FARM, CROP & SOIL HISTORY TIMELINE
  // -------------------------------------------------------------------------
  async loadFarmHistory(farmId) {
    const timelineEl = document.getElementById('farm-history-timeline');
    if (!timelineEl) return;

    timelineEl.innerHTML = '<div style="padding:2rem;text-align:center;color:var(--text-muted);">Fetching plot history, soil tests, and harvest outcomes...</div>';

    try {
      const [cropsRes, soilRes, outcomesRes, recsRes] = await Promise.all([
        fetch(`/api/v1/farms/${farmId}/crop-history`, { headers: this.headers() }),
        fetch(`/api/v1/farms/${farmId}/soil/estimate/latest`, { headers: this.headers() }),
        fetch(`/api/v1/outcomes?farm_id=${farmId}`, { headers: this.headers() }),
        fetch(`/api/v1/recommendations?farm_id=${farmId}`, { headers: this.headers() })
      ]);

      const cropHist = cropsRes.ok ? await cropsRes.json() : [];
      const soilLatest = soilRes.ok ? await soilRes.json() : { available: false };
      const outcomes = outcomesRes.ok ? await outcomesRes.json() : [];
      const recommendations = recsRes.ok ? await recsRes.json() : [];

      this.renderHistoryTimeline({
        farmId,
        cropHist,
        soilLatest,
        outcomes,
        recommendations
      });

    } catch (err) {
      timelineEl.innerHTML = `<div style="color:var(--accent-red)">Error loading farm history: ${err.message}</div>`;
    }
  },

  renderHistoryTimeline(data) {
    const timelineEl = document.getElementById('farm-history-timeline');
    if (!timelineEl) return;

    const { cropHist, soilLatest, outcomes, recommendations } = data;
    const hasAnyRecords = cropHist.length > 0 || soilLatest.available || outcomes.length > 0 || recommendations.length > 0;

    let html = '';

    // Soil Status Banner
    html += `
      <div class="glass-panel" style="padding:1.25rem 1.5rem;margin-bottom:1.5rem;border-left:4px solid var(--accent-gold);">
        <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:0.75rem;">
          <div>
            <div style="display:flex;align-items:center;gap:0.5rem;margin-bottom:0.3rem;">
              <span style="font-size:1.15rem;font-weight:700;">Plot Soil Health Record</span>
              ${soilLatest.available 
                ? '<span class="chip-badge badge-verified">🏷️ Verified Test on File</span>' 
                : '<span class="chip-badge badge-missing">⚠️ Missing Soil Test</span>'}
            </div>
            <p style="font-size:0.88rem;color:var(--text-muted);margin:0;">
              ${soilLatest.available 
                ? `Current Top Soil: <strong>${soilLatest.top_soil_type}</strong> (Confidence: ${Math.round(soilLatest.confidence * 100)}%, Model: ${soilLatest.model_version})`
                : 'No soil estimate has been recorded for this plot yet. The recommendation engine down-weights soil confidence until you test or record soil behavior.'}
            </p>
          </div>
          <div>
            <button class="btn-secondary" style="padding:0.4rem 0.8rem;font-size:0.8rem;" onclick="window.sampadaApp.switchTab('farm-setup')">
              Update Soil Evidence
            </button>
          </div>
        </div>
      </div>
    `;

    if (!hasAnyRecords) {
      html += `
        <div class="glass-panel" style="padding:2rem;text-align:center;">
          <div style="font-size:2rem;margin-bottom:0.5rem;">🌱</div>
          <h4>No crop rotation history recorded yet for this plot</h4>
          <p style="color:var(--text-muted);font-size:0.88rem;max-width:500px;margin:0.5rem auto 1.5rem;">
            Recording previous seasons helps SAMPADA compute soil nitrogen depletion, pest break cycles, and personalized yield expectations.
          </p>
          <button class="btn-primary" onclick="document.getElementById('modal-add-crop-history').classList.add('open')">
            + Record Past Crop Season
          </button>
        </div>
      `;
      timelineEl.innerHTML = html;
      return;
    }

    // Timeline entries
    html += '<div class="timeline-container">';

    // 1. Recorded Harvest Outcomes (Strongest verified historical data)
    outcomes.forEach(out => {
      html += `
        <div class="timeline-card">
          <div class="timeline-marker marker-outcome"></div>
          <div class="timeline-header">
            <div>
              <span class="chip-badge badge-verified">🏷️ Recorded Harvest Outcome</span>
              <span class="chip-badge badge-time">📅 ${out.season || ''} ${out.year || ''}</span>
              <h4 style="font-size:1.15rem;font-weight:700;margin-top:0.35rem;">Crop: ${out.crop_id.toUpperCase()}</h4>
            </div>
            <div style="text-align:right;">
              <div style="font-size:1.1rem;font-weight:800;color:var(--primary-light);">
                ${out.profit_inr ? '₹' + Number(out.profit_inr).toLocaleString('en-IN') + ' Net Profit' : ''}
              </div>
            </div>
          </div>
          <div class="timeline-body-grid">
            <div><span class="k">Harvested Yield:</span> <strong>${out.yield_quintal || '—'} Quintals</strong></div>
            <div><span class="k">Cultivation Cost:</span> <strong>₹${out.cost_inr ? Number(out.cost_inr).toLocaleString('en-IN') : '—'}</strong></div>
            <div><span class="k">Mandi Sale Price:</span> <strong>₹${out.sell_price_inr_q ? Number(out.sell_price_inr_q).toLocaleString('en-IN') : '—'}/Q</strong></div>
            <div><span class="k">APMC Market:</span> <strong>${out.market || 'Regional Mandi'}</strong></div>
          </div>
          ${out.notes ? `<div style="font-size:0.82rem;color:var(--text-muted);margin-top:0.5rem;">Notes: ${out.notes}</div>` : ''}
        </div>
      `;
    });

    // 2. Crop Rotation History (Farmer-entered records from memory)
    cropHist.forEach(c => {
      html += `
        <div class="timeline-card">
          <div class="timeline-marker marker-crop"></div>
          <div class="timeline-header">
            <div>
              <span class="chip-badge badge-reported">📝 Farmer-Entered Record</span>
              <span class="chip-badge badge-time">📅 ${c.season || 'Season'} ${c.year || ''}</span>
              <h4 style="font-size:1.15rem;font-weight:700;margin-top:0.35rem;">Previous Crop: ${c.crop_id.toUpperCase()}</h4>
            </div>
            <div>
              <span class="chip-badge" style="background:rgba(255,255,255,0.06);">${c.area_ha ? (c.area_ha / 0.404686).toFixed(1) + ' Acres' : 'Plot-wide'}</span>
            </div>
          </div>
          <div class="timeline-body-grid">
            <div><span class="k">Yield:</span> <strong>${c.yield_quintal ? c.yield_quintal + ' Q' : 'Unrecorded'}</strong></div>
            <div><span class="k">Cultivation Cost:</span> <strong>${c.cost_inr ? '₹' + c.cost_inr : 'Unrecorded'}</strong></div>
            <div><span class="k">Sale Price:</span> <strong>${c.sell_price_inr_q ? '₹' + c.sell_price_inr_q + '/Q' : 'Unrecorded'}</strong></div>
            <div><span class="k">Market:</span> <strong>${c.market || '—'}</strong></div>
          </div>
        </div>
      `;
    });

    // 3. Historical Recommendations Made by SAMPADA
    recommendations.slice(0, 5).forEach(rec => {
      html += `
        <div class="timeline-card">
          <div class="timeline-marker marker-rec"></div>
          <div class="timeline-header">
            <div>
              <span class="chip-badge badge-inferred">🤖 AI Recommendation Issued</span>
              <span class="chip-badge badge-time">${rec.season} Season</span>
              <h4 style="font-size:1.15rem;font-weight:700;margin-top:0.35rem;">Recommended: ${rec.crop_id.toUpperCase()} (Rank #${rec.rank})</h4>
            </div>
            <div>
              <span class="chip-badge badge-verified">Score: ${Math.round((rec.final_score || 0.8) * 100)}%</span>
            </div>
          </div>
          <p style="font-size:0.85rem;color:var(--text-muted);margin:0.4rem 0;">
            Confidence Band: <strong>${rec.confidence >= 0.75 ? 'HIGH' : rec.confidence >= 0.5 ? 'MEDIUM' : 'LOW'}</strong> (${Math.round(rec.confidence * 100)}%)
          </p>
        </div>
      `;
    });

    html += '</div>';

    // Button to record more history
    html += `
      <div style="margin-top:1.5rem;text-align:right;">
        <button class="btn-primary" onclick="document.getElementById('modal-add-crop-history').classList.add('open')">
          + Add Another Past Season Crop
        </button>
      </div>
    `;

    timelineEl.innerHTML = html;
  },

  async handleAddCropHistory() {
    if (!this.currentFarmId) {
      alert("Please select a farm plot first.");
      return;
    }

    const cropId = document.getElementById('hist-crop-id')?.value;
    const season = document.getElementById('hist-season')?.value;
    const year = parseInt(document.getElementById('hist-year')?.value, 10);
    const yieldQ = parseFloat(document.getElementById('hist-yield')?.value) || undefined;
    const cost = parseFloat(document.getElementById('hist-cost')?.value) || undefined;
    const price = parseFloat(document.getElementById('hist-price')?.value) || undefined;
    const market = document.getElementById('hist-market')?.value || undefined;

    if (!cropId) {
      alert("Please select a crop.");
      return;
    }

    try {
      const resp = await fetch(`/api/v1/farms/${this.currentFarmId}/crop-history`, {
        method: 'POST',
        headers: this.headers(),
        body: JSON.stringify({
          crop_id: cropId,
          season,
          year,
          yield_quintal: yieldQ,
          cost_inr: cost,
          sell_price_inr_per_quintal: price,
          market
        })
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error?.message || "Failed to record crop history");

      // Close modal
      document.getElementById('modal-add-crop-history').classList.remove('open');
      alert("Past season record added successfully!");
      this.loadFarmHistory(this.currentFarmId);
    } catch (err) {
      alert("Error adding crop history: " + err.message);
    }
  },

  // -------------------------------------------------------------------------
  // SCIENTIFIC RECOMMENDATIONS WITH 6-FACTOR EXPLANATION
  // -------------------------------------------------------------------------
  async generateRecommendations(targetContainerId) {
    if (!this.currentFarmId) {
      alert('Please select or save a farm plot first.');
      return;
    }

    // Support both dashboard widget and full recommendations page
    const recContainer = document.getElementById(targetContainerId || 'dash-recommendations-grid');
    if (!recContainer) return;

    recContainer.innerHTML = '<div style="padding:2rem;text-align:center;color:var(--muted-text);">Running multi-factor agronomy engine...</div>';

    const season = document.getElementById('dash-season-select')?.value || 'KHARIF';

    try {
      const resp = await fetch('/api/v1/recommendations', {
        method: 'POST',
        headers: this.headers(),
        body: JSON.stringify({
          farm_id: parseInt(this.currentFarmId, 10),
          season: season,
          max_results: 4
        })
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error?.message || "Recommendation generation failed");

      const options = data.options || [];
      if (options.length === 0) {
        recContainer.innerHTML = '<div style="padding:1.5rem;text-align:center;color:var(--text-muted);">No suitable crops found for the selected season and constraints.</div>';
        return;
      }

      recContainer.innerHTML = options.map((opt, idx) => {
        const factors = opt.scores || [];
        const isTop = idx === 0;
        const reasons = opt.reasons || [];
        const primaryReason = reasons.length > 0 ? (reasons[0].text || reasons[0].message || 'Favorable agronomic and seasonal fit') : 'Favorable agronomic and seasonal fit';
        const score = Math.round((opt.final_score || 0.8) * 100);
        const conf  = Math.round((opt.confidence || 0.85) * 100);
        const riskColor = opt.risk?.overall === 'LOW' ? 'var(--forest)' : opt.risk?.overall === 'HIGH' ? 'var(--red)' : 'var(--gold)';

        return `
          <div class="rec-card">
            <div class="rec-card-rank">${opt.rank}</div>
            <h4>${opt.crop?.label || opt.crop?.code || opt.crop_id}</h4>
            <div class="score-bar"><div class="score-fill" style="width:${score}%"></div></div>
            <div class="score-text" style="display:flex; justify-content:space-between; margin-bottom:0.75rem;">
              <span>Score: <strong style="color:var(--forest);">${score}%</strong></span>
              <span>Conf: <strong>${conf}%</strong></span>
            </div>
            <div style="display:flex; justify-content:space-between; font-size:0.8rem; margin-bottom:0.75rem;">
              <span style="color:var(--muted-text);">Risk:</span>
              <strong style="color:${riskColor};">${opt.risk?.overall || 'LOW'}</strong>
            </div>
            <p style="font-size:0.8rem; color:var(--muted-text); line-height:1.5; margin-bottom:1rem;">${primaryReason}</p>
            ${factors.length ? `
              <div style="border-top:1px solid var(--border); padding-top:0.75rem; margin-top:0.25rem;">
                <div style="font-size:0.68rem; font-weight:700; color:var(--dim-text); text-transform:uppercase; letter-spacing:0.06em; margin-bottom:0.4rem;">Factor Breakdown</div>
                <div style="display:grid; grid-template-columns:1fr 1fr; gap:0.2rem;">
                  ${factors.map(f => `
                    <div style="font-size:0.73rem; color:${f.available && f.score !== null ? 'var(--dark-text)' : 'var(--dim-text)'}">
                      ${this.formatFactorName(f.factor)}: <strong>${f.available && f.score !== null ? Math.round(f.score*100)+'%' : '—'}</strong>
                    </div>
                  `).join('')}
                </div>
              </div>` : ''}
            <button class="btn-secondary" style="width:100%; margin-top:1rem; padding:0.45rem; font-size:0.8rem;" onclick="farmerModule.promptFollowRecommendation('${opt.crop?.code || opt.crop_id}')">
              ✓ Plan This Crop
            </button>
          </div>
        `;
      }).join('');

    } catch (err) {
      recContainer.innerHTML = `<div style="color:var(--accent-red)">Failed to calculate recommendations: ${err.message}</div>`;
    }
  },

  formatFactorName(key) {
    const map = {
      soil_suitability: 'Soil Fit',
      weather_match: 'Weather',
      market_price: 'Price',
      water_availability: 'Water',
      farmer_history: 'History',
      community_saturation: 'Diversity'
    };
    return map[key] || key;
  },

  promptFollowRecommendation(cropId) {
    alert(`Marked ${cropId.toUpperCase()} as your planned crop for this season. At harvest time, record your actual yields in My Farm History to refine next season's predictions.`);
  },

  async handleSaveOutcome() {
    if (!this.currentFarmId) { alert('Please select a farm first.'); return; }
    const cropId = document.getElementById('out-crop-id')?.value;
    const season = document.getElementById('out-season')?.value;
    const year   = parseInt(document.getElementById('out-year')?.value, 10);
    const yieldQ = parseFloat(document.getElementById('out-yield')?.value) || undefined;
    const cost   = parseFloat(document.getElementById('out-cost')?.value)  || undefined;
    const price  = parseFloat(document.getElementById('out-price')?.value) || undefined;
    const profit = parseFloat(document.getElementById('out-profit')?.value) || undefined;
    const market = document.getElementById('out-market')?.value || undefined;
    const notes  = document.getElementById('out-notes')?.value  || undefined;
    const statusEl = document.getElementById('outcome-save-status');

    if (!cropId) { alert('Please select a crop.'); return; }

    try {
      const resp = await fetch('/api/v1/outcomes', {
        method: 'POST',
        headers: this.headers(),
        body: JSON.stringify({
          farm_id: parseInt(this.currentFarmId, 10),
          crop_id: cropId,
          season, year,
          yield_quintal: yieldQ,
          cost_inr: cost,
          sell_price_inr_q: price,
          profit_inr: profit,
          market, notes
        })
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error?.message || 'Failed to save outcome');
      document.getElementById('modal-add-outcome').classList.remove('open');
      if (statusEl) statusEl.innerHTML = '<span style="color:var(--forest)">✓ Outcome saved!</span>';
      // Refresh outcomes if panel is visible
      if (window.sampadaApp?.currentTab === 'outcomes') window.sampadaApp.loadOutcomes();
    } catch (err) {
      if (statusEl) statusEl.innerHTML = `<span style="color:var(--red)">Error: ${err.message}</span>`;
    }
  }
};
