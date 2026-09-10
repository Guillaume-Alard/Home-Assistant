/*
 * Un faux objet `hass` minimal, pour éprouver luna-card hors de Home Assistant.
 *
 * Trois chemins simulés :
 *  - `subscribeMessage({type:"sentinel_assist/converse"})` → streaming texte
 *    (event_message {delta}, puis {done,text}).
 *  - `callWS({type:"conversation/process"})` → repli non-streamé (un bloc).
 *  - `subscribeMessage({type:"assist_pipeline/run"})` → pipeline vocal : émet la
 *    séquence run-start / stt-end / intent-end / tts-end / run-end d'HA. La capture
 *    micro et la lecture audio sont stubées dans le banc (banc.html).
 *
 * Options : `streamAbsent` (l'abonnement converse échoue → repli), `erreur`
 * (événement d'erreur / échec du repli), `pipelineErreur` (le pipeline émet une
 * erreur), `transcript` (texte « entendu »).
 */
export function fauxHass({ reply = "Bonjour Guillaume, tout est calme.", delai = 40,
                           erreur = false, streamAbsent = false, pipelineErreur = false,
                           transcript = "éteins le salon" } = {}) {
  const appels = [];
  const abonnements = [];
  const pause = (ms) => new Promise((r) => setTimeout(r, ms));
  const fragments = reply.split(/(\s+)/).filter(Boolean);

  const socket = { binaryType: "arraybuffer", _frames: [], send(buf) { this._frames.push(buf); } };
  const dejaReveille = { v: false };  // le mot d'éveil ne se détecte qu'une fois (test)

  return {
    language: "fr",
    states: {
      "conversation.sentinel": { state: "unknown", attributes: { friendly_name: "Sentinel" } },
    },
    _appels: appels,
    _abonnements: abonnements,
    _socket: socket,

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
      socket,
      async subscribeMessage(cb, sub) {
        abonnements.push(sub);

        if (sub.type === "assist_pipeline/run") {
          const wake = sub.start_stage === "wake_word";
          let annule = false;
          const emit = (type, data) => { if (!annule) cb({ type, data: data || {} }); };
          (async () => {
            await pause(delai); emit("run-start", { runner_data: { stt_binary_handler_id: 1 } });
            if (wake) {
              emit("wake_word-start");
              // Le mot d'éveil ne « sonne » qu'une fois : au ré-armement, on reste
              // en veille (pas de nouvelle détection), pour ne pas boucler le test.
              if (dejaReveille.v) return;
              await pause(delai); emit("wake_word-end", { wake_word_output: { wake_word_id: "luna" } });
              dejaReveille.v = true;
            }
            await pause(delai); emit("stt-start");
            await pause(delai * 2); emit("stt-end", { stt_output: { text: transcript } });
            if (pipelineErreur) { await pause(delai); emit("error", { message: "Transcription impossible." }); return; }
            await pause(delai); emit("intent-start");
            await pause(delai); emit("intent-end", {
              intent_output: { conversation_id: "c1",
                response: { speech: { plain: { speech: reply } } } },
            });
            await pause(delai); emit("tts-start");
            await pause(delai); emit("tts-end", { tts_output: { url: "/api/tts_proxy/luna.mp3" } });
            await pause(delai); emit("run-end");
          })();
          return () => { annule = true; };
        }

        // Streaming texte (sentinel_assist/converse).
        if (streamAbsent) throw new Error("unknown_command");
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
        return () => { annule = true; };
      },
    },
  };
}
