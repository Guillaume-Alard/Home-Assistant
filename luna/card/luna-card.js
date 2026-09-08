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

/** Où la carte garde son identifiant d'appareil (C6). Rien de biométrique. */
const CLE_APPAREIL = "luna_appareil";

const ETATS_ORBE = ["idle", "listening", "thinking", "speaking", "alert"];

/** Les profils de §6, tels qu'on les montre. */
const NOMS = {
  guillaume: "Guillaume",
  clara: "Clara",
  liam: "Liam",
  guest: "Invité",
  unknown: "Inconnu",
};

const HEURE = new Intl.DateTimeFormat("fr-FR", {
  hour: "2-digit",
  minute: "2-digit",
});

/**
 * Un WAV vide de 44 octets.
 *
 * iOS n'autorise la lecture d'un élément audio que si un `play()` a déjà eu
 * lieu **pendant un vrai geste utilisateur**. On amorce donc le lecteur avec ce
 * silence au premier appui, pour pouvoir parler plus tard sans geste — quand
 * Luna répond, par exemple.
 */
const SILENCE =
  "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAgD4AAAB9AAACABAAZGF0YQAAAAA=";

/** Ce qu'attend le pipeline Assist : 16 kHz, mono, PCM 16 bits. */
const TAUX_CIBLE = 16000;

/** Combien d'échantillons par trame envoyée — 1024 à 16 kHz, soit 64 ms. */
const TRAME = 1024;

/**
 * Huit secondes de PCM 16 bits à 16 kHz (décision C3).
 *
 * Une empreinte ne gagne rien à écouter plus longtemps, et le N95 a mieux à
 * faire. Au-delà, on arrête simplement de copier.
 */
const OCTETS_IDENTITE_MAX = 16000 * 2 * 8;

/**
 * Le rééchantillonneur, exécuté dans un `AudioWorklet`.
 *
 * Il vit ici en texte plutôt que dans un second fichier : §8 exige **un seul
 * fichier**, et un `Blob` de même origine suffit à le charger. Sur iOS on ne
 * choisit pas la fréquence du `AudioContext` — elle vaut 44,1 ou 48 kHz — donc
 * le rééchantillonnage n'est pas une optimisation, c'est une obligation.
 */
const WORKLET = `
class LunaPcm extends AudioWorkletProcessor {
  constructor() {
    super();
    this._pas = sampleRate / ${TAUX_CIBLE};
    this._reste = 0;
    this._dernier = 0;
    this._sortie = new Int16Array(${TRAME});
    this._n = 0;
  }
  process(entrees) {
    const canal = entrees[0] && entrees[0][0];
    if (!canal || canal.length === 0) return true;
    let i = this._reste;
    while (i < canal.length) {
      const j = i | 0;
      const f = i - j;
      const a = j === 0 && this._reste < 0 ? this._dernier : canal[j];
      const b = j + 1 < canal.length ? canal[j + 1] : a;
      let v = a + (b - a) * f;
      if (v > 1) v = 1; else if (v < -1) v = -1;
      this._sortie[this._n++] = v < 0 ? v * 0x8000 : v * 0x7fff;
      if (this._n === this._sortie.length) {
        this.port.postMessage(this._sortie.slice());
        this._n = 0;
      }
      i += this._pas;
    }
    this._reste = i - canal.length;
    this._dernier = canal[canal.length - 1];
    return true;
  }
}
registerProcessor("luna-pcm", LunaPcm);
`;

/** Messages d'erreur fabriqués par la carte, jamais reçus du serveur (§8). */
const ERREURS_LOCALES = {
  insecure_context:
    "Le micro est bloqué parce que la page n'est pas en HTTPS. " +
    "En local, ouvre Loggia par l'adresse sécurisée de Nova " +
    "(voir la phase 0 : HTTPS local).",
  mic_denied:
    "L'accès au micro a été refusé. Autorise-le pour Home Assistant dans " +
    "les réglages de ton téléphone, puis recharge la page.",
  voice_phase: "La voix arrive en phase 2. Pour l'instant, écris-moi.",
  mic_failed:
    "Je n'ai pas réussi à démarrer le micro. Recharge la page ; si ça " +
    "recommence, regarde la console du navigateur.",
  stt_failed:
    "Je n'ai rien compris. Réessaie en parlant un peu plus près du micro.",
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
    cursor: pointer;
    font: inherit;
    color: var(--luna-texte);
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
  .carte[data-orbe="listening"] .micro { background: var(--luna-accent); color: #fff; }
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
    /* Appui maintenu sur iPad : ni sélection, ni menu, ni défilement. */
    touch-action: none;
    -webkit-user-select: none;
    user-select: none;
    -webkit-touch-callout: none;
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
  .panneau { display: none; overflow-y: auto; }
  .carte[data-panneau="veille"] .alertes,
  .carte[data-panneau="identite"] .identite { display: block; }
  .alerte + .alerte { margin-top: 8px; }
  .rond-large { touch-action: none; -webkit-user-select: none; user-select: none;
                -webkit-touch-callout: none; }
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
  <div class="carte" data-orbe="idle" data-tiroir="ferme" data-panneau="veille">
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
      <button class="badge" title="Qui suis-je pour Luna ?">
        <span class="avatar">?</span>
        <span class="prenom">…</span>
        <span class="confiance"></span>
      </button>
      <button class="cloche" data-alertes="0" title="Veille" aria-label="Veille">▤</button>
    </header>

    <div class="bandeau" hidden></div>
    <div class="fil" role="log" aria-live="polite"></div>

    <footer>
      <textarea rows="1" placeholder="Écris à Luna…" aria-label="Message"></textarea>
      <button class="rond micro" title="Micro" aria-label="Micro">🎙</button>
      <button class="rond envoi primaire" title="Envoyer" aria-label="Envoyer">➤</button>
    </footer>

    <aside class="tiroir">
      <header>
        <div class="titre">
          <b class="titre-tiroir">Veille</b>
          <small class="soustitre-tiroir">Ce que Luna a remarqué</small>
        </div>
        <button class="fermer rond" aria-label="Fermer">✕</button>
      </header>
      <div class="panneau alertes"></div>
      <div class="panneau identite"></div>
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

    // Voix (P2)
    this._ecoute = false;
    this._parVoix = false;
    this._lecteur = null;
    this._contexteAudio = null;
    this._fluxMicro = null;
    this._noeud = null;
    this._sourceAudio = null;
    this._handler = null;
    this._tampon = [];
    this._attenteStt = null;
    this._desabonnerStt = null;
    this._demarrage = null;

    // Identité (P3)
    this._appareil = null;
    this._voixNecessaire = false;
    this._inscrits = [];
    this._copiePcm = [];
    this._octetsCopies = 0;
    this._modeEcoute = "chat";
    this._attenteCapture = null;
    this._inscription = null;
  }

  // ── Contrat Lovelace ───────────────────────────────────────────────

  setConfig(config) {
    const hauteur = Number(config.height ?? 620);
    if (!Number.isFinite(hauteur) || hauteur < 400) {
      throw new Error("luna-card : « height » doit être un nombre ≥ 400.");
    }
    const parler = config.speak ?? "voix";
    if (!["voix", "toujours", "jamais"].includes(parler)) {
      throw new Error('luna-card : « speak » vaut "voix", "toujours" ou "jamais".');
    }
    this._config = {
      height: hauteur,
      drawers: { veille: true, ...(config.drawers || {}) },
      greeting: config.greeting !== false,
      // « voix » : Luna ne lit à voix haute que ce qu'on lui a demandé de vive
      // voix. C'est le moins surprenant : personne ne veut être lu à haute voix
      // parce qu'il a tapé une question.
      speak: parler,
    };
    if (this._monte) this._appliquerConfig();
  }

  static getStubConfig() {
    return { height: 620, drawers: { veille: true }, greeting: true, speak: "voix" };
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
    this._ecoute = false;
    this._couperCapture();
    this._fermerTranscription();
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

  /**
   * Un identifiant d'appareil, stable et anonyme (décision C6).
   *
   * §9.4 interdit de garder de la **biométrie** côté navigateur ; un numéro
   * aléatoire n'en est pas. C'est lui qui permet à l'iPad du couloir et au
   * téléphone de Clara d'avoir chacun leur profil actif.
   */
  _idAppareil() {
    if (this._appareil) return this._appareil;
    let garde = null;
    try {
      garde = localStorage.getItem(CLE_APPAREIL);
      if (!garde) {
        garde = `d_${Math.random().toString(36).slice(2, 10)}`;
        localStorage.setItem(CLE_APPAREIL, garde);
      }
    } catch {
      // Navigation privée, stockage refusé : un identifiant de session fera
      // l'affaire. L'identité expirera juste plus souvent.
      garde = `d_${Math.random().toString(36).slice(2, 10)}`;
    }
    this._appareil = garde;
    return garde;
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
    this._brancherMicro();
    this._q(".cloche").addEventListener("click", () => this._ouvrirTiroir("veille"));
    this._q(".badge").addEventListener("click", () => this._ouvrirTiroir("identite"));
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
      device: this._idAppareil(),
    });
  }

  async _ouvrirFeed() {
    if (this._desabonnerFeed) return;
    this._desabonnerFeed = await this._hass.connection.subscribeMessage(
      (evt) => this._surFeed(evt),
      { type: "luna/feed", client_id: "loggia", device: this._idAppareil() },
    );
  }

  _surFeed(evt) {
    switch (evt.event) {
      case "status":
        if (evt.addon === "offline") this._horsLigne(evt.detail);
        else if (evt.addon === "degraded") this._bandeau(evt.detail || "Luna fonctionne en mode réduit.");
        else this._bandeau(null);
        break;
      case "identity":
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

  async _envoyer(parVoix = false) {
    const saisie = this._q("textarea");
    const texte = saisie.value.trim();
    if (!texte || this._messageEnCours || this._enLigne === false) return;

    saisie.value = "";
    saisie.style.height = "auto";
    this._parVoix = parVoix;
    this._bulle("moi", texte, new Date());
    this._orbe("thinking");
    this._sousTitre("Luna réfléchit…");
    this._bulleEnCours = null;
    this._outilsEnCours = null;

    const message = {
      type: "luna/chat",
      text: texte,
      client_id: "loggia",
      device: this._idAppareil(),
    };
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
        if (this._doitParler()) this._parler(evt.text);
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
    const identite = info.identity || {};
    // C1 : sur un appareil où la session Home Assistant désigne déjà
    // quelqu'un, la carte n'envoie aucun audio d'identification.
    this._voixNecessaire = Boolean(identite.voice_needed);
    this._inscrits = identite.enrolled || [];
    this._appliquerProfil(info.profile);
    this._rendreIdentite();
    this._rendreAlertes();
    const micro = this._q(".micro");
    const pret = Boolean(this._phases.voice) && window.isSecureContext;
    micro.disabled = !pret;
    micro.title = pret
      ? "Maintenir pour parler"
      : this._phases.voice
        ? "Micro indisponible : la page n'est pas en HTTPS"
        : "La voix arrive en phase 2";
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

  _ouvrirTiroir(panneau) {
    const carte = this._q(".carte");
    const deja = carte.dataset.tiroir === "ouvert" && carte.dataset.panneau === panneau;
    carte.dataset.panneau = panneau;
    const identite = panneau === "identite";
    this._q(".titre-tiroir").textContent = identite ? "Qui parle" : "Veille";
    this._q(".soustitre-tiroir").textContent = identite
      ? "Les voix que Luna reconnaît"
      : "Ce que Luna a remarqué";
    if (identite) this._rendreIdentite();
    this._basculerTiroir(!deja);
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
    if (this._ecoute) {
      this._ecoute = false;
      this._couperCapture();
      this._fermerTranscription();
    }
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

  // ── Micro — appui pour parler (§8) ─────────────────────────────────

  _brancherMicro() {
    const micro = this._q(".micro");
    micro.addEventListener("pointerdown", (evt) => {
      evt.preventDefault();
      this._debloquerAudio();
      this._demarrerEcoute();
    });
    for (const fin of ["pointerup", "pointercancel", "pointerleave"]) {
      micro.addEventListener(fin, () => this._arreterEcoute());
    }
    // Sur iOS, un appui maintenu ouvre le menu contextuel : il ferait perdre
    // le `pointerup`, donc la fin de l'enregistrement.
    micro.addEventListener("contextmenu", (evt) => evt.preventDefault());
  }

  /**
   * Amorce le lecteur audio **pendant** un geste utilisateur.
   *
   * Sans ça, sur iPad, la réponse de Luna serait muette : iOS refuse tout
   * `play()` qui n'a pas été précédé d'un `play()` déclenché par un vrai geste.
   */
  _debloquerAudio() {
    if (this._lecteur) return;
    const lecteur = new Audio();
    lecteur.preload = "auto";
    lecteur.src = SILENCE;
    lecteur.play().catch(() => {
      /* Le navigateur refuse : on réessaiera au prochain geste. */
    });
    this._lecteur = lecteur;
  }

  /**
   * Démarre l'écoute, et retient sa propre promesse.
   *
   * Un appui bref peut être relâché avant que la capture soit prête. Sans
   * sérialisation, l'arrêt coupe alors un démarrage encore en cours, celui-ci
   * échoue, et ferme la transcription que l'arrêt attendait — l'enregistrement
   * meurt sans un mot. Sur iPad, où l'on tapote, ce n'est pas un cas rare.
   */
  _demarrerEcoute(mode = "chat") {
    this._demarrage = this._faireDemarrer(mode).finally(() => {
      this._demarrage = null;
    });
    return this._demarrage;
  }

  async _faireDemarrer(mode) {
    if (this._ecoute || this._messageEnCours || this._enLigne === false) return;
    if (!this._phases.voice) {
      this._erreur(ERREURS_LOCALES.voice_phase);
      return;
    }
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
      this._erreur(ERREURS_LOCALES.insecure_context);
      return;
    }

    this._ecoute = true;
    this._modeEcoute = mode;
    this._handler = null;
    this._tampon = [];
    this._copiePcm = [];
    this._octetsCopies = 0;
    this._orbe("listening");
    this._sousTitre(mode === "inscription" ? "Lis la phrase…" : "Je t'écoute…");

    try {
      this._fluxMicro = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
      });
    } catch (err) {
      this._echecEcoute(
        err && err.name === "NotAllowedError"
          ? ERREURS_LOCALES.mic_denied
          : ERREURS_LOCALES.mic_failed,
      );
      return;
    }

    try {
      // En inscription, on ne veut que l'audio : pas de transcription, donc pas
      // de pipeline ouvert pour rien.
      if (mode === "chat") await this._ouvrirTranscription();
      await this._brancherCapture();
    } catch (err) {
      this._echecEcoute(this._messageErreur(err) || ERREURS_LOCALES.mic_failed);
    }
  }

  async _ouvrirTranscription() {
    this._attenteStt = null;
    this._desabonnerStt = await this._hass.connection.subscribeMessage(
      (evt) => this._surPipeline(evt),
      {
        type: "assist_pipeline/run",
        start_stage: "stt",
        end_stage: "stt",
        input: { sample_rate: TAUX_CIBLE },
      },
    );
  }

  _surPipeline(evt) {
    if (evt.type === "run-start") {
      this._handler = evt.data?.runner_data?.stt_binary_handler_id ?? null;
      this._viderTampon();
    } else if (evt.type === "stt-end") {
      this._attenteStt?.ok(evt.data?.stt_output?.text ?? "");
      this._attenteStt = null;
    } else if (evt.type === "error") {
      this._attenteStt?.ko(new Error(evt.data?.message || "Le pipeline a échoué."));
      this._attenteStt = null;
    }
  }

  async _brancherCapture() {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    this._contexteAudio = new AudioCtx();
    // iOS démarre le contexte suspendu tant qu'aucun geste ne l'a réveillé.
    if (this._contexteAudio.state === "suspended") {
      await this._contexteAudio.resume();
    }
    // Le worklet vit dans ce fichier (§8 : un seul fichier) : un Blob de même
    // origine suffit à le charger.
    const url = URL.createObjectURL(
      new Blob([WORKLET], { type: "application/javascript" }),
    );
    try {
      await this._contexteAudio.audioWorklet.addModule(url);
    } finally {
      URL.revokeObjectURL(url);
    }
    this._sourceAudio = this._contexteAudio.createMediaStreamSource(
      this._fluxMicro,
    );
    this._noeud = new AudioWorkletNode(this._contexteAudio, "luna-pcm");
    this._noeud.port.onmessage = (evt) => this._envoyerPcm(evt.data);
    this._sourceAudio.connect(this._noeud);
  }

  _envoyerPcm(echantillons) {
    // Copie pour l'identification (C3) : une phrase, une requête, plafonnée.
    if (this._voixNecessaire && this._octetsCopies < OCTETS_IDENTITE_MAX) {
      this._copiePcm.push(echantillons);
      this._octetsCopies += echantillons.byteLength;
    }
    if (this._modeEcoute === "inscription") return;

    const socket = this._hass?.connection?.socket;
    if (this._handler === null || !socket) {
      // Le pipeline n'a pas encore dit sur quel canal binaire écrire : on garde
      // le début de la phrase plutôt que de le perdre.
      this._tampon.push(echantillons);
      return;
    }
    const trame = new Uint8Array(1 + echantillons.byteLength);
    trame[0] = this._handler;
    trame.set(new Uint8Array(echantillons.buffer), 1);
    socket.send(trame);
  }

  _viderTampon() {
    const attendus = this._tampon;
    this._tampon = [];
    for (const trame of attendus) this._envoyerPcm(trame);
  }

  async _arreterEcoute() {
    // On ne coupe jamais un démarrage à moitié fait (voir `_demarrerEcoute`).
    if (this._demarrage) await this._demarrage.catch(() => {});
    if (!this._ecoute) return;
    this._ecoute = false;
    this._couperCapture();

    if (this._modeEcoute === "inscription") {
      this._orbe("idle");
      this._sousTitre("Prête");
      this._attenteCapture?.ok(this._audioCopie());
      this._attenteCapture = null;
      return;
    }

    const socket = this._hass?.connection?.socket;
    if (this._handler !== null && socket) {
      // Trame réduite à l'octet de canal : « j'ai fini de parler ».
      socket.send(new Uint8Array([this._handler]));
    }

    this._orbe("thinking");
    this._sousTitre("Je transcris…");

    let texte = "";
    try {
      texte = await new Promise((ok, ko) => {
        this._attenteStt = { ok, ko };
        setTimeout(() => ko(new Error("La transcription n'est pas revenue.")), 20000);
      });
    } catch (err) {
      this._fermerTranscription();
      this._echecEcoute(this._messageErreur(err));
      return;
    }
    this._fermerTranscription();

    if (!texte.trim()) {
      this._echecEcoute(ERREURS_LOCALES.stt_failed);
      return;
    }

    // L'identification vient **avant** l'échange : c'est elle qui fixe le
    // profil, donc le scope des actions (§3, F3). La faire après reviendrait à
    // évaluer la demande au nom de quelqu'un d'autre.
    await this._identifier();

    this._q("textarea").value = texte;
    await this._envoyer(true);
  }

  _audioCopie() {
    const total = this._copiePcm.reduce((n, t) => n + t.length, 0);
    const tout = new Int16Array(total);
    let curseur = 0;
    for (const trame of this._copiePcm) {
      tout.set(trame, curseur);
      curseur += trame.length;
    }
    this._copiePcm = [];
    this._octetsCopies = 0;
    return tout;
  }

  _couperCapture() {
    this._noeud?.port && (this._noeud.port.onmessage = null);
    this._noeud?.disconnect();
    this._sourceAudio?.disconnect();
    this._fluxMicro?.getTracks().forEach((piste) => piste.stop());
    this._contexteAudio?.close().catch(() => {});
    this._noeud = null;
    this._sourceAudio = null;
    this._fluxMicro = null;
    this._contexteAudio = null;
  }

  _fermerTranscription() {
    this._attenteStt = null;
    if (this._desabonnerStt) {
      this._desabonnerStt();
      this._desabonnerStt = null;
    }
  }

  _echecEcoute(message) {
    this._ecoute = false;
    this._couperCapture();
    this._fermerTranscription();
    this._orbe("idle");
    this._sousTitre("Prête");
    this._erreur(message);
  }

  // ── Identité (P3) ──────────────────────────────────────────────────

  /** PCM 16 bits → base64, pour une requête unique (décision C3). */
  static _base64(echantillons) {
    const octets = new Uint8Array(echantillons.buffer, 0, echantillons.byteLength);
    let binaire = "";
    // Par tranches : `String.fromCharCode(...tout)` explose la pile au-delà de
    // quelques dizaines de milliers d'octets, et on en a des centaines.
    for (let i = 0; i < octets.length; i += 8192) {
      binaire += String.fromCharCode.apply(null, octets.subarray(i, i + 8192));
    }
    return btoa(binaire);
  }

  /**
   * Envoie une copie de la phrase pour reconnaître la voix.
   *
   * Ne fait rien si la session Home Assistant désigne déjà quelqu'un (C1) :
   * sur un N95, ne pas calculer est la meilleure optimisation.
   */
  async _identifier() {
    if (!this._voixNecessaire) {
      this._copiePcm = [];
      this._octetsCopies = 0;
      return;
    }
    const audio = this._audioCopie();
    if (!audio.length) return;

    try {
      const resultat = await this._appel({
        type: "luna/identity/voice",
        audio: LunaCard._base64(audio),
      });
      if (resultat.asked) this._demanderQuiParle();
    } catch (err) {
      const code = err && err.code;
      if (code === "not_enrolled") {
        this._erreur(
          "Je ne connais encore aucune voix. Ouvre le badge en haut à droite " +
            "pour m'apprendre la tienne.",
        );
      } else if (code !== "remote_biometrics" && code !== "model_unavailable") {
        this._erreur(this._messageErreur(err));
      }
      // Un refus en distant ou un modèle absent ne casse pas la conversation :
      // Luna continue, simplement sans savoir qui parle.
    }
  }

  /** La branche « ça ne suffit pas » de C4 : Luna demande plutôt que deviner. */
  _demanderQuiParle() {
    const bloc = document.createElement("div");
    bloc.className = "proposition";
    const titre = document.createElement("h4");
    titre.textContent = "Je ne suis pas sûre de qui parle.";
    const pourquoi = document.createElement("p");
    pourquoi.textContent = "Dis-le-moi, et je m'en souviens quelques minutes.";
    const actions = document.createElement("div");
    actions.className = "actions";

    const repondre = async (profil, accepte) => {
      for (const b of actions.querySelectorAll("button")) b.disabled = true;
      try {
        await this._appel({
          type: "luna/identity/confirm",
          profile: profil,
          accept: accepte,
        });
        bloc.replaceChildren(
          Object.assign(document.createElement("p"), {
            textContent: accepte ? `C'est noté, ${profil}.` : "Très bien.",
          }),
        );
      } catch (err) {
        this._erreur(this._messageErreur(err));
      }
    };

    for (const profil of this._inscrits) {
      const bouton = document.createElement("button");
      bouton.textContent = NOMS[profil] || profil;
      bouton.addEventListener("click", () => repondre(profil, true));
      actions.append(bouton);
    }
    const ni = document.createElement("button");
    ni.textContent = "Ni l'un ni l'autre";
    ni.addEventListener("click", () => repondre(this._inscrits[0] || "", false));
    actions.append(ni);

    bloc.append(titre, pourquoi, actions);
    this._q(".fil").append(bloc);
    this._defiler();
  }

  // ── Panneau d'identité ─────────────────────────────────────────────

  _rendreIdentite() {
    const hote = this._q(".identite");
    if (!hote) return;
    hote.replaceChildren();

    if (!this._phases.identity) {
      hote.append(this._texteSimple("La reconnaissance arrive en phase 3."));
      return;
    }
    if (!this._voixNecessaire) {
      hote.append(
        this._texteSimple(
          "Sur cet appareil, Home Assistant sait déjà qui tu es : je n'ai " +
            "aucune voix à reconnaître.",
        ),
      );
      return;
    }

    for (const profil of ["guillaume", "clara", "liam"]) {
      const ligne = document.createElement("div");
      ligne.className = "alerte";
      const nom = document.createElement("h5");
      nom.textContent = NOMS[profil] || profil;
      const etat = document.createElement("p");
      etat.textContent = this._inscrits.includes(profil)
        ? "Voix enregistrée."
        : "Voix inconnue.";
      const actions = document.createElement("div");
      actions.className = "actions";

      const apprendre = document.createElement("button");
      apprendre.className = "primaire";
      apprendre.textContent = this._inscrits.includes(profil)
        ? "Réapprendre"
        : "Apprendre ma voix";
      apprendre.addEventListener("click", () => this._inscrire(profil));
      actions.append(apprendre);

      if (this._inscrits.includes(profil)) {
        const oublier = document.createElement("button");
        oublier.textContent = "Oublier";
        oublier.addEventListener("click", () => this._oublier(profil));
        actions.append(oublier);
      }
      ligne.append(nom, etat, actions);
      hote.append(ligne);
    }
  }

  _texteSimple(texte) {
    const p = document.createElement("p");
    p.className = "vide";
    p.textContent = texte;
    return p;
  }

  /** Inscription : une phrase, un appui, cinq fois (H45, H46). */
  async _inscrire(profil) {
    const hote = this._q(".identite");
    let session;
    let phrases;
    try {
      ({ session, phrases } = await this._appel({
        type: "luna/identity/enroll/start",
        profile: profil,
      }));
    } catch (err) {
      this._erreur(this._messageErreur(err));
      return;
    }

    this._inscription = { session, profil, index: 0, phrases, retenues: 0 };
    hote.replaceChildren();

    const consigne = document.createElement("div");
    consigne.className = "alerte";
    const titre = document.createElement("h5");
    const phrase = document.createElement("p");
    const etat = document.createElement("p");
    etat.className = "vide";
    const actions = document.createElement("div");
    actions.className = "actions";
    const bouton = document.createElement("button");
    bouton.className = "primaire rond-large";
    bouton.textContent = "Maintenir et lire";
    const annuler = document.createElement("button");
    annuler.textContent = "Annuler";
    annuler.addEventListener("click", () => {
      this._inscription = null;
      this._rendreIdentite();
    });
    actions.append(bouton, annuler);
    consigne.append(titre, phrase, etat, actions);
    hote.append(consigne);

    const afficher = () => {
      const i = this._inscription.index;
      titre.textContent = `Phrase ${i + 1} sur ${phrases.length}`;
      phrase.textContent = phrases[i];
    };
    afficher();

    bouton.addEventListener("pointerdown", async (evt) => {
      evt.preventDefault();
      this._debloquerAudio();
      etat.textContent = "";
      await this._demarrerEcoute("inscription");
    });
    for (const fin of ["pointerup", "pointercancel", "pointerleave"]) {
      bouton.addEventListener(fin, async () => {
        if (!this._ecoute || this._modeEcoute !== "inscription") return;
        const audio = await new Promise((ok) => {
          this._attenteCapture = { ok };
          this._arreterEcoute();
        });
        if (!audio.length) return;

        let reponse;
        try {
          reponse = await this._appel({
            type: "luna/identity/enroll/sample",
            session,
            index: this._inscription.index,
            audio: LunaCard._base64(audio),
          });
        } catch (err) {
          this._erreur(this._messageErreur(err));
          return;
        }

        if (!reponse.accepted) {
          etat.textContent =
            reponse.quality === "trop_court"
              ? "Trop court — garde le bouton appuyé pendant toute la phrase."
              : "Trop faible — parle un peu plus près du micro.";
          return;
        }
        this._inscription.retenues += 1;
        this._inscription.index += 1;
        if (this._inscription.index < phrases.length) {
          afficher();
          etat.textContent = "";
          return;
        }
        try {
          const fini = await this._appel({
            type: "luna/identity/enroll/finish",
            session,
          });
          this._inscrits = [...new Set([...this._inscrits, profil])];
          this._inscription = null;
          this._rendreIdentite();
          this._erreur(
            fini.coherence < 0.75
              ? `J'ai retenu ta voix, mais sans grande netteté (${fini.coherence}). ` +
                  "Recommence au calme si je me trompe."
              : `C'est retenu, ${NOMS[profil] || profil}.`,
          );
        } catch (err) {
          this._erreur(this._messageErreur(err));
        }
      });
    }
    bouton.addEventListener("contextmenu", (evt) => evt.preventDefault());
  }

  async _oublier(profil) {
    try {
      await this._appel({ type: "luna/identity/forget", profile: profil });
      this._inscrits = this._inscrits.filter((p) => p !== profil);
      this._rendreIdentite();
    } catch (err) {
      this._erreur(this._messageErreur(err));
    }
  }

  // ── Lecture de la réponse ──────────────────────────────────────────

  _doitParler() {
    if (this._config.speak === "jamais" || !this._phases.voice) return false;
    if (this._config.speak === "toujours") return true;
    return this._parVoix;
  }

  async _parler(texte) {
    if (!texte.trim()) return;
    let url;
    try {
      ({ url } = await this._appel({ type: "luna/speak", text: texte }));
    } catch (err) {
      this._erreur(this._messageErreur(err));
      return;
    }
    const lecteur = this._lecteur || (this._lecteur = new Audio());
    lecteur.src = url;
    lecteur.onended = () => this._orbe(this._alertes.size ? "alert" : "idle");
    this._orbe("speaking");
    this._sousTitre("Luna parle…");
    try {
      await lecteur.play();
    } catch {
      // Refus de lecture automatique : le texte est déjà dans le fil, on ne
      // fabrique pas une erreur pour ça.
      this._orbe("idle");
    }
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
