# Modèles de mot d'éveil personnalisés

Ce dossier est monté dans le conteneur `sentinel-openwakeword` (`/custom`).

Par défaut, Sentinel écoute **« Hey Jarvis »** (modèle `hey_jarvis` fourni avec
openWakeWord — thématiquement, ça tombe bien).

Pour un mot personnalisé (« Sentinel ») :

1. Entraîner un modèle openWakeWord (`.tflite`) — le plus simple est le
   notebook d'entraînement automatique du projet openWakeWord (Google Colab,
   ~1 h, aucune donnée à enregistrer soi-même) :
   <https://github.com/dscripka/openWakeWord#training-new-models>
2. Déposer le fichier ici, par exemple `sentinel.tflite`.
3. Dans `.env` : `WAKEWORD_MODEL=sentinel`
4. `docker compose up -d` (recrée openwakeword et sentinel-core).

Le nom affiché dans l'interface suit `WAKEWORD_MODEL` (les tirets bas
deviennent des espaces).
