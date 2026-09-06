/* Sentinel — application cliente (cockpit) : états, micro, lecture, chat,
   waveform, transcript, onglets, tiroirs, atelier, aperçu, veille. */

import { WSClient } from './ws.js';
import { Capture } from './audio-capture.js';
import { Player } from './audio-play.js';
import { Thread } from './chat.js';

const LABELS = {
  idle: 'en veille',
  listening: 'je t’écoute',
  transcribing: 'transcription…',
  thinking: 'réflexion…',
  speaking: 'sentinel parle',
  offline: 'hors ligne',
};

// Détection de fin de parole (niveaux RMS après réduction de bruit du navigateur)
const VOICE_THRESHOLD = 0.02;
const SILENCE_MS = 1300;   // silence après parole → envoi
const NO_VOICE_MS = 7000;  // aucun son détecté → abandon
const HARD_CAP_MS = 45000; // durée maximale d'une prise de parole

const els = {
  // en-tête
  tabCockpit: document.getElementById('tab-cockpit'),
  tabSettings: document.getElementById('tab-settings'),
  liaisonNova: document.getElementById('liaison-nova'),
  liaisonGithub: document.getElementById('liaison-github'),
  wakeBtn: document.getElementById('wake-btn'),
  proposalsBtn: document.getElementById('proposals-btn'),
  propCount: document.getElementById('prop-count'),
  santeBtn: document.getElementById('sante-btn'),
  historyBtn: document.getElementById('history-btn'),
  connNova: document.getElementById('conn-nova'),
  connLabel: document.getElementById('conn-label'),
  // vues
  viewCockpit: document.getElementById('view-cockpit'),
  viewSettings: document.getElementById('view-settings'),
  // conversation
  thread: document.getElementById('thread'),
  chatMeta: document.getElementById('chat-meta'),
  composer: document.getElementById('composer'),
  input: document.getElementById('text-input'),
  mic: document.getElementById('mic'),
  // voix / orbe
  orb: document.getElementById('orb'),
  orbTap: document.getElementById('orb-tap'),
  orbMic: document.getElementById('orb-mic'),
  interrupt: document.getElementById('btn-interrupt'),
  stateLabel: document.getElementById('state-label'),
  waveform: document.getElementById('waveform'),
  transcript: document.getElementById('transcript'),
  quickChips: document.getElementById('quick-chips'),
  // atelier
  atelierStatus: document.getElementById('atelier-status'),
  atelierTasks: document.getElementById('atelier-tasks'),
  atelierToolbar: document.getElementById('atelier-toolbar'),
  atelierBranch: document.getElementById('atelier-branch'),
  atelierDiffBtn: document.getElementById('atelier-diff-btn'),
  atelierLog: document.getElementById('atelier-log'),
  atelierEmpty: document.getElementById('atelier-empty'),
  tasksCount: document.getElementById('tasks-count'),
  // aperçu / viewer
  previewTabVite: document.getElementById('preview-tab-vite'),
  previewTabCode: document.getElementById('preview-tab-code'),
  previewBar: document.getElementById('preview-bar'),
  previewUrl: document.getElementById('preview-url'),
  previewGo: document.getElementById('preview-go'),
  previewRefresh: document.getElementById('preview-refresh'),
  previewOpen: document.getElementById('preview-open'),
  previewFrame: document.getElementById('preview-frame'),
  previewCode: document.getElementById('preview-code'),
  previewDiff: document.getElementById('preview-diff'),
  previewHint: document.getElementById('preview-hint'),
  // tiroirs
  santeBody: document.getElementById('sante-body'),
  historyBody: document.getElementById('history-body'),
  proposalsList: document.getElementById('proposals-list'),
  // divers
  toast: document.getElementById('toast'),
  alertBanner: document.getElementById('alert-banner'),
  alertText: document.getElementById('alert-text'),
  alertClose: document.getElementById('alert-close'),
};

// Tiroirs latéraux exclusifs (un seul ouvert)
const drawers = {
  proposals: document.getElementById('proposals-panel'),
  sante: document.getElementById('sante-panel'),
  history: document.getElementById('history-panel'),
};

const ws = new WSClient();
const thread = new Thread(els.thread);

// ── État global (déclaré tôt : utilisé dès le démarrage) ──────────────────
const st = {
  server: 'idle',
  listening: false,
  pendingEnd: null,
  hadVoice: false,
  lastVoice: 0,
  startedAt: 0,
  proposals: new Map(),
  wakeArmed: false,
  wakeStreaming: false,
};

const dev = {
  tasks: [],
  selected: null,
  next: 0,
  tasksTimer: null,
  logTimer: null,
  workerDown: false,
  logPendingAt: 0,
};

let devConfigured = false;
let currentView = 'cockpit';
let viewerTab = 'vite';   // 'vite' | 'code'
let wakeWord = 'hey jarvis';
let liveLevel = 0;         // niveau audio courant (0..1) pour la waveform
let turnCount = 0;

try { st.wakeArmed = localStorage.getItem('sentinel-wake') === '1'; } catch { /* privé */ }

let audioCtx = null;
let player = null;
let capture = null;
let toastTimer = null;

// ── Orbe « Noyau Synaptique » (iframe WebGL, pilotée par postMessage) ──────
const ORB_STATE = {
  idle: 'idle', listening: 'listening', transcribing: 'thinking',
  thinking: 'thinking', speaking: 'speaking', offline: 'idle',
};
const orb = {
  _post(msg) {
    const w = els.orb && els.orb.contentWindow;
    if (w) { try { w.postMessage({ orb: true, ...msg }, '*'); } catch { /* pas prête */ } }
  },
  state(s) { this._post({ state: ORB_STATE[s] || 'idle' }); },
  level(v) { this._post({ level: v }); },
  release() { this._post({ level: null }); },
  pulse() { this._post({ pulse: true }); },
};
els.orb.addEventListener('load', () => orb.state(displayState()));

// ── Rendu de l'état ───────────────────────────────────────────────────────
function displayState() {
  if (!ws.alive) return 'offline';
  if (st.listening) return 'listening';
  if (st.server === 'listening') return 'idle'; // écoute d'un autre appareil
  return st.server;
}

function refreshUi() {
  const s = displayState();
  document.body.dataset.state = s;
  els.stateLabel.textContent = LABELS[s] || s;
  if (s === 'idle' && st.wakeStreaming) {
    els.stateLabel.textContent = `veille · « ${wakeWord} »`;
  }
  els.wakeBtn.classList.toggle('live', st.wakeStreaming);
  orb.state(s);
  updateTranscript();
}

function toast(text) {
  els.toast.textContent = text;
  els.toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { els.toast.hidden = true; }, 4200);
}

// ── Audio (créé au premier geste utilisateur) ─────────────────────────────
function makeAudio() {
  if (!audioCtx) {
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    player = new Player(audioCtx);
  }
}
async function ensureAudio() {
  makeAudio();
  if (audioCtx.state === 'suspended') await audioCtx.resume();
}
async function ensureCapture() {
  await ensureAudio();
  if (capture) return;
  const cap = new Capture();
  await cap.init(audioCtx);
  cap.addEventListener('chunk', (e) => ws.sendBytes(e.detail));
  cap.addEventListener('flushed', () => {
    if (st.pendingEnd === null) return;
    ws.sendJSON({ type: st.pendingEnd ? 'audio_end' : 'audio_cancel' });
    st.pendingEnd = null;
  });
  cap.addEventListener('level', (e) => {
    const { value, active } = e.detail;
    if (!active || !st.listening) return;
    liveLevel = value;
    const now = performance.now();
    if (value > VOICE_THRESHOLD) { st.hadVoice = true; st.lastVoice = now; }
    const total = now - st.startedAt;
    if (st.hadVoice && now - st.lastVoice > SILENCE_MS) stopListening(true);
    else if (!st.hadVoice && total > NO_VOICE_MS) { stopListening(false); toast('Je n’ai rien entendu.'); }
    else if (total > HARD_CAP_MS) stopListening(true);
  });
  capture = cap;
}

// ── Prise de parole ───────────────────────────────────────────────────────
async function startListening() {
  if (st.listening || !ws.alive) return;
  try { await ensureCapture(); }
  catch (err) {
    console.error(err);
    toast('Micro indisponible : vérifie l’autorisation du navigateur et l’accès HTTPS.');
    return;
  }
  if (st.wakeStreaming) { st.wakeStreaming = false; ws.sendJSON({ type: 'wake_stop' }); }
  if (player) player.stop();
  ws.sendJSON({ type: 'audio_start', rate: 16000 });
  st.listening = true;
  st.hadVoice = false;
  st.startedAt = performance.now();
  st.lastVoice = st.startedAt;
  capture.start();
  refreshUi();
}

function stopListening(send) {
  if (!st.listening) return;
  st.listening = false;
  st.pendingEnd = send;
  capture.stop();
  refreshUi();
}

function interrupt() {
  if (player) player.stop();
  ws.sendJSON({ type: 'cancel' });
}

function micAction() {
  if (!ws.alive) { toast('Connexion au serveur perdue…'); return; }
  if (st.listening) { stopListening(true); return; }
  if (st.server === 'speaking') { interrupt(); startListening(); return; }
  if (st.server === 'thinking' || st.server === 'transcribing') { interrupt(); return; }
  startListening();
}

// ── Événements serveur ────────────────────────────────────────────────────
ws.addEventListener('open', () => {
  document.body.classList.add('online');
  els.connLabel.textContent = 'en ligne';
  refreshUi();
});

ws.addEventListener('close', () => {
  document.body.classList.remove('online');
  els.connLabel.textContent = 'hors ligne';
  if (st.listening) { st.listening = false; capture && capture.stop(); }
  if (st.wakeStreaming) { st.wakeStreaming = false; capture && capture.stop(); }
  refreshUi();
});

ws.addEventListener('audio', (e) => { if (player) player.push(e.detail); });

ws.addEventListener('event', (e) => {
  const msg = e.detail;
  switch (msg.type) {
    case 'hello':
      thread.clear();
      turnCount = 0;
      (msg.history || []).forEach((m) => { thread.addMessage(m); if (m.role === 'user') turnCount += 1; });
      st.server = msg.state || 'idle';
      els.connNova.hidden = !msg.ha_configured;
      setNova(!!msg.ha_connected);
      devConfigured = !!msg.dev_configured;
      els.liaisonGithub.hidden = !devConfigured;
      setDevRunning(msg.dev_running || null);
      setAtelierPolling(devConfigured);
      wakeWord = msg.wake_word || wakeWord;
      els.wakeBtn.hidden = !msg.wake_available;
      renderWakeBtn();
      syncWake();
      st.proposals.clear();
      (msg.proposals || []).forEach((p) => st.proposals.set(p.num, p));
      renderProposals();
      updateChatMeta();
      refreshUi();
      break;
    case 'ha_status': setNova(!!msg.connected); break;
    case 'activity':
      if (st.server === 'thinking') { els.stateLabel.textContent = msg.text; els.transcript.textContent = msg.text; }
      break;
    case 'alert': showAlert(msg.level || 'info', msg.text || ''); break;
    case 'proposal_new':
      upsertProposal(msg.proposal);
      toast(`Nouvelle proposition n°${msg.proposal.num} : ${msg.proposal.title}`);
      break;
    case 'proposal_update': upsertProposal(msg.proposal); break;
    case 'status': st.server = msg.state; refreshUi(); syncWake(); break;
    case 'wake': st.wakeStreaming = false; orb.pulse(); chime(); startListening(); break;
    case 'wake_error': onWakeError(msg.text || 'Le mot d’éveil est indisponible.'); break;
    case 'message':
      thread.addMessage(msg.message);
      if (msg.message && msg.message.role === 'user') { turnCount += 1; updateChatMeta(); }
      break;
    case 'assistant_start': thread.startStream(msg.id); break;
    case 'assistant_delta': thread.addDelta(msg.id, msg.text); updateTranscript(); break;
    case 'assistant_end': thread.endStream(msg.id, msg.message, msg.cancelled); updateChatMeta(); break;
    case 'speak_start': if (player) player.begin(msg.rate); break;
    case 'speak_end': if (player) player.end(); break;
    case 'notice': thread.notice(msg.text); break;
    case 'error': thread.error(msg.text); toast(msg.text); break;
    case 'dev_status': setDevRunning(msg.running); break;
    case 'dev_tasks': onDevTasks(msg); break;
    case 'dev_log': onDevLog(msg); break;
    case 'dev_diff': onDevDiff(msg); break;
    case 'sante': renderSante(msg); break;
    case 'historique': renderHistory(msg); break;
    default: break;
  }
});

// ── Nova, alertes, propositions ───────────────────────────────────────────
function setNova(connected) {
  document.body.classList.toggle('nova-on', connected);
  els.connNova.title = connected ? 'Nova connectée' : 'Nova déconnectée';
  els.liaisonNova.classList.toggle('on', connected);
  els.liaisonNova.classList.toggle('warn', !connected);
}

function updateChatMeta() {
  const hm = new Date().toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
  els.chatMeta.textContent = turnCount ? `${hm} · ${turnCount} tour${turnCount > 1 ? 's' : ''}` : hm;
}

let alertTimer = null;
function showAlert(level, text) {
  els.alertText.textContent = text;
  els.alertBanner.className = `alert-banner ${level}`;
  els.alertBanner.hidden = false;
  clearTimeout(alertTimer);
  if (level !== 'critical') alertTimer = setTimeout(() => { els.alertBanner.hidden = true; }, 10000);
}
els.alertClose.addEventListener('click', () => { els.alertBanner.hidden = true; });

const RISK_LABELS = { low: 'faible', medium: 'moyen', sensitive: 'sensible' };

function upsertProposal(p) {
  if (!p || typeof p.num === 'undefined') return;
  if (p.status === 'pending' || p.status === 'deferred') st.proposals.set(p.num, p);
  else st.proposals.delete(p.num);
  renderProposals();
}

function renderProposals() {
  const items = [...st.proposals.values()].sort((a, b) => a.num - b.num);
  els.propCount.textContent = String(items.length);
  els.proposalsBtn.hidden = items.length === 0 && drawers.proposals.hidden;
  els.proposalsBtn.classList.toggle('attention', items.length > 0);

  els.proposalsList.textContent = '';
  if (!items.length) {
    const empty = document.createElement('p');
    empty.className = 'pane-empty';
    empty.textContent = 'Aucune proposition en attente.';
    els.proposalsList.appendChild(empty);
    return;
  }
  for (const p of items) {
    const card = document.createElement('article');
    card.className = `prop${p.status === 'deferred' ? ' deferred' : ''}`;
    const top = document.createElement('div');
    top.className = 'prop-top';
    const title = document.createElement('span');
    title.className = 'prop-title';
    title.textContent = p.title;
    const num = document.createElement('span');
    num.className = 'prop-num';
    num.textContent = `n°${p.num}${p.status === 'deferred' ? ' · reportée' : ''}`;
    top.append(title, num);
    const risk = document.createElement('span');
    risk.className = `risk ${p.risk}`;
    risk.textContent = `risque ${RISK_LABELS[p.risk] || p.risk}`;
    const text = document.createElement('p');
    text.className = 'prop-text';
    text.textContent = [p.description, p.justification].filter(Boolean).join(' — ');
    card.append(top, risk, text);
    if (p.rollback) {
      const rb = document.createElement('p');
      rb.className = 'prop-rollback';
      rb.textContent = `Retour arrière : ${p.rollback}`;
      card.appendChild(rb);
    }
    const actions = document.createElement('div');
    actions.className = 'prop-actions';
    for (const [decision, label, cls] of [
      ['approve', 'Approuver', 'approve'],
      ['reject', 'Refuser', 'reject'],
      ['defer', 'Reporter', 'defer'],
    ]) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = cls;
      btn.textContent = label;
      btn.addEventListener('click', () => ws.sendJSON({ type: 'proposal_decision', id: p.num, decision }));
      actions.appendChild(btn);
    }
    card.appendChild(actions);
    els.proposalsList.appendChild(card);
  }
}

// ── Onglets (Cockpit / Paramètres) + tiroirs ──────────────────────────────
function setView(view) {
  currentView = view;
  document.body.dataset.view = view;
  els.viewCockpit.hidden = view !== 'cockpit';
  els.viewSettings.hidden = view !== 'settings';
  els.tabCockpit.setAttribute('aria-selected', String(view === 'cockpit'));
  els.tabSettings.setAttribute('aria-selected', String(view === 'settings'));
}
els.tabCockpit.addEventListener('click', () => setView('cockpit'));
els.tabSettings.addEventListener('click', () => setView('settings'));

function anyDrawerOpen() { return Object.values(drawers).some((d) => !d.hidden); }
function openDrawer(name) {
  for (const [key, el] of Object.entries(drawers)) el.hidden = key !== name;
  drawersChanged();
}
function closeDrawers() { for (const el of Object.values(drawers)) el.hidden = true; drawersChanged(); }
function drawersChanged() {
  renderProposals();
  setSantePolling(!drawers.sante.hidden);
  if (!drawers.history.hidden) ws.sendJSON({ type: 'historique' });
}
els.proposalsBtn.addEventListener('click', () => openDrawer('proposals'));
els.santeBtn.addEventListener('click', () => openDrawer('sante'));
els.historyBtn.addEventListener('click', () => openDrawer('history'));
document.querySelectorAll('.drawer-x').forEach((btn) => btn.addEventListener('click', closeDrawers));

// ── Chips d'action rapide ─────────────────────────────────────────────────
els.quickChips.querySelectorAll('.chip-q').forEach((btn) => {
  btn.addEventListener('click', () => {
    const q = btn.dataset.q || btn.textContent;
    if (q && ws.alive) ws.sendJSON({ type: 'chat', text: q });
  });
});

// ── Aperçu / viewer (Vite ↔ Code) ─────────────────────────────────────────
function setViewerTab(tab) {
  viewerTab = tab;
  const code = tab === 'code';
  els.previewTabVite.setAttribute('aria-selected', String(!code));
  els.previewTabCode.setAttribute('aria-selected', String(code));
  els.previewBar.hidden = code;
  els.previewFrame.hidden = code;
  els.previewCode.hidden = !code;
  els.previewHint.textContent = code
    ? 'Diff de la tâche sélectionnée dans l’atelier.'
    : 'URL d’un serveur de dev (Vite) ou d’Atrium.';
  if (code) loadDiff();
}
function loadDiff() {
  if (!dev.selected) { els.previewDiff.textContent = ''; els.previewDiff.append(hintRow('Sélectionne une tâche dans l’atelier.')); return; }
  els.previewDiff.textContent = 'chargement du diff…';
  ws.sendJSON({ type: 'dev_diff', id: dev.selected });
}
els.previewTabVite.addEventListener('click', () => setViewerTab('vite'));
els.previewTabCode.addEventListener('click', () => setViewerTab('code'));

const PREVIEW_KEY = 'sentinel.preview.url';
const normalizeUrl = (u) => { u = (u || '').trim(); return u && !/^https?:\/\//i.test(u) ? `https://${u}` : u; };
function loadPreview() {
  const url = normalizeUrl(els.previewUrl.value);
  if (!url) return;
  els.previewUrl.value = url;
  els.previewFrame.src = url;
  try { localStorage.setItem(PREVIEW_KEY, url); } catch { /* privé */ }
}
els.previewGo.addEventListener('click', loadPreview);
els.previewUrl.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); loadPreview(); } });
els.previewRefresh.addEventListener('click', () => {
  if (viewerTab === 'code') { loadDiff(); return; }
  if (els.previewFrame.src) els.previewFrame.src = els.previewFrame.src;
});
els.previewOpen.addEventListener('click', () => {
  const url = normalizeUrl(els.previewUrl.value);
  if (url) window.open(url, '_blank', 'noopener');
});
try { const u = localStorage.getItem(PREVIEW_KEY); if (u) els.previewUrl.value = u; } catch { /* privé */ }

// ── Console de l'atelier de développement ─────────────────────────────────
const DEV_STATUS_FR = { queued: 'en file', running: 'en cours', done: 'terminée', failed: 'échec' };

function setAtelierPolling(on) {
  clearInterval(dev.tasksTimer); dev.tasksTimer = null;
  clearInterval(dev.logTimer); dev.logTimer = null;
  if (!on || !devConfigured) return;
  ws.sendJSON({ type: 'dev_tasks' });
  if (dev.selected) ws.sendJSON({ type: 'dev_log', id: dev.selected, after: dev.next });
  dev.tasksTimer = setInterval(() => ws.sendJSON({ type: 'dev_tasks' }), 5000);
  dev.logTimer = setInterval(() => {
    if (dev.workerDown) return;
    if (dev.logPendingAt && Date.now() - dev.logPendingAt < 8000) return;
    const task = dev.tasks.find((t) => t.id === dev.selected);
    if (task && (task.status === 'running' || task.status === 'queued')) {
      dev.logPendingAt = Date.now();
      ws.sendJSON({ type: 'dev_log', id: dev.selected, after: dev.next });
    }
  }, 2000);
}

function setDevRunning(running) {
  if (devConfigured) ws.sendJSON({ type: 'dev_tasks' });
}

function onDevTasks(msg) {
  dev.workerDown = !!msg.error;
  if (msg.error) { els.atelierStatus.textContent = msg.error; return; }
  const previous = dev.tasks;
  dev.tasks = msg.tasks || [];
  const a = msg.atelier || {};
  // en-tête concis + liaison GitHub
  const auth = a.auth ? `auth ${a.auth}` : 'auth ?';
  const nrepos = (a.repos || []).length;
  els.atelierStatus.textContent = `${auth} · ${nrepos} dépôt${nrepos > 1 ? 's' : ''}`;
  els.liaisonGithub.classList.toggle('on', !!a.push_possible);
  els.liaisonGithub.classList.toggle('warn', devConfigured && !a.push_possible);
  els.liaisonGithub.title = a.push_possible ? 'GitHub : push prêt' : 'GitHub : GITHUB_TOKEN absent';

  const running = dev.tasks.filter((t) => t.status === 'running').length;
  els.tasksCount.hidden = !dev.tasks.length;
  els.tasksCount.textContent = running ? `${running} en cours` : String(dev.tasks.length);

  const sel = dev.tasks.find((t) => t.id === dev.selected);
  const prevSel = previous.find((t) => t.id === dev.selected);
  if (sel && prevSel && prevSel.status !== sel.status) {
    setBranchLabel(sel);
    if (sel.status === 'done' || sel.status === 'failed') ws.sendJSON({ type: 'dev_log', id: sel.id, after: dev.next });
  }
  if (dev.selected && !sel) dev.selected = null;
  if (!dev.selected && dev.tasks.length) {
    const active = dev.tasks.find((t) => t.status === 'running' || t.status === 'queued');
    selectDevTask((active || dev.tasks[0]).id);
  } else {
    renderDevTasks();
  }
}

function renderDevTasks() {
  els.atelierTasks.textContent = '';
  els.atelierEmpty.hidden = dev.tasks.length > 0;
  if (!dev.tasks.length) {
    els.atelierToolbar.hidden = true;
    els.atelierLog.hidden = true;
    return;
  }
  for (const t of dev.tasks) {
    const row = document.createElement('button');
    row.type = 'button';
    row.className = 'dev-task' + (t.id === dev.selected ? ' selected' : '');
    const stDot = document.createElement('span');
    stDot.className = `st ${t.status}`;
    const repo = document.createElement('span');
    repo.className = 'repo';
    repo.textContent = t.repo;
    const desc = document.createElement('span');
    desc.className = 'desc';
    desc.textContent = t.instruction || '';
    const when = document.createElement('span');
    when.className = 'when';
    when.textContent = fmtWhen(t.created_at);
    row.append(stDot, repo, desc, when);
    row.addEventListener('click', () => selectDevTask(t.id));
    els.atelierTasks.appendChild(row);
  }
}

function selectDevTask(id) {
  dev.selected = id;
  dev.next = 0;
  dev.logPendingAt = 0;
  els.atelierLog.textContent = '';
  els.atelierLog.hidden = false;
  renderDevTasks();
  const task = dev.tasks.find((t) => t.id === id);
  els.atelierToolbar.hidden = !task;
  if (task) {
    setBranchLabel(task);
    ws.sendJSON({ type: 'dev_log', id, after: 0 });
    if (viewerTab === 'code') loadDiff();
  }
}

function setBranchLabel(task) {
  els.atelierBranch.textContent = `${task.branch} · ${DEV_STATUS_FR[task.status] || task.status}`;
}

function hintRow(text) {
  const row = document.createElement('div');
  row.className = 'log-row log-hint';
  row.textContent = text;
  return row;
}

function appendLogRow(t, line, cls) {
  const row = document.createElement('div');
  row.className = 'log-row' + (cls ? ` ${cls}` : '');
  const tEl = document.createElement('span');
  tEl.className = 't';
  tEl.textContent = t || '';
  const lEl = document.createElement('span');
  lEl.className = 'l';
  lEl.textContent = line;
  row.append(tEl, lEl);
  els.atelierLog.appendChild(row);
}

function onDevLog(msg) {
  if (msg.id !== dev.selected) return;
  dev.logPendingAt = 0;
  const el = els.atelierLog;
  if (msg.error) {
    const last = el.lastElementChild;
    if (!last || last.textContent.trim() !== msg.error.trim()) appendLogRow('', msg.error, 'log-hint');
    return;
  }
  const pinned = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
  let lines = msg.lines || [];
  const total = msg.next || 0;
  if (total < dev.next) { el.textContent = ''; dev.next = 0; }
  const start = total - lines.length;
  if (start < dev.next) lines = lines.slice(dev.next - start);
  if (lines.length) el.querySelectorAll('.log-hint').forEach((n) => n.remove());
  for (const entry of lines) appendLogRow(entry.t, entry.line);
  dev.next = total;
  if (!el.childElementCount) {
    const task = dev.tasks.find((t) => t.id === msg.id);
    const finished = task && (task.status === 'done' || task.status === 'failed');
    appendLogRow('', finished
      ? '(journal indisponible — il ne survit pas à un redémarrage de l’atelier)'
      : 'en attente des premières lignes…', 'log-hint');
  }
  if (pinned) el.scrollTop = el.scrollHeight;
  const task = dev.tasks.find((t) => t.id === msg.id);
  if (task && msg.status && task.status !== msg.status) {
    task.status = msg.status;
    renderDevTasks();
    setBranchLabel(task);
    if (task.status === 'done' || task.status === 'failed') ws.sendJSON({ type: 'dev_log', id: task.id, after: dev.next });
  }
}

els.atelierDiffBtn.addEventListener('click', () => { if (dev.selected) setViewerTab('code'); });

function diffClass(line) {
  if (line.startsWith('+++') || line.startsWith('---') || line.startsWith('diff ')) return 'd-file';
  if (line.startsWith('@@')) return 'd-hunk';
  if (line.startsWith('+')) return 'd-add';
  if (line.startsWith('-')) return 'd-del';
  return 'd-ctx';
}

function onDevDiff(msg) {
  if (msg.id !== dev.selected || viewerTab !== 'code') return;
  els.previewDiff.textContent = '';
  if (msg.error) { els.previewDiff.textContent = msg.error; return; }
  const diff = String(msg.diff || '').trim();
  if (!diff) { els.previewDiff.append(hintRow('Aucune modification pour l’instant.')); return; }
  for (const line of diff.split('\n')) {
    const row = document.createElement('div');
    row.className = `log-row ${diffClass(line)}`;
    row.textContent = line || ' ';
    els.previewDiff.appendChild(row);
  }
}

// ── Santé ─────────────────────────────────────────────────────────────────
let santeTimer = null;
function setSantePolling(on) {
  clearInterval(santeTimer); santeTimer = null;
  if (!on) return;
  ws.sendJSON({ type: 'sante' });
  santeTimer = setInterval(() => ws.sendJSON({ type: 'sante' }), 30000);
}
document.getElementById('sante-refresh').addEventListener('click', () => ws.sendJSON({ type: 'sante' }));

function tile(name, dot, rows, note) {
  const el = document.createElement('article');
  el.className = 'tile';
  const head = document.createElement('div');
  head.className = 'tile-head';
  const nameEl = document.createElement('span');
  nameEl.className = 'name';
  nameEl.textContent = name;
  const dotEl = document.createElement('span');
  dotEl.className = `tile-dot${dot ? ` ${dot}` : ''}`;
  head.append(nameEl, dotEl);
  el.appendChild(head);
  for (const [k, v] of rows) {
    const kv = document.createElement('div');
    kv.className = 'kv';
    const kEl = document.createElement('span');
    kEl.className = 'k';
    kEl.textContent = k;
    const vEl = document.createElement('span');
    vEl.className = 'v';
    vEl.textContent = v;
    kv.append(kEl, vEl);
    el.appendChild(kv);
  }
  if (note) {
    const noteEl = document.createElement('p');
    noteEl.className = 'tile-note';
    noteEl.textContent = note;
    el.appendChild(noteEl);
  }
  return el;
}

function emptyLine(text) {
  const p = document.createElement('p');
  p.className = 'pane-empty';
  p.textContent = text;
  return p;
}

const fmtNum = (x) => String(Math.round(x * 100) / 100).replace('.', ',');

function renderSante(msg) {
  const body = els.santeBody;
  body.textContent = '';
  if (msg.error) { body.appendChild(emptyLine(msg.error)); return; }
  const d = msg.data || {};

  const nova = d.nova || {};
  {
    let dot = 'ok'; const rows = []; let note = '';
    if (!nova.configuree) { dot = ''; rows.push(['état', 'non configurée']); }
    else if (!nova.connectee) { dot = 'bad'; rows.push(['état', 'déconnectée !']); }
    else {
      rows.push(['version', nova.version || '?']);
      rows.push(['entités', String(nova.entites ?? '?')]);
      if (nova.nb_indisponibles) { dot = 'warn'; rows.push(['indisponibles', String(nova.nb_indisponibles)]); }
      const maj = nova.mises_a_jour || [];
      if (maj.length) { rows.push(['mises à jour', String(maj.length)]); note = maj.slice(0, 6).join(', ') + (maj.length > 6 ? '…' : ''); }
    }
    body.appendChild(tile('Nova', dot, rows, note));
  }

  const sys = d.systeme || {};
  if (sys.charge || sys.ram) {
    let dot = 'ok'; const rows = [];
    if (sys.charge) { rows.push(['charge', `${fmtNum(sys.charge[0])} / ${sys.coeurs || '?'} cœurs`]); if (sys.charge[0] > (sys.coeurs || 1)) dot = 'warn'; }
    if (sys.ram) { rows.push(['mémoire', `${sys.ram.utilisee_pct} %`]); if (sys.ram.utilisee_pct >= 90) dot = 'warn'; }
    body.appendChild(tile('Nebula', dot, rows));
  }

  const docker = d.docker;
  if (docker) {
    if (docker.erreur) body.appendChild(tile('Docker', 'bad', [['erreur', docker.erreur]]));
    else {
      const problems = docker.problemes || [];
      const rows = [['conteneurs', `${docker.en_marche} en marche / ${docker.total}`]];
      for (const p of problems.slice(0, 4)) rows.push([p.nom, p.etat]);
      const mem = Object.entries(docker.top_memoire_mo || {}).slice(0, 4).map(([n, mo]) => `${n} ${mo} Mo`).join(' · ');
      body.appendChild(tile('Docker', problems.length ? 'warn' : 'ok', rows, mem ? `Mémoire : ${mem}` : ''));
    }
  }

  const atrium = d.atrium;
  if (atrium) body.appendChild(atrium.ok ? tile('Atrium', 'ok', [['latence', `${atrium.latence_ms} ms`]]) : tile('Atrium', 'bad', [['état', 'injoignable !']]));

  if (!body.childElementCount) body.appendChild(emptyLine('Aucun moniteur configuré.'));
}

// ── Historique ────────────────────────────────────────────────────────────
const OUTCOME_FR = { ok: 'ok', refused: 'refusée', failed: 'échec', needs_confirmation: 'à confirmer', created: 'créée', decided: 'décidée' };
const PROP_STATUS_FR = { done: 'exécutée', rejected: 'refusée', refused: 'refusée', failed: 'échec', expired: 'expirée', approved: 'approuvée', executing: 'en cours' };

function fmtWhen(ts) {
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return '';
  const hm = d.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
  return d.toDateString() === new Date().toDateString()
    ? hm
    : `${d.toLocaleDateString('fr-FR', { day: '2-digit', month: '2-digit' })} ${hm}`;
}

const clip = (text, n) => (text.length > n ? `${text.slice(0, n - 1)}…` : text);

function histRow(when, label, labelCls, badgeText, badgeCls, lines) {
  const row = document.createElement('article');
  row.className = 'j-row';
  const top = document.createElement('div');
  top.className = 'j-top';
  const whenEl = document.createElement('span');
  whenEl.className = 'when';
  whenEl.textContent = when;
  const labelEl = document.createElement('span');
  if (labelCls) labelEl.className = labelCls;
  labelEl.textContent = label;
  const badge = document.createElement('span');
  badge.className = `badge ${badgeCls}`;
  badge.textContent = badgeText;
  top.append(whenEl, labelEl, badge);
  row.appendChild(top);
  for (const [cls, text] of lines) {
    if (!text) continue;
    const div = document.createElement('div');
    div.className = cls;
    div.textContent = clip(text, 170);
    row.appendChild(div);
  }
  return row;
}

function renderHistory(msg) {
  const body = els.historyBody;
  body.textContent = '';
  const title = (text) => { const h = document.createElement('h3'); h.className = 'hist-title'; h.textContent = text; return h; };

  body.appendChild(title('Journal des actions'));
  const journal = msg.journal || [];
  if (!journal.length) body.appendChild(emptyLine('Journal vide pour l’instant.'));
  for (const e of journal) {
    body.appendChild(histRow(fmtWhen(e.ts), e.action_id, 'act', OUTCOME_FR[e.outcome] || e.outcome, e.outcome, [['j-auth', e.authorization], ['j-detail', e.detail]]));
  }

  body.appendChild(title('Propositions passées'));
  const props = msg.proposals || [];
  if (!props.length) body.appendChild(emptyLine('Aucune proposition passée.'));
  for (const p of props) {
    body.appendChild(histRow(fmtWhen(p.decided_at || p.created_at), `n°${p.num} — ${p.title}`, '', PROP_STATUS_FR[p.status] || p.status, p.status, [['j-detail', p.result || p.error]]));
  }
}

// ── Veille au mot d'éveil ─────────────────────────────────────────────────
let wakeRetryTimer = null;
let gestureHooked = false;

function renderWakeBtn() {
  els.wakeBtn.classList.toggle('armed', st.wakeArmed);
  els.wakeBtn.title = st.wakeArmed
    ? `Veille active — dis « ${wakeWord} » (cliquer pour couper)`
    : `Activer la veille au mot d'éveil (« ${wakeWord} »)`;
}
function saveWakePref() { try { localStorage.setItem('sentinel-wake', st.wakeArmed ? '1' : '0'); } catch { /* privé */ } }

function armOnGesture() {
  if (gestureHooked) return;
  gestureHooked = true;
  const handler = () => {
    document.removeEventListener('pointerdown', handler);
    document.removeEventListener('keydown', handler);
    gestureHooked = false;
    syncWake();
  };
  document.addEventListener('pointerdown', handler, { once: true });
  document.addEventListener('keydown', handler, { once: true });
}

async function tryEnsureCapture() {
  makeAudio();
  if (audioCtx.state === 'suspended') {
    await Promise.race([audioCtx.resume().catch(() => {}), new Promise((r) => setTimeout(r, 350))]);
    if (audioCtx.state !== 'running') throw new Error('audio verrouillé');
  }
  await ensureCapture();
}

function wakeWanted() {
  return st.wakeArmed && ws.alive && !els.wakeBtn.hidden && !st.listening && st.server === 'idle' && !document.hidden;
}

async function syncWake() {
  if (wakeWanted() && !st.wakeStreaming) {
    try { await tryEnsureCapture(); }
    catch (err) {
      if (String(err && err.message).includes('verrouillé')) { armOnGesture(); return; }
      console.error(err);
      st.wakeArmed = false; saveWakePref(); renderWakeBtn();
      toast('Micro indisponible : la veille au mot d’éveil est coupée.');
      return;
    }
    if (!wakeWanted() || st.wakeStreaming) return;
    st.wakeStreaming = true;
    ws.sendJSON({ type: 'wake_start', rate: 16000 });
    capture.start();
  } else if (!wakeWanted() && st.wakeStreaming) {
    st.wakeStreaming = false;
    if (capture && !st.listening) capture.stop();
    ws.sendJSON({ type: 'wake_stop' });
  }
  refreshUi();
}

function onWakeError(text) {
  if (st.wakeStreaming) { st.wakeStreaming = false; if (capture && !st.listening) capture.stop(); }
  if (st.wakeArmed) { toast(text); clearTimeout(wakeRetryTimer); wakeRetryTimer = setTimeout(syncWake, 8000); }
  refreshUi();
}

function chime() {
  if (!audioCtx || audioCtx.state !== 'running') return;
  const now = audioCtx.currentTime;
  const osc = audioCtx.createOscillator();
  const gain = audioCtx.createGain();
  osc.type = 'sine';
  osc.frequency.setValueAtTime(660, now);
  osc.frequency.exponentialRampToValueAtTime(990, now + 0.12);
  gain.gain.setValueAtTime(0.0001, now);
  gain.gain.exponentialRampToValueAtTime(0.1, now + 0.02);
  gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.28);
  osc.connect(gain); gain.connect(audioCtx.destination);
  osc.start(now); osc.stop(now + 0.3);
}

els.wakeBtn.addEventListener('click', () => { st.wakeArmed = !st.wakeArmed; saveWakePref(); renderWakeBtn(); syncWake(); });
document.addEventListener('visibilitychange', () => syncWake());

// ── Transcript (texte sous l'orbe) ────────────────────────────────────────
function updateTranscript() {
  const s = displayState();
  if (s === 'speaking') {
    const last = thread.lastAssistantText ? thread.lastAssistantText() : '';
    els.transcript.textContent = last || 'Je te réponds…';
  } else if (s === 'listening') {
    els.transcript.textContent = 'Je t’écoute…';
  } else if (s === 'thinking' || s === 'transcribing') {
    els.transcript.textContent = LABELS[s];
  } else if (s === 'offline') {
    els.transcript.textContent = '';
  } else {
    els.transcript.textContent = st.wakeStreaming
      ? `En veille — dis « ${wakeWord} »`
      : 'Touche l’orbe ou l’espace pour parler.';
  }
}

// ── Waveform (barres réactives au niveau audio + état) ────────────────────
const WF_COUNT = 48;
const wfBars = [];
const wfBase = [];
(function buildWaveform() {
  for (let i = 0; i < WF_COUNT; i++) {
    const p = i / (WF_COUNT - 1);
    const env = Math.pow(Math.sin(p * Math.PI), 0.65);        // enveloppe (haut au centre)
    const jag = 0.42 + 0.58 * Math.abs(Math.sin(i * 1.73) * Math.cos(i * 0.61));
    wfBase.push({ env, jag });
    const bar = document.createElement('div');
    bar.className = 'wf-bar';
    if (i % 6 === 0) bar.style.background = 'var(--state)';
    bar.style.opacity = String(0.3 + 0.7 * env);
    els.waveform.appendChild(bar);
    wfBars.push(bar);
  }
})();

function renderWaveform(amp) {
  const H = 44;
  for (let i = 0; i < WF_COUNT; i++) {
    const { env, jag } = wfBase[i];
    const h = Math.max(3, 3 + (H - 3) * amp * env * jag);
    wfBars[i].style.height = `${h.toFixed(1)}px`;
  }
}

// Niveau audio → orbe + waveform. Voix (lecture) et micro (capture) déforment ;
// sinon amplitude « au repos » douce selon l'état.
let orbLevelActive = false;
(function pump() {
  let amp;
  if (player && player.playing) { const l = player.level(); orb.level(l); orbLevelActive = true; amp = 0.35 + 0.65 * l; }
  else if (st.listening) { orb.level(liveLevel); orbLevelActive = true; amp = 0.3 + 0.7 * Math.min(1, liveLevel * 6); }
  else {
    if (orbLevelActive) { orb.release(); orbLevelActive = false; }
    const s = displayState();
    amp = s === 'thinking' || s === 'transcribing' ? 0.5 : s === 'offline' ? 0.06 : 0.16;
  }
  renderWaveform(amp);
  requestAnimationFrame(pump);
})();

// ── Interactions ──────────────────────────────────────────────────────────
els.mic.addEventListener('click', micAction);
els.orbMic.addEventListener('click', micAction);
els.orbTap.addEventListener('click', micAction);
els.interrupt.addEventListener('click', () => { if (st.listening) stopListening(false); interrupt(); });

els.composer.addEventListener('submit', (e) => {
  e.preventDefault();
  const text = els.input.value.trim();
  if (!text || !ws.alive) return;
  ws.sendJSON({ type: 'chat', text });
  els.input.value = '';
});

document.addEventListener('keydown', (e) => {
  if (e.code === 'Space' && document.activeElement !== els.input && !e.repeat) {
    e.preventDefault();
    micAction();
  } else if (e.key === 'Escape') {
    if (anyDrawerOpen()) { closeDrawers(); return; }
    if (st.listening) stopListening(false);
    interrupt();
  }
});

// ── Démarrage ─────────────────────────────────────────────────────────────
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('sw.js').catch(() => {});
}

setView('cockpit');
updateChatMeta();
refreshUi();
ws.connect();
