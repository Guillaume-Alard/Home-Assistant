/*
 * luna-card — le visage de Luna dans Home Assistant.
 *
 * Une carte Lovelace : l'orbe (repris tel quel de la branche « visage » de
 * Luna — SVG animé en CSS, sans aucun asset externe) et un fil de conversation.
 *
 * Elle ne parle qu'à Home Assistant, par `hass.connection` : JAMAIS un `fetch()`
 * vers un hôte externe, jamais de jeton dans la page. Chaque phrase passe par le
 * pipeline de conversation d'HA (`conversation/process`), qui la route vers
 * l'agent Sentinel (intégration sentinel_assist) — donc par le même cerveau, la
 * même sécurité et le même « propose puis approuve » que partout ailleurs. La
 * carte ne pilote rien elle-même.
 */

const ETATS_ORBE = ["idle", "listening", "thinking", "speaking", "alert"];

const STYLES = `
  :host { display: block; }
  .carte {
    --luna-fond: #0f1923;
    --luna-carte: #1e2d3d;
    --luna-accent: #c9396d;
    --luna-texte: #e8eef4;
    --luna-doux: #8fa3b8;
    --luna-bord: rgba(255, 255, 255, 0.07);
    --luna-alerte: #e8a33d;
    background: var(--luna-fond);
    color: var(--luna-texte);
    border-radius: var(--ha-card-border-radius, 12px);
    border: 1px solid var(--luna-bord);
    overflow: hidden;
    display: flex;
    flex-direction: column;
    height: var(--luna-hauteur, 460px);
    font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
    font-size: 15px;
    line-height: 1.45;
  }

  /* ── En-tête : orbe + identité ─────────────────────────────────────── */
  header {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 14px 16px;
    border-bottom: 1px solid var(--luna-bord);
    flex: 0 0 auto;
  }
  .titre { flex: 1; min-width: 0; }
  .titre b { display: block; font-size: 15px; font-weight: 600; }
  .titre small { display: block; color: var(--luna-doux); font-size: 12px; }

  /* ── L'orbe : SVG animé en CSS, aucun asset externe ────────────────── */
  .orbe { width: 40px; height: 40px; flex: 0 0 auto; }
  .orbe circle { transform-origin: 50% 50%; }
  .halo { fill: var(--luna-accent); opacity: 0.16; }
  .anneau { fill: none; stroke: var(--luna-accent); stroke-width: 2; opacity: 0.5; }
  .noyau { fill: var(--luna-accent); }
  .carte[data-orbe="idle"] .halo { animation: respire 5s ease-in-out infinite; }
  .carte[data-orbe="thinking"] .anneau {
    animation: tourne 1.1s linear infinite;
    stroke-dasharray: 26 60;
  }
  .carte[data-orbe="thinking"] .noyau { animation: pulse 1.1s ease-in-out infinite; }
  .carte[data-orbe="speaking"] .noyau { animation: parle 0.55s ease-in-out infinite; }
  .carte[data-orbe="listening"] .halo { animation: ecoute 1.3s ease-out infinite; }
  .carte[data-orbe="alert"] .noyau { fill: var(--luna-alerte); }
  .carte[data-orbe="alert"] .halo { fill: var(--luna-alerte); animation: pulse 1.8s infinite; }
  @keyframes respire { 0%,100% { opacity:.12; transform:scale(1) } 50% { opacity:.24; transform:scale(1.06) } }
  @keyframes tourne  { to { transform: rotate(360deg) } }
  @keyframes pulse   { 0%,100% { transform:scale(1) } 50% { transform:scale(0.82) } }
  @keyframes parle   { 0%,100% { transform:scale(1) } 50% { transform:scale(1.22) } }
  @keyframes ecoute  { 0% { opacity:.30; transform:scale(0.9) } 100% { opacity:0; transform:scale(1.5) } }
  @media (prefers-reduced-motion: reduce) {
    .halo, .anneau, .noyau { animation: none !important; }
  }

  /* ── Fil de conversation ───────────────────────────────────────────── */
  .fil {
    flex: 1 1 auto;
    overflow-y: auto;
    overscroll-behavior: contain;
    padding: 14px 16px;
    display: flex;
    flex-direction: column;
    gap: 10px;
    -webkit-overflow-scrolling: touch;
  }
  .vide {
    margin: auto;
    text-align: center;
    color: var(--luna-doux);
    font-size: 13.5px;
    max-width: 34ch;
  }
  .bulle {
    max-width: 82%;
    padding: 9px 13px;
    border-radius: 14px;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }
  .bulle.moi {
    align-self: flex-end;
    background: var(--luna-accent);
    color: #fff;
    border-bottom-right-radius: 4px;
  }
  .bulle.luna {
    align-self: flex-start;
    background: var(--luna-carte);
    border-bottom-left-radius: 4px;
  }
  .bulle.erreur { align-self: flex-start; background: rgba(232,163,61,.14); color: var(--luna-alerte); }
  .points span {
    display: inline-block; width: 6px; height: 6px; margin: 0 1px;
    border-radius: 50%; background: var(--luna-doux); opacity: .5;
    animation: clignote 1.2s infinite;
  }
  .points span:nth-child(2) { animation-delay: .2s; }
  .points span:nth-child(3) { animation-delay: .4s; }
  @keyframes clignote { 0%,100% { opacity:.25 } 50% { opacity:.9 } }

  /* ── Barre de saisie ───────────────────────────────────────────────── */
  .saisie {
    display: flex;
    gap: 8px;
    padding: 12px 16px;
    padding-bottom: calc(12px + env(safe-area-inset-bottom, 0px));
    border-top: 1px solid var(--luna-bord);
    flex: 0 0 auto;
  }
  .saisie textarea {
    flex: 1;
    resize: none;
    background: var(--luna-carte);
    border: 1px solid var(--luna-bord);
    border-radius: 12px;
    color: var(--luna-texte);
    font: inherit;
    padding: 9px 12px;
    max-height: 120px;
    min-height: 0;
  }
  .saisie textarea:focus-visible { outline: none; border-color: var(--luna-accent); }
  .envoi {
    flex: 0 0 auto;
    align-self: flex-end;
    width: 40px; height: 40px;
    border: none;
    border-radius: 12px;
    background: var(--luna-accent);
    color: #fff;
    font-size: 17px;
    cursor: pointer;
  }
  .envoi:disabled { opacity: .5; cursor: default; }

  .micro {
    flex: 0 0 auto;
    align-self: flex-end;
    width: 40px; height: 40px;
    border: 1px solid var(--luna-bord);
    border-radius: 12px;
    background: var(--luna-carte);
    color: var(--luna-texte);
    font-size: 16px;
    cursor: pointer;
    transition: background 0.15s, border-color 0.15s;
  }
  .micro:hover:not(:disabled) { border-color: var(--luna-accent); }
  .micro:disabled { opacity: 0.4; cursor: not-allowed; }
  .carte[data-orbe="listening"] .micro {
    background: var(--luna-accent); color: #fff; border-color: var(--luna-accent);
  }
`;

// Micro → PCM 16 bits mono 16 kHz (interpolation linéaire). Repris tel quel du
// worklet du cockpit Sentinel ; inséré via Blob pour ne dépendre d'aucun fichier.
const WORKLET_SRC = `
class LunaPcm extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const target = (options.processorOptions && options.processorOptions.targetRate) || 16000;
    this.ratio = sampleRate / target;
    this.readPos = 0; this.pending = new Float32Array(0);
    this.out = new Int16Array(2048); this.outLen = 0; this.active = false; this.frame = 0;
    this.port.onmessage = (e) => {
      if (e.data === 'start') { this.active = true; this.pending = new Float32Array(0); this.readPos = 0; this.outLen = 0; }
      else if (e.data === 'stop') { this.active = false; this.flush(); }
    };
  }
  flush() {
    if (this.outLen > 0) { const b = this.out.slice(0, this.outLen); this.port.postMessage({ type: 'chunk', buffer: b.buffer }, [b.buffer]); this.outLen = 0; }
    this.port.postMessage({ type: 'flushed' });
  }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    if ((this.frame++ & 3) === 0) {
      let s = 0; for (let i = 0; i < ch.length; i++) s += ch[i] * ch[i];
      this.port.postMessage({ type: 'level', value: Math.sqrt(s / ch.length), active: this.active });
    }
    if (!this.active) return true;
    const data = new Float32Array(this.pending.length + ch.length);
    data.set(this.pending); data.set(ch, this.pending.length);
    let pos = this.readPos;
    while (pos + 1 < data.length) {
      const i = Math.floor(pos), frac = pos - i;
      const v = data[i] * (1 - frac) + data[i + 1] * frac;
      this.out[this.outLen++] = Math.max(-32768, Math.min(32767, Math.round(v * 32767)));
      if (this.outLen === this.out.length) { const b = this.out.slice(0); this.port.postMessage({ type: 'chunk', buffer: b.buffer }, [b.buffer]); this.outLen = 0; }
      pos += this.ratio;
    }
    const consumed = Math.floor(pos);
    this.pending = data.slice(consumed); this.readPos = pos - consumed;
    return true;
  }
}
registerProcessor('luna-pcm', LunaPcm);
`;

const ORBE_SVG = `
  <svg class="orbe" viewBox="0 0 40 40" aria-hidden="true">
    <circle class="halo"   cx="20" cy="20" r="18"></circle>
    <circle class="anneau" cx="20" cy="20" r="14"></circle>
    <circle class="noyau"  cx="20" cy="20" r="6"></circle>
  </svg>`;

class LunaCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._config = {};
    this._messages = [];
    this._conversationId = null;
    this._busy = false;
    this._rendu = false;
    this._voix = null;        // état d'un tour vocal en cours (null = aucun)
    this._workletUrl = null;  // URL Blob du worklet micro (créée à la demande)
  }

  // Lovelace appelle toujours setConfig ; on part de défauts, jamais d'un objet vide.
  setConfig(config) {
    this._config = {
      title: "Luna",
      subtitle: "Ton intendante numérique",
      agent: "",       // entity_id d'un agent conversation ; vide = détection auto
      height: 460,
      stream: true,    // rendu « mot à mot » ; repli auto si indisponible
      entry_id: "",    // intégration Sentinel à interroger (vide = la première)
      voice: true,     // bouton micro (pipeline Assist d'HA) si l'appareil le permet
      pipeline: "",    // id du pipeline Assist ; vide = pipeline préféré d'HA
      ...(config || {}),
    };
    if (this._rendu) { this._appliquerConfig(); this._majMicro(); }
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._rendu) this._render();
  }

  getCardSize() {
    return 8;
  }

  static getStubConfig() {
    return { title: "Luna", subtitle: "Ton intendante numérique" };
  }

  // ── Rendu ────────────────────────────────────────────────────────────

  _render() {
    const style = document.createElement("style");
    style.textContent = STYLES;
    const carte = document.createElement("div");
    carte.className = "carte";
    carte.dataset.orbe = "idle";
    carte.innerHTML = `
      <header>
        ${ORBE_SVG}
        <div class="titre"><b></b><small></small></div>
      </header>
      <div class="fil"></div>
      <div class="saisie">
        <button class="micro" type="button" title="Parler à Luna" aria-label="Parler à Luna">🎙</button>
        <textarea rows="1" placeholder="Écris à Luna…" aria-label="Message à Luna"></textarea>
        <button class="envoi" type="button" title="Envoyer" aria-label="Envoyer">➤</button>
      </div>`;
    this.shadowRoot.replaceChildren(style, carte);

    this._carte = carte;
    this._fil = carte.querySelector(".fil");
    this._zone = carte.querySelector("textarea");
    this._bouton = carte.querySelector(".envoi");
    this._micro = carte.querySelector(".micro");

    this._bouton.addEventListener("click", () => this._envoyer());
    this._micro.addEventListener("click", () => this._ecouter());
    this._zone.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); this._envoyer(); }
    });
    this._zone.addEventListener("input", () => this._autoTaille());

    this._rendu = true;
    this._appliquerConfig();
    this._majMicro();
    this._peindreFil();
  }

  _appliquerConfig() {
    if (!this._carte) return;
    this._carte.querySelector(".titre b").textContent = this._config.title || "Luna";
    this._carte.querySelector(".titre small").textContent = this._config.subtitle || "";
    this._carte.style.setProperty("--luna-hauteur", `${this._config.height || 460}px`);
  }

  _autoTaille() {
    this._zone.style.height = "auto";
    this._zone.style.height = Math.min(this._zone.scrollHeight, 120) + "px";
  }

  _orbe(etat) {
    if (this._carte && ETATS_ORBE.includes(etat)) this._carte.dataset.orbe = etat;
  }

  // ── Conversation ──────────────────────────────────────────────────────

  async _envoyer() {
    const texte = (this._zone.value || "").trim();
    if (!texte || this._busy || !this._hass) return;
    this._zone.value = "";
    this._autoTaille();
    this._messages.push({ role: "moi", texte });
    this._busy = true;
    this._bouton.disabled = true;
    this._majMicro();
    this._orbe("thinking");
    this._peindreFil({ attente: true });

    const peutStreamer = this._config.stream !== false
      && !!this._hass.connection?.subscribeMessage;
    try {
      if (peutStreamer) {
        await this._streamer(texte);        // rendu mot à mot (peut basculer en repli)
      } else {
        await this._envoyerSimple(texte);   // un seul bloc
      }
    } catch (err) {
      // Streaming indisponible (commande absente sur une intégration ancienne) :
      // on retombe une fois sur le chemin non-streamé, sans doubler l'affichage.
      // eslint-disable-next-line no-console
      console.warn("luna-card: streaming indisponible, repli.", err);
      try {
        await this._envoyerSimple(texte);
      } catch (err2) {
        this._erreurBulle();
        // eslint-disable-next-line no-console
        console.error("luna-card:", err2);
      }
    } finally {
      this._busy = false;
      this._bouton.disabled = false;
      this._majMicro();
    }
  }

  // Streaming : la bulle de Luna se remplit fragment par fragment (event_message).
  // La promesse ne REJETTE que si l'abonnement lui-même échoue (commande absente)
  // → repli ; une erreur en cours de flux affiche une bulle et se résout.
  _streamer(texte) {
    return new Promise((resolve, reject) => {
      const enCours = { role: "luna", texte: "" };
      let bulle = null;
      let unsub = null;
      const finir = () => { if (unsub) { unsub(); unsub = null; } };

      const onEvt = (evt) => {
        if (evt.error) {
          finir();
          if (!bulle) { this._messages.push({ role: "erreur", texte: evt.error }); this._peindreFil(); }
          this._orbe("idle");
          resolve();
          return;
        }
        if (evt.delta) {
          if (!bulle) {  // premier fragment : on remplace les points par la bulle
            this._messages.push(enCours);
            this._peindreFil();
            bulle = [...this._fil.querySelectorAll(".bulle.luna")].pop();
            this._orbe("speaking");
          }
          enCours.texte += evt.delta;
          if (bulle) bulle.textContent = enCours.texte;
          this._fil.scrollTop = this._fil.scrollHeight;
        }
        if (evt.done) {
          finir();
          if (!bulle) {  // rien reçu (réponse vide) : on pose le texte final s'il existe
            enCours.texte = evt.text || "…";
            this._messages.push(enCours);
            this._peindreFil();
          }
          this._finDeTour();
          resolve();
        }
      };

      this._hass.connection
        .subscribeMessage(onEvt, {
          type: "sentinel_assist/converse",
          text: texte,
          entry_id: this._config.entry_id || undefined,
        })
        .then((u) => { unsub = u; })
        .catch(reject);  // commande inconnue / refus → repli non-streamé
    });
  }

  // Repli : le pipeline de conversation d'HA, réponse en un bloc.
  async _envoyerSimple(texte) {
    const reponse = await this._hass.callWS({
      type: "conversation/process",
      text: texte,
      agent_id: this._agentId(),
      conversation_id: this._conversationId || undefined,
      language: this._hass.language || "fr",
    });
    this._conversationId = reponse?.conversation_id || this._conversationId;
    this._messages.push({ role: "luna", texte: this._extraireReponse(reponse) });
    this._peindreFil();
    this._finDeTour();
  }

  _finDeTour() {
    this._orbe("speaking");
    clearTimeout(this._retour);
    this._retour = setTimeout(() => this._orbe("idle"), 1200);
  }

  _erreurBulle() {
    this._messages.push({
      role: "erreur",
      texte: "Luna est injoignable pour l'instant. Vérifie l'intégration Sentinel.",
    });
    this._orbe("idle");
    this._peindreFil();
  }

  // ── Voix (pipeline Assist de Home Assistant : micro → texte → Luna → voix) ──
  //
  // On s'appuie sur le pipeline Assist d'HA : il fait la transcription (STT), la
  // conversation (l'agent Sentinel) et la synthèse (TTS). La carte capture le
  // micro, streame le PCM à HA (canal binaire), affiche la transcription et la
  // réponse, et joue la voix. Jamais un service externe : tout passe par HA.

  _urlWorklet() {
    if (!this._workletUrl) {
      this._workletUrl = URL.createObjectURL(
        new Blob([WORKLET_SRC], { type: "application/javascript" })
      );
    }
    return this._workletUrl;
  }

  _voixSupportee() {
    return !!(
      window.isSecureContext
      && navigator.mediaDevices && navigator.mediaDevices.getUserMedia
      && (window.AudioContext || window.webkitAudioContext)
      && this._hass && this._hass.connection && this._hass.connection.socket
    );
  }

  _majMicro() {
    if (!this._micro) return;
    const actif = this._config.voice !== false;
    this._micro.hidden = !actif;
    if (!actif) return;
    const ok = this._voixSupportee();
    this._micro.disabled = !ok || (this._busy && !this._voix);
    this._micro.title = ok ? "Parler à Luna" : "Voix indisponible (HTTPS + micro requis)";
  }

  async _ecouter() {
    // Déjà en écoute → « j'ai fini de parler » : on clôt le micro ; le pipeline
    // poursuit tout seul (transcription → réponse → voix).
    if (this._voix && !this._voix.microArrete) { this._arreterMicro(); return; }
    if (this._voix || this._busy) return;
    if (!this._voixSupportee()) { this._noticeVoix(); return; }
    this._busy = true;
    this._bouton.disabled = true;
    this._voix = { microArrete: false, handlerId: null, finEnvoyee: false };
    this._majMicro();
    this._orbe("listening");
    try {
      await this._demarrerPipeline();
    } catch (err) {
      // eslint-disable-next-line no-console
      console.error("luna-card voix:", err);
      this._messages.push({
        role: "erreur",
        texte: "Micro indisponible ou refusé. Tu peux toujours écrire à Luna.",
      });
      this._peindreFil();
      this._finPipeline();
    }
  }

  async _demarrerPipeline() {
    const AC = window.AudioContext || window.webkitAudioContext;
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
    });
    if (!this._voix) { stream.getTracks().forEach((t) => t.stop()); return; } // annulé
    const ctx = new AC();
    await ctx.audioWorklet.addModule(this._urlWorklet());
    if (!this._voix) { stream.getTracks().forEach((t) => t.stop()); ctx.close(); return; }
    const src = ctx.createMediaStreamSource(stream);
    const node = new AudioWorkletNode(ctx, "luna-pcm", { processorOptions: { targetRate: 16000 } });
    node.port.onmessage = (e) => this._surChunk(e.data);
    src.connect(node); // pas relié à la sortie : on ne rejoue pas le micro
    Object.assign(this._voix, { stream, ctx, src, node });

    const sub = {
      type: "assist_pipeline/run",
      start_stage: "stt",
      end_stage: "tts",
      input: { sample_rate: 16000 },
    };
    if (this._config.pipeline) sub.pipeline = this._config.pipeline;
    const unsub = await this._hass.connection.subscribeMessage(
      (evt) => this._surEvenementPipeline(evt), sub
    );
    if (this._voix) this._voix.unsub = unsub;
    else unsub();
  }

  _surChunk(data) {
    const v = this._voix;
    const socket = this._hass && this._hass.connection && this._hass.connection.socket;
    if (!v || !socket || v.handlerId == null || v.finEnvoyee) return;
    if (data.type === "chunk") {
      const audio = new Uint8Array(data.buffer);
      const frame = new Uint8Array(audio.length + 1);
      frame[0] = v.handlerId;
      frame.set(audio, 1);
      try { socket.send(frame.buffer); } catch { /* socket parti */ }
    } else if (data.type === "flushed") {
      v.finEnvoyee = true;
      try { socket.send(new Uint8Array([v.handlerId]).buffer); } catch { /* rien */ }
    }
  }

  _surEvenementPipeline(evt) {
    const v = this._voix;
    if (!v) return;
    const t = evt.type;
    const d = evt.data || {};
    if (t === "run-start") {
      v.handlerId = d.runner_data && d.runner_data.stt_binary_handler_id;
      if (v.node && !v.microArrete) { try { v.node.port.postMessage("start"); } catch { /* rien */ } }
    } else if (t === "stt-end") {
      if (!v.microArrete) this._arreterMicro();
      const texte = (d.stt_output && d.stt_output.text) || "";
      if (texte) { this._messages.push({ role: "moi", texte }); this._peindreFil(); }
      this._orbe("thinking");
    } else if (t === "intent-end") {
      const io = d.intent_output || {};
      this._conversationId = io.conversation_id || this._conversationId;
      const dit = io.response && io.response.speech && io.response.speech.plain
        && io.response.speech.plain.speech;
      if (dit) { this._messages.push({ role: "luna", texte: dit }); this._peindreFil(); }
    } else if (t === "tts-end") {
      const url = d.tts_output && d.tts_output.url;
      this._orbe("speaking");
      if (url) this._jouerTts(url);
    } else if (t === "run-end") {
      if (!(v.audio && !v.audio.ended)) this._finPipeline(); // laisse la voix finir
    } else if (t === "error") {
      this._messages.push({ role: "erreur", texte: d.message || "La voix a échoué." });
      this._peindreFil();
      this._finPipeline();
    }
  }

  // « J'ai fini de parler » : on arrête la capture ; la fin d'audio (trame vide)
  // part sur le message 'flushed' du worklet, pour ne rien tronquer.
  _arreterMicro() {
    const v = this._voix;
    if (!v || v.microArrete) return;
    v.microArrete = true;
    this._orbe("thinking");
    try { v.node && v.node.port.postMessage("stop"); } catch { /* rien */ }
    try { v.stream && v.stream.getTracks().forEach((t) => t.stop()); } catch { /* rien */ }
    if (v.handlerId == null) this._finPipeline(); // rien n'a démarré : on referme
  }

  _jouerTts(url) {
    try {
      const audio = new Audio(url);
      if (this._voix) this._voix.audio = audio;
      audio.addEventListener("ended", () => this._finPipeline());
      audio.addEventListener("error", () => this._finPipeline());
      const p = audio.play();
      if (p && p.catch) p.catch(() => this._finPipeline());
    } catch {
      this._finPipeline();
    }
  }

  _finPipeline() {
    const v = this._voix;
    this._voix = null;
    if (v) {
      try { v.unsub && v.unsub(); } catch { /* rien */ }
      try { v.node && v.node.port.postMessage("stop"); } catch { /* rien */ }
      try { v.node && v.node.disconnect(); } catch { /* rien */ }
      try { v.src && v.src.disconnect(); } catch { /* rien */ }
      try { v.stream && v.stream.getTracks().forEach((t) => t.stop()); } catch { /* rien */ }
      try { v.ctx && v.ctx.state !== "closed" && v.ctx.close(); } catch { /* rien */ }
    }
    this._busy = false;
    this._bouton.disabled = false;
    this._orbe("idle");
    this._majMicro();
  }

  _noticeVoix() {
    this._messages.push({
      role: "erreur",
      texte: "Pour parler à Luna, ouvre Home Assistant en HTTPS et autorise le micro. Le clavier marche partout.",
    });
    this._peindreFil();
  }

  // Agent conversation à interroger : config explicite, sinon détection d'un agent
  // Sentinel dans les entités, sinon l'agent par défaut d'HA (agent_id omis).
  _agentId() {
    if (this._config.agent) return this._config.agent;
    const etats = this._hass?.states || {};
    for (const id of Object.keys(etats)) {
      if (!id.startsWith("conversation.")) continue;
      const nom = (etats[id].attributes?.friendly_name || id).toLowerCase();
      if (nom.includes("sentinel") || nom.includes("luna")) return id;
    }
    return undefined; // agent par défaut d'HA
  }

  _extraireReponse(reponse) {
    const dit = reponse?.response?.speech?.plain?.speech;
    if (dit && dit.trim()) return dit;
    if (reponse?.response?.response_type === "error") {
      return "Je n'ai pas pu répondre à l'instant.";
    }
    return "…";
  }

  _peindreFil({ attente = false } = {}) {
    if (!this._fil) return;
    this._fil.textContent = "";
    if (!this._messages.length && !attente) {
      const vide = document.createElement("div");
      vide.className = "vide";
      vide.textContent = "Demande-moi quelque chose — l'état de la maison, une action à préparer, un rappel…";
      this._fil.appendChild(vide);
      return;
    }
    for (const m of this._messages) {
      const bulle = document.createElement("div");
      bulle.className = "bulle " + (m.role === "moi" ? "moi" : m.role === "erreur" ? "erreur" : "luna");
      bulle.textContent = m.texte;
      this._fil.appendChild(bulle);
    }
    if (attente) {
      const points = document.createElement("div");
      points.className = "bulle luna points";
      points.innerHTML = "<span></span><span></span><span></span>";
      this._fil.appendChild(points);
    }
    this._fil.scrollTop = this._fil.scrollHeight;
  }
}

if (!customElements.get("luna-card")) {
  customElements.define("luna-card", LunaCard);
}

// Déclaration pour le sélecteur de cartes de Lovelace.
window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === "luna-card")) {
  window.customCards.push({
    type: "luna-card",
    name: "Luna",
    description: "Le visage de Luna : orbe et conversation, dans Home Assistant.",
    preview: true,
  });
}
