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
      refreshUi();
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
    default:
      break;
  }
});

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
