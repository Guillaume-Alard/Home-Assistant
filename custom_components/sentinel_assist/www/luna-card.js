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

  /* Bulle « en écoute » : un niveau sonore VIVANT (piloté par le micro), qui se
     résout en texte dès que la transcription arrive. */
  .bulle.ecoute { min-width: 58px; }
  .niveau { display: inline-flex; align-items: center; gap: 3px; height: 18px; color: #fff; --n: 0; }
  .niveau i {
    width: 3px; height: 100%; border-radius: 2px; background: currentColor;
    transform-origin: center; transform: scaleY(.2);
    animation: onde 1.1s ease-in-out infinite;
  }
  .niveau i:nth-child(2) { animation-delay: .15s; }
  .niveau i:nth-child(3) { animation-delay: .3s; }
  .niveau i:nth-child(4) { animation-delay: .45s; }
  .niveau i:nth-child(5) { animation-delay: .6s; }
  .niveau.actif i { animation: none; transition: transform .09s ease-out;
                    transform: scaleY(calc(0.16 + var(--n) * 0.9)); }
  .niveau.actif i:nth-child(2) { transform: scaleY(calc(0.16 + var(--n) * 1.3)); }
  .niveau.actif i:nth-child(4) { transform: scaleY(calc(0.16 + var(--n) * 1.15)); }
  @keyframes onde { 0%,100% { transform: scaleY(.2) } 50% { transform: scaleY(.75) } }
  @media (prefers-reduced-motion: reduce) { .niveau i { animation: none; } }

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

  .micro, .veille {
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
  .micro:hover:not(:disabled), .veille:hover:not(:disabled) { border-color: var(--luna-accent); }
  .micro:disabled, .veille:disabled { opacity: 0.4; cursor: not-allowed; }
  /* Micro actif pendant l'écoute d'un tour ; veille active quand elle est armée. */
  .carte[data-orbe="listening"] .micro,
  .veille.actif {
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
    this._voix = null;        // état du RUN pipeline en cours (null = aucun)
    this._audio = null;       // micro partagé (persistant en veille) : {stream,ctx,src,node}
    this._enVeille = false;   // veille au mot d'éveil armée
    this._workletUrl = null;  // URL Blob du worklet micro (créée à la demande)
  }

  disconnectedCallback() {
    // Carte retirée du DOM (édition du tableau de bord…) : on relâche le micro.
    this._arreterTout();
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
      wake: true,      // bouton veille « mot d'éveil » (mains libres)
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
        <button class="veille" type="button" title="Mains libres : écouter le mot d'éveil" aria-label="Écouter le mot d'éveil">👂</button>
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
    this._veilleBtn = carte.querySelector(".veille");

    this._bouton.addEventListener("click", () => this._envoyer());
    this._micro.addEventListener("click", () => this._ecouter());
    this._veilleBtn.addEventListener("click", () => this._basculerVeille());
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
    this._setBusy(true);
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
      this._setBusy(false);
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

  _setBusy(v) {
    this._busy = v;
    if (this._bouton) this._bouton.disabled = v;
    this._majMicro();
  }

  _majMicro() {
    const ok = this._voixSupportee();
    if (this._micro) {
      const on = this._config.voice !== false;
      this._micro.hidden = !on;
      // Micro « parler » : hors support, pendant la veille, ou pendant un autre tour.
      this._micro.disabled = !ok || this._enVeille
        || (this._busy && !(this._voix && this._voix.mode === "push"));
      this._micro.title = ok ? "Parler à Luna" : "Voix indisponible (HTTPS + micro requis)";
    }
    if (this._veilleBtn) {
      const on = this._config.voice !== false && this._config.wake !== false;
      this._veilleBtn.hidden = !on;
      this._veilleBtn.disabled = !ok || (this._busy && !this._enVeille);
      this._veilleBtn.classList.toggle("actif", !!this._enVeille);
      this._veilleBtn.title = !ok ? "Voix indisponible (HTTPS + micro requis)"
        : this._enVeille ? "Arrêter l'écoute du mot d'éveil" : "Mains libres : écouter le mot d'éveil";
    }
  }

  // Micro partagé : persistant tant que la veille est armée, ouvert le temps d'un
  // tour en push-to-talk. Ouvert sur un geste (le premier appui), donc audible.
  async _ouvrirMicro() {
    if (this._audio) return;
    const AC = window.AudioContext || window.webkitAudioContext;
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
    });
    const ctx = new AC();
    await ctx.audioWorklet.addModule(this._urlWorklet());
    const src = ctx.createMediaStreamSource(stream);
    const node = new AudioWorkletNode(ctx, "luna-pcm", { processorOptions: { targetRate: 16000 } });
    node.port.onmessage = (e) => this._surChunk(e.data);
    src.connect(node); // pas relié à la sortie : on ne rejoue pas le micro
    this._audio = { stream, ctx, src, node, actif: false };
  }

  _fermerMicro() {
    const a = this._audio;
    this._audio = null;
    if (!a) return;
    try { a.node.port.postMessage("stop"); } catch { /* rien */ }
    try { a.node.disconnect(); } catch { /* rien */ }
    try { a.src.disconnect(); } catch { /* rien */ }
    try { a.stream.getTracks().forEach((t) => t.stop()); } catch { /* rien */ }
    try { a.ctx.state !== "closed" && a.ctx.close(); } catch { /* rien */ }
  }

  async _demarrerRun(startStage) {
    const sub = {
      type: "assist_pipeline/run",
      start_stage: startStage,
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

  // Push-to-talk : appui sur le micro.
  async _ecouter() {
    if (this._enVeille) return; // pas de push-to-talk pendant la veille
    if (this._voix && this._voix.mode === "push" && !this._voix.microArrete) { this._arreterMicro(); return; }
    if (this._voix || this._busy) return;
    if (!this._voixSupportee()) { this._noticeVoix(); return; }
    this._voix = {
      mode: "push", microArrete: false, handlerId: null, finEnvoyee: false,
      transcrit: { role: "moi", texte: "", ecoute: true }, // bulle « en écoute »
    };
    this._messages.push(this._voix.transcrit);
    this._setBusy(true); // le micro « parler » reste actionnable (mode push)
    this._orbe("listening");
    this._peindreFil();
    try {
      await this._ouvrirMicro();
      if (!this._voix) { this._fermerMicro(); return; } // annulé pendant l'ouverture
      await this._demarrerRun("stt");
    } catch (err) {
      // eslint-disable-next-line no-console
      console.error("luna-card voix:", err);
      this._messages.push({ role: "erreur", texte: "Micro indisponible ou refusé. Tu peux toujours écrire à Luna." });
      this._peindreFil();
      this._arreterTout();
    }
  }

  // Mot d'éveil : veille mains libres. Un run pipeline commençant à « wake_word » ;
  // à la détection, on carillonne et on enchaîne sur la demande, puis on ré-arme.
  async _basculerVeille() {
    if (this._enVeille) { this._arreterTout(); return; }
    if (this._voix || this._busy) return;
    if (!this._voixSupportee()) { this._noticeVoix(); return; }
    this._enVeille = true;
    this._voix = { mode: "wake", handlerId: null, finEnvoyee: false, transcrit: null };
    this._majMicro();
    this._orbe("idle");
    try {
      await this._ouvrirMicro();
      if (!this._enVeille) { this._fermerMicro(); return; }
      await this._demarrerRun("wake_word");
    } catch (err) {
      // eslint-disable-next-line no-console
      console.error("luna-card veille:", err);
      this._messages.push({
        role: "erreur",
        texte: "Le mot d'éveil demande le micro (HTTPS) et un pipeline Assist avec détection de mot d'éveil.",
      });
      this._peindreFil();
      this._arreterTout();
    }
  }

  _reArmer() {
    if (!this._enVeille) { this._fermerMicro(); return; }
    this._voix = { mode: "wake", handlerId: null, finEnvoyee: false, transcrit: null };
    this._demarrerRun("wake_word").catch((err) => {
      // eslint-disable-next-line no-console
      console.error("luna-card veille (ré-armement) :", err);
      this._arreterTout();
    });
  }

  _surChunk(data) {
    const v = this._voix;
    if (!v) return;
    if (data.type === "level") {
      // Niveau sonore live → la bulle « en écoute » réagit à la voix.
      if (v.niveauEl) {
        v.niveauEl.classList.add("actif");
        const n = Math.max(0, Math.min(1, (data.value || 0) * 5));
        v.niveauEl.style.setProperty("--n", n.toFixed(3));
      }
      return;
    }
    const socket = this._hass && this._hass.connection && this._hass.connection.socket;
    if (!socket || v.handlerId == null || v.finEnvoyee) return;
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
    // Transcription PARTIELLE si l'STT en fournit (whisper standard n'en donne
    // pas : la bulle réagit alors au niveau sonore jusqu'au texte final).
    if (t !== "stt-end" && v.transcrit && v.transcrit.ecoute && d.stt_output && d.stt_output.text) {
      v.transcrit.texte = d.stt_output.text;
      this._peindreFil();
    }
    if (t === "run-start") {
      v.handlerId = d.runner_data && d.runner_data.stt_binary_handler_id;
      // Le micro n'est démarré qu'une fois ; il reste actif en veille (ré-armements).
      if (this._audio && !this._audio.actif) {
        try { this._audio.node.port.postMessage("start"); this._audio.actif = true; } catch { /* rien */ }
      }
    } else if (t === "wake_word-end") {
      // « Luna » entendu : carillon, puis on écoute la demande.
      this._chime();
      this._setBusy(true);
      v.transcrit = { role: "moi", texte: "", ecoute: true };
      this._messages.push(v.transcrit);
      this._orbe("listening");
      this._peindreFil();
    } else if (t === "stt-end") {
      if (v.mode === "push" && !v.microArrete) this._arreterMicro();
      else if (v.mode === "wake") v.finEnvoyee = true; // on cesse d'alimenter ce run
      this._finaliserTranscription((d.stt_output && d.stt_output.text) || "");
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
      if (!(v.audio && !v.audio.ended)) this._terminerEchange(); // laisse la voix finir
    } else if (t === "error") {
      this._messages.push({ role: "erreur", texte: d.message || "La voix a échoué." });
      this._peindreFil();
      this._arreterTout();
    }
  }

  // La bulle « en écoute » devient le texte transcrit (ou disparaît si rien).
  _finaliserTranscription(texte) {
    const v = this._voix;
    if (v && v.transcrit) {
      if (texte) { v.transcrit.texte = texte; v.transcrit.ecoute = false; }
      else {
        const i = this._messages.indexOf(v.transcrit);
        if (i >= 0) this._messages.splice(i, 1);
      }
      v.transcrit = null;
      v.niveauEl = null;
    } else if (texte) {
      this._messages.push({ role: "moi", texte });
    }
    this._peindreFil();
  }

  // Push-to-talk : « j'ai fini de parler ». La fin d'audio (trame vide) part sur
  // le message 'flushed' du worklet, pour ne rien tronquer.
  _arreterMicro() {
    const v = this._voix;
    if (!v || v.mode !== "push" || v.microArrete) return;
    v.microArrete = true;
    this._orbe("thinking");
    try { this._audio && this._audio.node.port.postMessage("stop"); } catch { /* rien */ }
    if (this._audio) this._audio.actif = false;
    if (v.handlerId == null) this._arreterTout(); // rien n'a démarré : on referme
  }

  _jouerTts(url) {
    try {
      const audio = new Audio(url);
      if (this._voix) this._voix.audio = audio;
      audio.addEventListener("ended", () => this._terminerEchange());
      audio.addEventListener("error", () => this._terminerEchange());
      const p = audio.play();
      if (p && p.catch) p.catch(() => this._terminerEchange());
    } catch {
      this._terminerEchange();
    }
  }

  // Petit carillon (deux notes) sur le contexte audio déjà ouvert — donc audible
  // (créé lors du geste d'armement de la veille).
  _chime() {
    try {
      const ctx = this._audio && this._audio.ctx;
      if (!ctx || ctx.state === "closed") return;
      const now = ctx.currentTime;
      for (const [f, dt] of [[880, 0], [1175, 0.09]]) {
        const o = ctx.createOscillator();
        const g = ctx.createGain();
        o.type = "sine"; o.frequency.value = f;
        g.gain.setValueAtTime(0.0001, now + dt);
        g.gain.exponentialRampToValueAtTime(0.16, now + dt + 0.02);
        g.gain.exponentialRampToValueAtTime(0.0001, now + dt + 0.15);
        o.connect(g).connect(ctx.destination);
        o.start(now + dt); o.stop(now + dt + 0.17);
      }
    } catch { /* pas de carillon, tant pis */ }
  }

  // Fin d'un run : ré-armer la veille si toujours armée, sinon tout arrêter.
  _terminerEchange() {
    const v = this._voix;
    const reArmer = !!(v && v.mode === "wake" && this._enVeille);
    this._finRunCourant();
    if (reArmer) {
      this._setBusy(false);
      this._orbe("idle");
      this._reArmer();
    } else {
      this._arreterTout();
    }
  }

  // Ferme le RUN courant (abonnement + bulle d'écoute non résolue), pas le micro.
  _finRunCourant() {
    const v = this._voix;
    this._voix = null;
    if (!v) return;
    if (v.transcrit && v.transcrit.ecoute) {
      const i = this._messages.indexOf(v.transcrit);
      if (i >= 0) { this._messages.splice(i, 1); this._peindreFil(); }
    }
    try { v.unsub && v.unsub(); } catch { /* rien */ }
  }

  // Tout arrêter : run + micro + veille, retour au repos.
  _arreterTout() {
    this._finRunCourant();
    this._fermerMicro();
    this._enVeille = false;
    this._setBusy(false);
    this._orbe("idle");
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
      if (m.ecoute && !m.texte) {
        // En écoute, sans texte encore : un niveau sonore vivant à la place.
        bulle.classList.add("ecoute");
        const meter = document.createElement("span");
        meter.className = "niveau";
        meter.innerHTML = "<i></i><i></i><i></i><i></i><i></i>";
        bulle.appendChild(meter);
      } else {
        if (m.ecoute) bulle.classList.add("ecoute"); // transcription partielle affichée
        bulle.textContent = m.texte;
      }
      this._fil.appendChild(bulle);
    }
    if (attente) {
      const points = document.createElement("div");
      points.className = "bulle luna points";
      points.innerHTML = "<span></span><span></span><span></span>";
      this._fil.appendChild(points);
    }
    // Réf. du niveau sonore pour le pilotage live (le cas échéant).
    if (this._voix) this._voix.niveauEl = this._fil.querySelector(".bulle.ecoute .niveau");
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
