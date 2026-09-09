# Luna — P2 : voix. Hypothèses et contrats

**Statut : validé le 8 septembre 2026, puis implémenté.** B1 à B5 acceptés.
Les écarts constatés pendant l'écriture sont en **partie F**, en fin de page ;
le reste du document décrit ce qui tourne.

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

> ~~**Q3 — La tablette murale.**~~ ✅ **Répondu : c'est un iPad.** Le micro
> existe donc, et deux contraintes iOS ont été traitées dans le code plutôt que
> découvertes sur place :
>
> 1. **Le déblocage audio.** iOS refuse tout `play()` qui n'a pas été précédé
>    d'un `play()` déclenché par un vrai geste. La carte amorce donc son lecteur
>    avec un silence de 44 octets **au premier appui sur le micro**. Sans ça,
>    Luna serait muette sur iPad — et seulement sur iPad.
> 2. **La fréquence d'échantillonnage.** iOS ne laisse pas choisir celle du
>    `AudioContext` : elle vaut 44,1 ou 48 kHz. Le rééchantillonnage vers
>    16 kHz n'est donc pas une optimisation, c'est une obligation. Il se fait
>    dans un `AudioWorklet`, hors du fil principal.
>
> S'y ajoute le détail qui se voit tout de suite à l'usage : un appui maintenu
> sur iPad ouvre le menu contextuel et fait perdre le `pointerup`, donc la fin
> de l'enregistrement. Le bouton micro est en `touch-action: none`,
> `-webkit-touch-callout: none`, et annule `contextmenu`.

---

# Partie F — Écarts entre ce contrat et ce qui tourne

Six points ont bougé pendant l'implémentation. Aucun ne change une décision ;
tous viennent d'une contrainte rencontrée en écrivant le code.

| # | Ce que disait le contrat | Ce qui a été fait | Pourquoi |
|---|---|---|---|
| 1 | L'événement `message` du feed porte `{id, text, ts, speak}` | Il porte aussi **`role`** et `conversation_id` | Sans le rôle, la carte ne sait pas s'il faut une bulle utilisateur ou une bulle Luna. Un tour de parole en produit deux. |
| 2 | Seuls les `message` sont diffusés | Les **propositions** le sont aussi | On ne clique pas dans un haut-parleur. Une proposition née d'un tour vocal serait restée invisible et aurait expiré en cinq minutes sans que personne ne puisse l'accepter. |
| 3 | Rien de précisé sur qui décide de diffuser | Un drapeau **`diffuser`** dans la charge du relais, posé par l'agent | La provenance est un fait de transport : l'orchestrateur ne sait pas si l'échange vient d'une carte ou d'Assist, le relais si. Mettre ça dans le contexte aurait mélangé identité et transport. |
| 4 | Rien sur *quand* la carte parle | Une option **`speak`** : `voix` (défaut), `toujours`, `jamais` | Personne ne veut être lu à voix haute parce qu'il a tapé une question. Le défaut ne lit que ce qui a été demandé de vive voix ; les deux autres modes sont une ligne de configuration, pas une modification de code. |
| 5 | `card/pcm-worklet.js` était prévu comme nouveau fichier | Le worklet vit **dans `luna-card.js`**, chargé par un `Blob` de même origine | §8 exige **un seul fichier**. Un Blob suffit, et la promesse est tenue à la lettre. |
| 6 | Rien sur la concurrence appui/relâchement | Le démarrage et l'arrêt de l'écoute sont **sérialisés** | Un appui bref relâché avant que la capture soit prête coupait un démarrage en cours ; celui-ci échouait et fermait la transcription que l'arrêt attendait. L'enregistrement mourait sans un mot. Sur iPad, où l'on tapote, ce n'est pas un cas rare. Trouvé par les tests. |

---

# Partie G — Recette de P2, état

| # | Vérification | État |
|---|---|---|
| 1 | Add-ons faster-whisper et piper, pipeline « Luna » | ⏳ à faire sur Nova — **procédure en Partie H** |
| 2 | Appuyer, parler, relâcher → la lumière s'allume, la réponse est lue | ✅ chemin testé de bout en bout (capture réelle, worklet réel, PCM réel) |
| 3 | Depuis le bouton Assist de l'app Companion | ✅ l'agent est testé dans une vraie instance HA ; le passage par l'app reste à faire sur Nova |
| 4 | Le tour de parole apparaît dans le fil de la carte | ✅ testé (diffusion du relais) |
| 5 | « ferme les volets » à l'oral → même refus qu'à l'écrit | ✅ testé — la voix ne donne aucun droit supplémentaire |
| 6 | Interruption | ✅ `luna/cancel` et Échap, inchangés depuis P1 |
| 7 | Latence STT sous 3 s | ⏳ **à mesurer sur Nova** — aucun test ne peut le faire ici, voir H.4 |
| 8 | Micro refusé ou page non sécurisée → message explicite | ✅ testé, les deux cas |
| 9 | `ruff`, `lint-imports`, `pytest` verts | ✅ 219 tests |

Restent deux réponses qui ne changent que des options : **Q1** (quelle voix
française) et **Q2** (un pipeline Assist existe-t-il déjà sur Nova).

---

# Partie H — La procédure sur Nova

Ce que la recette appelle « à faire sur Nova », en clair. Trente minutes, dont
une dizaine d'attente pendant que les modèles se téléchargent.

Prérequis : **P0 est finie**. Sans contexte sécurisé, le micro de la carte ne
peut pas s'ouvrir — le bouton existe, il explique pourquoi il ne fait rien
(H41), et c'est tout ce qu'il peut faire.

## H.1 — Les deux add-ons

**Paramètres → Modules complémentaires → Boutique.** Les deux sont officiels,
publiés par le projet Home Assistant, et n'ont besoin d'aucun dépôt ajouté.

**Whisper** (l'add-on porte ce nom ; il fait tourner *faster-whisper*) :

| Option | Valeur | Pourquoi |
|---|---|---|
| `model` | `base-int8` | H31. Le compromis visé par §7 : 1 à 3 s sur un N95. `int8` divise la mémoire et le temps CPU sans perte audible en français courant. |
| `language` | `fr` | Sans ça, Whisper détecte la langue à chaque tour — plus lent, et il se trompe sur les phrases courtes. |

**Piper** :

| Option | Valeur |
|---|---|
| `voice` | une voix française — `fr_FR-siwis-medium` si tu n'as pas d'avis (H32) |

Le choix de la voix est une question d'oreille, pas de technique : les
échantillons sont sur `rhasspy.github.io/piper-samples`, et changer d'avis coûte
une ligne d'option. Une voix `low` coûte moins de CPU mais s'entend. Si ta
version de l'add-on expose une option `streaming`, active-la : Luna envoie sa
réponse en flux, et Piper peut commencer à parler avant qu'elle soit finie.

Démarrer les deux. Le premier lancement télécharge le modèle — quelques minutes,
une seule fois.

## H.2 — Les brancher

Les add-ons ne se câblent pas tout seuls : ils parlent le protocole **Wyoming**,
et Home Assistant doit les découvrir.

**Paramètres → Appareils et services.** Deux découvertes « Wyoming Protocol »
attendent en haut de la page. **Configurer** les deux. Tu obtiens deux entités :
une de reconnaissance vocale, une de synthèse.

C'est l'étape la plus facile à sauter, parce que rien ne la réclame : sans elle,
les add-ons tournent, la page des assistants ne les propose pas, et il n'y a
aucun message d'erreur nulle part.

## H.3 — Le pipeline

**Paramètres → Assistants vocaux → Ajouter un assistant.**

| Champ | Valeur |
|---|---|
| Nom | `Luna` |
| Langue | Français |
| Agent de conversation | **Luna** |
| Reconnaissance vocale | l'entité Whisper, langue `fr` |
| Synthèse vocale | l'entité Piper, la voix choisie |
| Mot d'éveil | **aucun** |

Pas de mot d'éveil, c'est une décision, pas un oubli : B4. Un mot d'éveil suppose
d'envoyer de l'audio en continu au serveur, et le N95 fait déjà tourner Whisper.

Puis **définir ce pipeline comme préféré** (H33). Sans ça, la carte devrait le
nommer explicitement dans `assist_pipeline/run`, et le bouton Assist de l'app
Companion continuerait de parler à l'assistant intégré de Home Assistant — pas à
Luna. Le symptôme est déroutant : la carte marche, la voix répond, mais ce n'est
pas Luna qui répond.

## H.4 — La recette

Les points de la Partie G qui attendaient Nova :

1. **Point 1** — les deux add-ons tournent, le pipeline « Luna » existe, Luna en
   est l'agent.
2. **Point 3** — depuis le **bouton Assist de l'app Companion**, sans ouvrir
   Loggia : « allume le salon ». La lumière s'allume, la réponse est lue. Puis
   vérifier le **point 4** : ce tour de parole apparaît dans le fil de la carte
   restée ouverte sur un autre écran (H36).
3. **Point 7** — la latence. Sur la page de l'assistant, la vue de **débogage**
   liste les exécutions et le temps de chaque étape. Une phrase courte doit
   passer la reconnaissance **sous 3 s** (H38).

   ⚠️ **C'est l'étape de reconnaissance seule qui est mesurée**, pas le délai
   ressenti. Celui-ci additionne quatre choses : transcrire, réfléchir, agir,
   puis parler. Les confondre mène au mauvais réglage — changer de modèle
   Whisper parce que Claude a mis du temps ne fera rien gagner.

   La façon de les distinguer sans même ouvrir la vue de débogage : **Whisper
   dépend de la longueur de l'audio, pas de la difficulté de la demande.** Un
   délai qui varie selon ce qu'on demande vient d'après la transcription. Un
   délai qui varie selon la longueur de la phrase prononcée vient d'elle.

   Le premier échange après un redémarrage est plus lent que les suivants : le
   préfixe de prompt n'est pas encore en cache. C'est attendu, et c'est aussi la
   vérification de P1 qui restait à faire — `cache_read_input_tokens > 0` au
   second échange.

Si c'est trop lent, `tiny-int8`. Si la transcription est approximative,
`small-int8`. Le réglage se fait à l'oreille, après mesure — H31 le prévoit, et
c'est une ligne d'option.

## H.5 — Deux choses qui ne se voient qu'à l'usage

**Quand Luna parle.** L'option `speak` de la carte vaut `voix` par défaut : elle
ne lit à voix haute que ce qui a été demandé de vive voix. `toujours` lit aussi
les réponses tapées, `jamais` la rend muette dans la carte sans toucher aux
satellites.

**La longueur des réponses.** Le prompt porte déjà la consigne : une réponse lue
à voix haute tient en une ou deux phrases, sans énumération. C'est pour ça
qu'une même question donne une réponse plus courte au micro qu'au clavier — ce
n'est pas une troncature.

## H.6 — Le réveil à la voix, concrètement

B4 renvoyait le mot d'éveil « aux satellites » sans dire ce que c'était. Voici.

**Rien à coder côté Luna.** Le mot d'éveil vit entièrement dans le satellite et
le pipeline Assist : quand il se déclenche, un tour de parole ordinaire arrive à
Luna, exactement comme un appui sur le bouton de la carte. Luna ne saura même
pas qu'il y a eu un mot d'éveil. C'est une question de matériel et de
configuration, pas de code.

**Deux moteurs, et la différence est structurante.**

| | Où il tourne | Ce qui traverse le réseau |
|---|---|---|
| **microWakeWord** | dans le satellite, sur un ESP32-S3 | rien, jusqu'au déclenchement |
| **openWakeWord** | sur Nova, en add-on | **l'audio en continu**, en permanence |

Le second est celui que B4 écarte : il suppose qu'un appareil diffuse son micro
sans arrêt vers le N95, qui décode déjà de la parole par ailleurs. Le premier ne
coûte rien à Nova tant que personne ne parle.

**Le matériel de référence** est la *Home Assistant Voice Preview Edition* de
Nabu Casa, une soixantaine d'euros : ESP32-S3, DSP audio XMOS pour l'annulation
d'écho — c'est lui qui permet d'être entendu pendant que la musique joue —,
deux micros, et un interrupteur physique qui **coupe l'alimentation** des micros
plutôt que de les désactiver logiciellement. Cette dernière ligne compte : c'est
la seule forme de mise en sourdine qu'on puisse vérifier sans faire confiance à
un logiciel.

> ⚠️ **À vérifier avant d'acheter.** Une régression connue a cassé le mot
> d'éveil sur certaines versions d'ESPHome à partir de la 2026.3.3 : la nouvelle
> pile audio consomme assez de mémoire pour ne plus en laisser à
> `micro_wake_word`. Regarde où en est le correctif au moment où tu commandes.

**L'iPad du couloir ne peut pas jouer ce rôle.** Un navigateur ne fait pas de
détection locale, donc il faudrait diffuser en continu — le cas exclu ci-dessus.
Il reste ce qu'il est : un excellent appui-pour-parler, sur une tablette déjà au
mur.
