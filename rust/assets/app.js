const App = {
    state: {
        recording: false,
        merging: false,
        elapsed: 0,
        source: 'desktop',
        webcam: false,
        error: null,
    },
    settings: {},
    devices: { audio: [], webcam: [] },
    files: [],
    _timerInterval: null,
    _timerStart: 0,
    _deleteTarget: null,
    _settingsDebounce: null,
    _sse: null,

    // ==================== INIT ====================

    init() {
        this.initNavigation();
        this.initRecordControls();
        this.initSettingsControls();
        this.initDeleteModal();
        this.loadStatus();
        this.loadSettings();
        this.loadDevices();
        this.loadNextFilename();
        this.initSSE();
    },

    // ==================== SSE ====================

    initSSE() {
        if (this._sse) this._sse.close();
        this._sse = new EventSource('/api/events');
        this._sse.onmessage = (e) => {
            try {
                const data = JSON.parse(e.data);
                this.handleStateUpdate(data);
            } catch (err) { /* ignore parse errors */ }
        };
        this._sse.onerror = () => {
            this._sse.close();
            setTimeout(() => this.initSSE(), 3000);
        };
    },

    handleStateUpdate(data) {
        const wasRecording = this.state.recording;
        const wasMerging = this.state.merging;

        this.state.recording = data.recording;
        this.state.merging = data.merging;
        this.state.error = data.error;

        if (data.recording && data.elapsed != null) {
            this.state.elapsed = data.elapsed;
        }

        this.updateRecordUI();

        // When merging finishes, refresh files
        if (wasMerging && !data.merging && !data.recording) {
            this.loadFiles();
            this.loadNextFilename();
        }
    },

    // ==================== NAVIGATION ====================

    initNavigation() {
        document.querySelectorAll('.nav-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                this.showPanel(btn.dataset.panel);
            });
        });
    },

    showPanel(name) {
        document.querySelectorAll('.panel').forEach(el => {
            el.classList.remove('active');
        });
        const panel = document.getElementById('panel-' + name);
        if (panel) panel.classList.add('active');

        document.querySelectorAll('.nav-btn').forEach(btn => {
            if (btn.dataset.panel === name) {
                btn.classList.remove('text-slate-500');
                btn.classList.add('text-blue-400');
            } else {
                btn.classList.remove('text-blue-400');
                btn.classList.add('text-slate-500');
            }
        });

        if (name === 'files') this.loadFiles();
    },

    // ==================== RECORD CONTROLS ====================

    initRecordControls() {
        const btn = document.getElementById('btn-record');
        btn.addEventListener('click', () => {
            if (this.state.recording) {
                this.stopRecording();
            } else if (!this.state.merging) {
                this.startRecording();
            }
        });

        // Source selector
        document.getElementById('src-desktop').addEventListener('click', () => this.setSource('desktop'));
        document.getElementById('src-window').addEventListener('click', () => this.setSource('title'));

        // Webcam toggle
        const wcToggle = document.getElementById('toggle-webcam');
        wcToggle.addEventListener('click', () => {
            this.state.webcam = !this.state.webcam;
            wcToggle.classList.toggle('active', this.state.webcam);
            wcToggle.setAttribute('aria-checked', this.state.webcam);
            document.getElementById('webcam-device-group').classList.toggle('hidden', !this.state.webcam);
        });
        wcToggle.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); wcToggle.click(); }
        });
    },

    setSource(source) {
        this.state.source = source;
        const dBtn = document.getElementById('src-desktop');
        const wBtn = document.getElementById('src-window');
        const titleGroup = document.getElementById('window-title-group');
        const activeClasses = ['bg-blue-500/20', 'text-blue-400', 'border-blue-500/30'];
        const inactiveClasses = ['bg-slate-800', 'text-slate-400', 'border-slate-700'];

        const isDesktop = source === 'desktop';
        activeClasses.forEach(c => { dBtn.classList.toggle(c, isDesktop); wBtn.classList.toggle(c, !isDesktop); });
        inactiveClasses.forEach(c => { dBtn.classList.toggle(c, !isDesktop); wBtn.classList.toggle(c, isDesktop); });
        titleGroup.classList.toggle('hidden', isDesktop);
        dBtn.setAttribute('aria-pressed', isDesktop);
        wBtn.setAttribute('aria-pressed', !isDesktop);
    },

    async startRecording() {
        const body = {
            filename: document.getElementById('filename').value || null,
            source: this.state.source,
            window_title: document.getElementById('window-title').value,
            webcam: this.state.webcam,
            webcam_device: document.getElementById('webcam-device').value,
        };
        try {
            const res = await fetch('/api/record/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            const data = await res.json();
            if (!data.ok) {
                this.showError(data.error || 'Failed to start recording');
            }
        } catch (err) {
            this.showError('Network error: ' + err.message);
        }
    },

    async stopRecording() {
        try {
            await fetch('/api/record/stop', { method: 'POST' });
        } catch (err) {
            this.showError('Network error: ' + err.message);
        }
    },

    updateRecordUI() {
        const btn = document.getElementById('btn-record');
        const iconRecord = document.getElementById('icon-record');
        const iconStop = document.getElementById('icon-stop');
        const statusDot = document.getElementById('status-dot');
        const statusText = document.getElementById('status-text');
        const mergingIndicator = document.getElementById('merging-indicator');
        const errorDisplay = document.getElementById('error-display');

        // Disable controls during recording/merging
        const controls = ['filename', 'src-desktop', 'src-window', 'window-title'];
        controls.forEach(id => {
            const el = document.getElementById(id);
            if (el) {
                if (this.state.recording || this.state.merging) {
                    el.setAttribute('disabled', '');
                    el.style.opacity = '0.5';
                    el.style.pointerEvents = 'none';
                } else {
                    el.removeAttribute('disabled');
                    el.style.opacity = '';
                    el.style.pointerEvents = '';
                }
            }
        });

        if (this.state.recording) {
            btn.classList.add('recording-pulse');
            btn.classList.remove('bg-red-500', 'hover:bg-red-600', 'shadow-red-500/25');
            btn.classList.add('bg-red-600', 'hover:bg-red-700', 'shadow-red-600/30');
            iconRecord.classList.add('hidden');
            iconStop.classList.remove('hidden');
            statusDot.className = 'w-2 h-2 rounded-full bg-red-500 animate-pulse';
            statusText.textContent = 'Recording';
            statusText.className = 'text-red-400';
            mergingIndicator.classList.add('hidden');

            // Update timer
            this.updateTimer(this.state.elapsed);
            if (!this._timerInterval) {
                this._timerStart = Date.now() - (this.state.elapsed * 1000);
                this._timerInterval = setInterval(() => {
                    const elapsed = (Date.now() - this._timerStart) / 1000;
                    this.updateTimer(elapsed);
                }, 100);
            }
        } else {
            btn.classList.remove('recording-pulse', 'bg-red-600', 'hover:bg-red-700', 'shadow-red-600/30');
            btn.classList.add('bg-red-500', 'hover:bg-red-600', 'shadow-red-500/25');
            iconRecord.classList.remove('hidden');
            iconStop.classList.add('hidden');

            if (this._timerInterval) {
                clearInterval(this._timerInterval);
                this._timerInterval = null;
            }

            if (this.state.merging) {
                statusDot.className = 'w-2 h-2 rounded-full bg-blue-500 animate-pulse';
                statusText.textContent = 'Converting';
                statusText.className = 'text-blue-400';
                mergingIndicator.classList.remove('hidden');
                btn.setAttribute('disabled', '');
                btn.style.opacity = '0.5';
            } else {
                statusDot.className = 'w-2 h-2 rounded-full bg-slate-500';
                statusText.textContent = 'Idle';
                statusText.className = '';
                mergingIndicator.classList.add('hidden');
                btn.removeAttribute('disabled');
                btn.style.opacity = '';

                if (!this.state.recording) {
                    this.updateTimer(0);
                }
            }
        }

        // Error
        if (this.state.error) {
            this.showError(this.state.error);
        } else {
            errorDisplay.classList.add('hidden');
        }
    },

    updateTimer(seconds) {
        const h = Math.floor(seconds / 3600);
        const m = Math.floor((seconds % 3600) / 60);
        const s = Math.floor(seconds % 60);
        document.getElementById('timer').textContent =
            String(h).padStart(2, '0') + ':' +
            String(m).padStart(2, '0') + ':' +
            String(s).padStart(2, '0');
    },

    showError(msg) {
        const el = document.getElementById('error-display');
        document.getElementById('error-text').textContent = msg;
        el.classList.remove('hidden');
        setTimeout(() => el.classList.add('hidden'), 8000);
    },

    // ==================== FILES ====================

    async loadFiles() {
        const list = document.getElementById('files-list');
        const empty = document.getElementById('files-empty');

        try {
            const res = await fetch('/api/files');
            const data = await res.json();
            this.files = data.files || [];

            if (this.files.length === 0) {
                list.innerHTML = '';
                empty.classList.remove('hidden');
                return;
            }

            empty.classList.add('hidden');
            list.innerHTML = this.files.map(f => `
                <div class="file-item flex items-center gap-3 bg-slate-800 rounded-xl p-4" data-name="${this.escapeHtml(f.name)}">
                    <div class="flex-shrink-0">
                        <svg class="w-10 h-10 text-slate-500" fill="none" stroke="currentColor" stroke-width="1.5" viewBox="0 0 24 24">
                            <path d="M15.75 10.5l4.72-4.72a.75.75 0 011.28.53v11.38a.75.75 0 01-1.28.53l-4.72-4.72M4.5 18.75h9a2.25 2.25 0 002.25-2.25v-9a2.25 2.25 0 00-2.25-2.25h-9A2.25 2.25 0 002.25 7.5v9a2.25 2.25 0 002.25 2.25z"/>
                        </svg>
                    </div>
                    <div class="flex-1 min-w-0">
                        <p class="text-sm font-medium text-slate-100 truncate">${this.escapeHtml(f.name)}</p>
                        <p class="text-xs text-slate-400">${this.escapeHtml(f.size_human)} &middot; ${this.formatDate(f.date)}</p>
                    </div>
                    <div class="flex gap-1.5 flex-shrink-0">
                        <a href="/api/files/${encodeURIComponent(f.name)}/download"
                            class="p-2.5 rounded-lg bg-slate-700 hover:bg-blue-500/20 hover:text-blue-400
                                   cursor-pointer transition-colors duration-200 min-w-[44px] min-h-[44px]
                                   flex items-center justify-center"
                            aria-label="Download ${this.escapeHtml(f.name)}">
                            <svg class="w-5 h-5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                                <path d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5M16.5 12L12 16.5m0 0L7.5 12m4.5 4.5V3"/>
                            </svg>
                        </a>
                        <button class="btn-delete p-2.5 rounded-lg bg-slate-700 hover:bg-red-500/20 hover:text-red-400
                                       cursor-pointer transition-colors duration-200 min-w-[44px] min-h-[44px]
                                       flex items-center justify-center"
                                data-name="${this.escapeHtml(f.name)}"
                                aria-label="Delete ${this.escapeHtml(f.name)}">
                            <svg class="w-5 h-5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                                <path d="M14.74 9l-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 01-2.244 2.077H8.084a2.25 2.25 0 01-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 00-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 013.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 00-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 00-7.5 0"/>
                            </svg>
                        </button>
                    </div>
                </div>
            `).join('');

            // Attach delete handlers
            list.querySelectorAll('.btn-delete').forEach(btn => {
                btn.addEventListener('click', () => this.confirmDelete(btn.dataset.name));
            });
        } catch (err) {
            list.innerHTML = '<p class="text-red-400 text-sm py-4 text-center">Failed to load files</p>';
        }
    },

    formatDate(isoStr) {
        try {
            const d = new Date(isoStr);
            return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' }) +
                ' ' + d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
        } catch {
            return isoStr;
        }
    },

    escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    },

    // ==================== DELETE MODAL ====================

    initDeleteModal() {
        document.getElementById('delete-cancel').addEventListener('click', () => this.closeDeleteModal());
        document.getElementById('delete-confirm').addEventListener('click', () => this.executeDelete());
        document.getElementById('delete-modal').addEventListener('click', (e) => {
            if (e.target === e.currentTarget) this.closeDeleteModal();
        });
    },

    confirmDelete(name) {
        this._deleteTarget = name;
        document.getElementById('delete-modal-name').textContent = name;
        const modal = document.getElementById('delete-modal');
        modal.classList.remove('hidden');
        modal.classList.add('flex');
    },

    closeDeleteModal() {
        const modal = document.getElementById('delete-modal');
        modal.classList.add('hidden');
        modal.classList.remove('flex');
        this._deleteTarget = null;
    },

    async executeDelete() {
        const name = this._deleteTarget;
        if (!name) return;
        this.closeDeleteModal();

        const item = document.querySelector(`.file-item[data-name="${CSS.escape(name)}"]`);
        if (item) item.classList.add('removing');

        try {
            await fetch('/api/files/' + encodeURIComponent(name), { method: 'DELETE' });
            setTimeout(() => this.loadFiles(), 250);
        } catch (err) {
            this.showError('Failed to delete file');
            this.loadFiles();
        }
    },

    // ==================== SETTINGS ====================

    initSettingsControls() {
        // FPS slider
        const fpsSlider = document.getElementById('fps-slider');
        const fpsValue = document.getElementById('fps-value');
        fpsSlider.addEventListener('input', () => {
            fpsValue.textContent = fpsSlider.value + ' fps';
            this.debounceSaveSettings();
        });

        // Encoder toggle
        document.getElementById('enc-cpu').addEventListener('click', () => {
            this.setEncoder('mpeg4');
            this.debounceSaveSettings();
        });
        document.getElementById('enc-nvenc').addEventListener('click', () => {
            this.setEncoder('h264_nvenc');
            this.debounceSaveSettings();
        });

        // Draw mouse toggle
        const mouseToggle = document.getElementById('toggle-mouse');
        mouseToggle.addEventListener('click', () => {
            mouseToggle.classList.toggle('active');
            const active = mouseToggle.classList.contains('active');
            mouseToggle.setAttribute('aria-checked', active);
            this.debounceSaveSettings();
        });
        mouseToggle.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); mouseToggle.click(); }
        });

        // Audio mode
        document.getElementById('audio-default').addEventListener('click', () => {
            this.setAudioMode('default');
            this.debounceSaveSettings();
        });
        document.getElementById('audio-select').addEventListener('click', () => {
            this.setAudioMode('selected');
            this.debounceSaveSettings();
        });
    },

    setEncoder(encoder) {
        const cpuBtn = document.getElementById('enc-cpu');
        const nvBtn = document.getElementById('enc-nvenc');
        const activeClass = 'bg-blue-500/20 text-blue-400 border-blue-500/30';
        const inactiveClass = 'bg-slate-700 text-slate-400 border-slate-600';

        [cpuBtn, nvBtn].forEach(btn => {
            const isActive = btn.dataset.encoder === encoder;
            activeClass.split(' ').forEach(c => btn.classList.toggle(c, isActive));
            inactiveClass.split(' ').forEach(c => btn.classList.toggle(c, !isActive));
        });
    },

    setAudioMode(mode) {
        const defBtn = document.getElementById('audio-default');
        const selBtn = document.getElementById('audio-select');
        const devGroup = document.getElementById('audio-devices-group');
        const activeClass = 'bg-blue-500/20 text-blue-400 border-blue-500/30';
        const inactiveClass = 'bg-slate-700 text-slate-400 border-slate-600';

        [defBtn, selBtn].forEach(btn => {
            const isActive = btn.dataset.audiomode === mode;
            activeClass.split(' ').forEach(c => btn.classList.toggle(c, isActive));
            inactiveClass.split(' ').forEach(c => btn.classList.toggle(c, !isActive));
        });

        devGroup.classList.toggle('hidden', mode !== 'selected');
    },

    debounceSaveSettings() {
        clearTimeout(this._settingsDebounce);
        this._settingsDebounce = setTimeout(() => this.saveSettings(), 500);
    },

    async saveSettings() {
        const settings = {
            fps: parseInt(document.getElementById('fps-slider').value),
            encoder: document.querySelector('#enc-cpu.bg-blue-500\\/20') ? 'mpeg4' : 'h264_nvenc',
            draw_mouse: document.getElementById('toggle-mouse').classList.contains('active'),
            audio_mode: document.querySelector('#audio-default.bg-blue-500\\/20') ? 'default' : 'selected',
            audio_devices: this.getSelectedAudioDevices(),
        };
        try {
            await fetch('/api/settings', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(settings),
            });
        } catch (err) { /* silently fail */ }
    },

    getSelectedAudioDevices() {
        const checked = document.querySelectorAll('#audio-devices-list input[type="checkbox"]:checked');
        return Array.from(checked).map(cb => cb.value);
    },

    async loadSettings() {
        try {
            const res = await fetch('/api/settings');
            this.settings = await res.json();

            document.getElementById('fps-slider').value = this.settings.fps || 30;
            document.getElementById('fps-value').textContent = (this.settings.fps || 30) + ' fps';
            this.setEncoder(this.settings.encoder || 'mpeg4');

            const mouseToggle = document.getElementById('toggle-mouse');
            mouseToggle.classList.toggle('active', this.settings.draw_mouse !== false);
            mouseToggle.setAttribute('aria-checked', this.settings.draw_mouse !== false);

            this.setAudioMode(this.settings.audio_mode || 'default');
        } catch (err) { /* use defaults */ }
    },

    async loadDevices() {
        try {
            const res = await fetch('/api/devices');
            this.devices = await res.json();

            // Webcam devices
            const wcSelect = document.getElementById('webcam-device');
            if (this.devices.webcam && this.devices.webcam.length > 0) {
                wcSelect.innerHTML = this.devices.webcam.map(name =>
                    `<option value="${this.escapeHtml(name)}">${this.escapeHtml(name)}</option>`
                ).join('');
            } else {
                wcSelect.innerHTML = '<option value="">No devices found</option>';
            }

            // Audio devices
            const audList = document.getElementById('audio-devices-list');
            if (this.devices.audio && this.devices.audio.length > 0) {
                const selectedDevices = this.settings.audio_devices || [];
                audList.innerHTML = this.devices.audio.map(dev => {
                    const checked = selectedDevices.includes(dev.name) ? 'checked' : '';
                    const typeLabel = dev.device_type === 'output'
                        ? '<span class="text-xs px-1.5 py-0.5 rounded bg-blue-500/20 text-blue-400">扬声器</span>'
                        : '<span class="text-xs px-1.5 py-0.5 rounded bg-green-500/20 text-green-400">麦克风</span>';
                    return `
                        <label class="flex items-center gap-3 py-2 px-2 rounded-lg hover:bg-slate-700 cursor-pointer
                                      transition-colors duration-200 min-h-[44px]">
                            <input type="checkbox" value="${this.escapeHtml(dev.name)}" ${checked}
                                class="w-4 h-4 rounded border-slate-600 bg-slate-700 text-blue-500
                                       focus:ring-blue-500/50 cursor-pointer"
                                onchange="App.debounceSaveSettings()">
                            <div class="min-w-0 flex-1">
                                <p class="text-sm text-slate-200 truncate">${this.escapeHtml(dev.name)} ${typeLabel}</p>
                            </div>
                        </label>
                    `;
                }).join('');
            } else {
                audList.innerHTML = '<p class="text-slate-500 text-sm py-2">No audio devices found</p>';
            }
        } catch (err) { /* silently fail */ }
    },

    // ==================== MISC ====================

    async loadStatus() {
        try {
            const res = await fetch('/api/status');
            const data = await res.json();
            this.handleStateUpdate(data);
        } catch (err) { /* will reconnect via SSE */ }
    },

    async loadNextFilename() {
        try {
            const res = await fetch('/api/filename/next');
            const data = await res.json();
            const input = document.getElementById('filename');
            if (!this.state.recording && !this.state.merging) {
                input.value = data.filename || '';
            }
        } catch (err) { /* silently fail */ }
    },
};

document.addEventListener('DOMContentLoaded', () => App.init());
