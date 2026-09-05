/* Sentinel — application cliente : états, micro, lecture, chat, clavier. */

import { WSClient } from './ws.js';
import { Capture } from './audio-capture.js';
import { Player } from './audio-play.js';
import { Thread } from './chat.js';
import { Viz } from './viz.js';

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
  mic: document.getElementById('mic'),
  viz: document.getElementById('viz'),
  stateLabel: document.getElementById('state-label'),
  connLabel: document.getElementById('conn-label'),
  thread: document.getElementById('thread'),
  composer: document.getElementById('composer'),
  input: document.getElementById('text-input'),
  toast: document.getElementById('toast'),
  connNova: document.getElementById('conn-nova'),
  proposalsBtn: document.getElementById('proposals-btn'),
  propCount: document.getElementById('prop-count'),
  alertBanner: document.getElementById('alert-banner'),
  alertText: document.getElementById('alert-text'),
  alertClose: document.getElementById('alert-close'),
  panel: document.getElementById('proposals-panel'),
  proposalsList: document.getElementById('proposals-list'),
  atelierBtn: document.getElementById('atelier-btn'),
  devLive: document.getElementById('dev-live'),
  santeBtn: document.getElementById('sante-btn'),
  historyBtn: document.getElementById('history-btn'),
  atelierStatus: document.getElementById('atelier-status'),
  atelierTasks: document.getElementById('atelier-tasks'),
  atelierToolbar: document.getElementById('atelier-toolbar'),
  atelierBranch: document.getElementById('atelier-branch'),
  atelierViewBtn: document.getElementById('atelier-view-btn'),
  atelierLog: document.getElementById('atelier-log'),
  atelierDiff: document.getElementById('atelier-diff'),
  atelierEmpty: document.getElementById('atelier-empty'),
  santeBody: document.getElementById('sante-body'),
  historyBody: document.getElementById('history-body'),
};

const panels = {
  proposals: document.getElementById('proposals-panel'),
  atelier: document.getElementById('atelier-panel'),
  sante: document.getElementById('sante-panel'),
  history: document.getElementById('history-panel'),
};

const ws = new WSClient();
const thread = new Thread(els.thread);
const viz = new Viz(els.viz);

const st = {
  server: 'idle',   // état diffusé par le serveur
  listening: false, // capture micro locale en cours
  pendingEnd: null, // true → audio_end, false → audio_cancel (après flush du worklet)
  hadVoice: false,
  lastVoice: 0,
  startedAt: 0,
  proposals: new Map(), // num → proposition (pending/deferred)
};

let audioCtx = null;
let player = null;
let capture = null;
let toastTimer = null;

// ── Rendu de l'état ─────────────────────────────────────────────────────

function displayState() {
  if (!ws.alive) return 'offline';
  if (st.listening) return 'listening';
  // « listening » d'un autre appareil : on reste visuellement en veille ici
  if (st.server === 'listening') return 'idle';
  return st.server;
}

function refreshUi() {
  const s = displayState();
  document.body.dataset.state = s;
  els.stateLabel.textContent = LABELS[s] || s;
  viz.setMode(s);
}

function toast(text) {
  els.toast.textContent = text;
  els.toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { els.toast.hidden = true; }, 4200);
}

// ── Audio (créé au premier geste utilisateur) ───────────────────────────

async function ensureAudio() {
  if (!audioCtx) {
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    player = new Player(audioCtx);
  }
  if (audioCtx.state === 'suspended') await audioCtx.resume();
}

async function ensureCapture() {
  await ensureAudio();
  if (capture) return;
  const cap = new Capture();
  await cap.init(audioCtx); // si ça échoue, `capture` reste null → nouvel essai possible

  cap.addEventListener('chunk', (e) => ws.sendBytes(e.detail));

  cap.addEventListener('flushed', () => {
    if (st.pendingEnd === null) return;
    ws.sendJSON({ type: st.pendingEnd ? 'audio_end' : 'audio_cancel' });
    st.pendingEnd = null;
  });

  cap.addEventListener('level', (e) => {
    const { value, active } = e.detail;
    if (!active || !st.listening) return;
    viz.setLevel(value);
    const now = performance.now();
    if (value > VOICE_THRESHOLD) {
      st.hadVoice = true;
      st.lastVoice = now;
    }
    const total = now - st.startedAt;
    if (st.hadVoice && now - st.lastVoice > SILENCE_MS) {
      stopListening(true);
    } else if (!st.hadVoice && total > NO_VOICE_MS) {
      stopListening(false);
      toast('Je n’ai rien entendu.');
    } else if (total > HARD_CAP_MS) {
      stopListening(true);
    }
  });

  capture = cap; // publié seulement une fois l'initialisation réussie
}

// ── Prise de parole ─────────────────────────────────────────────────────

async function startListening() {
  if (st.listening || !ws.alive) return;
  try {
    await ensureCapture();
  } catch (err) {
    console.error(err);
    toast('Micro indisponible : vérifie l’autorisation du navigateur et l’accès HTTPS.');
    return;
  }
  if (player) player.stop(); // couper Sentinel s'il parlait
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
  st.pendingEnd = send; // l'envoi part quand le worklet a vidé son tampon
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

// ── Événements serveur ──────────────────────────────────────────────────

ws.addEventListener('open', () => {
  document.body.classList.add('online');
  els.connLabel.textContent = 'en ligne';
  refreshUi();
});

ws.addEventListener('close', () => {
  document.body.classList.remove('online');
  els.connLabel.textContent = 'hors ligne';
  if (st.listening) { st.listening = false; capture && capture.stop(); }
  refreshUi();
});

ws.addEventListener('audio', (e) => { if (player) player.push(e.detail); });

ws.addEventListener('event', (e) => {
  const msg = e.detail;
  switch (msg.type) {
    case 'hello':
      thread.clear();
      (msg.history || []).forEach((m) => thread.addMessage(m));
      st.server = msg.state || 'idle';
      els.connNova.hidden = !msg.ha_configured;
      setNova(!!msg.ha_connected);
      els.atelierBtn.hidden = !msg.dev_configured;
      setDevRunning(msg.dev_running || null);
      st.proposals.clear();
      (msg.proposals || []).forEach((p) => st.proposals.set(p.num, p));
      renderProposals();
      refreshUi();
      break;
    case 'ha_status':
      setNova(!!msg.connected);
      break;
    case 'activity':
      // Pendant la réflexion : montre ce que Sentinel fait (« consulte Nova… »)
      if (st.server === 'thinking') els.stateLabel.textContent = msg.text;
      break;
    case 'alert':
      showAlert(msg.level || 'info', msg.text || '');
      break;
    case 'proposal_new':
      upsertProposal(msg.proposal);
      toast(`Nouvelle proposition n°${msg.proposal.num} : ${msg.proposal.title}`);
      break;
    case 'proposal_update':
      upsertProposal(msg.proposal);
      break;
    case 'status':
      st.server = msg.state;
      refreshUi();
      break;
    case 'message':
      thread.addMessage(msg.message);
      break;
    case 'assistant_start':
      thread.startStream(msg.id);
      break;
    case 'assistant_delta':
      thread.addDelta(msg.id, msg.text);
      break;
    case 'assistant_end':
      thread.endStream(msg.id, msg.message, msg.cancelled);
      break;
    case 'speak_start':
      if (player) player.begin(msg.rate);
      break;
    case 'speak_end':
      if (player) player.end();
      break;
    case 'notice':
      thread.notice(msg.text);
      break;
    case 'error':
      thread.error(msg.text);
      toast(msg.text);
      break;
    case 'dev_status':
      setDevRunning(msg.running);
      break;
    case 'dev_tasks':
      onDevTasks(msg);
      break;
    case 'dev_log':
      onDevLog(msg);
      break;
    case 'dev_diff':
      onDevDiff(msg);
      break;
    case 'sante':
      renderSante(msg);
      break;
    case 'historique':
      renderHistory(msg);
      break;
    default:
      break;
  }
});

// ── Nova, alertes, propositions ─────────────────────────────────────────

function setNova(connected) {
  document.body.classList.toggle('nova-on', connected);
  els.connNova.title = connected ? 'Nova connectée' : 'Nova déconnectée';
}

let alertTimer = null;
function showAlert(level, text) {
  els.alertText.textContent = text;
  els.alertBanner.className = `alert-banner ${level}`;
  els.alertBanner.hidden = false;
  clearTimeout(alertTimer);
  if (level !== 'critical') {
    alertTimer = setTimeout(() => { els.alertBanner.hidden = true; }, 10000);
  }
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
  els.proposalsBtn.hidden = items.length === 0 && els.panel.hidden;
  els.proposalsBtn.classList.toggle('attention', items.length > 0);

  els.proposalsList.textContent = '';
  if (!items.length) {
    const empty = document.createElement('p');
    empty.className = 'panel-empty';
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
      btn.addEventListener('click', () => {
        ws.sendJSON({ type: 'proposal_decision', id: p.num, decision });
      });
      actions.appendChild(btn);
    }
    card.appendChild(actions);
    els.proposalsList.appendChild(card);
  }
}

// ── Panneaux latéraux (un seul ouvert à la fois) ────────────────────────

function anyPanelOpen() {
  return Object.values(panels).some((p) => !p.hidden);
}

function openPanel(name) {
  for (const [key, el] of Object.entries(panels)) el.hidden = key !== name;
  panelsChanged();
}

function closePanels() {
  for (const el of Object.values(panels)) el.hidden = true;
  panelsChanged();
}

function panelsChanged() {
  renderProposals(); // la visibilité du chip propositions dépend du panneau
  setAtelierPolling(!panels.atelier.hidden);
  setSantePolling(!panels.sante.hidden);
  if (!panels.history.hidden) ws.sendJSON({ type: 'historique' });
}

els.proposalsBtn.addEventListener('click', () => openPanel('proposals'));
els.atelierBtn.addEventListener('click', () => openPanel('atelier'));
els.santeBtn.addEventListener('click', () => openPanel('sante'));
els.historyBtn.addEventListener('click', () => openPanel('history'));
document.querySelectorAll('.panel-x').forEach((btn) => btn.addEventListener('click', closePanels));

// ── Console de l'atelier de développement ───────────────────────────────

const DEV_STATUS_FR = { queued: 'en file', running: 'en cours', done: 'terminée', failed: 'échec' };

const dev = {
  tasks: [],        // dernière liste reçue (plus récente d'abord)
  selected: null,   // id de la tâche affichée
  next: 0,          // curseur de lecture incrémentale du journal
  showDiff: false,
  tasksTimer: null,
  logTimer: null,
  workerDown: false, // dernier dev_tasks en erreur → on suspend le sondage du journal
  logPendingAt: 0,   // requête de journal en vol (anti-doublons, expire après 8 s)
};

function setAtelierPolling(on) {
  clearInterval(dev.tasksTimer); dev.tasksTimer = null;
  clearInterval(dev.logTimer); dev.logTimer = null;
  if (!on || els.atelierBtn.hidden) return;
  ws.sendJSON({ type: 'dev_tasks' });
  // Rattrapage à la réouverture : la fin du journal a pu arriver panneau fermé
  if (dev.selected) ws.sendJSON({ type: 'dev_log', id: dev.selected, after: dev.next });
  dev.tasksTimer = setInterval(() => ws.sendJSON({ type: 'dev_tasks' }), 5000);
  dev.logTimer = setInterval(() => {
    if (dev.workerDown) return;
    if (dev.logPendingAt && Date.now() - dev.logPendingAt < 8000) return;
    const task = dev.tasks.find((t) => t.id === dev.selected);
    if (task && (task.status === 'running' || task.status === 'queued') && !dev.showDiff) {
      dev.logPendingAt = Date.now();
      ws.sendJSON({ type: 'dev_log', id: dev.selected, after: dev.next });
    }
  }, 2000);
}

function setDevRunning(running) {
  els.devLive.hidden = !running;
  els.atelierBtn.title = running
    ? `Atelier au travail sur « ${running.repo} »`
    : 'Atelier de développement';
  if (!panels.atelier.hidden) ws.sendJSON({ type: 'dev_tasks' });
}

function onDevTasks(msg) {
  dev.workerDown = !!msg.error;
  if (msg.error) {
    els.atelierStatus.textContent = msg.error;
    return;
  }
  const previous = dev.tasks;
  dev.tasks = msg.tasks || [];
  const a = msg.atelier || {};
  const seg = (text, cls) => {
    const s = document.createElement('span');
    s.textContent = text;
    if (cls) s.className = cls;
    return s;
  };
  els.atelierStatus.textContent = '';
  els.atelierStatus.append(
    seg(`auth : ${a.auth || '?'}`),
    document.createTextNode('  ·  '),
    seg(a.push_possible ? 'push prêt' : 'push : GITHUB_TOKEN absent',
        a.push_possible ? 'ok' : 'ko'),
    document.createTextNode('  ·  '),
    seg(`dépôts : ${(a.repos || []).join(', ') || '—'}`),
  );

  // La tâche affichée vient de finir : rattraper les dernières lignes du journal
  const sel = dev.tasks.find((t) => t.id === dev.selected);
  const prevSel = previous.find((t) => t.id === dev.selected);
  if (sel && prevSel && prevSel.status !== sel.status) {
    setBranchLabel(sel);
    if (sel.status === 'done' || sel.status === 'failed') {
      ws.sendJSON({ type: 'dev_log', id: sel.id, after: dev.next });
    }
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
    els.atelierDiff.hidden = true;
    return;
  }
  for (const t of dev.tasks) {
    const row = document.createElement('button');
    row.type = 'button';
    row.className = 'dev-task' + (t.id === dev.selected ? ' selected' : '');
    const st = document.createElement('span');
    st.className = `st ${t.status}`;
    const repo = document.createElement('span');
    repo.className = 'repo';
    repo.textContent = t.repo;
    const desc = document.createElement('span');
    desc.className = 'desc';
    desc.textContent = t.instruction || '';
    const when = document.createElement('span');
    when.className = 'when';
    when.textContent = fmtWhen(t.created_at);
    row.append(st, repo, desc, when);
    row.addEventListener('click', () => selectDevTask(t.id));
    els.atelierTasks.appendChild(row);
  }
}

function selectDevTask(id) {
  dev.selected = id;
  dev.next = 0;
  dev.logPendingAt = 0;
  dev.showDiff = false;
  els.atelierLog.textContent = '';
  els.atelierLog.hidden = false;
  els.atelierDiff.hidden = true;
  els.atelierViewBtn.textContent = 'Voir le diff';
  renderDevTasks();
  const task = dev.tasks.find((t) => t.id === id);
  els.atelierToolbar.hidden = !task;
  if (task) {
    setBranchLabel(task);
    ws.sendJSON({ type: 'dev_log', id, after: 0 });
  }
}

function setBranchLabel(task) {
  els.atelierBranch.textContent =
    `${task.branch} · ${DEV_STATUS_FR[task.status] || task.status}`;
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
    // Pas de spam : une seule ligne pour une même erreur répétée
    const last = el.lastElementChild;
    if (!last || last.textContent.trim() !== msg.error.trim()) {
      appendLogRow('', msg.error, 'log-hint');
    }
    return;
  }
  const pinned = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
  let lines = msg.lines || [];
  const total = msg.next || 0;
  if (total < dev.next) { // l'atelier a redémarré : le journal est reparti de zéro
    el.textContent = '';
    dev.next = 0;
  }
  // Dédoublonnage : deux réponses peuvent se chevaucher (réponse lente + resélection)
  const start = total - lines.length; // index absolu de la première ligne reçue
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
    if (task.status === 'done' || task.status === 'failed') {
      // Fin découverte par le journal : une dernière lecture attrape la traîne
      ws.sendJSON({ type: 'dev_log', id: task.id, after: dev.next });
    }
  }
}

els.atelierViewBtn.addEventListener('click', () => {
  if (!dev.selected) return;
  dev.showDiff = !dev.showDiff;
  els.atelierViewBtn.textContent = dev.showDiff ? 'Voir le journal' : 'Voir le diff';
  els.atelierLog.hidden = dev.showDiff;
  els.atelierDiff.hidden = !dev.showDiff;
  if (dev.showDiff) {
    els.atelierDiff.textContent = 'chargement du diff…';
    ws.sendJSON({ type: 'dev_diff', id: dev.selected });
  }
});

function diffClass(line) {
  if (line.startsWith('+++') || line.startsWith('---') || line.startsWith('diff ')) return 'd-file';
  if (line.startsWith('@@')) return 'd-hunk';
  if (line.startsWith('+')) return 'd-add';
  if (line.startsWith('-')) return 'd-del';
  return 'd-ctx';
}

function onDevDiff(msg) {
  if (msg.id !== dev.selected || !dev.showDiff) return;
  els.atelierDiff.textContent = '';
  if (msg.error) {
    els.atelierDiff.textContent = msg.error;
    return;
  }
  for (const line of String(msg.diff || '').split('\n')) {
    const row = document.createElement('div');
    row.className = `log-row ${diffClass(line)}`;
    row.textContent = line || ' ';
    els.atelierDiff.appendChild(row);
  }
}

// ── Panneau santé ───────────────────────────────────────────────────────

let santeTimer = null;

function setSantePolling(on) {
  clearInterval(santeTimer); santeTimer = null;
  if (!on) return;
  ws.sendJSON({ type: 'sante' });
  santeTimer = setInterval(() => ws.sendJSON({ type: 'sante' }), 30000);
}

document.getElementById('sante-refresh')
  .addEventListener('click', () => ws.sendJSON({ type: 'sante' }));

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
  p.className = 'panel-empty';
  p.textContent = text;
  return p;
}

const fmtNum = (x) => String(Math.round(x * 100) / 100).replace('.', ',');

function renderSante(msg) {
  const body = els.santeBody;
  body.textContent = '';
  if (msg.error) {
    body.appendChild(emptyLine(msg.error));
    return;
  }
  const d = msg.data || {};

  const nova = d.nova || {};
  {
    let dot = 'ok';
    const rows = [];
    let note = '';
    if (!nova.configuree) {
      dot = '';
      rows.push(['état', 'non configurée']);
    } else if (!nova.connectee) {
      dot = 'bad';
      rows.push(['état', 'déconnectée !']);
    } else {
      rows.push(['version', nova.version || '?']);
      rows.push(['entités', String(nova.entites ?? '?')]);
      if (nova.nb_indisponibles) {
        dot = 'warn';
        rows.push(['indisponibles', String(nova.nb_indisponibles)]);
      }
      const maj = nova.mises_a_jour || [];
      if (maj.length) {
        rows.push(['mises à jour', String(maj.length)]);
        note = maj.slice(0, 6).join(', ') + (maj.length > 6 ? '…' : '');
      }
    }
    body.appendChild(tile('Nova', dot, rows, note));
  }

  const sys = d.systeme || {};
  if (sys.charge || sys.ram) {
    let dot = 'ok';
    const rows = [];
    if (sys.charge) {
      rows.push(['charge', `${fmtNum(sys.charge[0])} / ${sys.coeurs || '?'} cœurs`]);
      if (sys.charge[0] > (sys.coeurs || 1)) dot = 'warn';
    }
    if (sys.ram) {
      rows.push(['mémoire', `${sys.ram.utilisee_pct} %`]);
      if (sys.ram.utilisee_pct >= 90) dot = 'warn';
    }
    body.appendChild(tile('Nebula', dot, rows));
  }

  const docker = d.docker;
  if (docker) {
    if (docker.erreur) {
      body.appendChild(tile('Docker', 'bad', [['erreur', docker.erreur]]));
    } else {
      const problems = docker.problemes || [];
      const rows = [['conteneurs', `${docker.en_marche} en marche / ${docker.total}`]];
      for (const p of problems.slice(0, 4)) rows.push([p.nom, p.etat]);
      const mem = Object.entries(docker.top_memoire_mo || {}).slice(0, 4)
        .map(([n, mo]) => `${n} ${mo} Mo`).join(' · ');
      body.appendChild(tile('Docker', problems.length ? 'warn' : 'ok', rows,
        mem ? `Mémoire : ${mem}` : ''));
    }
  }

  const atrium = d.atrium;
  if (atrium) {
    body.appendChild(atrium.ok
      ? tile('Atrium', 'ok', [['latence', `${atrium.latence_ms} ms`]])
      : tile('Atrium', 'bad', [['état', 'injoignable !']]));
  }

  if (!body.childElementCount) body.appendChild(emptyLine('Aucun moniteur configuré.'));
}

// ── Panneau historique ──────────────────────────────────────────────────

const OUTCOME_FR = {
  ok: 'ok', refused: 'refusée', failed: 'échec',
  needs_confirmation: 'à confirmer', created: 'créée', decided: 'décidée',
};
const PROP_STATUS_FR = {
  done: 'exécutée', rejected: 'refusée', refused: 'refusée',
  failed: 'échec', expired: 'expirée', approved: 'approuvée',
  executing: 'en cours',
};

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
  const title = (text) => {
    const h = document.createElement('h3');
    h.className = 'hist-title';
    h.textContent = text;
    return h;
  };

  body.appendChild(title('Journal des actions'));
  const journal = msg.journal || [];
  if (!journal.length) body.appendChild(emptyLine('Journal vide pour l’instant.'));
  for (const e of journal) {
    body.appendChild(histRow(
      fmtWhen(e.ts), e.action_id, 'act',
      OUTCOME_FR[e.outcome] || e.outcome, e.outcome,
      [['j-auth', e.authorization], ['j-detail', e.detail]],
    ));
  }

  body.appendChild(title('Propositions passées'));
  const props = msg.proposals || [];
  if (!props.length) body.appendChild(emptyLine('Aucune proposition passée.'));
  for (const p of props) {
    body.appendChild(histRow(
      fmtWhen(p.decided_at || p.created_at), `n°${p.num} — ${p.title}`, '',
      PROP_STATUS_FR[p.status] || p.status, p.status,
      [['j-detail', p.result || p.error]],
    ));
  }
}

// ── Interactions ────────────────────────────────────────────────────────

els.mic.addEventListener('click', micAction);

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
    if (anyPanelOpen()) {
      closePanels();
      return;
    }
    if (st.listening) stopListening(false);
    interrupt();
  }
});

// Niveau de la voix de Sentinel → visualiseur
(function pumpPlayerLevel() {
  if (player && player.playing) viz.setLevel(player.level());
  requestAnimationFrame(pumpPlayerLevel);
})();

// ── Démarrage ───────────────────────────────────────────────────────────

if ('serviceWorker' in navigator) {
  // Échoue silencieusement avec un certificat auto-signé non approuvé :
  // l'application fonctionne quand même, seule l'installation PWA est indisponible.
  navigator.serviceWorker.register('sw.js').catch(() => {});
}

refreshUi();
ws.connect();
