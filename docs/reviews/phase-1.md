# Brief de revue externe — Phase 1 (squelette voix ↔ LLM)

> À destination de Codex, pour un avis extérieur. Contexte complet : [PLAN.md](../PLAN.md)
> (vision et phasage) et [ARCHITECTURE.md](../ARCHITECTURE.md) (état courant détaillé).

## Contexte en trois lignes

Sentinel est un assistant type Jarvis auto-hébergé (Unraid/Docker), français, voix + chat
dans le même fil. Phase 1 livrée : navigateur (PWA sans build) ↔ serveur FastAPI ↔
whisper/piper locaux (protocole Wyoming) ↔ Claude en streaming. Pas encore de domotique
ni d'actions : le modèle « propose puis approuve » (PLAN §5) arrive en Phase 2 et
deviendra le seul chemin d'écriture.

## Périmètre exact de cette revue

- Serveur : `core/app/` (~600 lignes) — `main.py` (hub WS, tours de parole),
  `brain/llm.py`, `brain/speech_text.py`, `voice/wyoming.py`, `store/db.py`, `config.py`.
- Client : `ui/js/` (~700 lignes) — `app.js`, `pcm-worklet.js`, `audio-play.js`,
  `chat.js`, `ws.js`, `viz.js`.
- Infra : `docker-compose.yml`, `core/Dockerfile`, `core/entrypoint.sh`.
- Tests : `core/tests/` — 14 tests, dont un bout-en-bout WebSocket avec faux serveurs
  Wyoming (vrai protocole, vraie pile réseau locale) et LLM simulé.

## Décisions d'architecture prises (et pourquoi)

1. **Wyoming pour STT/TTS** (conteneurs officiels rhasspy) plutôt qu'un embarquement
   in-process : zéro maintenance, réutilisable par Home Assistant/Assist et les futurs
   satellites, isolation de la charge CPU.
2. **Un seul tour de parole actif, interruptible** (`Sentinel.start_turn` annule le
   précédent) : reflète l'usage réel d'un assistant vocal ; le partiel est conservé
   et marqué « interrompu ».
3. **Découpage en phrases pendant le streaming LLM** (`SentenceChunker`) → Piper phrase
   par phrase : première parole en ~1 phrase de latence au lieu d'attendre la réponse
   complète. Le texte affiché reste intact ; seule la version parlée est nettoyée
   (`markdown_to_speech`).
4. **Fil unique global diffusé à tous les clients**, audio de réponse à l'origine
   seulement. `conversation_id` déjà en base pour des fils multiples plus tard.
5. **PWA sans build, zéro CDN** : modules ES natifs, fonctionne LAN sans Internet
   (hors appel LLM). AudioWorklet pour le 16 kHz mono PCM16.
6. **HTTPS d'office** (certificat auto-signé généré au premier démarrage) car
   `getUserMedia` exige un contexte sécurisé ; mkcert documenté pour la PWA installable.
7. **Détection de fin de parole côté client** (RMS + temporisations) plutôt qu'un VAD
   serveur : simple, sans dépendance, ajustable ; revisité en Phase 5 (mains-libres).

## Points de doute — avis sollicité

1. **Annulation asyncio** (`core/app/main.py`, `run_reply_turn` / `_speak_worker` /
   `start_turn`) : la topologie tâche-mère + tâche TTS + `finally` qui diffuse les
   événements de clôture te semble-t-elle robuste ? Des fuites/races plausibles
   (double cancel, arrêt serveur en plein tour) ?
2. **Rééchantillonnage 48→16 kHz par interpolation linéaire sans filtre passe-bas**
   (`ui/js/pcm-worklet.js`) : acceptable pour whisper, ou le repliement risque-t-il de
   dégrader la STT au point de justifier un petit FIR ?
3. **Découpage de phrases** (`core/app/brain/speech_text.py`) : limites connues
   (abréviations « M. », fences ``` en streaming). Cas dégradés à craindre en français ?
4. **Conteneur core en root** (compromis volume Unraid, documenté dans ARCHITECTURE) :
   priorité à corriger dès maintenant, ou acceptable jusqu'à la phase de durcissement ?
5. **Protocole WS** (un canal, événements diffusés, audio à l'origine) : évolutivité
   correcte pour la Phase 2 (file de propositions, protocoles, alertes proactives) ou
   structure à revoir avant d'empiler ?
6. **Angles morts de test** : le bout-en-bout couvre le protocole et le pipeline voix
   avec faux Wyoming, mais ni la reconnexion WS, ni le vrai streaming Anthropic, ni le
   worklet navigateur. Qu'ajouterais-tu en priorité ?

## Comment tester

```bash
cd core && python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt && pytest -q   # 14 tests
```

Déploiement réel : voir le [README](../../README.md) (docker compose, clé API, HTTPS).

## Hors périmètre (ne pas commenter)

Le choix du modèle d'autorisation, le phasage, la scission 3A/3B et les contraintes de
sécurité du PLAN sont **validés par Guillaume** et non rediscutables ici — en
particulier : aucune action d'écriture hors moteur de propositions (Phase 2), pas de
cloud pour la voix, LAN uniquement.
