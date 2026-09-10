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
      ...(config || {}),
    };
    if (this._rendu) this._appliquerConfig();
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
        <textarea rows="1" placeholder="Écris à Luna…" aria-label="Message à Luna"></textarea>
        <button class="envoi" type="button" title="Envoyer" aria-label="Envoyer">➤</button>
      </div>`;
    this.shadowRoot.replaceChildren(style, carte);

    this._carte = carte;
    this._fil = carte.querySelector(".fil");
    this._zone = carte.querySelector("textarea");
    this._bouton = carte.querySelector(".envoi");

    this._bouton.addEventListener("click", () => this._envoyer());
    this._zone.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); this._envoyer(); }
    });
    this._zone.addEventListener("input", () => this._autoTaille());

    this._rendu = true;
    this._appliquerConfig();
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
