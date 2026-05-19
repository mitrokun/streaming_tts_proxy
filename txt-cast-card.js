class TxtCastCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this.isInitialized = false;
    this._voicesByConfig = {};
  }

  setConfig(config) {
    this.config = { domain: 'streaming_tts_proxy', ...config };
  }

  set hass(hass) {
    this._hass = hass;
    if (!this.isInitialized) {
      this.isInitialized = true;
      this.render();
      this.loadData();
    }
  }

  render() {
    this.shadowRoot.innerHTML = `
      <style>
        ha-card {
          padding: 16px;
        }
        .field {
          margin-bottom: 12px;
        }
        label {
          display: block;
          font-size: 11px;
          font-weight: 600;
          color: var(--secondary-text-color);
          margin-bottom: 4px;
          text-transform: uppercase;
          letter-spacing: 0.5px;
        }
        select, input {
          width: 100%;
          padding: 8px 10px;
          border: 1px solid var(--divider-color, #e0e0e0);
          border-radius: 4px;
          background: var(--card-background-color, #fff);
          color: var(--primary-text-color, #000);
          font-size: 14px;
          box-sizing: border-box;
          font-family: inherit;
          outline: none;
          transition: border-color 0.2s;
        }
        select:focus, input:focus {
          border-color: var(--primary-color);
        }
        select:disabled {
          opacity: 0.6;
          cursor: not-allowed;
        }
        .row {
          display: flex;
          gap: 12px;
        }
        .row .field {
          flex: 1;
        }
        .buttons {
          display: flex;
          gap: 12px;
          margin-top: 16px;
        }
        button {
          flex: 1;
          padding: 10px;
          border: none;
          border-radius: 4px;
          font-size: 14px;
          font-weight: 500;
          cursor: pointer;
          transition: opacity 0.2s;
        }
        button:active {
          opacity: 0.8;
        }
        .btn-play {
          background: var(--primary-color);
          color: var(--text-primary-color, #fff);
        }
        .btn-resume {
          background: var(--secondary-background-color, #e0e0e0);
          color: var(--primary-text-color, #000);
        }
      </style>
      <ha-card>
        <div class="field">
          <label>Target Player</label>
          <select id="player-select"><option>Loading...</option></select>
        </div>

        <div class="field">
          <label>TTS Configuration</label>
          <select id="config-select"><option>Loading...</option></select>
        </div>

        <div class="field">
          <label>TXT File</label>
          <select id="file-select"><option>Loading...</option></select>
        </div>

        <div class="field">
          <label>Voice</label>
          <select id="voice-select" disabled>
            <option value="">Default Voice</option>
          </select>
        </div>

        <div class="row">
          <div class="field">
            <label>Start Block</label>
            <input type="number" id="block-input" placeholder="Optional" min="0">
          </div>
          <div class="field">
            <label>Timer (min)</label>
            <input type="number" id="timer-input" placeholder="Optional" min="1">
          </div>
        </div>

        <div class="buttons">
          <button class="btn-resume" id="btn-resume">Resume</button>
          <button class="btn-play" id="btn-play">Play</button>
        </div>
      </ha-card>
    `;

    this.shadowRoot.getElementById('btn-play').addEventListener('click', () => this.handlePlay());
    this.shadowRoot.getElementById('btn-resume').addEventListener('click', () => this.handleResume());
    
    this.shadowRoot.getElementById('player-select').addEventListener('change', (e) => {
      localStorage.setItem('txtCast_lastPlayer', e.target.value);
    });

    this.shadowRoot.getElementById('config-select').addEventListener('change', (e) => {
      localStorage.setItem('txtCast_lastConfig', e.target.value);
      this.updateVoiceDropdown(e.target.value);
    });

    this.shadowRoot.getElementById('voice-select').addEventListener('change', (e) => {
      localStorage.setItem('txtCast_lastVoice', e.target.value);
    });
  }

  async loadData() {
    const domain = this.config.domain;

    // 1. Load MP
    const players = Object.keys(this._hass.states)
      .filter(entity => entity.startsWith('media_player.'))
      .sort();
    
    const playerSelect = this.shadowRoot.getElementById('player-select');
    playerSelect.innerHTML = players.map(p => 
      `<option value="${p}">${this._hass.states[p].attributes.friendly_name || p}</option>`
    ).join('');

    // Restore MP
    const savedPlayer = localStorage.getItem('txtCast_lastPlayer');
    if (savedPlayer && players.includes(savedPlayer)) {
      playerSelect.value = savedPlayer;
    }

    // 2. Entries
    try {
      const entries = await this._hass.callWS({ type: 'config_entries/get' });
      const ourEntries = entries.filter(e => e.domain === domain);
      const configSelect = this.shadowRoot.getElementById('config-select');
      
      if (ourEntries.length > 0) {
        configSelect.innerHTML = ourEntries.map(e => 
          `<option value="${e.entry_id}">${e.title || 'Default Config'}</option>`
        ).join('');
        
        // Restore config from localStorage
        const savedConfig = localStorage.getItem('txtCast_lastConfig');
        let activeConfigId = ourEntries[0].entry_id;

        if (savedConfig && ourEntries.some(e => e.entry_id === savedConfig)) {
          activeConfigId = savedConfig;
          configSelect.value = activeConfigId;
        }
        
        // Load voices
        this.updateVoiceDropdown(activeConfigId);
      } else {
        configSelect.innerHTML = `<option value="">No configurations found</option>`;
      }
    } catch (e) {
      console.error("Failed to load config entries:", e);
    }

    // 3. txt
    try {
      const fileSelect = this.shadowRoot.getElementById('file-select');
      fileSelect.innerHTML = '<option>Searching media...</option>';
      const files = await this.fetchAllMedia(`media-source://${domain}`);
      
      if (files.length > 0) {
        fileSelect.innerHTML = files.map(f => 
          `<option value="${f.media_content_id}">${f.title}</option>`
        ).join('');
      } else {
        fileSelect.innerHTML = '<option value="">No .txt files found</option>';
      }
    } catch (e) {
      fileSelect.innerHTML = '<option value="">Error loading media</option>';
    }
  }

  async fetchAllMedia(contentId) {
    let allFiles = [];
    try {
      const result = await this._hass.callWS({
        type: 'media_source/browse_media',
        media_content_id: contentId
      });
      
      if (result && result.children) {
        for (const child of result.children) {
          if (child.can_expand) {
            const subFiles = await this.fetchAllMedia(child.media_content_id);
            allFiles.push(...subFiles);
          } else if (child.can_play) {
            allFiles.push(child);
          }
        }
      }
    } catch (e) {

    }
    return allFiles;
  }

  async updateVoiceDropdown(configEntryId) {
    const voiceSelect = this.shadowRoot.getElementById('voice-select');
    voiceSelect.disabled = true;
    
    if (!configEntryId) return;

    if (!this._voicesByConfig[configEntryId]) {
      try {
        const response = await this._hass.callWS({
          type: `${this.config.domain}/get_voices`,
          config_entry_id: configEntryId
        });

        let voices = [];
        if (response && response.voices) {
          Object.values(response.voices).forEach(langVoices => {
            voices.push(...langVoices);
          });
        }
        this._voicesByConfig[configEntryId] = voices;
      } catch (e) {
        console.warn("Failed to fetch custom voices:", e);
        this._voicesByConfig[configEntryId] = [];
      }
    }

    const voices = this._voicesByConfig[configEntryId] || [];
    let html = '<option value="">Default Voice</option>';
    voices.forEach(v => {
      html += `<option value="${v.voice_id}">${v.name}</option>`;
    });
    
    voiceSelect.innerHTML = html;
    voiceSelect.disabled = false;

    // restore the voice: first from localStorage, if not, from what was selected before loading
    const savedVoice = localStorage.getItem('txtCast_lastVoice');
    if (savedVoice && voices.some(v => v.voice_id === savedVoice)) {
      voiceSelect.value = savedVoice;
    }
  }

  handlePlay() {
    const configEntry = this.shadowRoot.getElementById('config-select').value;
    const entityId = this.shadowRoot.getElementById('player-select').value;
    const filePath = this.shadowRoot.getElementById('file-select').value;
    const voice = this.shadowRoot.getElementById('voice-select').value;
    const blockIndex = this.shadowRoot.getElementById('block-input').value;
    const timer = this.shadowRoot.getElementById('timer-input').value;

    if (!configEntry || !entityId || !filePath) {
      this.fireEvent('hass-notification', { message: 'Please select a player, config, and file.' });
      return;
    }

    const serviceData = {
      config_entry: configEntry,
      entity_id: entityId,
      file_path: filePath
    };

    if (voice) serviceData.voice = voice;
    if (blockIndex) serviceData.block_index = parseInt(blockIndex, 10);
    if (timer) serviceData.timer = parseInt(timer, 10);

    this._hass.callService(this.config.domain, "play", serviceData);
  }

  handleResume() {
    const entityId = this.shadowRoot.getElementById('player-select').value;
    const timer = this.shadowRoot.getElementById('timer-input').value;

    if (!entityId) {
      this.fireEvent('hass-notification', { message: 'Please select a Target Player.' });
      return;
    }

    const serviceData = { entity_id: entityId };
    if (timer) serviceData.timer = parseInt(timer, 10);

    this._hass.callService(this.config.domain, "resume", serviceData);
  }

  fireEvent(type, detail) {
    const event = new CustomEvent(type, { bubbles: true, composed: true, detail });
    this.dispatchEvent(event);
  }
}

if (!customElements.get('txt-cast-card')) {
  customElements.define('txt-cast-card', TxtCastCard);

  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "txt-cast-card",
    name: "TXT Cast Card",
    description: "UI for launching txt file synthesis"
  });
}
