/*
 * Un faux objet `hass` minimal, pour éprouver luna-card hors de Home Assistant.
 *
 * On simule les deux chemins de la carte :
 *  - `connection.subscribeMessage({type:"sentinel_assist/converse"})` → streaming :
 *    la réponse est émise mot par mot (event_message {delta}), puis {done,text}.
 *  - `callWS({type:"conversation/process"})` → repli non-streamé (un seul bloc).
 *
 * Options : `streamAbsent` rejette l'abonnement (imite une intégration sans la
 * commande de streaming → la carte doit basculer sur le repli) ; `erreur` émet
 * un événement d'erreur (ou fait échouer le repli).
 */
export function fauxHass({ reply = "Bonjour Guillaume, tout est calme.", delai = 40,
                           erreur = false, streamAbsent = false } = {}) {
  const appels = [];
  const abonnements = [];
  const pause = (ms) => new Promise((r) => setTimeout(r, ms));
  // Mots ET espaces comme fragments distincts : l'accumulation reconstitue le texte exact.
  const fragments = reply.split(/(\s+)/).filter(Boolean);

  return {
    language: "fr",
    // Un agent de conversation « Sentinel » présent, pour la détection auto (repli).
    states: {
      "conversation.sentinel": { state: "unknown", attributes: { friendly_name: "Sentinel" } },
    },
    _appels: appels,
    _abonnements: abonnements,

    async callWS(msg) {
      appels.push(msg);
      if (msg.type !== "conversation/process") return {};
      await pause(delai);
      if (erreur) throw new Error("faux échec réseau");
      return {
        conversation_id: msg.conversation_id || "conv-test-1",
        response: { response_type: "action_done", speech: { plain: { speech: reply } } },
      };
    },

    connection: {
      async subscribeMessage(cb, sub) {
        abonnements.push(sub);
        if (streamAbsent) throw new Error("unknown_command"); // intégration sans streaming
        let annule = false;
        (async () => {
          if (erreur) {
            await pause(delai);
            if (!annule) cb({ error: "Sentinel est injoignable pour l'instant.", done: true });
            return;
          }
          for (const f of fragments) {
            await pause(delai);
            if (annule) return;
            cb({ delta: f });
          }
          if (!annule) cb({ done: true, text: reply });
        })();
        return () => { annule = true; }; // désabonnement
      },
    },
  };
}
