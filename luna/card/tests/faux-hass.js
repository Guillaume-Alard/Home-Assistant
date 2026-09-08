/**
 * Un faux `hass`, réduit à ce dont la carte se sert : `connection`, `states`.
 *
 * Il rejoue le contrat de luna/docs/P1-CONTRATS.md §4. Ce que ces tests
 * vérifient, c'est que la carte respecte ce contrat — pas que l'add-on le
 * respecte, ce qui est déjà couvert ailleurs.
 */
window.creerFauxHass = (options = {}) => {
  const journal = { envoyes: [], souscriptions: [], desabonnements: 0 };

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
    phases: { voice: false, identity: false, veille: false, guardian: false },
  };

  const historique = options.historique ?? {
    conversation_id: "c_1",
    messages: [],
    has_more: false,
  };

  const flux = { chat: null, feed: null };

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
          case "luna/alerts/feedback":
            return Promise.reject({
              code: "not_implemented",
              message: "Cette capacité arrive en phase 4.",
            });
          default:
            return Promise.reject({ code: "internal", message: "inconnu" });
        }
      },
      subscribeMessage(rappel, message) {
        journal.souscriptions.push(message);
        if (message.type === "luna/chat" && options.echecChat) {
          return Promise.reject(options.echecChat);
        }
        const cle = message.type === "luna/chat" ? "chat" : "feed";
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
