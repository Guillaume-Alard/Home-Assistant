# Sentinel

Assistant personnel type Jarvis — auto-hébergé, en français, à la **voix** et à l'**écrit**,
depuis n'importe quel appareil du réseau local doté d'un micro et d'une enceinte.

Le pipeline vocal est **local** (openWakeWord/whisper/piper via le protocole Wyoming,
compatible écosystème Assist de Home Assistant) ; seul le « cerveau » conversationnel
appelle l'API Anthropic (Claude). La voix et le chat écrit partagent le même fil de
conversation, synchronisé en temps réel entre tous les appareils connectés.

| Phase | Contenu | État |
|---|---|---|
| 0 | Plan d'architecture ([docs/PLAN.md](docs/PLAN.md)) | ✅ validée |
| 1 | Squelette voix ↔ LLM : je parle/j'écris dans le navigateur, Sentinel répond en voix et en texte | ✅ livrée |
| **2** | **Domotique : Nova (Home Assistant), intents locaux, protocoles (Forteresse…), moteur « propose puis approuve », alertes proactives** | ✅ **livrée** |
| 3A/3B | Copilote technique : santé des systèmes, audits, délégation de dev, maintenance Loggia/Atrium | ⏳ |
| 4 | UI/UX finale | ⏳ |
| 5 | Mot d'éveil & satellites (ESP32, app HA via Assist) | ⏳ |

---

## Ce que sait faire Sentinel

**Converser (Phase 1)**
- 🎙️ **Voix** : bouton (ou barre espace) → transcription locale (faster-whisper FR) →
  réponse de Claude en streaming → voix locale (Piper FR) **phrase par phrase**.
- ⌨️ **Chat écrit** : même cerveau, même fil, synchronisé sur tous les appareils.
- 🧠 **Mémoire** du fil (SQLite), **interruption** à tout moment (Échap, reparler),
  **PWA** sombre sans CDN.

**Piloter la maison (Phase 2)**
- ⚡ **Intents locaux** : « allume la lumière du salon », « ferme les volets »,
  « quelle est la température ? », « quelle heure est-il ? » — compris **sans LLM**,
  en moins d'une seconde, même sans Internet. Les pièces viennent des areas de Nova.
- 🏰 **Protocoles** : « Sentinel, protocole forteresse » — séquences d'actions
  déclaratives ([docs/PROTOCOLES.md](docs/PROTOCOLES.md)), génériques (Nuit, Cinéma…).
- 🛡️ **Moteur « propose puis approuve »** : la domotique courante s'exécute sur ton
  ordre direct (journalisé) ; **tout le reste** devient une proposition à approuver
  (panneau ▤ en haut à droite, ou « approuve la proposition 3 » à la voix). Les actions
  sensibles (déverrouiller, désarmer) exigent une double confirmation, et leurs
  propositions ne s'approuvent que dans l'interface. Tout est journalisé.
- 🚨 **Alertes proactives** : Sentinel surveille les événements de Nova (fumée,
  intrusion, …) et prend la parole sur tes appareils + notifie ton téléphone
  (règles déclaratives dans `config/alerts.yml`).
- 🧰 **Outils du cerveau** : Claude lit l'état réel de la maison avant de répondre,
  agit sur ta demande explicite, et **propose** pour tout ce qui dépasse la liste
  blanche — il ne peut techniquement pas la contourner.

Pas encore là (et c'est voulu) : surveillance des serveurs et copilote de dev
(Phase 3), mot d'éveil (Phase 5 — la PWA fonctionne en appui-pour-parler d'ici là).

## Prérequis

- Un hôte Docker avec `docker compose` (typiquement **Nebula**, ton NAS Unraid).
  CPU x86 récent conseillé pour la transcription (`small-int8` ≈ 1–2 s par phrase).
- Une **clé API Anthropic** : <https://console.anthropic.com/>.
- Un navigateur récent (Chrome/Edge/Firefox/Safari) sur le même LAN.

## Installation sur Unraid

1. Ouvre un terminal sur Unraid (ou en SSH) :

   ```bash
   cd /mnt/user/appdata
   git clone https://github.com/Guillaume-Alard/Home-Assistant sentinel
   cd sentinel
   cp .env.example .env
   nano .env        # renseigne au minimum ANTHROPIC_API_KEY
   ```

2. Lance l'ensemble :

   ```bash
   docker compose up -d --build
   ```

   Premier démarrage : l'image core se construit, whisper télécharge son modèle et piper
   sa voix française (quelques minutes, une seule fois). Suivi : `docker compose logs -f`.

3. Ouvre **`https://IP-DE-NEBULA:8443`** depuis n'importe quel appareil du LAN.

> 💡 Le plugin *Docker Compose Manager* (Community Applications) fonctionne aussi :
> pointe une « stack » vers ce dossier. Les trois conteneurs (`sentinel-core`,
> `sentinel-whisper`, `sentinel-piper`) apparaissent dans l'onglet Docker d'Unraid.

## Première utilisation

1. **Avertissement de certificat** : Sentinel génère un certificat auto-signé au premier
   démarrage (HTTPS est obligatoire pour le micro). Clique « Avancé → Continuer vers le
   site ». Pour supprimer l'avertissement définitivement : voir [HTTPS](#https--certificats).
2. **Autorise le micro** quand le navigateur le demande (premier clic sur le bouton).
3. Parle : clique le bouton central (ou barre espace), pose ta question, tais-toi —
   l'envoi part tout seul après ~1,3 s de silence. Reclique pour envoyer immédiatement.
4. Écris : le champ en bas partage le même fil. Réponse écrite silencieuse ; réponse
   parlée quand la question était vocale.
5. **Échap** (ou clic pendant que Sentinel parle/réfléchit) : interruption.

## Configuration (`.env`)

| Variable | Défaut | Rôle |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | **Obligatoire.** Clé API Anthropic |
| `SENTINEL_MODEL` | `claude-sonnet-5` | Modèle du cerveau (`claude-opus-5`, `claude-haiku-4-5`…) |
| `SENTINEL_MAX_TOKENS` | `1024` | Longueur max d'une réponse |
| `SENTINEL_EFFORT` | `low` | Profondeur de réflexion (`low`/`medium`/`high`) — `low` = latence minimale |
| `SENTINEL_HISTORY_WINDOW` | `30` | Messages d'historique envoyés au modèle |
| `SENTINEL_PORT` | `8443` | Port HTTPS de l'UI sur le LAN |
| `SENTINEL_TLS` | `on` | `off` **uniquement** derrière un reverse proxy HTTPS |
| `WHISPER_MODEL` | `small-int8` | Modèle STT (`tiny-int8` plus rapide, `medium` plus juste) |
| `PIPER_VOICE` | `fr_FR-siwis-medium` | Voix française de Sentinel |
| `TZ` | `Europe/Paris` | Fuseau horaire (Sentinel connaît la date et l'heure) |
| `LOG_LEVEL` | `INFO` | Verbosité des journaux |
| `HA_URL` | — | URL de Nova, ex. `http://192.168.1.10:8123` |
| `HA_TOKEN` | — | Jeton longue durée Home Assistant (voir ci-dessous) |

Après modification : `docker compose up -d` (et `--build` si le code a changé).

## Connecter Nova (Home Assistant)

1. **Crée un jeton longue durée** dans Nova : clique ton avatar (en bas à gauche) →
   onglet **Sécurité** → « Jetons d'accès longue durée » → *Créer un jeton*, nommé
   `sentinel`. ⚠️ Utilise un compte **administrateur** : Sentinel a besoin des
   registres (pièces/entités) pour comprendre « le salon ».
2. Renseigne `HA_URL` et `HA_TOKEN` dans `.env`, puis `docker compose up -d`.
3. La pastille **nova** du bandeau passe au vert. Dis : « Allume la lumière du
   salon » (le nom de pièce doit exister dans Nova → Paramètres → Zones).

### Tester la Phase 2 en 3 minutes

- **Protocole** : « Sentinel, protocole test » → il répond et une notification
  apparaît dans Nova. Puis adapte `forteresse` dans `config/protocols.yml`
  ([guide](docs/PROTOCOLES.md)).
- **Alerte proactive** : dans Nova, crée un bouton à bascule nommé « Test
  Sentinel » (Paramètres → Appareils et services → Entrées) et active-le :
  Sentinel prend la parole tout seul. Adapte ensuite `config/alerts.yml`
  (fumée, intrusion…).
- **Propositions** : demande par écrit « purge la base de données de Nova en
  gardant 30 jours » → Sentinel ne peut pas le faire directement : il crée une
  proposition, à approuver dans le panneau ▤ (ou « approuve la proposition 1 »).
- **Sensible** : « déverrouille la porte » → Sentinel exige « Sentinel,
  confirme » avant d'agir.

## Coûts API (à lire une fois)

⚠️ **L'abonnement Claude (Pro/Max) et l'API sont deux facturations séparées.** Un forfait
claude.ai — même Max — couvre l'application Claude et Claude Code, mais **pas** la clé API
utilisée par Sentinel : il faut créditer le compte API sur
[console.anthropic.com](https://console.anthropic.com/) (minimum ~5 $). C'est
[documenté par Anthropic](https://support.claude.com/en/articles/9876003) et il n'existe
pas de contournement propre.

La bonne nouvelle : Sentinel coûte très peu. Un échange vocal typique consomme ~2 000 à
3 000 tokens d'entrée (persona mise en cache) et ~100 à 300 en sortie, soit **moins d'un
centime par échange** avec `claude-sonnet-5` (2 $/M entrée, 10 $/M sortie) — 5 $ couvrent
plusieurs centaines d'échanges. Leviers si besoin : `SENTINEL_MODEL=claude-haiku-4-5`
(2× moins cher), `SENTINEL_HISTORY_WINDOW`, et surtout la Phase 2 : les commandes
domotiques courantes passeront par les intents locaux, **sans aucun appel API**. Pense à
définir une **limite de dépense mensuelle** dans la Console (Settings → Limits) et à
laisser l'auto-recharge désactivée si tu veux garder la main.

À noter : la délégation de tâches de développement (Phase 3B) passera par **Claude Code**,
qui, lui, est couvert par l'abonnement Pro/Max — le gros des tokens ira donc sur ton
forfait, l'API ne payant que la conversation légère.

## HTTPS & certificats

Le micro du navigateur (`getUserMedia`) exige un **contexte sécurisé** — c'est pour ça
que Sentinel sert du HTTPS même en LAN.

- **Par défaut** : certificat auto-signé généré dans `data/certs/` (avertissement à
  accepter une fois par navigateur). Tout fonctionne, **sauf** l'installation PWA et le
  service worker, qui exigent un certificat de confiance.
- **Recommandé — [mkcert](https://github.com/FiloSottile/mkcert)** (certificat reconnu
  par tes appareils, avertissement supprimé, PWA installable) :

  ```bash
  mkcert -install                          # une fois, sur ton poste
  mkcert -cert-file sentinel.crt -key-file sentinel.key IP-DE-NEBULA nebula.local
  # copie les deux fichiers dans data/certs/ (écrase les existants) puis :
  docker compose restart sentinel-core
  ```

  Installe aussi la racine mkcert sur le téléphone (fichier `rootCA.pem`).
- **Reverse proxy** (NPM, SWAG, Caddy…) : mets `SENTINEL_TLS=off`, publie
  `sentinel-core:8443` derrière ton proxy HTTPS. Ne jamais exposer Sentinel sur Internet
  en l'état — l'authentification arrive avant toute exposition (voir le plan).

## Dépannage

| Symptôme | Piste |
|---|---|
| « Micro indisponible… » | L'URL doit être en `https://` (ou le certificat refusé) ; vérifie l'autorisation micro du navigateur |
| Pas de réponse, erreur `ANTHROPIC_API_KEY` | Clé absente/incorrecte dans `.env`, puis `docker compose up -d` |
| « L'API Anthropic a renvoyé une erreur (400) : … » | Le détail affiché dit la cause ; la plus fréquente en cours d'usage est le **crédit API épuisé** → recharge sur console.anthropic.com (Plans & Billing). Détail aussi dans `docker compose logs sentinel-core` |
| Pastille **nova** rouge | `HA_URL` joignable depuis le conteneur ? Jeton valide ? `docker compose logs sentinel-core` dit si Nova refuse le jeton |
| « Je ne trouve pas de pièce… » | Le nom vient des **Zones** de Nova (Paramètres → Zones) ; ajoute un alias dans `config/intents.yml` si tu la nommes autrement |
| Protocole/alerte sans effet | Vérifie le journal de démarrage (`docker compose logs sentinel-core`) : les entrées YAML invalides y sont listées avec la raison |
| « Service de transcription injoignable » | `docker compose ps` : whisper démarré ? Premier téléchargement du modèle en cours ? |
| Transcription lente | Essaie `WHISPER_MODEL=tiny-int8` ; `small-int8` vise 1–2 s sur un x86 récent |
| Sentinel coupe trop tôt / trop tard | Ajuste les constantes de silence dans `ui/js/app.js` (`SILENCE_MS`, `VOICE_THRESHOLD`) |
| Le son ne sort pas | Le navigateur bloque l'audio avant un geste : clique une fois dans la page ; vérifie le volume |
| Erreur au premier lancement compose | Relance `docker compose up -d --build` (téléchargements initiaux), puis `docker compose logs -f sentinel-core` |

## Développement local (sans Docker pour le core)

```bash
cd core
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest                          # 14 tests, dont un bout-en-bout avec faux whisper/piper
# Lancer le serveur (UI sur http://localhost:8000, TLS off ⇒ micro sur localhost seulement)
SENTINEL_TLS=off SENTINEL_DATA_DIR=../data WHISPER_HOST=IP-DE-NEBULA PIPER_HOST=IP-DE-NEBULA \
  python -m uvicorn app.main:app --reload
```

`localhost` est un contexte sécurisé : le micro fonctionne sans HTTPS en local.

## Structure du dépôt

```
docker-compose.yml   # les 3 services (core, whisper, piper)
.env.example         # configuration commentée
config/              # protocoles, alertes, alias d'intents (éditables à chaud)
core/                # serveur Python (FastAPI)
  app/actions/       #   LE moteur « propose puis approuve » : registre, exécuteurs
  app/ha/            #   client WebSocket Nova, protocoles, alertes
  app/brain/         #   LLM (Claude + outils), intents locaux FR, texte→parole
  app/voice/         #   clients Wyoming (whisper/piper) + sessions de capture
  app/store/         #   SQLite : conversation, propositions, journal
  tests/             #   72 tests (moteur, intents, invariant d'écriture, bout-en-bout)
ui/                  # PWA statique sans build : HTML/CSS/JS natifs
docs/                # PLAN, ARCHITECTURE, PROTOCOLES, OUTILS-LLM, briefs de revue
data/                # créé à l'exécution : SQLite, certificats (non versionné)
```

Détails techniques (protocole WebSocket, formats audio, décisions) :
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Licence

[MIT](LICENSE) — © Guillaume Alard (Alardware).
