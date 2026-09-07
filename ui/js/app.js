/* Sentinel — application cliente (cockpit) : états, micro, lecture, chat,
   transcript, onglets, tiroirs, atelier, aperçu, veille. */

import { WSClient } from './ws.js';
import { Capture } from './audio-capture.js';
import { Player } from './audio-play.js';
import { Thread } from './chat.js';

const LABELS = {
  idle: 'en veille',
  listening: 'je t’écoute',
  transcribing: 'transcription…',
  thinking: 'réflexion…',
  speaking: 'luna parle',
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
  mailBtn: document.getElementById('mail-btn'),
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
  voiceReply: document.getElementById('voice-reply'),
  mic: document.getElementById('mic'),
  // voix / orbe
  orb: document.getElementById('orb'),
  orbTap: document.getElementById('orb-tap'),
  orbMic: document.getElementById('orb-mic'),
  interrupt: document.getElementById('btn-interrupt'),
  stateLabel: document.getElementById('state-label'),
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
  mailBody: document.getElementById('mail-body'),
  proposalsList: document.getElementById('proposals-list'),
  // divers
  toast: document.getElementById('toast'),
  alertBanner: document.getElementById('alert-banner'),
  alertText: document.getElementById('alert-text'),
  alertClose: document.getElementById('alert-close'),
  // paramètres
  setConnexions: document.getElementById('set-connexions'),
  setWakeword: document.getElementById('set-wakeword'),
  setWakeavail: document.getElementById('set-wakeavail'),
  setWakeToggle: document.getElementById('set-wake-toggle'),
  setVoice: document.getElementById('set-voice'),
  setVoiceEngine: document.getElementById('set-voice-engine'),
  setModel: document.getElementById('set-model'),
  setEffort: document.getElementById('set-effort'),
  setMaxtok: document.getElementById('set-maxtok'),
  setStt: document.getElementById('set-stt'),
  setTts: document.getElementById('set-tts'),
  setReport: document.getElementById('set-report'),
  setTz: document.getElementById('set-tz'),
  setProtocols: document.getElementById('set-protocols'),
  // mémoire (Paramètres)
  memoireForm: document.getElementById('memoire-form'),
  memoireCat: document.getElementById('memoire-cat'),
  memoireInput: document.getElementById('memoire-input'),
  memoireList: document.getElementById('memoire-list'),
  memoireCount: document.getElementById('memoire-count'),
  // profils vocaux (Paramètres)
  speakerForm: document.getElementById('speaker-form'),
  speakerName: document.getElementById('speaker-name'),
  speakerOwner: document.getElementById('speaker-owner'),
  speakerList: document.getElementById('speaker-list'),
  speakerCount: document.getElementById('speaker-count'),
  speakerState: document.getElementById('speaker-state'),
  whoSpeaks: document.getElementById('who-speaks'),
  // pages web (Paramètres)
  pagesList: document.getElementById('pages-list'),
  pagesCount: document.getElementById('pages-count'),
  pagePreview: document.getElementById('page-preview'),
  ppTitle: document.getElementById('pp-title'),
  ppUrl: document.getElementById('pp-url'),
  ppFrame: document.getElementById('pp-frame'),
  ppPublish: document.getElementById('pp-publish'),
  ppOpen: document.getElementById('pp-open'),
  ppClose: document.getElementById('pp-close'),
  // auto-amélioration (Paramètres › Évolutions)
  navEvolutions: document.getElementById('nav-evolutions'),
  evolutionsList: document.getElementById('evolutions-list'),
  evolutionsCount: document.getElementById('evolutions-count'),
  evoReview: document.getElementById('evo-review'),
  evoTitle: document.getElementById('evo-title'),
  evoTarget: document.getElementById('evo-target'),
  evoWhy: document.getElementById('evo-why'),
  evoDiff: document.getElementById('evo-diff'),
  evoAccept: document.getElementById('evo-accept'),
  evoReject: document.getElementById('evo-reject'),
  evoClose: document.getElementById('evo-close'),
  // proactivité (Phase 7)
  proactiveBtn: document.getElementById('proactive-btn'),
  proactiveCount: document.getElementById('proactive-count'),
  proactiveBody: document.getElementById('proactive-body'),
  navProactivite: document.getElementById('nav-proactivite'),
  mutesList: document.getElementById('mutes-list'),
  mutesCount: document.getElementById('mutes-count'),
  // routines (Phase 8)
  navRoutines: document.getElementById('nav-routines'),
  routinesProposed: document.getElementById('routines-proposed'),
  routinesActive: document.getElementById('routines-active'),
  routinesProposedCount: document.getElementById('routines-proposed-count'),
  routinesActiveCount: document.getElementById('routines-active-count'),
  // musique (Phase 9)
  mediaBtn: document.getElementById('media-btn'),
  mediaBody: document.getElementById('media-body'),
};

// Tiroirs latéraux exclusifs (un seul ouvert)
const drawers = {
  proposals: document.getElementById('proposals-panel'),
  proactive: document.getElementById('proactive-panel'),
  media: document.getElementById('media-panel'),
  sante: document.getElementById('sante-panel'),
  history: document.getElementById('history-panel'),
  mail: document.getElementById('mail-panel'),
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
let liveLevel = 0;         // niveau audio courant (0..1) → amplitude de l'orbe
let turnCount = 0;
let lastHello = null;      // dernier message hello (moteur/config) pour les Paramètres
let devAtelier = null;     // dernières infos atelier (auth/push/dépôts)
let voiceReply = true;     // Luna répond-elle à voix haute ? (aussi à l'écrit)

try { st.wakeArmed = localStorage.getItem('sentinel-wake') === '1'; } catch { /* privé */ }
try { voiceReply = localStorage.getItem('sentinel-voice-reply') !== '0'; } catch { /* privé */ }

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
      lastHello = msg;
      thread.clear();
      turnCount = 0;
      (msg.history || []).forEach((m) => { thread.addMessage(m); if (m.role === 'user') turnCount += 1; });
      st.server = msg.state || 'idle';
      els.connNova.hidden = !msg.ha_configured;
      setNova(!!msg.ha_connected);
      devConfigured = !!msg.dev_configured;
      els.liaisonGithub.hidden = !devConfigured;
      els.mailBtn.hidden = !(msg.config && msg.config.mail);
      if (els.navEvolutions) els.navEvolutions.hidden = !(msg.config && msg.config.self_improve);
      if (els.navProactivite) els.navProactivite.hidden = !(msg.config && msg.config.proactive);
      if (els.navRoutines) els.navRoutines.hidden = !(msg.config && msg.config.routines);
      els.mediaBtn.hidden = !(msg.config && msg.config.music);
      renderProactive({ suggestions: msg.proactive || [], muted: msg.proactive_muted || [] });
      renderRoutines({ routines: msg.routines || [] });
      renderMedia({ players: msg.media || [], enabled: !!(msg.config && msg.config.music) });
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
      renderSettings();
      renderSpeakers({ enabled: !!(msg.config && msg.config.speaker), speakers: msg.speakers || [] });
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
    case 'sources': thread.addSources(msg.sources); break;
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
    case 'mail': renderMail(msg); break;
    case 'memoires': renderMemoires(msg); break;
    case 'speakers': renderSpeakers(msg); break;
    case 'speaker': updateWhoSpeaks(msg); break;
    case 'enroll_result': onEnrollResult(msg); break;
    case 'pages': renderPages(msg); break;
    case 'page': showPagePreview(msg); break;
    case 'evolutions': renderEvolutions(msg); break;
    case 'evolution': showEvolution(msg); break;
    case 'proactive': renderProactive(msg); break;
    case 'routines': renderRoutines(msg); break;
    case 'media': renderMedia(msg); break;
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
  if (view === 'settings') renderSettings();
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
  if (!drawers.mail.hidden) ws.sendJSON({ type: 'mail' });
  if (!drawers.proactive.hidden) ws.sendJSON({ type: 'proactive' });
  if (!drawers.media.hidden) ws.sendJSON({ type: 'media' });
}
els.proposalsBtn.addEventListener('click', () => openDrawer('proposals'));
els.santeBtn.addEventListener('click', () => openDrawer('sante'));
els.historyBtn.addEventListener('click', () => openDrawer('history'));
els.mailBtn.addEventListener('click', () => openDrawer('mail'));
els.mediaBtn.addEventListener('click', () => openDrawer('media'));
document.getElementById('mail-refresh').addEventListener('click', () => ws.sendJSON({ type: 'mail' }));
document.getElementById('media-refresh').addEventListener('click', () => ws.sendJSON({ type: 'media' }));
document.querySelectorAll('.drawer-x').forEach((btn) => btn.addEventListener('click', closeDrawers));

// ── Chips d'action rapide ─────────────────────────────────────────────────
els.quickChips.querySelectorAll('.chip-q').forEach((btn) => {
  btn.addEventListener('click', () => {
    const q = btn.dataset.q || btn.textContent;
    if (q && ws.alive) { ensureAudio().catch(() => {}); ws.sendJSON({ type: 'chat', text: q, speak: voiceReply }); }
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
  devAtelier = a;
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
  if (currentView === 'settings') renderSettings();
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

// ── Courriel (tiroir, lecture seule) ──────────────────────────────────────
function renderMail(msg) {
  const body = els.mailBody;
  body.textContent = '';
  if (msg.error) { body.appendChild(emptyLine(msg.error)); return; }
  const data = msg.data || {};
  const msgs = data.messages || [];
  const total = data.unread_total || 0;

  if (!total) { body.appendChild(emptyLine('Boîte à jour — aucun message non lu. ✨')); return; }

  const head = document.createElement('div');
  head.className = 'mail-head';
  const n = document.createElement('span');
  n.className = 'n';
  n.textContent = String(total);
  const lbl = document.createElement('span');
  lbl.className = 'lbl';
  lbl.textContent = `non lu${total > 1 ? 's' : ''}${msgs.length < total ? ` · ${msgs.length} affichés` : ''}`;
  head.append(n, lbl);
  body.appendChild(head);

  for (const m of msgs) {
    const name = m.from_name || m.from_email || '?';
    const item = document.createElement('article');
    item.className = 'mail-item' + (m.important ? ' important' : '');

    const ava = document.createElement('span');
    ava.className = 'mail-ava';
    ava.textContent = (name.trim()[0] || '?');

    const main = document.createElement('div');
    main.className = 'mail-main';

    const l1 = document.createElement('div');
    l1.className = 'mail-l1';
    const from = document.createElement('span');
    from.className = 'mail-from';
    from.textContent = name;
    const when = document.createElement('span');
    when.className = 'mail-when';
    when.textContent = m.date ? fmtWhen(m.date) : '';
    l1.append(from, when);

    const subj = document.createElement('div');
    subj.className = 'mail-subj';
    subj.textContent = m.subject || '(sans objet)';
    main.append(l1, subj);

    if (m.snippet) {
      const snip = document.createElement('div');
      snip.className = 'mail-snip';
      snip.textContent = m.snippet;
      main.appendChild(snip);
    }
    item.append(ava, main);
    body.appendChild(item);
  }
}

// ── Page Paramètres (réel + feuille de route) ─────────────────────────────
function renderSettings() {
  const h = lastHello || {};
  const eng = h.engine || {};
  const cfg = h.config || {};
  els.setWakeword.textContent = `« ${wakeWord} »`;
  els.setWakeavail.textContent = h.wake_available ? 'actif' : 'indisponible';
  els.setWakeToggle.setAttribute('aria-checked', String(st.wakeArmed));
  els.setWakeToggle.disabled = !h.wake_available;
  const cloned = eng.tts_engine === 'cloned';
  els.setVoiceEngine.textContent = cloned ? 'clonage local · repli Piper' : 'Piper · local';
  els.setVoice.textContent = cloned ? `« ${eng.cloned_tts_voice || 'luna'} » (clonée)` : (eng.piper_voice || '—');
  els.setModel.textContent = eng.model || '—';
  els.setEffort.textContent = eng.effort || '—';
  els.setMaxtok.textContent = eng.max_tokens ? String(eng.max_tokens) : '—';
  els.setStt.textContent = eng.whisper_model ? `${eng.whisper_model} · faster-whisper` : '—';
  els.setTts.textContent = cloned
    ? `voix clonée « ${eng.cloned_tts_voice || 'luna'} » (repli Piper)`
    : (eng.piper_voice ? `${eng.piper_voice} · Piper` : '—');
  els.setReport.textContent = cfg.daily_report ? cfg.daily_report : 'désactivé';
  els.setTz.textContent = eng.tz || '—';
  renderProtocols(h.protocols || []);
  renderConnexions(h, cfg);
}

function renderProtocols(list) {
  els.setProtocols.textContent = '';
  if (!list.length) {
    const s = document.createElement('span');
    s.className = 'set-note';
    s.textContent = 'Aucun protocole reconnu.';
    els.setProtocols.appendChild(s);
    return;
  }
  for (const p of list) {
    const c = document.createElement('span');
    c.className = `set-proto ${p.risque || ''}`;
    c.textContent = p.nom;
    els.setProtocols.appendChild(c);
  }
}

function svcCard(o) {
  const card = document.createElement('div');
  card.className = 'svc' + (o.soon ? ' soon' : '');
  const top = document.createElement('div');
  top.className = 'svc-top';
  const icon = document.createElement('div');
  icon.className = 'svc-ic';
  icon.textContent = o.ic;
  const mid = document.createElement('div');
  mid.style.flex = '1';
  mid.style.minWidth = '0';
  const nm = document.createElement('div');
  nm.className = 'svc-name';
  nm.textContent = o.name;
  const status = document.createElement('div');
  status.className = `svc-status ${o.statusCls || ''}`;
  status.textContent = o.status;
  mid.append(nm, status);
  top.append(icon, mid);
  if (o.badge) {
    const b = document.createElement('span');
    b.className = 'svc-badge';
    b.textContent = o.badge;
    top.appendChild(b);
  }
  card.appendChild(top);
  if (o.desc) {
    const d = document.createElement('div');
    d.className = 'svc-desc';
    d.textContent = o.desc;
    card.appendChild(d);
  }
  return card;
}

function renderConnexions(h, cfg) {
  const grid = els.setConnexions;
  grid.textContent = '';
  const at = devAtelier || {};
  const real = [
    { ic: 'HA', name: 'Home Assistant (Nova)',
      status: h.ha_configured ? (h.ha_connected ? 'Connecté' : 'Déconnecté') : 'Non configuré',
      statusCls: h.ha_configured ? (h.ha_connected ? 'on' : 'warn') : '',
      desc: 'Lumières, chauffage, volets, scènes, capteurs.' },
    { ic: 'GH', name: 'GitHub (atelier de dev)',
      status: h.dev_configured ? (at.push_possible ? `push prêt${at.auth ? ' · ' + at.auth : ''}` : 'jeton absent') : 'désactivé',
      statusCls: h.dev_configured ? (at.push_possible ? 'on' : 'warn') : '',
      desc: 'Branches, diffs, push après proposition approuvée.' },
    { ic: 'IA', name: 'Cerveau (Anthropic)',
      status: cfg.anthropic ? 'Actif' : 'Clé absente',
      statusCls: cfg.anthropic ? 'on' : 'warn',
      desc: 'Compréhension, dialogue et décisions.' },
    { ic: 'AS', name: 'Agent Assist (Nova)',
      status: cfg.assist ? 'Actif' : 'Désactivé',
      statusCls: cfg.assist ? 'on' : '',
      desc: 'Luna comme agent conversationnel de Home Assistant.' },
  ];
  if (cfg.docker) real.push({ ic: 'DK', name: 'Surveillance Docker', status: 'Active · lecture', statusCls: 'on', desc: 'État des conteneurs, mémoire, redémarrage sur proposition.' });
  if (cfg.atrium) real.push({ ic: 'AT', name: 'Atrium', status: 'Surveillé', statusCls: 'on', desc: 'Disponibilité et latence du service.' });
  if (cfg.mail) real.push({ ic: 'GM', name: 'Gmail (lecture seule)', status: 'Connecté', statusCls: 'on', desc: 'Résumé de tes non-lus, pour toi seul. Aucun envoi ni suppression.' });
  if (cfg.web_search) real.push({ ic: 'WB', name: 'Recherche web', status: 'Active', statusCls: 'on', desc: 'Actualité et connaissances externes, avec sources citées.' });
  for (const s of real) grid.appendChild(svcCard(s));

  const soon = [
    { ic: 'SP', name: 'Spotify', desc: 'Lecture, volume, transfert entre pièces.' },
    ...(cfg.mail ? [] : [{ ic: 'GM', name: 'Gmail', desc: 'Résumés de tes non-lus (lecture seule).' }]),
    { ic: 'CA', name: 'Google Agenda', desc: 'Créneaux, invitations, rappels vocaux.' },
    { ic: 'DR', name: 'Google Drive', desc: 'Recherche documentaire et pièces jointes.' },
    { ic: 'NO', name: 'Notion', desc: 'Notes de réunion et base de tâches.' },
    { ic: 'ME', name: 'Météo & trafic', desc: 'Briefing du matin, alertes trajet.' },
  ];
  for (const s of soon) grid.appendChild(svcCard({ ...s, status: 'Bientôt', badge: 'bientôt', soon: true }));
}

function setSettingsSection(name) {
  document.querySelectorAll('.set-navitem').forEach((b) => b.setAttribute('aria-selected', String(b.dataset.sec === name)));
  document.querySelectorAll('.set-sec').forEach((s) => { s.hidden = s.dataset.sec !== name; });
  if (name === 'memoire' && ws.alive) ws.sendJSON({ type: 'memoires' });
  if (name === 'profils' && ws.alive) ws.sendJSON({ type: 'speakers' });
  if (name === 'pages' && ws.alive) ws.sendJSON({ type: 'pages' });
  if (name === 'evolutions' && ws.alive) ws.sendJSON({ type: 'evolutions' });
  if (name === 'proactivite' && ws.alive) ws.sendJSON({ type: 'proactive' });
  if (name === 'routines' && ws.alive) ws.sendJSON({ type: 'routines' });
}
document.querySelectorAll('.set-navitem').forEach((b) => b.addEventListener('click', () => setSettingsSection(b.dataset.sec)));

// ── Mémoire (Paramètres › Mémoire) ────────────────────────────────────────
const MEM_CATS = {
  preference: 'Préférences', habitude: 'Habitudes',
  style: 'Style de langage', fait: 'À savoir',
};

function renderMemoires(msg) {
  const mems = msg.memories || [];
  els.memoireCount.textContent = mems.length ? String(mems.length) : '';
  els.memoireList.textContent = '';
  if (!mems.length) {
    els.memoireList.appendChild(emptyLine('Luna n’a encore rien retenu.'));
    return;
  }
  const byCat = {};
  for (const m of mems) (byCat[m.category] || (byCat[m.category] = [])).push(m);
  for (const [cat, label] of Object.entries(MEM_CATS)) {
    const items = byCat[cat];
    if (!items) continue;
    const head = document.createElement('div');
    head.className = 'mem-cat';
    head.textContent = label;
    els.memoireList.appendChild(head);
    for (const m of items) {
      const row = document.createElement('div');
      row.className = 'mem-row';
      const text = document.createElement('span');
      text.className = 'mem-text';
      text.textContent = m.content;
      const src = document.createElement('span');
      src.className = 'mem-src';
      src.textContent = m.source === 'manuel' ? 'ajouté' : 'appris';
      const del = document.createElement('button');
      del.type = 'button';
      del.className = 'mem-del';
      del.textContent = '✕';
      del.title = 'Oublier ce souvenir';
      del.setAttribute('aria-label', `Oublier : ${m.content}`);
      del.addEventListener('click', () => ws.sendJSON({ type: 'memoire_delete', id: m.id }));
      row.append(text, src, del);
      els.memoireList.appendChild(row);
    }
  }
}

els.memoireForm.addEventListener('submit', (e) => {
  e.preventDefault();
  const content = els.memoireInput.value.trim();
  if (!content || !ws.alive) return;
  ws.sendJSON({ type: 'memoire_add', content, category: els.memoireCat.value });
  els.memoireInput.value = '';
});

// ── Profils vocaux (Paramètres › Profils vocaux) ──────────────────────────
let speakerEnabled = false;
let enrolling = false;

function renderSpeakers(msg) {
  speakerEnabled = msg.enabled !== false;
  const list = msg.speakers || [];
  els.speakerCount.textContent = list.length ? String(list.length) : '';
  els.speakerList.textContent = '';
  if (!speakerEnabled) {
    els.speakerList.appendChild(emptyLine('Reconnaissance désactivée (SPEAKER_HOST vide). Luna traite tout le monde comme le propriétaire.'));
    return;
  }
  if (!list.length) {
    els.speakerList.appendChild(emptyLine('Aucune voix enrôlée. Crée un profil, puis enregistre quelques échantillons.'));
    return;
  }
  for (const s of list) {
    const row = document.createElement('div');
    row.className = 'mem-row';
    const name = document.createElement('span');
    name.className = 'mem-text';
    const n = s.samples || 0;
    name.textContent = `${s.name} · ${n} échantillon${n > 1 ? 's' : ''}`;
    row.appendChild(name);
    if (s.is_owner) {
      const b = document.createElement('span');
      b.className = 'mem-badge';
      b.textContent = 'propriétaire';
      row.appendChild(b);
    }
    const enroll = document.createElement('button');
    enroll.type = 'button';
    enroll.className = 'mem-enroll';
    enroll.textContent = '🎙 échantillon';
    enroll.addEventListener('click', () => enrollSample(s.id, enroll));
    const del = document.createElement('button');
    del.type = 'button';
    del.className = 'mem-del';
    del.textContent = '✕';
    del.title = 'Supprimer ce profil';
    del.setAttribute('aria-label', `Supprimer ${s.name}`);
    del.addEventListener('click', () => ws.sendJSON({ type: 'speaker_delete', id: s.id }));
    row.append(enroll, del);
    els.speakerList.appendChild(row);
  }
}

async function enrollSample(id, btn) {
  if (enrolling || !ws.alive) return;
  if (!speakerEnabled) { toast('La reconnaissance de locuteur n’est pas activée.'); return; }
  try { await ensureCapture(); }
  catch { toast('Micro indisponible : vérifie l’autorisation du navigateur et l’accès HTTPS.'); return; }
  enrolling = true;
  if (st.wakeStreaming) { st.wakeStreaming = false; ws.sendJSON({ type: 'wake_stop' }); }
  btn.classList.add('rec');
  els.speakerState.textContent = 'Parle maintenant… (environ 3 secondes)';
  ws.sendJSON({ type: 'speaker_enroll_start', id, rate: 16000 });
  capture.start();
  await new Promise((r) => setTimeout(r, 3200));
  capture.stop();
  ws.sendJSON({ type: 'speaker_enroll_end' });
  btn.classList.remove('rec');
  enrolling = false;
}

function onEnrollResult(msg) {
  els.speakerState.textContent = msg.text || (msg.ok ? 'Échantillon enregistré.' : 'Enrôlement impossible.');
  toast(msg.text || (msg.ok ? 'Échantillon enregistré.' : 'Enrôlement impossible.'));
}

function updateWhoSpeaks(msg) {
  if (!msg || !msg.label) { els.whoSpeaks.hidden = true; return; }
  els.whoSpeaks.hidden = false;
  els.whoSpeaks.textContent = msg.known ? `🎙 ${msg.name || msg.label}` : '🎙 invité';
  els.whoSpeaks.classList.toggle('guest', !msg.known);
}

els.speakerForm.addEventListener('submit', (e) => {
  e.preventDefault();
  const name = els.speakerName.value.trim();
  if (!name || !ws.alive) return;
  ws.sendJSON({ type: 'speaker_add', name, is_owner: els.speakerOwner.checked });
  els.speakerName.value = '';
  els.speakerOwner.checked = false;
});

// ── Pages web (Paramètres › Pages web) : Luna rédige, tu relis et publies ───
function pageBtn(label, onClick, danger) {
  const b = document.createElement('button');
  b.type = 'button';
  b.textContent = label;
  if (danger) b.className = 'danger';
  b.addEventListener('click', onClick);
  return b;
}

function renderPages(msg) {
  const list = msg.pages || [];
  els.pagesCount.textContent = list.length ? String(list.length) : '';
  els.pagesList.textContent = '';
  if (!list.length) {
    els.pagesList.appendChild(emptyLine('Aucune page. Demande à Luna : « prépare-moi une page de suivi pour… ».'));
    return;
  }
  for (const p of list) {
    const row = document.createElement('div');
    row.className = 'page-row';
    const main = document.createElement('div');
    main.className = 'page-main';
    const title = document.createElement('div');
    title.className = 'page-title';
    title.textContent = p.title;
    const meta = document.createElement('div');
    meta.className = 'page-meta';
    const st = document.createElement('span');
    st.className = 'page-state' + (p.published ? ' on' : '');
    st.textContent = p.published ? 'publiée' : 'brouillon';
    meta.appendChild(st);
    if (p.dirty) {
      const d = document.createElement('span');
      d.className = 'page-state dirty';
      d.textContent = 'modifs en attente';
      meta.appendChild(d);
    }
    if (p.published) {
      const a = document.createElement('a');
      a.className = 'page-link';
      a.href = `/p/${p.slug}`;
      a.target = '_blank';
      a.rel = 'noopener noreferrer';
      a.textContent = `/p/${p.slug}`;
      meta.appendChild(a);
    }
    main.append(title, meta);

    const acts = document.createElement('div');
    acts.className = 'page-acts';
    acts.appendChild(pageBtn('Aperçu', () => ws.sendJSON({ type: 'page_get', id: p.id })));
    const pub = pageBtn(p.published ? 'Republier' : 'Publier', () => {
      ws.sendJSON({ type: 'page_publish', id: p.id });
      toast('Page mise en ligne.');
    });
    if (p.published && !p.dirty) { pub.disabled = true; pub.textContent = 'à jour'; }
    acts.appendChild(pub);
    if (p.published) {
      acts.appendChild(pageBtn('Dépublier', () => {
        ws.sendJSON({ type: 'page_unpublish', id: p.id });
        toast('Page retirée du réseau.');
      }));
    }
    acts.appendChild(pageBtn('Supprimer', () => {
      if (confirm(`Supprimer définitivement la page « ${p.title} » ?`)) {
        ws.sendJSON({ type: 'page_delete', id: p.id });
      }
    }, true));
    row.append(main, acts);
    els.pagesList.appendChild(row);
  }
}

let previewPage = null;
function showPagePreview(msg) {
  previewPage = { id: msg.id, slug: msg.slug, published: msg.published };
  els.ppTitle.textContent = msg.title || 'Aperçu';
  els.ppFrame.srcdoc = msg.html || '';
  const url = `${location.origin}/p/${msg.slug}`;
  els.ppUrl.textContent = msg.published ? url : 'brouillon — non publié';
  els.ppOpen.hidden = !msg.published;
  els.ppOpen.onclick = () => window.open(url, '_blank', 'noopener');
  els.ppPublish.textContent = msg.published ? 'Republier' : 'Publier';
  els.pagePreview.hidden = false;
}
function closePagePreview() {
  els.pagePreview.hidden = true;
  els.ppFrame.srcdoc = '';
  previewPage = null;
}
els.ppPublish.addEventListener('click', () => {
  if (!previewPage) return;
  ws.sendJSON({ type: 'page_publish', id: previewPage.id });
  toast('Page mise en ligne.');
  closePagePreview();
});
els.ppClose.addEventListener('click', closePagePreview);

// ── Auto-amélioration (Paramètres › Évolutions) ───────────────────────────
const EVO_STATUS = { pending: 'en attente', accepted: 'acceptée', rejected: 'rejetée' };

function renderEvolutions(msg) {
  const list = msg.suggestions || [];
  els.evolutionsCount.textContent = list.length ? String(list.length) : '';
  els.evolutionsList.textContent = '';
  if (!list.length) {
    els.evolutionsList.appendChild(emptyLine('Aucune proposition. Demande à Luna : « propose une amélioration de… » — elle prépare un diff que tu relis ici.'));
    return;
  }
  for (const s of list) {
    const row = document.createElement('div');
    row.className = 'evo-row';
    const main = document.createElement('div');
    main.className = 'evo-main';
    const title = document.createElement('div');
    title.className = 'evo-title';
    title.textContent = s.title;
    const meta = document.createElement('div');
    meta.className = 'evo-meta';
    const kind = document.createElement('span');
    kind.className = 'evo-kind ' + (s.kind === 'config' ? 'config' : 'code');
    kind.textContent = s.kind === 'config' ? 'config' : 'code';
    meta.appendChild(kind);
    const stt = document.createElement('span');
    stt.className = 'evo-state ' + s.status;
    stt.textContent = EVO_STATUS[s.status] || s.status;
    meta.appendChild(stt);
    if (s.target) {
      const tg = document.createElement('span');
      tg.className = 'evo-target-chip';
      tg.textContent = s.target;
      meta.appendChild(tg);
    }
    main.append(title, meta);

    const acts = document.createElement('div');
    acts.className = 'evo-acts';
    acts.appendChild(pageBtn('Relire le diff', () => ws.sendJSON({ type: 'evolution_get', id: s.id })));
    acts.appendChild(pageBtn('Supprimer', () => {
      if (confirm(`Supprimer la proposition « ${s.title} » ?`)) ws.sendJSON({ type: 'evolution_delete', id: s.id });
    }, true));
    row.append(main, acts);
    els.evolutionsList.appendChild(row);
  }
}

let reviewEvo = null;
function colorizeDiff(pre, diff) {
  pre.textContent = '';
  for (const line of (diff || '').split('\n')) {
    const el = document.createElement('span');
    el.className = 'dl';
    if (line.startsWith('+++') || line.startsWith('---') || line.startsWith('diff ') || line.startsWith('index ')) el.classList.add('dl-meta');
    else if (line.startsWith('@@')) el.classList.add('dl-hunk');
    else if (line.startsWith('+')) el.classList.add('dl-add');
    else if (line.startsWith('-')) el.classList.add('dl-del');
    el.textContent = line || ' ';
    pre.appendChild(el);
  }
}

function showEvolution(msg) {
  reviewEvo = { id: msg.id, status: msg.status };
  els.evoTitle.textContent = msg.title || 'Proposition';
  const files = (msg.paths || []).join(', ') || msg.target || '';
  els.evoTarget.textContent = `${files}  ·  +${msg.added || 0} −${msg.removed || 0}`;
  els.evoWhy.textContent = msg.rationale || '';
  els.evoWhy.hidden = !msg.rationale;
  colorizeDiff(els.evoDiff, msg.diff);
  const decided = msg.status !== 'pending';
  els.evoAccept.hidden = decided;
  els.evoReject.hidden = decided;
  els.evoAccept.textContent = 'Accepter';
  els.evoReview.hidden = false;
}
function closeEvolution() {
  els.evoReview.hidden = true;
  els.evoDiff.textContent = '';
  reviewEvo = null;
}
els.evoAccept.addEventListener('click', () => {
  if (!reviewEvo) return;
  ws.sendJSON({ type: 'evolution_accept', id: reviewEvo.id });
  toast('Proposition acceptée — applique le diff quand tu veux.');
  closeEvolution();
});
els.evoReject.addEventListener('click', () => {
  if (!reviewEvo) return;
  ws.sendJSON({ type: 'evolution_reject', id: reviewEvo.id });
  toast('Proposition rejetée.');
  closeEvolution();
});
els.evoClose.addEventListener('click', closeEvolution);

// ── Proactivité (tiroir Suggestions + Paramètres › Proactivité) ───────────
const PROACTIVE_RULES = {
  ouverture_nuit: 'Porte / fenêtre ouverte la nuit',
  volet_nuit: 'Volet / garage ouvert le soir',
  absence_appareils: 'Personne à la maison + appareils',
  fenetre_chauffage: 'Fenêtre ouverte + chauffage',
  lumiere_tard: 'Lumières allumées tard',
  temperature: 'Température inconfortable',
};
const PROACTIVE_CAT = { securite: 'sécurité', confort: 'confort', energie: 'énergie' };
let lastProactive = { suggestions: [], muted: [] };

function renderProactive(msg) {
  lastProactive = { suggestions: msg.suggestions || [], muted: msg.muted || [] };
  const items = lastProactive.suggestions;
  const available = !!(lastHello && lastHello.config && lastHello.config.proactive);
  els.proactiveCount.textContent = String(items.length);
  els.proactiveBtn.hidden = !available && drawers.proactive.hidden;
  els.proactiveBtn.classList.toggle('attention', items.length > 0);

  els.proactiveBody.textContent = '';
  if (!items.length) {
    els.proactiveBody.appendChild(emptyLine('Rien à signaler. Luna veille sur la maison.'));
  } else {
    for (const s of items) els.proactiveBody.appendChild(proactiveCard(s));
  }
  renderMutes(lastProactive.muted);
}

function proactiveCard(s) {
  const card = document.createElement('div');
  card.className = `pro-card ${s.severity || 'info'}`;
  const head = document.createElement('div');
  head.className = 'pro-head';
  const cat = document.createElement('span');
  cat.className = `pro-cat ${s.category || ''}`;
  cat.textContent = PROACTIVE_CAT[s.category] || s.category || 'info';
  const title = document.createElement('div');
  title.className = 'pro-title';
  title.textContent = s.title;
  head.append(cat, title);
  const detail = document.createElement('div');
  detail.className = 'pro-detail';
  detail.textContent = s.detail || '';
  card.append(head, detail);

  const acts = document.createElement('div');
  acts.className = 'pro-acts';
  if (s.action) {
    const prep = pageBtn('Préparer la proposition', () => {
      ws.sendJSON({ type: 'proactive_make_proposal', id: s.id });
      toast('Proposition préparée — à approuver dans les propositions.');
    });
    prep.classList.add('primary');
    acts.appendChild(prep);
  }
  acts.appendChild(pageBtn('Plus tard', () => ws.sendJSON({ type: 'proactive_snooze', id: s.id })));
  acts.appendChild(pageBtn('Ignorer', () => ws.sendJSON({ type: 'proactive_dismiss', id: s.id })));
  acts.appendChild(pageBtn('Ne plus suggérer ça', () => {
    ws.sendJSON({ type: 'proactive_mute', rule: s.rule });
    toast('Compris — je ne te le suggérerai plus.');
  }, true));
  card.appendChild(acts);
  return card;
}

function renderMutes(muted) {
  if (!els.mutesList) return;
  els.mutesCount.textContent = muted.length ? String(muted.length) : '';
  els.mutesList.textContent = '';
  if (!muted.length) {
    els.mutesList.appendChild(emptyLine('Aucune règle tue — Luna te suggère tout ce qu\'elle observe.'));
    return;
  }
  for (const rule of muted) {
    const row = document.createElement('div');
    row.className = 'mem-row';
    const label = document.createElement('span');
    label.className = 'mem-text';
    label.textContent = PROACTIVE_RULES[rule] || rule;
    const btn = pageBtn('Réactiver', () => ws.sendJSON({ type: 'proactive_unmute', rule }));
    row.append(label, btn);
    els.mutesList.appendChild(row);
  }
}

els.proactiveBtn.addEventListener('click', () => openDrawer('proactive'));

// ── Routines (Paramètres › Routines) ──────────────────────────────────────
const ROUTINE_SOURCE = { appris: 'appris', llm: 'suggéré', manuel: 'manuel' };

function renderRoutines(msg) {
  const list = msg.routines || [];
  const proposed = list.filter((r) => r.status === 'proposed');
  const active = list.filter((r) => r.status === 'active');
  els.routinesProposedCount.textContent = proposed.length ? String(proposed.length) : '';
  els.routinesActiveCount.textContent = active.length ? String(active.length) : '';

  els.routinesProposed.textContent = '';
  if (!proposed.length) {
    els.routinesProposed.appendChild(emptyLine('Aucune proposition. Dis à Luna « fais-en une routine », ou laisse-la repérer tes habitudes.'));
  } else {
    for (const r of proposed) els.routinesProposed.appendChild(routineRow(r, 'proposed'));
  }
  els.routinesActive.textContent = '';
  if (!active.length) {
    els.routinesActive.appendChild(emptyLine('Aucune routine active. Active une proposition ci-dessus.'));
  } else {
    for (const r of active) els.routinesActive.appendChild(routineRow(r, 'active'));
  }
}

function stepsChips(steps) {
  const wrap = document.createElement('div');
  wrap.className = 'routine-steps';
  const labels = (steps || []).map((s) => s.label || s.action_id);
  labels.slice(0, 5).forEach((l) => {
    const c = document.createElement('span');
    c.className = 'routine-step';
    c.textContent = l;
    wrap.appendChild(c);
  });
  if (labels.length > 5) {
    const more = document.createElement('span');
    more.className = 'routine-step more';
    more.textContent = `+${labels.length - 5}`;
    wrap.appendChild(more);
  }
  return wrap;
}

function routineRow(r, kind) {
  const row = document.createElement('div');
  row.className = 'routine-row';
  const main = document.createElement('div');
  main.className = 'routine-main';
  const head = document.createElement('div');
  head.className = 'routine-head';
  const name = document.createElement('span');
  name.className = 'routine-name';
  name.textContent = r.name;
  head.appendChild(name);
  if (kind === 'proposed') {
    const src = document.createElement('span');
    src.className = 'routine-src';
    src.textContent = ROUTINE_SOURCE[r.source] || r.source;
    head.appendChild(src);
  } else if (r.run_count) {
    const rc = document.createElement('span');
    rc.className = 'routine-runs';
    rc.textContent = `lancée ${r.run_count}×`;
    head.appendChild(rc);
  }
  main.append(head, stepsChips(r.steps));
  if (r.description && kind === 'proposed') {
    const d = document.createElement('div');
    d.className = 'routine-desc';
    d.textContent = r.description;
    main.appendChild(d);
  }

  const acts = document.createElement('div');
  acts.className = 'routine-acts';
  if (kind === 'proposed') {
    const ok = pageBtn('Activer', () => { ws.sendJSON({ type: 'routine_approve', id: r.id }); toast('Routine activée.'); });
    ok.classList.add('primary');
    acts.appendChild(ok);
    acts.appendChild(pageBtn('Rejeter', () => ws.sendJSON({ type: 'routine_reject', id: r.id }), true));
  } else {
    const run = pageBtn('Lancer', () => ws.sendJSON({ type: 'routine_run', id: r.id }));
    run.classList.add('primary');
    acts.appendChild(run);
    acts.appendChild(pageBtn('Renommer', () => {
      const name = prompt('Nouveau nom de la routine :', r.name);
      if (name && name.trim()) ws.sendJSON({ type: 'routine_rename', id: r.id, name: name.trim() });
    }));
    acts.appendChild(pageBtn('Supprimer', () => {
      if (confirm(`Supprimer la routine « ${r.name} » ?`)) ws.sendJSON({ type: 'routine_delete', id: r.id });
    }, true));
  }
  row.append(main, acts);
  return row;
}

// ── Musique (tiroir ♫ Musique) ────────────────────────────────────────────
let mediaBusy = false;        // vrai pendant qu'on manipule un curseur
let pendingMedia = null;      // dernier état reçu à appliquer après manipulation
let lastMediaPlayers = [];

function mediaCmd(entity, op, extra) {
  ws.sendJSON({ type: 'media_control', op, entity_ids: [entity], ...(extra || {}) });
}

function renderMedia(msg) {
  if (msg && msg.players) lastMediaPlayers = msg.players;
  if (mediaBusy) { pendingMedia = msg; return; }   // ne pas casser un curseur en cours
  const players = lastMediaPlayers;
  els.mediaBody.textContent = '';
  if (!players.length) {
    els.mediaBody.appendChild(emptyLine('Rien ne joue. Demande à Luna : « mets de la musique dans le salon ».'));
    return;
  }
  for (const p of players) els.mediaBody.appendChild(mediaCard(p));
}

function tBtn(label, title, onClick) {
  const b = document.createElement('button');
  b.type = 'button';
  b.className = 'media-t';
  b.textContent = label;
  b.title = title;
  b.addEventListener('click', onClick);
  return b;
}

function mediaCard(p) {
  const card = document.createElement('div');
  card.className = 'media-card';
  const head = document.createElement('div');
  head.className = 'media-head';
  const nm = document.createElement('span');
  nm.className = 'media-name';
  nm.textContent = p.nom;
  head.appendChild(nm);
  if (p.piece) {
    const rm = document.createElement('span');
    rm.className = 'media-room';
    rm.textContent = p.piece;
    head.appendChild(rm);
  }
  card.appendChild(head);

  const now = document.createElement('div');
  now.className = 'media-now';
  now.textContent = p.titre ? (p.artiste ? `${p.titre} — ${p.artiste}` : p.titre)
    : (p.source ? p.source : '—');
  card.appendChild(now);

  // Transport
  const transport = document.createElement('div');
  transport.className = 'media-transport';
  transport.appendChild(tBtn('⏮', 'Précédent', () => mediaCmd(p.entity_id, 'previous')));
  transport.appendChild(tBtn(p.joue ? '⏸' : '▶', p.joue ? 'Pause' : 'Lecture',
    () => mediaCmd(p.entity_id, p.joue ? 'pause' : 'play')));
  transport.appendChild(tBtn('⏭', 'Suivant', () => mediaCmd(p.entity_id, 'next')));
  transport.appendChild(tBtn(p.coupe ? '🔇' : '🔈', p.coupe ? 'Rétablir le son' : 'Couper le son',
    () => mediaCmd(p.entity_id, p.coupe ? 'unmute' : 'mute')));
  card.appendChild(transport);

  // Volume
  if (p.volume != null) {
    const vol = document.createElement('div');
    vol.className = 'media-vol';
    const slider = document.createElement('input');
    slider.type = 'range';
    slider.min = '0'; slider.max = '100'; slider.value = String(p.volume);
    slider.setAttribute('aria-label', `Volume ${p.nom}`);
    slider.addEventListener('input', () => { mediaBusy = true; });
    slider.addEventListener('change', () => {
      mediaCmd(p.entity_id, 'volume', { level: Number(slider.value) / 100 });
      setTimeout(() => { mediaBusy = false; if (pendingMedia) { const m = pendingMedia; pendingMedia = null; renderMedia(m); } }, 600);
    });
    vol.append(slider);
    card.appendChild(vol);
  }

  // Source
  if (p.sources && p.sources.length) {
    const sel = document.createElement('select');
    sel.className = 'media-source';
    sel.setAttribute('aria-label', `Source ${p.nom}`);
    for (const s of p.sources) {
      const o = document.createElement('option');
      o.value = s; o.textContent = s;
      if (s === p.source) o.selected = true;
      sel.appendChild(o);
    }
    sel.addEventListener('change', () => mediaCmd(p.entity_id, 'source', { source: sel.value }));
    card.appendChild(sel);
  }

  // Transfert multi-pièces : rejoindre un autre lecteur
  const others = lastMediaPlayers.filter((o) => o.entity_id !== p.entity_id);
  if (others.length) {
    const sel = document.createElement('select');
    sel.className = 'media-transfer';
    const def = document.createElement('option');
    def.value = ''; def.textContent = '＋ diffuser aussi dans…';
    sel.appendChild(def);
    for (const o of others) {
      const opt = document.createElement('option');
      opt.value = o.entity_id; opt.textContent = o.piece || o.nom;
      sel.appendChild(opt);
    }
    sel.addEventListener('change', () => {
      if (sel.value) { mediaCmd(p.entity_id, 'join', { group_members: [sel.value] }); toast('Musique diffusée aussi dans l’autre pièce.'); sel.value = ''; }
    });
    card.appendChild(sel);
  }
  return card;
}

// ── Veille au mot d'éveil ─────────────────────────────────────────────────
let wakeRetryTimer = null;
let gestureHooked = false;

function renderWakeBtn() {
  els.wakeBtn.classList.toggle('armed', st.wakeArmed);
  els.wakeBtn.title = st.wakeArmed
    ? `Veille active — dis « ${wakeWord} » (cliquer pour couper)`
    : `Activer la veille au mot d'éveil (« ${wakeWord} »)`;
  els.setWakeToggle.setAttribute('aria-checked', String(st.wakeArmed));
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

function toggleWake() { st.wakeArmed = !st.wakeArmed; saveWakePref(); renderWakeBtn(); syncWake(); }
els.wakeBtn.addEventListener('click', toggleWake);
els.setWakeToggle.addEventListener('click', () => { if (!els.setWakeToggle.disabled) toggleWake(); });
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

// ── Niveau audio → orbe (voix de lecture + capture micro) ─────────────────
// L'orbe WebGL porte seule l'état visuel ; on lui pousse juste l'amplitude.
let orbLevelActive = false;
(function pump() {
  if (player && player.playing) { orb.level(player.level()); orbLevelActive = true; }
  else if (st.listening) { orb.level(liveLevel); orbLevelActive = true; }
  else if (orbLevelActive) { orb.release(); orbLevelActive = false; }
  requestAnimationFrame(pump);
})();

// ── Interactions ──────────────────────────────────────────────────────────
els.mic.addEventListener('click', micAction);
els.orbMic.addEventListener('click', micAction);
els.orbTap.addEventListener('click', micAction);
els.interrupt.addEventListener('click', () => { if (st.listening) stopListening(false); interrupt(); });

// Interrupteur « Luna répond à voix haute » (vaut aussi quand on écrit)
function renderVoiceReply() {
  els.voiceReply.setAttribute('aria-checked', String(voiceReply));
  els.voiceReply.title = voiceReply
    ? 'Luna répond à voix haute (cliquer pour couper le son)'
    : 'Luna répond en silence (cliquer pour réactiver la voix)';
}
els.voiceReply.addEventListener('click', () => {
  voiceReply = !voiceReply;
  try { localStorage.setItem('sentinel-voice-reply', voiceReply ? '1' : '0'); } catch { /* privé */ }
  if (voiceReply) ensureAudio().catch(() => {});  // débloque le haut-parleur du même geste
  else if (player) player.stop();                 // coupe la voix déjà en cours
  renderVoiceReply();
});

els.composer.addEventListener('submit', (e) => {
  e.preventDefault();
  const text = els.input.value.trim();
  if (!text || !ws.alive) return;
  // Débloque le haut-parleur du navigateur sur ce geste (nécessaire pour
  // entendre la réponse de Luna quand on écrit au lieu de parler) et demande
  // au serveur de PARLER la réponse (`speak`) : Luna est une assistante vocale,
  // elle répond à voix haute même quand on lui écrit — sauf si on l'a mise en
  // sourdine (bouton haut-parleur).
  ensureAudio().catch(() => {});
  ws.sendJSON({ type: 'chat', text, speak: voiceReply });
  els.input.value = '';
});

document.addEventListener('keydown', (e) => {
  if (e.code === 'Space' && document.activeElement !== els.input && !e.repeat) {
    e.preventDefault();
    micAction();
  } else if (e.key === 'Escape') {
    if (!els.evoReview.hidden) { closeEvolution(); return; }
    if (!els.pagePreview.hidden) { closePagePreview(); return; }
    if (anyDrawerOpen()) { closeDrawers(); return; }
    if (st.listening) stopListening(false);
    interrupt();
  }
});

// Débloque le haut-parleur du navigateur au tout premier geste (clic ou touche),
// où qu'il soit : la voix de Luna se joue alors même si on n'a jamais touché le
// micro (cas de l'écrit, et de l'accès http où le micro est bloqué).
(function unlockAudioOnFirstGesture() {
  const h = () => {
    document.removeEventListener('pointerdown', h);
    document.removeEventListener('keydown', h);
    ensureAudio().catch(() => {});
  };
  document.addEventListener('pointerdown', h);
  document.addEventListener('keydown', h);
})();

// ── Démarrage ─────────────────────────────────────────────────────────────
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('sw.js').catch(() => {});
}

setView('cockpit');
renderVoiceReply();
updateChatMeta();
refreshUi();
ws.connect();
