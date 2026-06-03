(() => {
  const STATE = {
    ws: null,
    sessionId: null,
    currentMood: 'neutral',
    connected: false,
    reconnecting: false,
    reconnectTimer: null,
    messageQueue: [],
    pendingMessage: null,
  };

  const MOOD = {
    emojis: { neutral: '😐', happy: '😊', annoyed: '😤', curious: '🤔', excited: '🌟' },
    labels: { neutral: 'neutral', happy: 'happy', annoyed: 'annoyed', curious: 'curious', excited: 'excited' },
  };

  const DOM = {};
  const CACHE = {};

  function init() {
    DOM.messages = document.getElementById('messages');
    DOM.input = document.getElementById('input');
    DOM.btnSend = document.getElementById('btn-send');
    DOM.btnVoice = document.getElementById('btn-voice');
    DOM.btnChat = document.getElementById('btn-chat');
    DOM.btnHistory = document.getElementById('btn-history');
    DOM.btnMemory = document.getElementById('btn-memory');
    DOM.btnSettings = document.getElementById('btn-settings');
    DOM.btnReconnect = document.getElementById('btn-reconnect');
    DOM.btnNewSession = document.getElementById('btn-new-session');
    DOM.drawer = document.getElementById('drawer');
    DOM.drawerOverlay = document.getElementById('drawer-overlay');
    DOM.drawerClose = document.getElementById('drawer-close');
    DOM.moodBadge = document.getElementById('mood-badge');
    DOM.moodEmoji = document.getElementById('mood-emoji');
    DOM.moodText = document.getElementById('mood-text');
    DOM.avatarRing = document.getElementById('avatar-ring');
    DOM.connDot = document.getElementById('conn-dot');
    DOM.connText = document.getElementById('conn-text');
    DOM.sessionLabel = document.getElementById('session-label');
    DOM.footerInfo = document.getElementById('footer-info');
    DOM.cfgServer = document.getElementById('cfg-server');
    DOM.cfgSession = document.getElementById('cfg-session');
    DOM.statusDot = document.getElementById('status-dot');

    DOM.btnSend.addEventListener('click', sendMessage);
    DOM.input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
    });
    DOM.input.addEventListener('input', autoResize);

    DOM.btnSettings.addEventListener('click', () => toggleDrawer(true));
    DOM.drawerClose.addEventListener('click', () => toggleDrawer(false));
    DOM.drawerOverlay.addEventListener('click', () => toggleDrawer(false));
    DOM.btnReconnect.addEventListener('click', () => connect());
    DOM.btnNewSession.addEventListener('click', newSession);
    DOM.btnHistory.addEventListener('click', fetchHistory);
    DOM.btnMemory.addEventListener('click', fetchMemory);
    DOM.btnVoice.addEventListener('click', toggleVoiceMode);

    DOM.cfgSession.addEventListener('change', (e) => {
      if (e.target.value) switchSession(e.target.value);
    });

    document.getElementById('memory-link').addEventListener('click', (e) => {
      e.preventDefault(); fetchMemory();
    });

    setStatus('connecting');
    connect();

    tryInitLive2D();
  }

  // ─── Connection ───

  function connect() {
    if (STATE.ws) {
      STATE.ws.onclose = null;
      STATE.ws.close();
    }
    if (STATE.reconnectTimer) { clearTimeout(STATE.reconnectTimer); STATE.reconnectTimer = null; }

    const url = DOM.cfgServer.value.trim() || 'ws://localhost:8765/ws';
    const wsUrl = url.endsWith('/ws') ? url : url.replace(/\/?$/, '/ws');

    setStatus('connecting');
    addSystemMsg('Connecting...');

    try {
      STATE.ws = new WebSocket(wsUrl);
    } catch (e) {
      setStatus('offline');
      addSystemMsg(`Connection failed: ${e.message}`);
      scheduleReconnect();
      return;
    }

    STATE.ws.onopen = () => {
      STATE.connected = true;
      STATE.reconnecting = false;
      setStatus('online');
      addSystemMsg('Connected');
      subscribeMood();
      loadSessionList();
    };

    STATE.ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        handleMessage(msg);
      } catch (e) {
        console.error('Parse error:', e);
      }
    };

    STATE.ws.onclose = () => {
      STATE.connected = false;
      STATE.ws = null;
      setStatus('offline');
      if (!STATE.reconnecting) {
        addSystemMsg('Disconnected');
        scheduleReconnect();
      }
    };

    STATE.ws.onerror = () => {
      // onclose will fire after this
    };
  }

  function scheduleReconnect() {
    if (STATE.reconnecting) return;
    STATE.reconnecting = true;
    STATE.reconnectTimer = setTimeout(() => {
      STATE.reconnecting = false;
      connect();
    }, 5000);
  }

  function subscribeMood() {
    send({ type: 'command', command: 'subscribe_mood' });
  }

  // ─── Sending ───

  function send(obj) {
    if (STATE.ws && STATE.ws.readyState === WebSocket.OPEN) {
      STATE.ws.send(JSON.stringify(obj));
    }
  }

  function sendMessage() {
    const text = DOM.input.value.trim();
    if (!text) return;
    DOM.input.value = '';
    autoResize();

    if (!STATE.connected) {
      addSystemMsg('Not connected. Waiting for reconnect...');
      return;
    }

    addMessage('user', text);
    showTyping();

    const msg = { type: 'message', data: text, session_id: STATE.sessionId };
    STATE.pendingMessage = text;
    send(msg);
  }

  // ─── Receiving ───

  function handleMessage(msg) {
    hideTyping();
    const type = msg.type;

    if (type === 'done') {
      const data = msg.data;
      STATE.sessionId = data.session_id;
      STATE.pendingMessage = null;
      DOM.sessionLabel.textContent = data.session_id.slice(0, 8) + '...';
      addMessage('kuro', data.text);
      setMood(data.mood, data.emotion);
      loadSessionList();

    } else if (type === 'mood_update') {
      setMood(msg.data.mood, msg.data.emotion);

    } else if (type === 'command_result') {
      if (msg.command === 'history' || msg.command === 'memory') {
        showCommandResult(msg.command, msg.data);
      } else if (msg.command === 'subscribe_mood') {
        // subscribed
      } else if (msg.command === 'sessions') {
        populateSessionList(msg.data);
      } else if (msg.command === 'new_session') {
        addSystemMsg(msg.data);
      }

    } else if (type === 'token') {
      // Streaming token — append to current message
      appendKuroToken(msg.data);

    } else if (type === 'error') {
      STATE.pendingMessage = null;
      addSystemMsg(`Error: ${msg.data}`);
    }
  }

  // ─── Chat UI ───

  function addMessage(role, text) {
    const div = document.createElement('div');
    div.className = `msg ${role}`;
    const sender = role === 'user' ? 'You' : 'Kuro';
    const time = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    div.innerHTML = `<div class="sender">${sender}</div><div class="text">${escapeHtml(text)}</div><div class="time">${time}</div>`;
    DOM.messages.appendChild(div);
    scrollBottom();
  }

  function appendKuroToken(token) {
    let last = DOM.messages.lastElementChild;
    if (!last || !last.classList.contains('kuro') || last.classList.contains('typing')) {
      const div = document.createElement('div');
      div.className = 'msg kuro';
      div.innerHTML = '<div class="sender">Kuro</div><div class="text"></div>';
      DOM.messages.appendChild(div);
      last = div;
    }
    const textEl = last.querySelector('.text');
    textEl.textContent += token;
    scrollBottom();
  }

  function showTyping() {
    const existing = DOM.messages.querySelector('.typing');
    if (existing) return;
    const div = document.createElement('div');
    div.className = 'msg typing';
    div.innerHTML = '<div class="typing-dots"><span></span><span></span><span></span></div>';
    DOM.messages.appendChild(div);
    scrollBottom();
  }

  function hideTyping() {
    const el = DOM.messages.querySelector('.typing');
    if (el) el.remove();
  }

  function addSystemMsg(text) {
    const div = document.createElement('div');
    div.style.cssText = 'text-align:center;font-size:12px;color:var(--text-muted);padding:4px 0;';
    div.textContent = text;
    DOM.messages.appendChild(div);
    scrollBottom();
  }

  function showCommandResult(command, data) {
    const title = command === 'history' ? 'Conversation History' : 'User Memory';
    const div = document.createElement('div');
    div.className = 'msg kuro';
    div.style.maxWidth = '100%';
    div.innerHTML = `<div class="sender">${title}</div><pre style="font-size:12px;white-space:pre-wrap;font-family:monospace;max-height:300px;overflow-y:auto;">${escapeHtml(data)}</pre>`;
    DOM.messages.appendChild(div);
    scrollBottom();
  }

  function scrollBottom() {
    DOM.messages.scrollTop = DOM.messages.scrollHeight;
  }

  // ─── Mood ───

  function setMood(mood, emotion = 0.5) {
    STATE.currentMood = mood;
    const emoji = MOOD.emojis[mood] || '😐';
    const label = MOOD.labels[mood] || 'neutral';
    DOM.moodEmoji.textContent = emoji;
    DOM.moodText.textContent = label;
    DOM.moodBadge.textContent = `${emoji} ${label}`;
    DOM.avatarRing.dataset.mood = mood;
    updateLive2DMood(mood, emotion);
  }

  // ─── Commands ───

  function fetchHistory() {
    send({ type: 'command', command: 'history', session_id: STATE.sessionId });
  }

  function fetchMemory() {
    send({ type: 'command', command: 'memory' });
  }

  function newSession() {
    send({ type: 'command', command: 'new_session' });
  }

  function switchSession(id) {
    STATE.sessionId = id;
    DOM.sessionLabel.textContent = id.slice(0, 8) + '...';
    addSystemMsg(`Switched to session ${id.slice(0, 8)}...`);
    DOM.cfgSession.value = id;
    fetchHistory();
  }

  function loadSessionList() {
    send({ type: 'command', command: 'sessions' });
  }

  // ─── Voice mode ───

  let voiceActive = false;
  let mediaRecorder = null;
  let audioChunks = [];

  function toggleVoiceMode() {
    if (voiceActive) {
      stopVoice();
    } else {
      startVoice();
    }
  }

  function startVoice() {
    if (!navigator.mediaDevices) {
      addSystemMsg('Voice input not supported in this browser');
      return;
    }
    DOM.btnVoice.style.background = 'var(--error)';
    DOM.btnVoice.style.color = '#fff';
    voiceActive = true;
    addSystemMsg('Voice mode: press and hold Space to record');
  }

  function stopVoice() {
    DOM.btnVoice.style.background = '';
    DOM.btnVoice.style.color = '';
    voiceActive = false;
    if (mediaRecorder) {
      mediaRecorder.stop();
      mediaRecorder = null;
    }
  }

  document.addEventListener('keydown', (e) => {
    if (e.key === ' ' && voiceActive && !mediaRecorder && e.target.tagName !== 'TEXTAREA' && e.target.tagName !== 'INPUT') {
      e.preventDefault();
      startRecording();
    }
  });

  document.addEventListener('keyup', (e) => {
    if (e.key === ' ' && voiceActive && mediaRecorder) {
      stopRecording();
    }
  });

  async function startRecording() {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      audioChunks = [];
      mediaRecorder = new MediaRecorder(stream);
      mediaRecorder.ondataavailable = (e) => audioChunks.push(e.data);
      mediaRecorder.start();
      addSystemMsg('Recording... (release space)');
    } catch (e) {
      addSystemMsg(`Mic error: ${e.message}`);
    }
  }

  function stopRecording() {
    if (!mediaRecorder) return;
    mediaRecorder.onstop = async () => {
      addSystemMsg('Processing voice...');
      const blob = new Blob(audioChunks, { type: 'audio/webm' });
      mediaRecorder.stream.getTracks().forEach(t => t.stop());
      mediaRecorder = null;
      // Send to ASR endpoint (future) — for now just note it
      addSystemMsg('Voice processing not yet wired to ASR — type instead for now');
    };
    mediaRecorder.stop();
  }

  function populateSessionList(data) {
    const select = DOM.cfgSession;
    select.innerHTML = '<option value="">New session</option>';
    if (Array.isArray(data)) {
      data.forEach(s => {
        const opt = document.createElement('option');
        opt.value = s.session_id;
        opt.textContent = `${s.session_id.slice(0, 8)}... ${s.timestamp || ''} - ${s.preview || ''}`;
        if (STATE.sessionId && s.session_id === STATE.sessionId) opt.selected = true;
        select.appendChild(opt);
      });
    }
  }

  // ─── Status ───

  function setStatus(state) {
    const states = { online: ['online', 'connected', 'Connected'], connecting: ['connecting', 'Connecting...', 'Connecting...'], offline: ['offline', 'disconnected', 'Disconnected'] };
    const [dotClass, connText, footer] = states[state] || states.offline;
    DOM.connDot.className = `dot ${dotClass}`;
    DOM.connText.textContent = connText;
    DOM.footerInfo.textContent = footer;
    DOM.statusDot.className = `status-dot ${dotClass === 'online' ? '' : dotClass}`;
    DOM.btnSend.disabled = state !== 'online';
  }

  // ─── Drawer ───

  function toggleDrawer(open) {
    DOM.drawer.classList.toggle('open', open);
    DOM.drawerOverlay.classList.toggle('open', open);
  }

  // ─── Live2D ───

  let live2dApp = null;
  let live2dModel = null;

  async function tryInitLive2D() {
    // Check if model files exist by fetching model list
    try {
      const resp = await fetch('models/');
      if (!resp.ok) throw new Error('No models dir');
      // Models directory exists — try loading Live2D
      loadLive2DScripts();
    } catch {
      // No models — emoji avatar is the default, all good
      document.querySelector('.hint').style.display = 'block';
    }
  }

  function loadLive2DScripts() {
    const scripts = [
      'https://cdn.jsdelivr.net/npm/pixi.js@7/dist/pixi.min.js',
      'https://cdn.jsdelivr.net/npm/pixi-live2d-display@1.0.0/dist/index.min.js',
    ];
    let loaded = 0;
    scripts.forEach(url => {
      const s = document.createElement('script');
      s.src = url;
      s.onload = () => {
        loaded++;
        if (loaded === scripts.length) initLive2DRenderer();
      };
      s.onerror = () => console.warn('Failed to load Live2D script:', url);
      document.head.appendChild(s);
    });
  }

  async function initLive2DRenderer() {
    try {
      const models = await listModelFiles();
      if (models.length === 0) return;
      const canvas = document.getElementById('live2d-canvas');
      canvas.classList.add('ready');
      document.getElementById('live2d-placeholder').style.display = 'none';

      const app = new PIXI.Application({
        view: canvas,
        width: 360, height: 400,
        transparent: true,
        resizeTo: document.getElementById('avatar-panel'),
      });

      const model = await PIXI.live2d.Live2DModel.from(models[0]);
      app.stage.addChild(model);
      model.scale.set(0.25);
      model.anchor.set(0.5, 0.5);
      model.position.set(app.screen.width / 2, app.screen.height / 2);

      live2dApp = app;
      live2dModel = model;
      document.querySelector('.hint').textContent = 'Live2D model loaded';
      setMood(STATE.currentMood);
    } catch (e) {
      console.warn('Live2D init failed:', e);
    }
  }

  async function listModelFiles() {
    try {
      const resp = await fetch('models/');
      const html = await resp.text();
      const parser = new DOMParser();
      const doc = parser.parseFromString(html, 'text/html');
      const links = doc.querySelectorAll('a[href$=".model3.json"]');
      const modelFiles = [];
      links.forEach(a => {
        const dir = a.getAttribute('href');
        if (dir && dir !== '../') modelFiles.push('models/' + dir);
      });
      // If no model3.json found, check for subdirectories
      if (modelFiles.length === 0) {
        const dirs = doc.querySelectorAll('a[href]');
        for (const a of dirs) {
          const dir = a.getAttribute('href');
          if (dir && dir.endsWith('/') && dir !== '../') {
            const modelResp = await fetch(`models/${dir}`);
            const modelHtml = await modelResp.text();
            if (modelHtml.includes('.model3.json') || modelHtml.includes('.model.json')) {
              modelFiles.push(`models/${dir}`);
              break;
            }
          }
        }
      }
      return modelFiles;
    } catch {
      return [];
    }
  }

  function updateLive2DMood(mood, emotion) {
    if (!live2dModel) return;
    const paramsMap = {
      happy: { ParamMouthOpenY: 0.3 + emotion * 0.3, ParamEyeLOpen: 0.8, ParamEyeROpen: 0.8, ParamAngleX: 0 },
      annoyed: { ParamMouthOpenY: 0.1, ParamEyeLOpen: 0.5, ParamEyeROpen: 0.5, ParamAngleX: -5, ParamAngleY: 0, ParamAngleZ: 0 },
      curious: { ParamMouthOpenY: 0.2, ParamEyeLOpen: 0.9, ParamEyeROpen: 0.9, ParamAngleX: 5, ParamAngleY: 0, ParamAngleZ: 0 },
      excited: { ParamMouthOpenY: 0.5, ParamEyeLOpen: 1.0, ParamEyeROpen: 1.0, ParamAngleX: 0, ParamAngleY: 5 },
      neutral: { ParamMouthOpenY: 0.15, ParamEyeLOpen: 0.7, ParamEyeROpen: 0.7, ParamAngleX: 0 },
    };
    const params = paramsMap[mood] || paramsMap.neutral;
    Object.entries(params).forEach(([key, val]) => {
      try { live2dModel.internalModel.coreModel.setParameterValueById(key, val); } catch {}
    });
  }

  // ─── Helpers ───

  function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }

  function autoResize() {
    DOM.input.style.height = 'auto';
    DOM.input.style.height = Math.min(DOM.input.scrollHeight, 120) + 'px';
  }

  // ─── Init ───

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
