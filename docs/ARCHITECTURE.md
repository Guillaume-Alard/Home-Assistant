# Architecture — état courant (fin de Phase 4)

> Document vivant : mis à jour à chaque phase. La cible globale est décrite dans
> [PLAN.md](PLAN.md) ; ici, seulement ce qui **existe** et pourquoi.

## Vue d'ensemble

```mermaid
flowchart LR
    B["Navigateur (PWA)<br/>capture 16 kHz · lecture · chat<br/>propositions · alertes"]
    C["sentinel-core<br/>FastAPI · Hub WS · tours de parole<br/>intents FR · moteur d'actions"]
    W["sentinel-whisper<br/>Wyoming · STT fr"]
    P["sentinel-piper<br/>Wyoming · TTS fr"]
    A["API Anthropic<br/>messages.stream + outils"]
    N["Nova — Home Assistant<br/>WebSocket : états, événements, services"]
    D[("SQLite<br/>conversation · propositions · journal")]

    B <-->|"WSS /ws : JSON + PCM binaire"| C
    C -->|Wyoming TCP| W
    C -->|Wyoming TCP| P
    C <-->|streaming| A
    C <-->|"jeton longue durée<br/>reconnexion auto"| N
    C <--> D
```

Trois conteneurs (`docker-compose.yml`). Seul `sentinel-core` est publié sur le LAN
(HTTPS 8443) ; whisper et piper vivent sur le réseau interne Docker et parlent le
protocole **Wyoming** (réutilisables plus tard par Nova/Assist et les satellites).
Le dossier `config/` (protocoles, alertes, alias) est monté en volume : éditable
sans reconstruction, rechargé au redémarrage du conteneur.

## Le moteur « propose puis approuve » (`core/app/actions/`)

Point de passage **unique** des écritures — PLAN §5, appliqué techniquement :

- **Registre** (`registry.py` + `executors.py`) : la liste blanche exhaustive.
  Chaque action déclare son risque (`low`/`medium`/`sensitive`) et si elle est
  exécutable en ordre direct. Les exécuteurs valident strictement leurs
  paramètres (domaines autorisés, bornes) — seuls fichiers, avec `ha/client.py`,
  à toucher `call_service`.
- **Ordre direct** : « allume la lumière » exécute immédiatement (l'ordre EST
  l'autorisation, journalisée avec la phrase exacte). Une action `sensitive`
  (déverrouiller, désarmer, protocole sensible) exige la **double
  confirmation** (« Sentinel, confirme », 60 s, annulable).
- **Propositions** : tout le reste (`ha.call_service` générique, initiatives)
  passe en file : `pending → approved/rejected/deferred → executing → done/failed`.
  Approbation à la voix pour risque faible/moyen ; **UI uniquement** pour le
  sensible. Exécution seulement après approbation — il n'existe aucun autre
  chemin dans le code.
- **Chemin « système »** : les règles d'alerte peuvent notifier (`ha.notify`)
  avec l'autorisation « règle X » — limité techniquement au risque `low`.
- **Journal** append-only : qui, quoi, quand, autorisation, résultat.
- **Verrou statique** : `tests/test_invariant.py` interdit `call_service` et
  `_send_wait` hors des fichiers autorisés — la CI casse si on contourne.

## Nova (`core/app/ha/`)

- `client.py` : WebSocket HA (auth jeton, `get_states`, registres
  areas/entités/appareils, `subscribe_events`), cache d'états tenu à jour,
  reconnexion backoff (30 s max), commandes avec futures + timeout. Registres
  inaccessibles (jeton non admin) → mode dégradé sans résolution de pièces.
- `protocols.py` : chargement/validation de `config/protocols.yml`, phrases de
  déclenchement implicites (« protocole X », « mode X ») + explicites,
  normalisées sans accents. L'exécution est un exécuteur du moteur.
- `alerts.py` : règles de `config/alerts.yml` évaluées sur `state_changed` —
  transition entrante uniquement, condition optionnelle (ex. alarme armée),
  anti-rebond par (règle, entité). Déclenchement → annonce (fil + bannière +
  voix sur tous les appareils ; « critique » interrompt Sentinel) + notification
  via le moteur.

## Surveillance (`core/app/monitors/`) — Phase 3A

- **Docker** (`docker.py`) via DEUX proxys tecnativa (aucun port LAN, socket
  jamais monté dans core) : `sentinel-dockerproxy` en lecture pure (tous les
  POST refusés — conteneurs, stats mémoire, logs démultiplexés) et
  `sentinel-dockerproxy-restart` qui n'expose QUE la route de redémarrage
  (`CONTAINERS=0` + `ALLOW_RESTARTS` : pas de liste, pas de création, pas
  d'exec). `restart_container` refuse de toucher Sentinel lui-même et n'est
  appelable que par l'exécuteur `docker.restart` — `direct=False`, donc
  **proposition obligatoire**. L'invariant statique couvre ce chemin
  (`test_invariant.py`).
- **Système** (`system.py`) : charge et RAM de l'hôte via `/proc`. Limite assumée :
  disques/SMART de l'array Unraid inaccessibles sans privilèges — passeront par
  les capteurs de Nova si une intégration les expose.
- **Nova** (dans `health.py`) : entités indisponibles (domaines « bruit » filtrés),
  entités `update.*` actives, version HA (get_config au bootstrap du client).
- **Atrium** (`atrium.py`) : healthcheck HTTP + latence (`ATRIUM_URL`).
- **`HealthService`** agrège le tout pour quatre consommateurs : l'outil LLM
  `sante_systemes`, l'intent local « comment vont les systèmes », l'**audit**
  déterministe (constats critique/attention/info + actions suggérées) et le
  **rapport quotidien** (planificateur asyncio, heure locale `SENTINEL_DAILY_REPORT`,
  publié dans le fil via `announce(speak=False)`).
- Outils LLM ajoutés : `sante_systemes`, `logs_conteneur`, `audit_systemes`
  (lecture) ; `redemarrer_conteneur` (crée une proposition, n'exécute jamais).

## Le routeur intents → LLM (`core/app/brain/`)

1. **Intents locaux** (`intents.py`) : mots-clés FR sur texte normalisé, pièces
   résolues par les areas de Nova (+ alias `config/intents.yml`). Couverts :
   lumières, volets, serrures, température, protocoles, propositions
   (approuver/refuser/reporter/lister), confirmation/annulation, heure/date.
   Latence < 1 s, zéro Internet, zéro token.
2. **LLM avec outils** (`llm.py` + `toolbox.py`) : boucle manuelle en streaming
   (texte diffusé pendant les tours d'outils, 6 tours max). Lecture libre
   (`etat_maison`, `liste_pieces`, `details_entite`, `lister_propositions`) ;
   actions uniquement via le moteur (`action_domotique`, `lancer_protocole`,
   `creer_proposition`). Le déverrouillage/désarmement est **absent** des
   outils, volontairement.

## Protocole WebSocket (`/ws`)

Un seul canal par appareil. Les événements de conversation sont **diffusés à tous** les
appareils connectés (fil unique partagé) ; l'audio de la réponse ne va qu'à l'appareil
qui a parlé.

### Client → serveur

| Message | Payload | Rôle |
|---|---|---|
| `chat` | `{text, speak?}` | Message écrit (réponse parlée si `speak: true`, défaut non) |
| `audio_start` | `{rate}` | Début de capture micro (interrompt la réponse en cours) |
| *(binaire)* | PCM 16 bits mono | Chunks micro, entre `audio_start` et `audio_end` |
| `audio_end` | — | Fin de capture → transcription → tour de parole |
| `audio_cancel` | — | Abandon de la capture (rien n'est transcrit) |
| `cancel` | — | Interrompt le tour en cours (LLM + voix) |
| `proposal_decision` | `{id, decision}` | `approve` · `reject` · `defer` depuis l'UI |
| `dev_tasks` | — | Panneau atelier : liste des tâches + état (auth, push, dépôts) |
| `dev_log` | `{id, after}` | Journal en direct d'une tâche (incrémental : `after` = dernier `next` reçu) |
| `dev_diff` | `{id}` | Diff complet d'une tâche |
| `sante` | — | Instantané santé (panneau santé) |
| `historique` | — | Journal des actions + propositions passées |
| `ping` | — | Maintien de connexion (le serveur répond `pong`) |

Les cinq requêtes de panneau sont de la **lecture pure** (aucune action possible par
ce chemin) et reçoivent leur réponse du même type, adressée au seul demandeur.

### Serveur → client(s)

| Message | Payload | Rôle |
|---|---|---|
| `hello` | `{version, state, history, ha_configured, ha_connected, dev_configured, proposals, protocols}` | À la connexion : état + 50 derniers messages + file de propositions |
| `status` | `{state}` | `idle` · `listening` · `transcribing` · `thinking` · `speaking` |
| `message` | `{message}` | Message persisté (`source: text\|voice\|alert`) |
| `ha_status` | `{connected}` | Connexion à Nova (pastille de l'UI) |
| `activity` | `{text}` | Ce que fait Sentinel pendant la réflexion (« consulte Nova… ») |
| `alert` | `{level, text}` | Alerte proactive (`info`/`warning`/`critical`) — bannière |
| `proposal_new` | `{proposal}` | Nouvelle proposition dans la file |
| `proposal_update` | `{proposal}` | Changement de statut d'une proposition |
| `assistant_start` | `{id}` | Début de réponse |
| `assistant_delta` | `{id, text}` | Delta de texte (streaming) |
| `assistant_end` | `{id, message, cancelled}` | Fin (message persisté, ou `null` si rien) |
| `speak_start` | `{rate}` | La voix arrive (fréquence du PCM) — *origine seulement* |
| *(binaire)* | PCM 16 bits mono | Chunks de voix Piper — *origine seulement* |
| `speak_end` | — | Fin du flux vocal — *origine seulement* |
| `notice` | `{text}` | Information non bloquante (« Je n'ai rien entendu. ») |
| `error` | `{text}` | Erreur à afficher (clé API absente, service injoignable…) |
| `dev_status` | `{running}` | Tâche de dev en cours (`{id, repo}` ou `null`) — pastille ⚒ |
| `dev_tasks` · `dev_log` · `dev_diff` · `sante` · `historique` | *(réponses)* | Réponses aux requêtes de panneau (`error` en cas d'échec) — *demandeur seulement* |

## Audio

- **Montée (micro)** : `getUserMedia` → `AudioWorklet` (`ui/js/pcm-worklet.js`) →
  rééchantillonnage linéaire vers **16 kHz mono PCM16** → trames binaires WS (~128 ms).
  Fin de phrase détectée côté client au **silence** (RMS < 0,02 pendant 1,3 s), avec
  plafonds (7 s sans voix, 45 s au total) ; le serveur borne aussi à 60 s.
- **Descente (voix)** : Piper renvoie du PCM **22 050 Hz** streamé chunk par chunk ;
  le client planifie des `AudioBuffer` bout à bout (Web Audio rééchantillonne seul).
- **Latence** : la réponse LLM est découpée en **phrases** (`SentenceChunker`) envoyées à
  Piper au fil du streaming — Sentinel parle dès la première phrase terminée. Le texte
  passe par `markdown_to_speech` (le code, les tableaux et le style ne sont pas lus).

## Le tour de parole

Un seul tour actif à la fois (`Sentinel.start_turn`), **interruptible** : un nouveau
message, une nouvelle prise de parole ou `cancel` annule proprement le tour courant
(tâche asyncio annulée, partiel conservé et marqué « interrompu », états rediffusés).

```mermaid
sequenceDiagram
    participant N as Navigateur
    participant C as core
    participant W as whisper
    participant A as Claude
    participant P as piper
    N->>C: audio_start + PCM… + audio_end
    C->>W: Transcribe (Wyoming)
    W-->>C: transcript
    C-->>N: message (user, voice) [diffusé]
    C->>A: messages.stream(historique)
    loop deltas
        A-->>C: texte
        C-->>N: assistant_delta [diffusé]
        C->>P: Synthesize(phrase complète)
        P-->>C: PCM 22,05 kHz
        C-->>N: binaire [origine]
    end
    C-->>N: speak_end · assistant_end · status idle
```

## Cerveau (Phase 1)

- `anthropic.AsyncAnthropic().messages.stream(...)`, modèle configurable
  (`claude-sonnet-5` par défaut), `output_config.effort` configurable (`low` par défaut :
  latence et coût minimaux pour la conversation courante).
- System prompt en deux blocs : persona **stable** (avec `cache_control` → mis en cache
  côté API) puis date/heure **variable** après le point de cache.
- Historique : fenêtre glissante (30 messages), premier message toujours `user`.
- Erreurs typées traduites en messages français (`LLMUnavailable`) affichés dans l'UI.
- **Aucun outil** : le tool use arrive en Phase 2, exclusivement à travers le moteur
  « propose puis approuve » (PLAN §5).

## Persistance

SQLite (`data/sentinel.db`, WAL) via aiosqlite. Table unique `messages`
(id, conversation_id, role, content, source, created_at) — `conversation_id` déjà là
pour des fils multiples futurs, sans migration.

## Décisions & compromis assumés (à revisiter)

| Sujet | Choix Phase 1 | Suite prévue |
|---|---|---|
| Conteneur core en root | Simplifie le volume `./data` monté par compose sur Unraid ; aucun socket sensible monté | Utilisateur dédié + gestion des droits en phase de durcissement |
| Images `rhasspy/*:latest` | Suivi de l'écosystème sans friction | Épingler des tags une fois la stack stabilisée |
| Détection de silence côté client (RMS) | Simple, zéro dépendance | VAD serveur (silero) si besoin en mode mains-libres (Phase 5) |
| Rééchantillonnage linéaire 48→16 kHz | Suffisant pour la parole (whisper y est robuste) | Filtre anti-repliement si la qualité STT déçoit |
| Un seul fil de conversation global | Correspond à l'usage (un foyer, un assistant) | `conversation_id` prêt si séparation nécessaire |
| Certificat auto-signé par défaut | Zéro friction au premier lancement | mkcert documenté ; authentification avant toute exposition |

## Compromis supplémentaires de la Phase 2 (assumés)

| Sujet | Choix | Suite prévue |
|---|---|---|
| Annonce vocale pendant qu'un utilisateur parle à Sentinel | l'annonce attend la fin du tour (30 s max), sauf `critical` qui interrompt ; un chevauchement audio reste théoriquement possible | file audio par client si le besoin se confirme |
| Approbation « texte » des propositions sensibles | traitée comme l'UI (même canal authentifié par l'accès LAN) ; seule la **voix** est restreinte | authentification utilisateur avant toute exposition hors LAN |
| Cibles des protocoles | identifiants natifs HA (`entity_id`/`area_id`), pas de noms parlés | résolution de noms si l'édition YAML s'avère pénible |
| Rechargement de `config/` | au redémarrage du conteneur (2 s) | rechargement à chaud si le besoin se confirme |
| `for:` (durée) dans les alertes | non géré (transition immédiate uniquement) | timers si un vrai cas l'exige |

## Atelier de développement (Phase 3B)

- **`sentinel-worker`** (conteneur dédié, `worker/`) : CLI Claude Code headless
  + mini-API FastAPI interne (aucun port LAN). Isolation stricte : il ne reçoit
  que `CLAUDE_CODE_OAUTH_TOKEN`/`ANTHROPIC_API_KEY`, `GITHUB_TOKEN` et
  `DEV_REPOS` — ni jeton Nova, ni socket Docker. Volume nommé `worker-workspace`
  (clones jetables). Tourne en non-root (exigé par le mode headless).
- **Flux d'une tâche** : `POST /tasks` (dépôt de la liste blanche uniquement,
  une tâche à la fois) → clone `--depth 50` → branche `sentinel/<id>` →
  `claude -p … --output-format stream-json --dangerously-skip-permissions`
  (acceptable car le conteneur EST le bac à sable) → commit → `diff.patch` +
  résumé. Persistance `tasks.json`, secrets purgés de toute sortie, 30 tâches
  gardées.
- **Journal en direct (Phase 4)** : la sortie `stream-json` est traduite ligne à
  ligne en français (`worker/streamlog.py` : « ▸ modifie README.md », texte de
  l'assistant, résultat final) dans un tampon mémoire borné par tâche, servi par
  `GET /tasks/{id}/log?after=N` (lecture incrémentale). La console de l'atelier
  dans l'UI le sonde toutes les 2 s pendant qu'une tâche tourne. Le tampon ne
  survit pas à un redémarrage du worker — le résumé et le diff, si.
- **Côté core** (`app/devwork/`) : `WorkerClient` (lecture libre ; `start_task`
  et `push_branch` réservés aux exécuteurs — invariant testé) ; actions au
  registre : `dev.task` (**direct, risque faible** : n'écrit que dans le bac à
  sable, journalisé avec la demande) et `dev.push` (**propositions uniquement** :
  écriture GitHub) ; `DevWatcher` (boucle 15 s) annonce les fins de tâches et
  crée automatiquement la proposition de push quand il y a un diff.
- **Outils LLM** : `lancer_tache_dev` (demande explicite), `etat_taches_dev`,
  `lire_diff_dev`.
- **Authentification** : jeton OAuth du forfait Pro/Max (`claude setup-token`)
  en priorité, clé API en repli — le gros des tokens de dev est donc couvert
  par l'abonnement, l'API ne payant que la conversation vocale.
- Déploiement : Guillaume merge la branche sur GitHub puis met à jour via HACS
  (Loggia) ou l'image (Atrium) — la prod n'est jamais modifiée par Sentinel.
