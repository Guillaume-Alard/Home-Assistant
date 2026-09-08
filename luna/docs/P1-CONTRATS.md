# Luna — P1 : contrats d'API

**Statut : en attente de validation.** Ce document décrit *ce qui sera écrit*,
pas ce qui est écrit. Il suppose les décisions de
[`P1-HYPOTHESES.md`](P1-HYPOTHESES.md) — en particulier **A1** (l'intégration
comme troisième livrable) et **A2** (ouvrants et alarme en lecture seule).

Chaque contrat porte sa phase. Ceux marqués **[Pxx]** sont *documentés
maintenant, implémentés plus tard* — c'est ce qu'exige §12. En P1, une commande
non implémentée répond `{"code": "not_implemented"}` : jamais un silence, jamais
un plantage.

---

## 1. Vue d'ensemble

### Trois artefacts

| Artefact | Rôle | Où | Taille visée |
|---|---|---|---|
| **`addon/`** | Le cerveau. Orchestrateur, API Claude, couches L0→L3. | Conteneur HAOS sur Nova | quelques milliers de lignes |
| **`integration/`** | Le nerf. Enregistre `luna/*`, relaie, expose `binary_sensor.luna_en_ligne`. | Processus Python de HA | ~250 lignes, aucune logique métier |
| **`card/luna-card.js`** | Le visage. Orbe, fil, saisie, badge, tiroir. | Navigateur, dans Loggia | un fichier, aucun build |

### Le chemin d'un message

```
 ①  L'utilisateur tape « allume le salon » dans la carte.
 ②  carte → hass.connection.subscribeMessage(cb, {type:"luna/chat", text:…})
 ③  HA authentifie, route vers l'intégration. connection.user est connu.
 ④  intégration → add-on   (WS interne, secret partagé, profil joint)
 ⑤  add-on → Claude        (streaming, outils, cache de prompt)
 ⑥  Claude demande l'outil commander_lumiere(salon, on)
 ⑦  L'ARBITRE d'autonomie résout light.turn_on → niveau 2 → autorisé pour ce profil
 ⑧  add-on → Home Assistant (call_service via le Supervisor)
 ⑨  add-on → intégration → carte : delta, delta, tool, done
 ⑩  action_log si niveau ≥ 3 (ici non : niveau 2)
```

Les points ⑦ et ⑧ sont l'invariant de sécurité : **aucun chemin de code
n'atteint ⑧ sans passer par ⑦.** Un test statique le vérifie.

---

## 2. Arborescence du dépôt `Alardware/luna`

```
luna/
├── addon/                              # ── LE CERVEAU ──────────────────
│   ├── config.yaml                     #   manifeste add-on (options, ports)
│   ├── Dockerfile
│   ├── run.sh
│   ├── pyproject.toml
│   ├── .importlinter                   #   les 4 contrats de couches
│   └── luna/
│       ├── kernel/                     # L0 — n'importe rien du projet
│       │   ├── contracts.py            #   Protocols : BrainProvider, HomeProvider…
│       │   ├── schemas.py              #   pydantic : Message, Delta, ToolCall…
│       │   ├── bus.py                  #   pub/sub asyncio
│       │   ├── settings.py             #   lecture de /data/options.json
│       │   ├── errors.py               #   LunaError → code + message français
│       │   ├── autonomy.py             #   ActionLevel + registre §9
│       │   └── permissions.py          #   scopes confort / personnel / critique
│       ├── providers/                  # L1 — n'importe que L0
│       │   ├── claude.py               #   client Anthropic, streaming + outils
│       │   ├── home.py                 #   client WebSocket Home Assistant
│       │   └── store.py                #   SQLite (conversations, action_log)
│       ├── engine/                     # L2 — n'importe que L0, providers injectés
│       │   ├── orchestrator.py         #   conversation, arbitrage, propositions
│       │   └── tools.py                #   définition des outils exposés à Claude
│       └── interfaces/                 # L3 — peut tout importer
│           ├── __main__.py             #   bootstrap, injection
│           ├── http.py                 #   les routes §12 (aiohttp)
│           └── relay.py                #   WS interne, consommé par l'intégration
│
├── integration/                        # ── LE NERF ─────────────────────
│   └── custom_components/luna/
│       ├── manifest.json
│       ├── const.py
│       ├── config_flow.py              #   hôte, port, secret partagé
│       ├── __init__.py                 #   connexion au relais, binary_sensor
│       └── websocket.py                #   enregistrement des commandes luna/*
│
├── card/
│   └── luna-card.js                    # ── LE VISAGE ───────────────────
│
├── docs/
│   ├── P0-HTTPS.md
│   ├── P1-HYPOTHESES.md
│   └── P1-CONTRATS.md                  #   ce fichier
│
├── ops/
│   ├── caddy/Caddyfile
│   └── deploy.sh                       #   rsync vers Nova via l'add-on SSH
│
└── .github/workflows/ci.yml            #   ruff + lint-imports + pytest
```

Sont **absents** de cette arborescence, volontairement : `stt.py`, `tts.py`,
`identity.py`, `memory.py`, `veille.py`, `learning.py`, `scheduler.py`,
`notifications.py`. Ils arrivent à leur phase, pas avant (§11 : « Pas
d'anticipation sur les phases suivantes »).

---

## 3. Couches verrouillées

### Correspondance avec §3.1

| Couche | Paquet | Contenu en P1 | Contenu prévu plus tard |
|---|---|---|---|
| **L0** | `luna.kernel` | contrats, schémas, bus, settings, erreurs, autonomie, permissions | — |
| **L1** | `luna.providers` | `claude`, `home`, `store` | `stt`, `tts`, `memory`, `identity` |
| **L2** | `luna.engine` | `orchestrator`, `tools` | `veille`, `learning`, `notifications`, `scheduler` |
| **L3** | `luna.interfaces` | `__main__`, `http`, `relay` | — |

### `.importlinter`

```ini
[importlinter]
root_package = luna
include_external_packages = True

[importlinter:contract:l0-kernel]
name = L0 kernel n'importe rien du projet
type = forbidden
source_modules =
    luna.kernel
forbidden_modules =
    luna.providers
    luna.engine
    luna.interfaces

[importlinter:contract:l1-providers]
name = L1 providers n'importe que L0
type = forbidden
source_modules =
    luna.providers
forbidden_modules =
    luna.engine
    luna.interfaces

[importlinter:contract:l2-engine]
name = L2 engine n'importe que L0 (les providers arrivent par injection)
type = forbidden
source_modules =
    luna.engine
forbidden_modules =
    luna.providers
    luna.interfaces

[importlinter:contract:l0-stdlib]
name = L0 kernel : stdlib et pydantic uniquement
type = forbidden
source_modules =
    luna.kernel
forbidden_modules =
    anthropic
    websockets
    aiohttp
    aiosqlite
```

Les trois premiers sont ceux de §3.1, littéralement. **Le quatrième est un
ajout** (hypothèse H15) : sans lui, la phrase « stdlib + pydantic uniquement »
n'est vérifiée par rien. `include_external_packages = True` est ce qui le rend
possible.

### Communication montante

Une couche basse ne rappelle **jamais** une couche haute par import. Elle publie
sur le bus de L0 :

```python
# L1, providers/home.py — le client HA remarque un changement d'état
await bus.publish(EntityChanged(entity_id="light.salon", state="on"))

# L2, engine/orchestrator.py — l'orchestrateur y réagit
bus.subscribe(EntityChanged, self._on_entity_changed)
```

Le bus est typé : `publish` accepte un modèle pydantic de `kernel.schemas`,
`subscribe` s'abonne sur la classe. Un événement non déclaré ne compile pas au
sens de `mypy` et ne passe pas les tests.

---

## 4. Contrat carte ↔ Home Assistant

C'est le contrat que la carte voit. Toutes les commandes sont dans l'espace de
noms `luna/`, enregistrées par l'intégration.

### Conventions

- **Requête ponctuelle** → `hass.connection.sendMessagePromise({type, …})`.
  Résout avec l'objet `result`, rejette avec `{code, message}`.
- **Flux** → `hass.connection.subscribeMessage(cb, {type, …})`. Résout avec une
  fonction de désabonnement. **La carte doit l'appeler dans
  `disconnectedCallback()`** — Lovelace détruit et recrée les cartes à chaque
  changement de vue, et une souscription oubliée fuit.
- **Toute erreur** porte un `code` machine et un `message` français affichable
  tel quel. Taxonomie complète en §8.
- **Permissions** : chaque commande vérifie `connection.user`. Un utilisateur HA
  non administrateur ne peut pas approuver une proposition de niveau ≥ 3.

### Table des commandes

| Commande | Forme | Phase | Rôle |
|---|---|---|---|
| `luna/info` | ponctuelle | **P1** | État initial : version, disponibilité, capacités, profil |
| `luna/chat` | flux | **P1** | Un échange, du premier delta au `done` |
| `luna/cancel` | ponctuelle | **P1** | Interrompre un échange en cours |
| `luna/history` | ponctuelle | **P1** | Restaurer le fil après un rechargement |
| `luna/feed` | flux | **P1** | Flux permanent : statut, et plus tard identité et alertes |
| `luna/proposal/decide` | ponctuelle | **P1** | Accepter ou refuser une action de niveau ≥ 3 |
| `luna/identity` | ponctuelle | *[P3]* | Forcer un recalcul du profil actif |
| `luna/alerts/feedback` | ponctuelle | *[P4]* | `Agir` / `Ignorer` / `Ne plus me le dire` |
| `luna/patterns` | ponctuelle | *[P4]* | Routines apprises d'un profil |
| `luna/suggestions` | ponctuelle | *[P4]* | Suggestions contextuelles du moment |
| `luna/identity/face` | ponctuelle | *[P6]* | Envoi d'une image, retour `{user, confidence, ttl}` |

### `luna/info` — **P1**

```jsonc
// requête
{ "type": "luna/info" }

// réponse
{
  "version": "0.1.0",
  "addon": "online",              // "online" | "offline" | "degraded"
  "capabilities": ["chat", "ha_control"],
  "profile": {
    "id": "guillaume",            // guillaume | clara | liam | guest | unknown
    "display_name": "Guillaume",
    "confidence": 1.0,
    "signals": { "ha_user": 1.0 } // P3 ajoutera presence, voice ; P6 face
  },
  "phases": {                     // ce que la carte doit afficher ou griser
    "voice": false, "identity": false, "veille": false, "guardian": false
  }
}
```

`addon: "degraded"` signifie : le relais répond, mais l'add-on n'atteint pas
Claude ou n'atteint pas HA. La carte reste utilisable en lecture. `"offline"`
déclenche le mode dégradé complet de §8.

### `luna/chat` — **P1**

Une souscription = un échange. La carte se désabonne après `done` ou `error`.

```jsonc
// requête
{
  "type": "luna/chat",
  "text": "allume le salon",
  "conversation_id": "c_01J…",   // optionnel ; absent = nouvelle conversation
  "client_id": "loggia-tablette" // pour la traçabilité dans action_log
}
```

Événements émis, dans cet ordre :

```jsonc
{ "event": "accepted",  "message_id": "m_01J…", "conversation_id": "c_01J…" }

{ "event": "delta",     "message_id": "m_01J…", "text": "J'allume" }
{ "event": "delta",     "message_id": "m_01J…", "text": " le salon." }

// pilote l'orbe et affiche « j'allume le salon… » sous la bulle
{ "event": "tool", "message_id": "m_01J…",
  "name": "commander_lumiere", "label": "Allumer le salon",
  "level": 2, "status": "running" }              // running | done | error

// action de niveau ≥ 3 : Luna s'arrête et demande
{ "event": "proposal", "message_id": "m_01J…",
  "proposal": {
    "id": "p_01J…",
    "level": 3,
    "title": "Régler le thermostat du séjour sur 20 °C",
    "why": "Tu m'as demandé d'avoir plus chaud, il fait 17,5 °C.",
    "actions": [
      { "domain": "climate", "service": "set_temperature",
        "target": { "entity_id": "climate.sejour" }, "data": { "temperature": 20 } }
    ],
    "expires_at": "2026-09-08T21:14:00+02:00"
  }}

{ "event": "done", "message_id": "m_01J…",
  "text": "J'allume le salon.",                  // texte complet, pour l'historique
  "usage": { "input_tokens": 2841, "output_tokens": 12,
             "cache_read_input_tokens": 2560 } } // 0 durablement = cache cassé

{ "event": "error", "message_id": "m_01J…",
  "code": "claude_no_credit",
  "message": "Le crédit de la clé API Anthropic est épuisé — recharge le compte sur console.anthropic.com, puis réessaie." }
```

Règles :

- `done` et `error` sont **exclusifs et terminaux**. Après l'un des deux, plus
  aucun événement n'arrive sur cette souscription.
- `proposal` **n'est pas terminal** : Luna peut continuer à parler après.
  La décision passe par `luna/proposal/decide`.
- Se désabonner en cours de flux **annule** l'échange côté add-on : la requête
  Claude est interrompue, les outils en cours sont laissés finir, aucun nouvel
  outil n'est lancé.

### `luna/cancel` — **P1**

```jsonc
{ "type": "luna/cancel", "message_id": "m_01J…" }
→ { "cancelled": true }
```

Pour un bouton « stop » qui garde la souscription ouverte. Le désabonnement
produit le même effet côté serveur.

### `luna/history` — **P1**

```jsonc
{ "type": "luna/history", "conversation_id": "c_01J…", "limit": 50 }

→ {
  "conversation_id": "c_01J…",
  "messages": [
    { "id": "m_01J…", "role": "user",      // user | luna | system
      "text": "allume le salon",
      "ts": "2026-09-08T20:31:04+02:00",
      "profile": "guillaume" },
    { "id": "m_01J…", "role": "luna", "text": "J'allume le salon.",
      "ts": "2026-09-08T20:31:06+02:00", "profile": null,
      "tools": [ { "name": "commander_lumiere", "label": "Allumer le salon",
                   "level": 2, "status": "done" } ] }
  ],
  "has_more": false
}
```

`conversation_id` absent → la conversation la plus récente du profil. C'est ce
qui permet à la carte de retrouver son fil après un rechargement de Loggia.

### `luna/feed` — **P1**

Souscription unique, ouverte au montage de la carte, fermée au démontage. Elle
porte tout ce que Luna dit **sans qu'on lui ait rien demandé**.

```jsonc
// P1 — le seul événement réellement émis en P1
{ "event": "status", "addon": "online", "detail": null }
{ "event": "status", "addon": "degraded", "detail": "Home Assistant injoignable" }

// [P3] — met à jour le badge d'identité de §8
{ "event": "identity",
  "profile": { "id": "clara", "display_name": "Clara", "confidence": 0.82,
               "signals": { "ha_user": 0.0, "presence": 0.6, "voice": 0.91 } } }

// [P4] — alimente le tiroir « Veille » de §8
{ "event": "alert",
  "alert": { "id": "a_01J…", "level": "warning",   // info | warning | critical
             "title": "La baie vitrée est ouverte",
             "why": "Il est 23 h 40 et l'alarme n'est pas armée.",
             "entity_id": "binary_sensor.baie_vitree",
             "ts": "2026-09-08T23:40:00+02:00",
             "actions": [ { "id": "act_close", "label": "Fermer" } ] } }
{ "event": "alert_cleared", "id": "a_01J…", "reason": "resolved" }

// [P4] — Luna prend la parole d'elle-même (rappel de coucher)
{ "event": "message",
  "message": { "id": "m_01J…", "text": "Il est l'heure d'aller te coucher.",
               "ts": "2026-09-08T23:45:00+02:00", "speak": true } }
```

En P1, seul `status` est émis. Les trois autres sont documentés pour que la
carte soit écrite une fois pour toutes — mais l'add-on ne les produit jamais
avant P3 et P4.

### `luna/proposal/decide` — **P1**

```jsonc
{ "type": "luna/proposal/decide", "proposal_id": "p_01J…",
  "decision": "accept" }              // accept | reject

→ { "executed": true,
    "results": [ { "domain": "climate", "service": "set_temperature",
                   "ok": true, "error": null } ] }
```

Règles, tirées de §9 :

- Une proposition expire au bout de **5 minutes** (`expires_at`). Passé ce
  délai : `{"code": "proposal_expired"}`.
- Acceptée **ou** refusée, elle est écrite dans `action_log` avec sa
  justification. §9.1 : « qu'elle soit acceptée ou refusée ».
- Une proposition **de niveau 5 ne peut pas exister** en v1 : elle est refusée à
  la construction, pas à l'exécution.
- Seul un utilisateur HA administrateur peut accepter un niveau 4.

### Commandes documentées, non implémentées en P1

```jsonc
// [P3]
{ "type": "luna/identity" } → { "profile": { … } }   // recalcul forcé

// [P4] — la boucle de feedback de §12
{ "type": "luna/alerts/feedback",
  "suggestion_id": "s_01J…", "action": "accepted" }  // accepted | rejected | muted
→ { "ok": true, "muted_until": null }

// [P4]
{ "type": "luna/patterns", "profile": "guillaume" }
→ { "patterns": [ { "id": "pat_01J…", "predicate": "heure_de_coucher",
                    "value": "23:20", "days": ["mon","tue","wed","thu"],
                    "entity_id": null, "confidence": 0.74,
                    "observations": 23, "last_seen": "2026-09-07T23:18:00+02:00" } ] }

// [P4]
{ "type": "luna/suggestions" }
→ { "suggestions": [ { "id": "s_01J…", "title": "Éteindre le séjour",
                       "why": "Tu es monté te coucher il y a 10 minutes.",
                       "score": 0.68, "level": 2,
                       "actions": [ … ] } ] }

// [P6]
{ "type": "luna/identity/face", "image": "<base64 jpeg>" }
→ { "profile": "guillaume", "confidence": 0.88, "ttl": 300 }
```

En P1 chacune répond `{"code": "not_implemented", "message": "Cette capacité
arrive en phase 4."}`. La carte grise le bouton correspondant au lieu de le
cacher : §8 interdit l'échec silencieux.

---

## 5. Contrat intégration ↔ add-on

Invisible pour la carte. Documenté parce que c'est là que la sécurité se joue.

- **Transport** : WebSocket, `ws://local-luna:8099/relay` (H7).
- **Authentification** : en-tête `X-Luna-Secret`, secret partagé (H9). Une
  connexion sans secret valide est fermée avec le code `4401`, sans détail.
- **Trame montante** (intégration → add-on) :

```jsonc
{ "id": 42, "op": "chat",
  "payload": { "text": "allume le salon", "conversation_id": "c_01J…" },
  "context": {
    "ha_user_id": "abc123…",       // posé par l'intégration, jamais par la carte
    "ha_user_name": "Guillaume",
    "is_admin": true,
    "profile": "guillaume",        // résolu par la correspondance de A6
    "client_id": "loggia-tablette",
    "local": true                  // §6 : la biométrie ne s'active qu'en local
  } }
```

  **Le `context` est posé par l'intégration, à partir de `connection.user`.** La
  carte ne peut ni le fournir ni l'influencer : c'est ce qui empêche un client
  de se déclarer administrateur.

- **Trame descendante** : `{ "id": 42, "event": "delta", … }`, avec les mêmes
  charges utiles qu'en §4. L'intégration ne fait que transposer `id` → `id` de
  souscription HA. **Elle ne relit ni ne réécrit le contenu.**
- **Reconnexion** : backoff exponentiel 1 s → 30 s. Pendant la coupure,
  `luna/info` renvoie `"offline"` et `binary_sensor.luna_en_ligne` passe à `off`
  — ce qui permet à la carte de détecter le mode dégradé sans aucun aller-retour.

---

## 6. Contrat §12 — API interne de l'add-on

Les routes littérales de §12, sur `http://local-luna:8099`. Consommées par
l'intégration et par `curl` en débogage, **jamais par le navigateur** (voir A5).

| Route §12 | Commande WS jumelle | Phase |
|---|---|---|
| `GET  /profile/{user}/patterns` | `luna/patterns` | [P4] |
| `GET  /suggestions` | `luna/suggestions` | [P4] |
| `POST /feedback` | `luna/alerts/feedback` | [P4] |
| `POST /identity/voice` | *aucune* — appelée par le pipeline vocal, pas par la carte | [P3] |
| `POST /identity/face` | `luna/identity/face` | [P6] |

Routes ajoutées, nécessaires en P1 :

| Route | Rôle | Phase |
|---|---|---|
| `GET  /health` | `{status, ha, claude, db, uptime}` — sonde de l'add-on | **P1** |
| `GET  /info` | ce que renvoie `luna/info` | **P1** |
| `WS   /relay` | le canal de §5 | **P1** |

### La boucle de feedback

§12 pose la règle : « un rejet fait baisser le score […] une suggestion refusée
trois fois ne remonte plus pendant 30 jours ». Traduction exacte, à implémenter
en P4 :

```
score ∈ [0, 1], initialisé à 0.5

accepted → score ← min(1.0, score + 0.15) ; rejections ← 0
rejected → score ← max(0.0, score − 0.25) ; rejections ← rejections + 1
muted    → muted_until ← maintenant + 30 jours

si rejections ≥ 3        → muted_until ← maintenant + 30 jours ; rejections ← 0
si muted_until > maintenant → la suggestion n'est jamais proposée
si score < 0.2           → la suggestion n'est jamais proposée
```

Le compteur porte sur la **clé de suggestion** (`predicate` + `profil` +
`entity_id`), pas sur l'instance : refuser trois fois « éteins le séjour le
soir » mute la règle, pas trois occurrences distinctes.

---

## 7. Échelle d'autonomie — le registre

§9.1 définit six niveaux. Voici comment un niveau est *décidé*.

**Il ne l'est jamais par le modèle.** Claude demande un outil ; l'orchestrateur
résout l'outil en un couple `domaine.service` concret ; le registre de
`kernel/autonomy.py` donne le niveau ; l'arbitre applique la règle. Claude n'a
aucun moyen de nommer un niveau, ni de le contourner : il ne voit même pas la
notion.

```python
# luna/kernel/autonomy.py — extrait du contrat, pas du code final
NIVEAU_PAR_DEFAUT = 3            # §9 : « le niveau par défaut […] est 3 »

REGISTRE: dict[str, int] = {
    # 1 — lecture, libre
    "homeassistant.read":            1,

    # 2 — confort réversible, libre si le profil l'autorise
    "light.turn_on":                 2,
    "light.turn_off":                2,
    "light.toggle":                  2,
    "switch.turn_on":                2,
    "switch.turn_off":               2,
    "scene.turn_on":                 2,
    "media_player.media_play":       2,
    "media_player.media_pause":      2,
    "media_player.volume_set":       2,
    "script.turn_on":                2,

    # 3 — état persistant, validation explicite
    "climate.set_temperature":       3,
    "climate.set_hvac_mode":         3,
    "input_number.set_value":        3,
    "input_datetime.set_datetime":   3,

    # 4 — configuration de HA ou de Loggia, validation explicite
    "automation.turn_off":           4,
    "automation.reload":             4,
    "lovelace.save_config":          4,

    # 5 — HORS PÉRIMÈTRE v1 (§9, F3). Refus à la construction.
    "cover.open_cover":              5,
    "cover.close_cover":             5,
    "lock.lock":                     5,
    "lock.unlock":                   5,
    "alarm_control_panel.alarm_arm_home":  5,
    "alarm_control_panel.alarm_arm_away":  5,
    "alarm_control_panel.alarm_disarm":    5,
    "notify.*":                      5,   # « envoi vers l'extérieur »
}
```

Comportement de l'arbitre :

| Niveau | Comportement |
|---|---|
| 0, 1 | Exécution directe. Pas de journal. |
| 2 | Exécution directe **si** le scope du profil actif contient `confort`. Sinon → proposition. Pas de journal. |
| 3, 4 | **Toujours** une proposition. Jamais d'exécution directe, même pour Guillaume, même si la demande est explicite. Journalisée acceptée **ou** refusée. |
| 5 | Refus immédiat, message explicatif, journalisé. **En v1 ces services ne sont pas dans la table des outils** : Claude ne peut pas les demander. Le niveau 5 du registre est une deuxième barrière, pas la première. |
| absent du registre | **3.** Donc proposition. Un service oublié échoue du côté sûr. |

Scopes de profil (§3, F3) :

```python
SCOPES = {
    "guillaume": {"confort", "personnel"},
    "clara":     {"confort", "personnel"},
    "liam":      {"confort"},
    "guest":     {"confort"},
    "unknown":   set(),        # ne peut rien déclencher, peut poser des questions
}
```

`critique` n'apparaît nulle part : il est hors périmètre v1 par §3.

---

## 8. Taxonomie d'erreurs

Tout `{code, message}` que la carte peut recevoir. Le `message` est en français
et **affichable tel quel** — §8 : « Jamais d'échec silencieux ».

| `code` | Quand | Ce que la carte fait |
|---|---|---|
| `addon_offline` | Le relais ne répond pas | Mode dégradé complet, chat désactivé |
| `addon_degraded` | Le relais répond, une dépendance manque | Bandeau d'avertissement, chat actif |
| `ha_unavailable` | L'add-on n'atteint plus HA | Bandeau ; les outils échouent proprement |
| `claude_unavailable` | Erreur réseau ou 5xx côté API | Bulle d'erreur, bouton « réessayer » |
| `claude_no_credit` | Crédit épuisé | Bulle d'erreur, **pas** de réessai automatique |
| `claude_rate_limited` | 429 | Réessai automatique une fois, après `retry-after` |
| `claude_refused` | `stop_reason: "refusal"` sans repli disponible | Bulle d'erreur explicite |
| `not_allowed` | Le profil n'a pas le scope | Message, pas de proposition |
| `needs_approval` | Niveau ≥ 3 | Carte de proposition avec `Accepter` / `Refuser` |
| `out_of_scope_v1` | Niveau 5 | Message expliquant la limite v1 |
| `proposal_expired` | Décision après `expires_at` | Grise la proposition |
| `not_implemented` | Commande d'une phase future | Grise le bouton |
| `insecure_context` | *côté carte* : `window.isSecureContext === false` | **Message actionnable** renvoyant à P0 |
| `mic_denied` | *côté carte* : permission micro refusée | Message expliquant comment la rétablir |
| `internal` | Tout le reste | Message générique + identifiant de corrélation |

Les deux derniers codes « côté carte » ne viennent jamais du serveur : la carte
les fabrique. §8 les exige nommément : « le refus de permission micro et le
contexte non-HTTPS produisent un message d'erreur explicite et actionnable ».

---

## 9. Contrat API Claude

Fixé maintenant parce qu'il détermine le coût, la latence et la forme du
cerveau. Suppose H21 (`claude-opus-5`).

```python
# luna/providers/claude.py — la forme de l'appel, pas le code final
async with client.messages.stream(
    model="claude-opus-5",
    max_tokens=8192,
    thinking={"type": "adaptive"},          # jamais budget_tokens : rejeté (400)
    output_config={"effort": "low"},        # low : réponses courtes, lues à voix haute
    betas=["server-side-fallback-2026-07-01"],
    fallbacks="default",                    # H23 : un refus ne rend pas Luna muette
    system=[
        {"type": "text", "text": PROMPT_SYSTEME,
         "cache_control": {"type": "ephemeral"}},   # ← le seul point de rupture
    ],
    tools=OUTILS,                           # ordre stable, sinon le cache saute
    messages=messages,
) as stream:
    async for texte in stream.text_stream:
        await bus.publish(Delta(text=texte))
    final = await stream.get_final_message()
```

Règles, dans l'ordre d'importance :

1. **Rien de variable dans le bloc système.** Pas d'heure, pas d'état de la
   maison, pas de nom de profil. Une seule date glissée dedans et le cache ne
   prend plus jamais. Le contexte volatil passe en message système de milieu de
   conversation (H25), après le point de rupture :

   ```python
   messages.append({"role": "system", "content":
       "Contexte : mardi 8 septembre, 20 h 31. Profil actif : Guillaume. "
       "Pièce : séjour. Contexte détecté : soirée."})
   ```

2. **`tools` a un ordre stable et déterministe.** L'ordre de rendu est
   `tools` → `system` → `messages` : un outil qui change de place invalide tout
   ce qui suit.

3. **Un test vérifie le cache.** Deux tours consécutifs, assertion
   `usage.cache_read_input_tokens > 0` au second. C'est le seul moyen de
   détecter une régression de coût, qui autrement passe inaperçue.

4. **Pas de `temperature`, pas de `top_p`, pas de `budget_tokens`, pas de
   préremplissage de la réponse.** Tous rejetés en 400 sur `claude-opus-5`.

5. **Outils en `strict: true`**, résultats d'outils parallèles renvoyés dans un
   **seul** message utilisateur.

6. **Plafond de tours d'outils : 8.** Au-delà, l'échange se termine par un
   message explicatif plutôt que de boucler.

7. **La clé API vit dans les options de l'add-on**, jamais dans le dépôt, jamais
   dans la carte, jamais dans un `entity_id`.

8. **Aucun test de la CI n'appelle l'API.** Réponses enregistrées (H19).

### Ce que je reprends de Sentinel

`core/app/brain/llm.py` est réutilisable dans son principe — streaming, boucle
d'outils manuelle, plafond de tours, `cache_control` déjà bien placé, traduction
des erreurs API en français. Quatre manques à combler à la reprise : pas de
`thinking`, pas d'`effort`, pas de `fallbacks`, pas de message système de milieu
de conversation. Il vise `claude-sonnet-5` par défaut.

`core/app/ha/client.py` est réutilisable presque tel quel — connexion unique,
cache d'états, registres *areas*/entités, reconnexion avec backoff. Il porte
déjà l'invariant qui nous intéresse : « `call_service` ne doit être appelé QUE
par les exécuteurs du moteur d'actions ». À adapter : viser
`ws://supervisor/core/websocket` avec le `SUPERVISOR_TOKEN` au lieu d'une URL et
d'un jeton longue durée.

---

## 10. Outils exposés à Claude en P1

Volontairement courts. Un outil de plus, c'est du contexte à chaque tour et une
surface d'erreur en plus.

| Outil | Niveau | Rôle |
|---|---|---|
| `lister_pieces()` | 1 | Les *areas* de Nova, avec le nombre d'entités par domaine |
| `etat_maison(piece=None, domaine=None)` | 1 | États filtrés, en langage lisible. **Inclut les ouvrants et l'alarme** — lecture seule (A2) |
| `commander_lumiere(cible, action, luminosite=None)` | 2 | `cible` = pièce ou entité ; `action` = `on`/`off`/`toggle` |
| `commander_interrupteur(cible, action)` | 2 | Idem pour `switch` |
| `activer_scene(nom)` | 2 | Résolution floue sur le nom de la scène |
| `regler_thermostat(cible, temperature)` | 3 | **Produit toujours une proposition**, jamais une exécution |

`cover.*`, `lock.*`, `alarm_control_panel.*` et `notify.*` **ne sont pas
déclarés**. Claude ne peut pas les demander (A2). Si l'utilisateur insiste,
Luna explique la limite — c'est une consigne du prompt système, doublée par le
niveau 5 du registre.

---

## 11. Schéma SQLite en P1

`/data/luna.db`, mode WAL. Trois tables, pas une de plus (A7).

```sql
CREATE TABLE schema_version (version INTEGER NOT NULL);

CREATE TABLE conversations (
    id          TEXT PRIMARY KEY,          -- c_01J…
    profile     TEXT NOT NULL,
    started_at  TEXT NOT NULL,             -- ISO 8601 avec fuseau
    last_at     TEXT NOT NULL
);

CREATE TABLE messages (
    id              TEXT PRIMARY KEY,      -- m_01J…
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    role            TEXT NOT NULL,         -- user | luna | system
    text            TEXT NOT NULL,
    ts              TEXT NOT NULL,
    profile         TEXT,
    client_id       TEXT,
    tools_json      TEXT                   -- outils appelés, pour rejouer le fil
);
CREATE INDEX idx_messages_conv ON messages(conversation_id, ts);

-- §9.1 : « Toute action de niveau ≥ 3 est journalisée avec sa justification,
--         qu'elle soit acceptée ou refusée. »
CREATE TABLE action_log (
    id           TEXT PRIMARY KEY,         -- a_01J…
    ts           TEXT NOT NULL,
    profile      TEXT NOT NULL,
    ha_user_id   TEXT,
    level        INTEGER NOT NULL,
    domain       TEXT NOT NULL,
    service      TEXT NOT NULL,
    target_json  TEXT NOT NULL,
    data_json    TEXT,
    justification TEXT NOT NULL,           -- le « why » montré à l'utilisateur
    decision     TEXT NOT NULL,            -- accepted | rejected | expired | refused_v1
    executed     INTEGER NOT NULL,         -- 0 | 1
    error        TEXT,
    message_id   TEXT REFERENCES messages(id)
);
CREATE INDEX idx_action_log_ts ON action_log(ts);
```

En P4, `events` viendra s'ajouter et `action_log` l'alimentera. Aucune migration
destructive n'est prévue : `schema_version` porte le numéro, les migrations sont
des scripts numérotés appliqués au démarrage.

---

## 12. Contrat de la carte

```jsonc
// configuration Lovelace
{
  "type": "custom:luna-card",
  "height": 620,                 // px ; défaut 620, min 400
  "drawers": { "veille": true }, // le tiroir existe dès P1, vide jusqu'en P4
  "greeting": true               // message d'accueil au montage
}
```

Interface attendue :

```js
class LunaCard extends HTMLElement {
  setConfig(config) {}     // valide, lève si invalide
  set hass(hass) {}        // appelé très souvent — ne re-rendre que sur delta utile
  getCardSize() {}         // → config.height / 50
  connectedCallback() {}   // ouvre luna/feed
  disconnectedCallback() {}// FERME luna/feed — sinon fuite à chaque changement de vue
  static getStubConfig() {}// pour l'aperçu de l'éditeur
}
customElements.define("luna-card", LunaCard);
window.customCards = window.customCards || [];
window.customCards.push({ type: "luna-card", name: "Luna",
                          description: "Le visage de Luna", preview: true });
```

Charte, imposée par §8 :

```css
--luna-fond:   #0f1923;
--luna-carte:  #1e2d3d;
--luna-accent: #c9396d;
padding-bottom: env(safe-area-inset-bottom);   /* iOS WKWebView */
```

Les cinq états de l'orbe et ce qui les déclenche :

| État | Déclencheur |
|---|---|
| `idle` | Rien en cours |
| `listening` | *[P2]* micro ouvert |
| `thinking` | Entre l'envoi et le premier `delta`, et pendant chaque `tool` en `running` |
| `speaking` | Pendant les `delta` ; *[P2]* pendant la lecture audio |
| `alert` | *[P4]* au moins une alerte non traitée dans le tiroir |

En P1, seuls `idle`, `thinking` et `speaking` sont atteignables. Les deux autres
sont dessinés et testables à la main, mais rien ne les déclenche.

Mode dégradé (§8) : si `luna/info` répond `offline` ou si
`binary_sensor.luna_en_ligne` est `off` — la carte lit les deux, le second sans
aller-retour — alors bandeau « Luna est hors ligne », saisie désactivée, fil
consultable, **entités HA toujours lisibles**.

---

## 13. Options de l'add-on

```yaml
# addon/config.yaml — bloc options
options:
  anthropic_api_key: ""
  modele: "claude-opus-5"
  effort: "low"                 # low | medium | high | xhigh | max
  relay_secret: ""              # H9 — même valeur que dans le config_flow
  relay_port: 8099
  fuseau: "Europe/Paris"
  journal: "info"               # debug | info | warning | error
  profils:
    - utilisateur_ha: "Guillaume"
      profil: "guillaume"
  profil_par_defaut: "guest"

schema:
  anthropic_api_key: password
  modele: str
  effort: list(low|medium|high|xhigh|max)
  relay_secret: password
  relay_port: port
  fuseau: str
  journal: list(debug|info|warning|error)
  profils:
    - utilisateur_ha: str
      profil: list(guillaume|clara|liam|guest)
  profil_par_defaut: list(guillaume|clara|liam|guest|unknown)

homeassistant_api: true        # H6 — accès à ws://supervisor/core/websocket
hassio_api: false
ingress: false                 # la carte est une carte Lovelace, pas un panneau
ports: {}                      # H8 — rien de publié sur l'hôte
arch: [amd64]                  # H2
```

Les deux `password` ne sortent jamais du Supervisor : ni dans le dépôt, ni dans
les journaux, ni vers la carte.

---

## 14. CI

```yaml
# .github/workflows/ci.yml
- ruff check .
- ruff format --check .
- lint-imports                 # les 4 contrats de §3
- pytest -q                    # aucun appel réseau, aucun crédit consommé
```

Bloquante sur toute branche. §3.1 : « exécuté en CI à chaque push, aux côtés de
`ruff` et de la suite de tests ».

Trois tests structurels, au-delà des tests unitaires :

| Test | Ce qu'il empêche |
|---|---|
| `test_invariant_call_service` | Qu'un chemin de code atteigne `call_service` sans passer par l'arbitre d'autonomie (§9.2) |
| `test_cache_prompt` | Qu'une variable se glisse dans le bloc système et casse le cache (coût) |
| `test_niveau_defaut` | Qu'un service absent du registre reçoive autre chose que 3 (§9.1) |

---

## 15. Ce que « P1 est fini » veut dire

§11 exige un livrable testable : « Piloter une lumière par écrit depuis Loggia ».
Concrètement, la recette :

1. L'add-on démarre sur Nova, `GET /health` répond `200`.
2. L'intégration se connecte, `binary_sensor.luna_en_ligne` est `on`.
3. La carte s'affiche dans Loggia, à la charte, orbe `idle`.
4. « allume le salon » → orbe `thinking`, puis `speaking`, **la lumière
   s'allume**, la réponse est en français et tient en une phrase.
5. « ferme les volets » → refus motivé, rien ne bouge, ligne dans `action_log`.
6. « mets 20 degrés dans le séjour » → proposition affichée. Refusée : rien ne
   bouge, ligne journalisée. Acceptée : le thermostat change, ligne journalisée.
7. Rechargement de la page → le fil est retrouvé via `luna/history`.
8. Add-on arrêté → la carte passe en mode dégradé en moins de 5 secondes, sans
   erreur dans la console, les entités HA restent lisibles.
9. Deux échanges d'affilée → `cache_read_input_tokens > 0` au second.
10. `ruff`, `lint-imports` et `pytest` verts.

Rien sur la voix, l'identité, la veille ou l'apprentissage : ce sont P2 à P5.
