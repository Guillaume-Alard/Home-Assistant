/*
 * Un faux objet `hass` minimal, pour éprouver luna-card hors de Home Assistant.
 *
 * On ne simule QUE ce que la carte utilise : `callWS` (avec le type
 * `conversation/process`), `states` et `language`. La réponse imite la forme
 * réelle d'HA — `response.speech.plain.speech` + `conversation_id` — pour que le
 * test exerce le vrai chemin d'extraction, pas une version simplifiée.
 */
export function fauxHass({ reply = "Bonjour Guillaume, tout est calme.", delai = 60, erreur = false } = {}) {
  const appels = [];
  return {
    language: "fr",
    // Un agent de conversation « Sentinel » présent, pour la détection auto.
    states: {
      "conversation.sentinel": { state: "unknown", attributes: { friendly_name: "Sentinel" } },
    },
    _appels: appels,
    async callWS(msg) {
      appels.push(msg);
      if (msg.type !== "conversation/process") return {};
      await new Promise((r) => setTimeout(r, delai));
      if (erreur) throw new Error("faux échec réseau");
      return {
        conversation_id: msg.conversation_id || "conv-test-1",
        response: {
          response_type: "action_done",
          speech: { plain: { speech: reply } },
        },
      };
    },
  };
}
