# Connecteur Sentinel pour Home Assistant

Expose **Sentinel** comme **agent conversationnel** d'Assist, sans dépendre
d'*Extended OpenAI Conversation* (souvent cassée sur les versions récentes de
HA). Minimal, sans bibliothèque tierce, il relaie chaque phrase vers l'API
`/v1/chat/completions` de Sentinel.

Prérequis : **Home Assistant 2024.2 ou plus récent** (API des agents
conversationnels par entité), et `SENTINEL_ASSIST_TOKEN` renseigné côté Sentinel.

## Installation

1. Copie le dossier `sentinel_assist/` dans le `custom_components/` de Nova :
   `\\192.168.0.251\config\custom_components\sentinel_assist\`
   (crée le dossier `custom_components` s'il n'existe pas).
2. **Redémarre Home Assistant** (Paramètres → Système → ⋮ → Redémarrer).
3. **Paramètres → Appareils et services → Ajouter une intégration →** cherche
   **« Sentinel »**.
4. Renseigne :
   - **URL de Sentinel** : `https://192.168.0.251:8443` (sans `/v1`)
   - **Jeton** : la valeur de `SENTINEL_ASSIST_TOKEN`
   Le certificat auto-signé est accepté automatiquement (pas de vérif TLS).
5. **Paramètres → Voix (Assist) → Ajouter un assistant** : choisis **Sentinel**
   comme agent conversationnel (+ le STT/TTS de ton choix), puis sélectionne cet
   assistant dans l'app HA de ton téléphone.

## Ce qu'il fait

- Envoie la phrase de l'utilisateur à Sentinel et prononce sa réponse.
- Sentinel gère le reste : même fil que l'interface web, mêmes règles de
  sécurité (actions sensibles refusées hors interface). Voir `docs/ASSIST.md`.
