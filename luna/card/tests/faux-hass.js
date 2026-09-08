/**
 * Un faux `hass`, réduit à ce dont la carte se sert : `connection`, `states`.
 *
 * Il rejoue le contrat de luna/docs/P1-CONTRATS.md §4. Ce que ces tests
 * vérifient, c'est que la carte respecte ce contrat — pas que l'add-on le
 * respecte, ce qui est déjà couvert ailleurs.
 */
window.creerFauxHass = (options = {}) => {
  const journal = {
    envoyes: [],
    souscriptions: [],
    desabonnements: 0,
    binaire: [],
  };

  const info = options.info ?? {
    version: "0.1.0",
    addon: "online",
    capabilities: ["chat", "ha_control"],
    profile: {
      id: "guillaume",
      display_name: "Guillaume",
      confidence: 1.0,
      signals: { ha_user: 1.0 },
    },
    phases: { voice: true, identity: true, veille: false, guardian: false },
    identity: {
      profile: {
        id: "guillaume",
        display_name: "Guillaume",
        confidence: 1.0,
        signals: { ha_user: 1.0 },
      },
      expires_at: null,
      enrolled: options.inscrits ?? ["guillaume", "clara"],
      // C1 : par défaut on simule l'iPad partagé, le cas que P3 traite.
      voice_needed: options.voixNecessaire ?? true,
      voice_available: true,
    },
  };

  const historique = options.historique ?? {
    conversation_id: "c_1",
    messages: [],
    has_more: false,
  };

  const flux = { chat: null, feed: null, pipeline: null };

  // Un WAV vide : la lecture aboutit vraiment, au lieu d'un 404 sur file://.
  const AUDIO =
    "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAgD4AAAB9AAACABAAZGF0YQAAAAA=";

  const hass = {
    states: {
      "binary_sensor.luna_en_ligne": { state: options.enLigne ?? "on" },
      "light.salon": { state: "off" },
    },
    connection: {
      sendMessagePromise(message) {
        journal.envoyes.push(message);
        if (options.echecPonctuel) return Promise.reject(options.echecPonctuel);
        switch (message.type) {
          case "luna/info":
            return Promise.resolve(info);
          case "luna/history":
            return Promise.resolve(historique);
          case "luna/cancel":
            return Promise.resolve({ cancelled: true });
          case "luna/proposal/decide":
            return Promise.resolve(
              options.decision ?? { executed: true, results: [] },
            );
          case "luna/speak":
            if (options.echecSpeak) return Promise.reject(options.echecSpeak);
            return Promise.resolve({ url: AUDIO, engine: "tts.piper" });
          case "luna/identity":
            return Promise.resolve(info.identity);
          case "luna/identity/voice":
            if (options.echecIdentite) return Promise.reject(options.echecIdentite);
            return Promise.resolve({
              profile: "guillaume",
              confidence: 0.82,
              margin: 0.31,
              asked: options.demande ?? false,
            });
          case "luna/identity/confirm":
            return Promise.resolve({
              profile: message.profile,
              confidence: 1.0,
              expires_at: null,
            });
          case "luna/identity/enroll/start":
            return Promise.resolve({
              session: "e_1",
              phrases: ["Première phrase.", "Deuxième phrase."],
            });
          case "luna/identity/enroll/sample":
            return Promise.resolve(
              options.echantillon ?? {
                accepted: true,
                quality: "ok",
                remaining: 1,
              },
            );
          case "luna/identity/enroll/finish":
            return Promise.resolve({
              profile: "guillaume",
              samples: 2,
              coherence: options.coherence ?? 0.91,
            });
          case "luna/identity/forget":
            return Promise.resolve({ removed: 5 });
          case "luna/alerts/feedback":
            return Promise.reject({
              code: "not_implemented",
              message: "Cette capacité arrive en phase 4.",
            });
          default:
            return Promise.reject({ code: "internal", message: "inconnu" });
        }
      },
      // La carte écrit les trames PCM directement sur la socket, préfixées de
      // l'octet de canal donné par `run-start` — comme le fait le dialogue
      // vocal de Home Assistant.
      socket: {
        readyState: 1,
        send(donnees) {
          journal.binaire.push(Array.from(new Uint8Array(donnees)));
        },
      },
      subscribeMessage(rappel, message) {
        journal.souscriptions.push(message);
        if (message.type === "luna/chat" && options.echecChat) {
          return Promise.reject(options.echecChat);
        }
        const cle =
          message.type === "luna/chat"
            ? "chat"
            : message.type === "assist_pipeline/run"
              ? "pipeline"
              : "feed";
        flux[cle] = rappel;
        return Promise.resolve(() => {
          journal.desabonnements += 1;
          flux[cle] = null;
        });
      },
    },
  };

  window.__luna = {
    hass,
    journal,
    emettre(cle, evenement) {
      if (!flux[cle]) throw new Error(`aucun flux « ${cle} » ouvert`);
      flux[cle](evenement);
    },
    fluxOuvert: (cle) => flux[cle] !== null,
  };
  return hass;
};
