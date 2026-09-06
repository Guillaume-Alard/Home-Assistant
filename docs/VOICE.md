# La voix de Luna — clonage local

Par défaut, Sentinel parle avec **Piper** (voix locale, instantanée). Luna peut
aussi utiliser une **voix clonée locale** — proche d'un échantillon fourni —
sans rien envoyer à un service cloud. Le clonage est **optionnel** et **désactivé
par défaut** ; s'il est indisponible, Sentinel **retombe automatiquement sur
Piper** (Luna parle toujours).

## Architecture

- `sentinel-core` demande la synthèse via une interface unique
  (`synthesize(texte) → PCM`). Deux implémentations :
  - **PiperTTS** (Wyoming, local, instantané) — par défaut.
  - **ClonedTTS** — appelle un serveur local à **API compatible OpenAI**
    (`POST /v1/audio/speech`, format `pcm`), enveloppé dans **FallbackTTS** qui
    bascule sur Piper si le serveur clone échoue *avant* le premier son.
- Le serveur de clonage est le service Docker **`sentinel-xtts`**
  (image `openedai-speech`, XTTS), lancé via le **profil `voix`** — donc absent
  du démarrage normal.

## Activation (sur Nebula)

1. **Déposer l'échantillon** de référence : `config/voix/voices/luna.wav`
   (~6–15 s, mono, propre, sans musique ni bruit). *Les `.wav` ne sont pas
   versionnés (voir `.gitignore`) — ils restent locaux.*
2. **Vérifier le mapping** `config/voix/config/voice_to_speaker.yaml`
   (voix `luna` → `voices/luna.wav`, langue `fr`).
3. Dans `.env` :
   ```
   TTS_ENGINE=cloned
   # optionnels (valeurs par défaut) :
   # CLONED_TTS_URL=http://sentinel-xtts:8000
   # CLONED_TTS_VOICE=luna
   ```
4. Démarrer le stack **avec le profil voix** :
   ```
   docker compose --profile voix up -d
   ```
   (au 1ᵉʳ lancement, le modèle XTTS se télécharge — quelques minutes.)

Vérifie dans **Paramètres → Voix & réveil** que « Voix » indique
« *luna* (clonée) ».

## CPU vs GPU

Nebula est en **CPU** : XTTS fonctionne mais avec **quelques secondes de
latence** par réponse. C'est acceptable pour des réponses courtes ; le repli
Piper couvre les cas où le serveur est occupé. Pour une synthèse quasi temps
réel, un **GPU NVIDIA** est nécessaire (image CUDA d'openedai-speech +
réservation GPU dans compose) — à voir plus tard si besoin.

> Les chemins de volumes du service `sentinel-xtts` (`/app/config`,
> `/app/voices`) et le nom du modèle (`xtts_v2.0.2`) suivent openedai-speech ;
> **à confirmer** avec la version réellement tirée. En cas d'erreur, le repli
> Piper garantit que Luna continue de parler.

## Prononciation « Louna »

Le nom s'écrit **Luna** mais se prononce **« Louna »**. Si la voix dit « Lu-na »,
deux options : ajouter une règle de pré-traitement côté serveur
(`config/voix/config/pre_process_map.yaml` : `Luna` → `Louna`), ou garder
l'écriture « Luna » à l'affichage et « Louna » seulement pour la synthèse.

## Mot d'éveil « Luna »

La **détection** du mot d'éveil (openWakeWord) ne connaît que des modèles
pré-entraînés (`hey_jarvis` par défaut). Pour réveiller sur « Luna », il faut un
**modèle `.tflite` dédié** :

1. En entraîner un (notebook officiel openWakeWord) ou en récupérer un.
2. Le déposer dans `config/wakewords/luna.tflite`.
3. `.env` : `WAKEWORD_MODEL=luna`, puis
   `docker compose up -d sentinel-openwakeword sentinel-core`.

En attendant, la persona reste **Luna** partout ; seule la détection vocale du
mot d'éveil utilise encore `hey_jarvis`.
