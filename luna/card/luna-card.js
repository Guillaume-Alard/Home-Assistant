/**
 * Luna — la carte Loggia.
 *
 * Un seul fichier, JavaScript natif, aucun build, aucun npm, aucun CDN, aucune
 * dépendance à l'exécution (§8 du cahier des charges).
 *
 * Elle ne parle qu'à Home Assistant, par `hass.connection` : jamais un `fetch()`
 * vers un hôte externe. Les commandes `luna/*` sont enregistrées par
 * l'intégration, qui relaie vers l'add-on.
 *
 * Ce qui compte dans ce fichier :
 *
 * - **Le désabonnement.** Lovelace détruit et recrée les cartes à chaque
 *   changement de vue. Une souscription oubliée dans `disconnectedCallback`
 *   fuit à chaque aller-retour entre onglets, et l'add-on garde un flux ouvert.
 * - **Le rendu incrémental.** `set hass` est appelé à chaque changement d'état
 *   de la maison — plusieurs fois par seconde. Re-rendre le fil à chaque appel
 *   mettrait le N95 à genoux et casserait le défilement.
 * - **Jamais d'échec silencieux.** Micro refusé, contexte non sécurisé, add-on
 *   hors ligne, capacité de phase future : chacun a son message, explicite et
 *   actionnable.
 */

const VERSION = "0.1.0";

const ETATS_ORBE = ["idle", "listening", "thinking", "speaking", "alert"];

const HEURE = new Intl.DateTimeFormat("fr-FR", {
  hour: "2-digit",
  minute: "2-digit",
});

/** Messages d'erreur fabriqués par la carte, jamais reçus du serveur (§8). */
const ERREURS_LOCALES = {
  insecure_context:
    "Le micro est bloqué parce que la page n'est pas en HTTPS. " +
    "En local, ouvre Loggia par l'adresse sécurisée de Nova " +
    "(voir la phase 0 : HTTPS local).",
  mic_denied:
    "L'accès au micro a été refusé. Autorise-le pour Home Assistant dans " +
    "les réglages de ton téléphone, puis recharge la page.",
  voice_phase:
    "La voix arrive en phase 2. Pour l'instant, écris-moi.",
};

const STYLES = `
  :host {
    --luna-fond: #0f1923;
    --luna-carte: #1e2d3d;
    --luna-accent: #c9396d;
    --luna-texte: #e8eef4;
    --luna-doux: #8fa3b8;
    --luna-bord: rgba(255, 255, 255, 0.07);
    --luna-alerte: #e8a33d;
    display: block;
  }

  .carte {
    display: flex;
    flex-direction: column;
    height: var(--luna-hauteur, 620px);
    background: var(--luna-fond);
    color: var(--luna-texte);
    border-radius: 14px;
    overflow: hidden;
    font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
    font-size: 15px;
    line-height: 1.45;
    position: relative;
  }

  /* ── En-tête : orbe, identité, tiroir ─────────────────────────────── */

  header {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 14px 16px;
    padding-top: calc(14px + env(safe-area-inset-top, 0px));
    border-bottom: 1px solid var(--luna-bord);
    flex: 0 0 auto;
  }

  .titre { flex: 1; min-width: 0; }
  .titre b { display: block; font-size: 15px; font-weight: 600; }
  .titre small { display: block; color: var(--luna-doux); font-size: 12px; }

  .badge {
    display: flex;
    align-items: center;
    gap: 8px;
    background: var(--luna-carte);
    border: 1px solid var(--luna-bord);
    border-radius: 999px;
    padding: 4px 10px 4px 4px;
    font-size: 12px;
    white-space: nowrap;
  }
  .avatar {
    width: 22px; height: 22px; border-radius: 50%;
    background: var(--luna-accent);
    display: grid; place-items: center;
    font-size: 11px; font-weight: 700; color: #fff;
  }
  .confiance { color: var(--luna-doux); }

  .cloche {
    background: var(--luna-carte);
    border: 1px solid var(--luna-bord);
    color: var(--luna-texte);
    border-radius: 10px;
    width: 34px; height: 34px;
    cursor: pointer;
    position: relative;
    font-size: 15px;
  }
  .cloche[data-alertes]:not([data-alertes="0"])::after {
    content: attr(data-alertes);
    position: absolute; top: -5px; right: -5px;
    background: var(--luna-alerte); color: #14202b;
    font-size: 10px; font-weight: 700;
    min-width: 16px; height: 16px; border-radius: 8px;
    display: grid; place-items: center;
  }

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
  .bulle time {
    display: block;
    font-size: 10.5px;
    opacity: 0.55;
    margin-top: 4px;
  }
  .bulle.erreur {
    align-self: stretch;
    max-width: 100%;
    background: rgba(232, 163, 61, 0.12);
    border: 1px solid rgba(232, 163, 61, 0.4);
    color: var(--luna-alerte);
    font-size: 14px;
  }

  .outils { align-self: flex-start; display: flex; flex-direction: column; gap: 3px; }
  .outil {
    font-size: 12px;
    color: var(--luna-doux);
    display: flex; align-items: center; gap: 6px;
  }
  .outil::before { content: "◦"; }
  .outil[data-statut="done"]::before  { content: "✓"; color: #6fbf8b; }
  .outil[data-statut="error"]::before { content: "✕"; color: var(--luna-alerte); }
  .outil[data-niveau="3"], .outil[data-niveau="4"] { color: var(--luna-alerte); }

  /* ── Proposition (niveau ≥ 3, §9) ──────────────────────────────────── */

  .proposition {
    align-self: stretch;
    background: var(--luna-carte);
    border: 1px solid var(--luna-accent);
    border-radius: 12px;
    padding: 12px 14px;
  }
  .proposition h4 { margin: 0 0 4px; font-size: 14.5px; }
  .proposition p { margin: 0 0 10px; font-size: 13px; color: var(--luna-doux); }
  .proposition .niveau {
    font-size: 11px; text-transform: uppercase; letter-spacing: .5px;
    color: var(--luna-accent); font-weight: 700;
  }
  .actions { display: flex; gap: 8px; flex-wrap: wrap; }

  button {
    font: inherit;
    font-size: 13px;
    border-radius: 9px;
    border: 1px solid var(--luna-bord);
    background: var(--luna-carte);
    color: var(--luna-texte);
    padding: 7px 13px;
    cursor: pointer;
  }
  button:hover:not(:disabled) { border-color: var(--luna-accent); }
  button:disabled { opacity: 0.45; cursor: not-allowed; }
  button.primaire { background: var(--luna-accent); border-color: var(--luna-accent); color: #fff; }

  /* ── Saisie ────────────────────────────────────────────────────────── */

  footer {
    flex: 0 0 auto;
    border-top: 1px solid var(--luna-bord);
    padding: 10px 12px;
    padding-bottom: calc(10px + env(safe-area-inset-bottom, 0px));
    display: flex;
    gap: 8px;
    align-items: flex-end;
  }
  textarea {
    flex: 1;
    resize: none;
    font: inherit;
    color: var(--luna-texte);
    background: var(--luna-carte);
    border: 1px solid var(--luna-bord);
    border-radius: 11px;
    padding: 9px 12px;
    max-height: 110px;
    min-height: 40px;
  }
  textarea:focus { outline: none; border-color: var(--luna-accent); }
  textarea:disabled { opacity: 0.5; }
  .rond {
    width: 40px; height: 40px; padding: 0;
    display: grid; place-items: center;
    border-radius: 50%;
    flex: 0 0 auto;
    font-size: 16px;
  }

  /* ── Tiroir « Veille » ─────────────────────────────────────────────── */

  .tiroir {
    position: absolute;
    top: 0; right: 0; bottom: 0;
    width: min(330px, 88%);
    background: var(--luna-carte);
    border-left: 1px solid var(--luna-bord);
    transform: translateX(100%);
    transition: transform .22s ease;
    display: flex; flex-direction: column;
    padding: 14px;
    padding-top: calc(14px + env(safe-area-inset-top, 0px));
    padding-bottom: calc(14px + env(safe-area-inset-bottom, 0px));
    gap: 10px;
    z-index: 3;
  }
  .carte[data-tiroir="ouvert"] .tiroir { transform: translateX(0); }
  @media (prefers-reduced-motion: reduce) { .tiroir { transition: none; } }
  .tiroir header { padding: 0 0 10px; border: none; }
  .tiroir .vide { color: var(--luna-doux); font-size: 13.5px; }
  .alerte {
    background: var(--luna-fond);
    border: 1px solid var(--luna-bord);
    border-left: 3px solid var(--luna-alerte);
    border-radius: 9px;
    padding: 10px 12px;
  }
  .alerte h5 { margin: 0 0 3px; font-size: 14px; }
  .alerte p { margin: 0 0 8px; font-size: 12.5px; color: var(--luna-doux); }

  /* ── Bandeau hors ligne ────────────────────────────────────────────── */

  .bandeau {
    flex: 0 0 auto;
    background: rgba(232, 163, 61, 0.14);
    border-bottom: 1px solid rgba(232, 163, 61, 0.35);
    color: var(--luna-alerte);
    font-size: 13px;
    padding: 8px 16px;
  }
  .bandeau[hidden] { display: none; }

  @media (max-width: 420px) {
    .badge .prenom, .titre small { display: none; }
    .bulle { max-width: 92%; }
  }
`;

const GABARIT = `
  <div class="carte" data-orbe="idle" data-tiroir="ferme">
    <header>
      <svg class="orbe" viewBox="0 0 40 40" aria-hidden="true">
        <circle class="halo"   cx="20" cy="20" r="18"></circle>
        <circle class="anneau" cx="20" cy="20" r="14"></circle>
        <circle class="noyau"  cx="20" cy="20" r="6"></circle>
      </svg>
      <div class="titre">
        <b>Luna</b>
        <small class="sous-titre">Prête</small>
      </div>
      <div class="badge" title="Profil actif">
        <span class="avatar">?</span>
        <span class="prenom">…</span>
        <span class="confiance"></span>
      </div>
      <button class="cloche" data-alertes="0" title="Veille" aria-label="Veille">▤</button>
    </header>

    <div class="bandeau" hidden></div>
    <div class="fil" role="log" aria-live="polite"></div>

    <footer>
      <textarea rows="1" placeholder="Écris à Luna…" aria-label="Message"></textarea>
      <button class="rond micro" title="Micro" aria-label="Micro">🎙</button>
      <button class="rond envoi primaire" title="Envoyer" aria-label="Envoyer">➤</button>
    </footer>

    <aside class="tiroir" aria-label="Veille">
      <header>
        <div class="titre"><b>Veille</b><small>Ce que Luna a remarqué</small></div>
        <button class="fermer rond" aria-label="Fermer">✕</button>
      </header>
      <div class="alertes"></div>
    </aside>
  </div>
`;

class LunaCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._config = {};
    this._monte = false;
    this._pret = false;

    this._desabonnerFeed = null;
    this._desabonnerChat = null;

    this._conversationId = null;
    this._messageEnCours = null;
    this._bulleEnCours = null;
    this._outilsEnCours = null;
    this._alertes = new Map();
    this._phases = { voice: false, identity: false, veille: false, guardian: false };
    this._enLigne = null;
    this._colle = true;
  }

  // ── Contrat Lovelace ───────────────────────────────────────────────

  setConfig(config) {
    const hauteur = Number(config.height ?? 620);
    if (!Number.isFinite(hauteur) || hauteur < 400) {
      throw new Error("luna-card : « height » doit être un nombre ≥ 400.");
    }
    this._config = {
      height: hauteur,
      drawers: { veille: true, ...(config.drawers || {}) },
      greeting: config.greeting !== false,
    };
    if (this._monte) this._appliquerConfig();
  }

  static getStubConfig() {
    return { height: 620, drawers: { veille: true }, greeting: true };
  }

  getCardSize() {
    return Math.ceil((this._config.height || 620) / 50);
  }

  set hass(hass) {
    const premier = this._hass === null;
    this._hass = hass;
    if (premier && this._monte) this._demarrer();
    this._rafraichirEnLigne();
  }

  get hass() {
    return this._hass;
  }

  // ── Cycle de vie ───────────────────────────────────────────────────

  connectedCallback() {
    if (!this.shadowRoot.firstChild) {
      const style = document.createElement("style");
      style.textContent = STYLES;
      this.shadowRoot.append(style);
      const gabarit = document.createElement("template");
      gabarit.innerHTML = GABARIT;
      this.shadowRoot.append(gabarit.content.cloneNode(true));
      this._brancher();
    }
    this._monte = true;
    this._appliquerConfig();
    if (this._hass) this._demarrer();
  }

  /**
   * Lovelace détruit les cartes à chaque changement de vue : sans ce
   * désabonnement, chaque aller-retour entre onglets laisse un flux ouvert
   * côté add-on.
   */
  disconnectedCallback() {
    this._monte = false;
    this._pret = false;
    this._fermerFlux();
    if (this._desabonnerFeed) {
      this._desabonnerFeed();
      this._desabonnerFeed = null;
    }
  }

  // ── Construction ───────────────────────────────────────────────────

  _q(selecteur) {
    return this.shadowRoot.querySelector(selecteur);
  }

  _brancher() {
    const saisie = this._q("textarea");
    saisie.addEventListener("keydown", (evt) => {
      if (evt.key === "Enter" && !evt.shiftKey) {
        evt.preventDefault();
        this._envoyer();
      }
      if (evt.key === "Escape") this._annuler();
    });
    saisie.addEventListener("input", () => {
      saisie.style.height = "auto";
      saisie.style.height = `${Math.min(saisie.scrollHeight, 110)}px`;
    });
    // Le fil ne « colle » au bas que si l'utilisateur y était déjà : sinon,
    // relire un échange plus haut serait interrompu par chaque delta.
    const fil = this._q(".fil");
    this._colle = true;
    fil.addEventListener("scroll", () => {
      this._colle = fil.scrollHeight - fil.scrollTop - fil.clientHeight < 60;
    });

    this._q(".envoi").addEventListener("click", () => this._envoyer());
    this._q(".micro").addEventListener("click", () => this._micro());
    this._q(".cloche").addEventListener("click", () => this._basculerTiroir());
    this._q(".fermer").addEventListener("click", () => this._basculerTiroir(false));
  }

  _appliquerConfig() {
    const carte = this._q(".carte");
    if (!carte) return;
    carte.style.setProperty("--luna-hauteur", `${this._config.height}px`);
    this._q(".cloche").hidden = !this._config.drawers.veille;
  }

  async _demarrer() {
    if (this._pret) return;
    this._pret = true;
    try {
      const info = await this._appel({ type: "luna/info" });
      this._appliquerInfo(info);
      await this._charger();
      await this._ouvrirFeed();
    } catch (err) {
      this._pret = false;
      this._horsLigne(this._messageErreur(err));
    }
  }

  // ── Communication ──────────────────────────────────────────────────

  _appel(message) {
    return this._hass.connection.sendMessagePromise({
      ...message,
      client_id: "loggia",
    });
  }

  async _ouvrirFeed() {
    if (this._desabonnerFeed) return;
    this._desabonnerFeed = await this._hass.connection.subscribeMessage(
      (evt) => this._surFeed(evt),
      { type: "luna/feed", client_id: "loggia" },
    );
  }

  _surFeed(evt) {
    switch (evt.event) {
      case "status":
        if (evt.addon === "offline") this._horsLigne(evt.detail);
        else if (evt.addon === "degraded") this._bandeau(evt.detail || "Luna fonctionne en mode réduit.");
        else this._bandeau(null);
        break;
      case "identity": // [P3]
        this._appliquerProfil(evt.profile);
        break;
      case "alert": // [P4]
        this._alertes.set(evt.alert.id, evt.alert);
        this._rendreAlertes();
        break;
      case "alert_cleared": // [P4]
        this._alertes.delete(evt.id);
        this._rendreAlertes();
        break;
      case "message": // [P4] Luna prend la parole d'elle-même
        this._bulle("luna", evt.message.text, new Date(evt.message.ts));
        break;
      case "error":
        this._erreur(evt.message);
        break;
    }
  }

  async _charger() {
    const resultat = await this._appel({ type: "luna/history", limit: 50 });
    this._conversationId = resultat.conversation_id;
    const fil = this._q(".fil");
    fil.replaceChildren();
    for (const message of resultat.messages) {
      if (message.role === "system") continue;
      this._bulle(
        message.role === "user" ? "moi" : "luna",
        message.text,
        new Date(message.ts),
      );
      if (message.tools?.length) this._rendreOutils(message.tools);
    }
    if (!resultat.messages.length && this._config.greeting) {
      this._bulle("luna", "Bonsoir. Qu'est-ce que je peux faire ?", new Date());
    }
    this._colle = true;
    this._defiler(true);
  }

  // ── Envoi d'un message ─────────────────────────────────────────────

  async _envoyer() {
    const saisie = this._q("textarea");
    const texte = saisie.value.trim();
    if (!texte || this._messageEnCours || this._enLigne === false) return;

    saisie.value = "";
    saisie.style.height = "auto";
    this._bulle("moi", texte, new Date());
    this._orbe("thinking");
    this._sousTitre("Luna réfléchit…");
    this._bulleEnCours = null;
    this._outilsEnCours = null;

    const message = { type: "luna/chat", text: texte, client_id: "loggia" };
    if (this._conversationId) message.conversation_id = this._conversationId;

    try {
      this._desabonnerChat = await this._hass.connection.subscribeMessage(
        (evt) => this._surChat(evt),
        message,
      );
    } catch (err) {
      this._orbe("idle");
      this._erreur(this._messageErreur(err));
    }
  }

  _surChat(evt) {
    switch (evt.event) {
      case "accepted":
        this._messageEnCours = evt.message_id;
        this._conversationId = evt.conversation_id;
        break;

      case "delta":
        if (!this._bulleEnCours) {
          this._bulleEnCours = this._bulle("luna", "", new Date());
          this._orbe("speaking");
          this._sousTitre("Luna répond…");
        }
        this._bulleEnCours.corps.textContent += evt.text;
        this._defiler();
        break;

      case "tool":
        this._outil(evt);
        break;

      case "proposal":
        this._proposition(evt.proposal);
        break;

      case "done":
        this._terminer();
        break;

      case "error":
        this._terminer();
        this._erreur(evt.message);
        break;
    }
  }

  _terminer() {
    this._fermerFlux();
    this._messageEnCours = null;
    this._bulleEnCours = null;
    this._outilsEnCours = null;
    this._orbe(this._alertes.size ? "alert" : "idle");
    this._sousTitre(this._alertes.size ? `${this._alertes.size} à signaler` : "Prête");
    this._defiler();
  }

  _fermerFlux() {
    if (this._desabonnerChat) {
      this._desabonnerChat();
      this._desabonnerChat = null;
    }
  }

  async _annuler() {
    if (!this._messageEnCours) return;
    const identifiant = this._messageEnCours;
    this._terminer();
    try {
      await this._appel({ type: "luna/cancel", message_id: identifiant });
    } catch {
      /* L'échange est déjà fini de notre côté : rien à dire de plus. */
    }
  }

  // ── Rendu ──────────────────────────────────────────────────────────

  _bulle(qui, texte, quand) {
    const bulle = document.createElement("div");
    bulle.className = `bulle ${qui}`;
    const corps = document.createElement("span");
    corps.textContent = texte;
    const heure = document.createElement("time");
    heure.textContent = HEURE.format(quand);
    heure.dateTime = quand.toISOString();
    bulle.append(corps, heure);
    this._q(".fil").append(bulle);
    this._defiler();
    return { bulle, corps };
  }

  _erreur(texte) {
    const bloc = document.createElement("div");
    bloc.className = "bulle erreur";
    bloc.textContent = texte;
    this._q(".fil").append(bloc);
    this._defiler();
  }

  _outil(evt) {
    if (!this._outilsEnCours) {
      this._outilsEnCours = { bloc: document.createElement("div"), lignes: new Map() };
      this._outilsEnCours.bloc.className = "outils";
      this._q(".fil").append(this._outilsEnCours.bloc);
    }
    const cle = `${evt.name}|${evt.label}`;
    let ligne = this._outilsEnCours.lignes.get(cle);
    if (!ligne) {
      ligne = document.createElement("div");
      ligne.className = "outil";
      this._outilsEnCours.lignes.set(cle, ligne);
      this._outilsEnCours.bloc.append(ligne);
    }
    ligne.textContent = evt.label;
    ligne.dataset.statut = evt.status;
    ligne.dataset.niveau = String(evt.level);
    this._defiler();
  }

  _rendreOutils(outils) {
    const bloc = document.createElement("div");
    bloc.className = "outils";
    for (const outil of outils) {
      const ligne = document.createElement("div");
      ligne.className = "outil";
      ligne.textContent = outil.label;
      ligne.dataset.statut = outil.status;
      ligne.dataset.niveau = String(outil.level);
      bloc.append(ligne);
    }
    this._q(".fil").append(bloc);
  }

  /** §9 : niveau ≥ 3 → validation explicite, avec sa justification lisible. */
  _proposition(proposition) {
    const bloc = document.createElement("div");
    bloc.className = "proposition";

    const niveau = document.createElement("div");
    niveau.className = "niveau";
    niveau.textContent = `Niveau ${proposition.level} — validation requise`;

    const titre = document.createElement("h4");
    titre.textContent = proposition.title;

    const pourquoi = document.createElement("p");
    pourquoi.textContent = proposition.why;

    const actions = document.createElement("div");
    actions.className = "actions";
    const accepter = document.createElement("button");
    accepter.className = "primaire";
    accepter.textContent = "Accepter";
    const refuser = document.createElement("button");
    refuser.textContent = "Refuser";

    const decider = async (decision) => {
      accepter.disabled = refuser.disabled = true;
      try {
        const resultat = await this._appel({
          type: "luna/proposal/decide",
          proposal_id: proposition.id,
          decision,
        });
        actions.replaceChildren();
        const verdict = document.createElement("p");
        verdict.textContent =
          decision === "reject"
            ? "Refusé. Rien n'a changé."
            : resultat.executed
              ? "Fait."
              : "Je n'ai pas réussi à l'appliquer.";
        bloc.append(verdict);
      } catch (err) {
        accepter.disabled = refuser.disabled = false;
        this._erreur(this._messageErreur(err));
      }
    };
    accepter.addEventListener("click", () => decider("accept"));
    refuser.addEventListener("click", () => decider("reject"));

    actions.append(accepter, refuser);
    bloc.append(niveau, titre, pourquoi, actions);
    this._q(".fil").append(bloc);
    this._defiler();
  }

  _rendreAlertes() {
    const hote = this._q(".alertes");
    hote.replaceChildren();
    this._q(".cloche").dataset.alertes = String(this._alertes.size);

    if (!this._alertes.size) {
      const vide = document.createElement("p");
      vide.className = "vide";
      vide.textContent = this._phases.veille
        ? "Rien à signaler."
        : "La veille arrive en phase 4. Rien à signaler d'ici là.";
      hote.append(vide);
      return;
    }

    for (const alerte of this._alertes.values()) {
      const bloc = document.createElement("div");
      bloc.className = "alerte";
      const titre = document.createElement("h5");
      titre.textContent = alerte.title;
      const pourquoi = document.createElement("p");
      pourquoi.textContent = alerte.why || "";
      const actions = document.createElement("div");
      actions.className = "actions";
      for (const [libelle, action] of [
        ["Agir", "accepted"],
        ["Ignorer", "rejected"],
        ["Ne plus me le dire", "muted"],
      ]) {
        const bouton = document.createElement("button");
        bouton.textContent = libelle;
        bouton.addEventListener("click", async () => {
          try {
            await this._appel({
              type: "luna/alerts/feedback",
              suggestion_id: alerte.id,
              action,
            });
            this._alertes.delete(alerte.id);
            this._rendreAlertes();
          } catch (err) {
            this._erreur(this._messageErreur(err));
          }
        });
        actions.append(bouton);
      }
      bloc.append(titre, pourquoi, actions);
      hote.append(bloc);
    }
  }

  // ── État visuel ────────────────────────────────────────────────────

  _defiler(force = false) {
    const fil = this._q(".fil");
    if (!fil) return;
    if (force || this._colle) fil.scrollTop = fil.scrollHeight;
  }

  _orbe(etat) {
    if (!ETATS_ORBE.includes(etat)) return;
    this._q(".carte").dataset.orbe = etat;
  }

  _sousTitre(texte) {
    this._q(".sous-titre").textContent = texte;
  }

  _appliquerInfo(info) {
    this._phases = info.phases || this._phases;
    this._appliquerProfil(info.profile);
    this._rendreAlertes();
    this._q(".micro").disabled = true; // la voix arrive en phase 2
    if (info.addon === "degraded") {
      this._bandeau("Luna ne joint pas Home Assistant. Elle répond, mais n'agit pas.");
    }
  }

  _appliquerProfil(profil) {
    if (!profil) return;
    const nom = profil.display_name || "Inconnu";
    this._q(".avatar").textContent = nom.slice(0, 1).toUpperCase();
    this._q(".prenom").textContent = nom;
    const confiance = this._q(".confiance");
    // En P1 la confiance vaut 1 ou 0 : l'afficher n'apprendrait rien. Elle
    // devient parlante en P3, quand plusieurs signaux se fondent.
    confiance.textContent =
      this._phases.identity && profil.confidence < 1
        ? `${Math.round(profil.confidence * 100)} %`
        : "";
  }

  _basculerTiroir(ouvrir) {
    const carte = this._q(".carte");
    const ouvert = carte.dataset.tiroir === "ouvert";
    carte.dataset.tiroir = (ouvrir ?? !ouvert) ? "ouvert" : "ferme";
  }

  _bandeau(texte) {
    const bandeau = this._q(".bandeau");
    bandeau.textContent = texte || "";
    bandeau.hidden = !texte;
  }

  /**
   * Mode dégradé (§8) : le chat est coupé, mais les entités Home Assistant
   * restent lisibles — la carte ne masque rien d'autre.
   */
  _horsLigne(detail) {
    this._bandeau(
      detail ||
        "Luna est hors ligne. Vérifie l'add-on dans Paramètres → Modules complémentaires.",
    );
    this._q("textarea").disabled = true;
    this._q(".envoi").disabled = true;
    this._q(".micro").disabled = true;
    this._orbe("idle");
    this._sousTitre("Hors ligne");
  }

  _enLigneRetrouvee() {
    this._bandeau(null);
    this._q("textarea").disabled = false;
    this._q(".envoi").disabled = false;
    this._sousTitre("Prête");
    this._pret = false;
    this._demarrer();
  }

  /**
   * `binary_sensor.luna_en_ligne` est lu directement dans `hass.states` : c'est
   * instantané, alors qu'un `luna/info` coûte un aller-retour et échoue
   * justement quand l'add-on est absent.
   */
  _rafraichirEnLigne() {
    if (!this._hass || !this._monte) return;
    const etat = this._hass.states["binary_sensor.luna_en_ligne"];
    if (!etat) return;
    const enLigne = etat.state === "on";
    if (enLigne === this._enLigne) return;
    const premier = this._enLigne === null;
    this._enLigne = enLigne;
    if (!enLigne) this._horsLigne();
    else if (!premier) this._enLigneRetrouvee();
  }

  // ── Micro (phase 2) ────────────────────────────────────────────────

  /**
   * La voix arrive en P2. D'ici là le bouton existe mais explique — §8 :
   * « le refus de permission micro et le contexte non-HTTPS produisent un
   * message d'erreur explicite et actionnable ».
   */
  _micro() {
    if (!window.isSecureContext) {
      this._erreur(ERREURS_LOCALES.insecure_context);
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      this._erreur(ERREURS_LOCALES.insecure_context);
      return;
    }
    this._erreur(ERREURS_LOCALES.voice_phase);
  }

  // ── Erreurs ────────────────────────────────────────────────────────

  _messageErreur(err) {
    if (err && typeof err === "object" && err.message) return err.message;
    if (typeof err === "string") return err;
    return "Quelque chose a cassé. Regarde les journaux de Home Assistant.";
  }
}

customElements.define("luna-card", LunaCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "luna-card",
  name: "Luna",
  description: "Le visage de Luna : orbe, conversation, veille.",
  preview: true,
});

console.info(
  `%c LUNA-CARD %c ${VERSION} `,
  "background:#c9396d;color:#fff;border-radius:3px 0 0 3px;padding:2px 6px",
  "background:#1e2d3d;color:#e8eef4;border-radius:0 3px 3px 0;padding:2px 6px",
);
