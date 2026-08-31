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
| **1** | **Squelette voix ↔ LLM : je parle/j'écris dans le navigateur, Sentinel répond en voix et en texte** | ✅ **livrée** |
| 2 | Domotique : Nova (Home Assistant), intents locaux, protocoles (Forteresse…), moteur « propose puis approuve », alertes | ⏳ |
| 3A/3B | Copilote technique : santé des systèmes, audits, délégation de dev, maintenance Loggia/Atrium | ⏳ |
| 4 | UI/UX finale | ⏳ |
| 5 | Mot d'éveil & satellites (ESP32, app HA via Assist) | ⏳ |

---

## Ce que fait la Phase 1

- 🎙️ **Voix** : bouton (ou barre espace) → capture micro, fin de phrase détectée au
  silence → transcription locale (faster-whisper FR) → réponse de Claude en streaming →
  synthèse vocale locale (Piper FR) **phrase par phrase** : Sentinel commence à parler
  avant d'avoir fini d'écrire.
- ⌨️ **Chat écrit** : même cerveau, même fil. On commence à la voix, on poursuit au
  clavier, et inversement. Tous les appareils connectés voient la conversation en direct.
- 🧠 **Mémoire** : le fil est persisté (SQLite) et rechargé à la connexion.
- 🛑 **Interruption** : reparler, envoyer un message ou presser Échap interrompt Sentinel.
- 📱 **PWA** : interface sombre installable, zéro dépendance externe (fonctionne sans CDN).

Pas encore là (et c'est voulu, voir le plan) : le pilotage Home Assistant, les protocoles,
les propositions à approuver, le mot d'éveil. La PWA fonctionne en « appui-pour-parler »
jusqu'à la Phase 5.

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

Après modification : `docker compose up -d` (et `--build` si le code a changé).

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
core/                # serveur Python (FastAPI) : voix, cerveau, WebSocket, store
  app/brain/         #   LLM (Claude) + découpage en phrases pour le TTS
  app/voice/         #   clients Wyoming (whisper/piper) + sessions de capture
  app/store/         #   fil de conversation (SQLite)
  tests/             #   pytest (unitaires + intégration WebSocket)
ui/                  # PWA statique sans build : HTML/CSS/JS natifs
config/              # (Phase 2) protocoles, alertes, intents
docs/                # PLAN, ARCHITECTURE, briefs de revue
data/                # créé à l'exécution : SQLite, certificats (non versionné)
```

Détails techniques (protocole WebSocket, formats audio, décisions) :
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Licence

[MIT](LICENSE) — © Guillaume Alard (Alardware).
