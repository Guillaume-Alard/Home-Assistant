# Luna — P2 : voix. Hypothèses et contrats

**Statut : en attente de validation.** Aucun code n'est écrit tant que ce
document n'est pas tranché (§11 du cahier des charges).

Sortie testable attendue (§11) : **piloter une lumière à l'oral.**

---

## 0. Où on en est

**P0 ne bloque pas.** Vider le champ « URL interne » dans l'app Companion donne
un contexte sécurisé immédiatement — l'adresse Nabu Casa est en HTTPS valide et
fonctionne depuis le réseau local. DuckDNS remplacera la béquille ce week-end ;
P2 peut s'écrire et se tester d'ici là, sans rien attendre.

**Ce qui existe déjà et sert directement.** Le fil de conversation, la
persistance, l'arbitre d'autonomie, les outils et le relais sont livrés en P1.
La voix n'ajoute pas un second cerveau : elle ajoute **une deuxième porte
d'entrée** vers le même.

**Trois pièces de Sentinel sont récupérables** — une de plus que les deux
prévues par §2 du cahier des charges :

| Fichier | Ce qui sert |
|---|---|
| `custom_components/sentinel_assist/conversation.py` | La forme d'un `ConversationEntity` qui délègue tout au cerveau |
| `ui/js/pcm-worklet.js` | Le rééchantillonnage vers 16 kHz mono PCM16, dans un `AudioWorklet` |
| `ui/js/audio-capture.js` | La logique d'appui-pour-parler |

---

# Partie A — Cinq décisions

## B1. Luna devient un agent de conversation. Elle ne fait ni STT ni TTS.

**La contradiction.** Le schéma de §3 accroche « Whisper (STT) » et « Piper
(TTS) » sous l'add-on, et §3.1 les liste comme providers de L1. Mais §7 dit :
« **Voie principale : pipeline Assist de HA.** Pas de dépendance cloud pour la
reconnaissance. »

Les deux ne peuvent pas être vrais. Ou Luna porte le STT et le TTS, ou le
pipeline Assist les porte.

**Ce que je propose : Assist gagne.** Quatre raisons, dans l'ordre du poids :

1. **Whisper et Piper existent déjà en add-ons officiels**, par le protocole
   Wyoming, maintenus par le projet Home Assistant. Les réécrire dans Luna,
   c'est refaire la capture audio, la détection de fin de phrase, le découpage,
   le cycle de vie des modèles — pour un résultat moins bon, sur un CPU qui n'a
   rien à donner.
2. **§7 le dit déjà.** « Voie principale : pipeline Assist de HA. »
3. **Les satellites viennent gratuitement.** Le bouton Assist de l'app
   Companion, la tablette murale, un ESP32 plus tard : tout ce qui parle à
   Assist parlera à Luna, sans une ligne de plus.
4. **Les couches ne bougent pas.** L1 ne gagne aucun provider, L2 aucun moteur.
   C'est bon signe : une phase qui n'oblige pas à tordre l'architecture est une
   phase bien posée.

**Conséquence.** `providers/stt.py` et `providers/tts.py` n'existeront jamais.
À la place :

- l'**intégration** gagne `conversation.py` : un `ConversationEntity` qui relaie
  vers l'add-on, par le même op `chat` que la carte ;
- deux add-ons officiels à installer sur Nova : **faster-whisper** et **piper** ;
- un pipeline Assist dans HA qui les câble : whisper → Luna → piper.

**Vérifié contre Home Assistant 2026.2.3 :** `ConversationEntity` expose
`_async_handle_message(user_input, chat_log)`, et `ChatLog` expose
`async_add_delta_content_stream`. **Les agents en streaming sont donc
supportés** : les deltas de Luna descendent dans le pipeline au fil de l'eau, et
Piper peut commencer à parler avant la fin de la réponse. C'est ce que faisait
Sentinel « phrase par phrase », et on le récupère sans effort.

> **Écart à acter.** Le schéma de §3 devient faux : Whisper et Piper ne pendent
> plus sous l'add-on Luna, ils sont à côté, dans Home Assistant.

## B2. Le micro de la carte passe par le pipeline Assist, en STT seul.

La carte a son propre bouton micro (§8, composant 3). Il faut donc que le
navigateur envoie de l'audio quelque part.

**Ce que je propose.** La carte appelle `assist_pipeline/run` avec
`start_stage: "stt"` et `end_stage: "stt"` — c'est exactement ce que fait le
dialogue vocal de Home Assistant. Elle récupère la transcription, puis
**l'envoie dans `luna/chat`, comme si elle avait été tapée**.

*Vérifié :* la commande `assist_pipeline/run` existe bien, avec `start_stage` et
`end_stage`.

**Pourquoi pas le pipeline complet** (STT → agent → TTS en un seul appel) :

| | STT seul + `luna/chat` | Pipeline complet |
|---|---|---|
| Chemin du cerveau | **un seul**, écrit et oral confondus (§7 : « indifféremment ») | deux chemins qui doivent rester d'accord |
| Streaming dans la carte | conservé — l'orbe et le fil se comportent à l'identique | la réponse arrive d'un bloc |
| Stratégie TTS de §7 | reste chez Luna, y compris le cache de phrases figées | passe sous le contrôle du pipeline |
| Coût | il faut déclencher la synthèse soi-même (voir B3) | gratuit |

Le seul coût est réglé par B3, et il est petit.

**Ce que ça garantit accessoirement :** aucun octet d'audio ne transite par le
relais de Luna, et la carte continue de ne parler qu'à Home Assistant — §3 est
respecté à la lettre.

## B3. La carte parle par `luna/speak`, elle ne va pas chercher l'audio elle-même.

Une fois la réponse écrite, il faut la faire entendre. La carte n'a pas le droit
de faire un `fetch()` (§3), et lui faire manipuler un jeton d'authentification
pour appeler l'API TTS serait une mauvaise idée.

**Ce que je propose.** Une commande `luna/speak {text}` → `{url}`.
L'intégration appelle `tts.async_create_stream(...)` côté Python et renvoie
l'URL **de même origine** que Home Assistant produit. La carte se contente de :

```js
lecteur.src = url;   // /api/tts_proxy/… — même origine, aucun fetch, aucun jeton
```

*Vérifié :* `tts.async_create_stream(hass, engine, language, options)` existe et
rend un `ResultStream` avec `.url`, `.async_set_message()` et
`.async_set_message_stream()`. Ce dernier servira en P4 pour la synthèse au fil
de l'eau, et `async_override_result()` est exactement le crochet dont le cache
de phrases figées aura besoin. Rien à inventer plus tard.

## B4. Pas de mot d'éveil en P2.

§8 dit « push-to-talk, **et wake-word si le pipeline le permet** ».

Il ne le permet pas, pour la carte. Un mot d'éveil dans un onglet de navigateur
suppose d'envoyer de l'audio en continu au serveur — le N95 fait déjà tourner
Whisper, il n'a pas ça à donner. Le mot d'éveil de Home Assistant
(openWakeWord) est conçu pour des **satellites** qui écoutent en local et
n'envoient qu'après déclenchement.

**Donc : appui-pour-parler seulement.** Le mot d'éveil viendra avec les
satellites, et ce n'est pas une phase du cahier des charges. Je le dis plutôt
que de le laisser tomber en silence.

## B5. Des trois couches de TTS de §7, P2 n'en fait qu'une.

| Couche | Sert à | Phase |
|---|---|---|
| 1. Phrases figées → cache audio | **les alertes de veille** | **P4** — elle arrive avec ce qu'elle sert |
| 2. Réponses dynamiques → Piper local | tout le reste | **P2** |
| 3. Voix custom | le caractère | side-quest, ne bloque rien |

La couche 1 n'a rien à synthétiser en P2 : il n'y a pas encore d'alerte. La
construire maintenant serait de l'anticipation (§11 : « Pas d'anticipation sur
les phases suivantes »).

---

# Partie B — Hypothèses

Si tu ne dis rien sur une ligne, je code ce qui est écrit dedans.

| # | Hypothèse | Si c'est faux |
|---|---|---|
| H31 | Add-on **faster-whisper**, modèle `base-int8`, langue `fr`. §7 annonce 1 à 3 s sur le N95. | `tiny-int8` si c'est trop lent, `small-int8` si la transcription est trop approximative. Le réglage se fait à l'oreille, après mesure. |
| H32 | Add-on **piper**, une voix française `medium`. | Le choix t'appartient — voir la question Q1. Une voix `low` coûte moins de CPU mais s'entend. |
| H33 | **Un seul pipeline Assist**, nommé « Luna », agent de conversation = Luna, marqué comme préféré. | S'il en existe déjà un, la carte devra nommer explicitement le pipeline dans `assist_pipeline/run`. |
| H34 | La tablette murale a un micro qui marche. | Voir Q3. Sans micro matériel, la tablette reste en écrit — le reste marche quand même. |
| H35 | Le prompt système gagne une consigne : **une réponse lue à voix haute est plus courte qu'une réponse lue à l'écran**. | Le prompt figé change une fois : un seul défaut de cache au déploiement, puis c'est stable. Sans ça, Luna récitera des paragraphes au micro. |
| H36 | Un tour de parole traité **hors de la carte** (satellite, bouton Assist) atterrit dans la **même conversation** SQLite et est poussé aux cartes ouvertes par `luna/feed`. | Sans ça, §7 (« à l'écrit et à l'oral, indifféremment ») est faux : deux fils parallèles qui s'ignorent. |
| H37 | Pas d'identité en P2 : un tour venu d'un satellite, sans utilisateur HA authentifié, prend `profil_par_defaut`. | C'est P3 qui donnera un vrai profil à la voix. En attendant, un satellite n'est pas plus qu'un invité. |
| H38 | Budget de latence : **STT sous 3 s** sur Nova, conformément à §7. Mesuré, pas supposé. | Si le N95 ne tient pas, H31 change de modèle. La mesure fait partie de la recette. |
| H39 | Format audio : **16 kHz, mono, PCM 16 bits little-endian** — ce qu'attend le pipeline Assist. Rééchantillonnage dans un `AudioWorklet`, repris de Sentinel. | Format imposé par Assist, pas par nous. |
| H40 | **Interruption** : relâcher puis réappuyer sur le micro, ou la touche Échap, annule l'échange en cours et coupe la lecture. `luna/cancel` existe déjà. | Sans ça, Luna continue de parler par-dessus la question suivante. |
| H41 | La carte n'active son micro que si `phases.voice` est vrai **et** `window.isSecureContext` est vrai. Sinon elle explique laquelle des deux conditions manque. | §8 : jamais d'échec silencieux. Le message existe déjà en P1. |

---

# Partie C — Contrats

## C.1 Nouvelles commandes WebSocket

```jsonc
// P2 — synthèse d'une réponse, pour la carte
{ "type": "luna/speak", "text": "J'allume le salon.", "language": "fr" }
→ { "url": "/api/tts_proxy/…", "engine": "tts.piper" }
```

Erreurs possibles : `tts_unavailable` (aucun moteur configuré),
`not_implemented` si `phases.voice` est faux.

## C.2 Contrats existants qui changent

| Contrat | Avant | Après |
|---|---|---|
| `luna/info` → `phases.voice` | `false` | **`true`** |
| `luna/feed` → événement `message` | documenté *[P4]* | **actif en P2** — c'est par là que passe un tour de parole traité hors de la carte (H36) |
| `ContexteRequete` | pas de provenance | gagne **`source: "texte" \| "voix"`** — Luna adapte sa longueur, pas ses droits |

`source` ne touche **jamais** à l'autorisation : le niveau d'une action reste
décidé par le registre de L0, à partir du couple `domaine.service` (§9.2). Une
demande à l'oral n'a pas plus de droits qu'une demande écrite.

## C.3 L'agent de conversation

```python
# integration/custom_components/luna/conversation.py — la forme, pas le code final
class AgentLuna(conversation.ConversationEntity):
    _attr_supported_features = conversation.ConversationEntityFeature.CONTROL

    @property
    def supported_languages(self) -> list[str]:
        return ["fr"]

    async def _async_handle_message(
        self,
        user_input: conversation.ConversationInput,
        chat_log: conversation.ChatLog,
    ) -> conversation.ConversationResult:
        # Les deltas de Luna descendent dans le pipeline au fil de l'eau :
        # Piper commence à parler avant la fin de la réponse.
        await chat_log.async_add_delta_content_stream(
            self.entity_id, self._flux_depuis_luna(user_input)
        )
        ...
```

Le contexte est construit comme pour la carte, avec deux différences :
`source: "voix"`, et l'utilisateur vient de `user_input.context.user_id` quand
il existe — sinon `profil_par_defaut` (H37).

## C.4 Ce que la carte envoie au pipeline

```jsonc
// STT seul : on veut une transcription, pas une réponse
{ "type": "assist_pipeline/run",
  "start_stage": "stt",
  "end_stage": "stt",
  "input": { "sample_rate": 16000 },
  "pipeline": "<id du pipeline Luna>"   // optionnel si Luna est le préféré
}
```

Home Assistant répond un `run-start` contenant un `handler_id` binaire. La carte
pousse ensuite les trames PCM sur la même connexion WebSocket, préfixées de cet
octet, puis une trame vide pour dire qu'elle a fini. Elle attend l'événement
`stt-end`, en tire le texte, et l'envoie dans `luna/chat`.

## C.5 Ce qui change dans le code existant

| Fichier | Changement |
|---|---|
| `addon/luna/kernel/schemas.py` | `ContexteRequete.source` |
| `addon/luna/providers/claude.py` | Une consigne de brièveté à l'oral dans le prompt figé (H35) |
| `addon/luna/engine/orchestrator.py` | `phases.voice = True` ; publier les tours de parole hors carte sur le bus, pour `luna/feed` |
| `addon/luna/interfaces/relay.py` | Diffuser l'événement `message` du feed |
| `integration/.../conversation.py` | **nouveau** — l'agent |
| `integration/.../websocket.py` | `luna/speak` |
| `integration/.../manifest.json` | `dependencies: ["conversation", "tts", "assist_pipeline"]` |
| `card/luna-card.js` | Capture micro, worklet PCM, appui-pour-parler, état `listening`, lecture audio |
| `card/pcm-worklet.js` | **nouveau** — repris de Sentinel |

Rien dans `kernel/autonomy.py`, `kernel/permissions.py` ni `engine/arbiter.py` :
la voix ne change aucune règle de §9. C'est le meilleur indicateur que le
découpage tient.

---

# Partie D — Recette de P2

Sortie testable de §11 : « Piloter une lumière à l'oral. »

| # | Vérification |
|---|---|
| 1 | Les add-ons faster-whisper et piper tournent, le pipeline « Luna » existe et Luna en est l'agent |
| 2 | Depuis la carte : appuyer, dire « allume le salon », relâcher → la transcription apparaît en bulle utilisateur, **la lumière s'allume**, la réponse est lue à voix haute |
| 3 | Depuis le bouton Assist de l'app Companion : même chose, sans ouvrir Loggia |
| 4 | Le tour de parole du point 3 **apparaît dans le fil de la carte** restée ouverte (H36) |
| 5 | « ferme les volets » à l'oral → même refus motivé qu'à l'écrit, même ligne dans `action_log` |
| 6 | Réappuyer pendant que Luna parle : elle s'arrête net (H40) |
| 7 | **Latence STT mesurée sous 3 s** sur Nova pour une phrase courte (H38) |
| 8 | Micro refusé ou page non sécurisée → message explicite, pas un bouton mort |
| 9 | `ruff`, `lint-imports`, `pytest` verts sur les trois artefacts |

Les points 2, 4, 5, 6 et 8 sont automatisables ; le 1, le 3 et le 7 se font à la
main sur Nova, comme la vérification du cache de prompt en P1.

---

# Partie E — Ce dont j'ai besoin de toi

> **Q1 — La voix.** Piper propose plusieurs voix françaises. C'est une question
> d'oreille, pas de technique : écoute-les sur `rhasspy.github.io/piper-samples`
> et dis-moi laquelle. Si tu n'as pas d'avis, je prends une voix `medium` et on
> ajuste — c'est une ligne d'option, changeable à tout moment.

> **Q2 — L'existant.** As-tu déjà un pipeline Assist configuré sur Nova, ou des
> add-ons de reconnaissance vocale installés ? Si oui, lesquels : je m'y branche
> au lieu d'en créer un deuxième.

> **Q3 — La tablette murale.** A-t-elle un micro, et fonctionne-t-il ? C'est
> l'appareil pour lequel P0 a été écrite ; si son micro est mort côté matériel,
> autant le savoir avant d'optimiser pour lui.

Ces trois réponses ne bloquent pas l'écriture du code : elles ne changent que
des options. Dis-moi si tu valides B1 à B5, et je code P2.
