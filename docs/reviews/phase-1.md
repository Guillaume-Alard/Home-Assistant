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

## Addendum — revue interne du 31/08 (7 constats, tous corrigés)

Codex n'étant pas exécutable depuis l'environnement de développement, une passe de revue
adversariale interne (8 angles, périmètre complet de la Phase 1) a été menée. Constats,
tous vérifiés puis corrigés :

1. `run_voice_turn` : une exception inattendue du STT laissait l'état global bloqué sur
   « transcription » → filet `Exception` + retour à `idle` garanti ; les coupures de
   connexion Wyoming en plein échange deviennent des `VoiceServiceError` propres.
2. `ensureCapture` (PWA) : un échec d'initialisation micro laissait un objet cassé en
   cache et pouvait coincer le serveur en « listening » → l'objet n'est publié qu'après
   une initialisation réussie (nouvel essai possible au clic suivant).
3. `SENTINEL_TLS` interprété différemment par `config.py`, `entrypoint.sh` et
   `healthcheck.py` (`true` désactivait silencieusement le TLS, donc le micro) →
   interprétation unifiée `on/true/yes/1`, et champ `Settings.tls` mort supprimé.
4. `Hub.broadcast` séquentiel : un client gelé (Wi-Fi coupé sans fermeture TCP) figeait
   le tour pour tous les appareils → envois parallèles avec timeout de 5 s et fermeture
   du client muet.
5. `renderMarkdown` : une fence ``` en ligne rendait un marqueur parasite et perdait le
   code → marqueur isolé sur sa ligne, tag de langage compté seulement s'il est suivi
   d'un saut de ligne.
6. Réglage mort : `max_utterance_seconds` est maintenant réellement transmis aux
   sessions de capture.
7. Les réponses prononcées sont stockées avec `source="voice"` (données exactes pour
   les usages futurs).

S'y ajoute, suite au premier test réel : le détail des erreurs API Anthropic est
désormais affiché et journalisé (cas « crédit épuisé » traduit explicitement, c'est un
400 fréquent), et l'historique envoyé au modèle est assaini (contenus vides filtrés,
jamais de message assistant en dernière position → prefill interdit sur les modèles
récents). Les points de doute listés plus haut restent ouverts pour un second avis.

## Hors périmètre (ne pas commenter)

Le choix du modèle d'autorisation, le phasage, la scission 3A/3B et les contraintes de
sécurité du PLAN sont **validés par Guillaume** et non rediscutables ici — en
particulier : aucune action d'écriture hors moteur de propositions (Phase 2), pas de
cloud pour la voix, LAN uniquement.
