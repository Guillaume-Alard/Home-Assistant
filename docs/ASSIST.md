# Sentinel comme agent conversationnel d'Assist (Phase 5B)

Objectif : parler à **Sentinel** depuis l'app Home Assistant (et tout satellite
Assist), en gardant **un seul cerveau, un seul fil, la même sécurité**. Ce que tu
dis au téléphone apparaît dans l'interface web de Sentinel, et inversement.

## Comment ça marche

Sentinel expose une **API compatible OpenAI** (`/v1/chat/completions`). Côté Nova,
l'intégration HACS **Extended OpenAI Conversation** pointe dessus : elle devient un
« agent conversationnel » qu'on peut choisir dans un pipeline Assist. L'app HA
utilise ce pipeline → chaque phrase part vers Sentinel, qui répond en français.

```
App HA / satellite ──▶ Nova (pipeline Assist)
                          └─ agent : Extended OpenAI Conversation
                               └─▶ http://<nebula>:8443/v1  ──▶ cerveau Sentinel
                                      (intents locaux → LLM + outils → réponse)
```

La domotique reste gouvernée par le moteur : ordre direct pour la domotique
courante (journalisé), proposition sinon, **action sensible refusée** — un
déverrouillage ne s'approuve toujours que depuis l'interface web.

## 1. Côté Sentinel (Nebula)

Dans `.env` :

```bash
SENTINEL_ASSIST_TOKEN=colle-ici-un-jeton-au-hasard   # openssl rand -hex 24
```

Puis `docker compose up -d`. Sans ce jeton, l'endpoint renvoie 404 (désactivé).

Vérifie depuis le LAN (remplace l'IP et le jeton) :

```bash
curl -k https://192.168.0.251:8443/v1/models -H "Authorization: Bearer TON_JETON"
```

Tu dois voir une liste de modèles (`sentinel`).

## 2. Côté Nova (Home Assistant)

1. **HACS → Intégrations →** installe **« Extended OpenAI Conversation »**
   (jekalmin). Redémarre Nova si demandé.
2. **Paramètres → Appareils et services → Ajouter → Extended OpenAI Conversation** :
   - **Base URL** : `https://192.168.0.251:8443/v1` (l'IP de Nebula)
   - **API Key** : le `SENTINEL_ASSIST_TOKEN`
   - **Skip authentication / verify SSL** : le certificat de Sentinel est
     auto-signé — si l'intégration refuse la connexion, c'est le point à
     désactiver (ou dépose un vrai certificat dans `data/certs/`, voir README).
3. Le **modèle** : `sentinel` (ou laisse le défaut).
4. **Paramètres → Voix (Assist) → Ajouter un assistant** :
   - **Agent conversationnel** : Extended OpenAI Conversation (créé ci-dessus)
   - **Transcription (STT)** et **synthèse (TTS)** : tu peux réutiliser
     directement les conteneurs de Sentinel — Nova → **Wyoming Protocol**,
     hôte `192.168.0.251`, ports `10300` (whisper) et `10200` (piper) — ou les
     services que tu utilises déjà. (Pour les exposer, publie ces ports dans
     `docker-compose.yml` ; ils sont internes par défaut.)
5. Dans l'**app HA** de ton téléphone : **Paramètres → Assistants**, choisis ce
   nouvel assistant. Le bouton micro de l'app parle désormais à Sentinel.

## 3. Et les Echo Dot (Alexa) ?

Franchement : **un Echo Dot ne peut pas devenir un micro de Sentinel.** Ce sont
des appareils Amazon fermés ; « Alexa, … » reste traité par Amazon, sans moyen
propre de rediriger la phrase vers Sentinel.

Ce qui reste possible avec les Echo, si tu y tiens (hors périmètre Sentinel, côté
Nova) :

- **Faire parler un Echo** depuis Nova/Sentinel (annonces) via l'intégration
  custom **Alexa Media Player** (HACS) — utile pour qu'une alerte de Sentinel
  soit dite à voix haute sur l'Echo. Sentinel pourrait à terme cibler ce service
  de notification.
- **Piloter tes appareils HA à la voix** via le skill Alexa officiel — mais le
  cerveau reste Alexa, ce n'est pas Sentinel.

Le vrai satellite mains libres, aujourd'hui, c'est **l'app HA sur ton téléphone**
(et, si tu en achètes un jour, un **Home Assistant Voice PE** ou un ESP32-S3
Assist, qui marcheront avec ce même pipeline sans rien changer à Sentinel).

## Sécurité

- L'endpoint est protégé par le jeton porteur et n'écoute que sur le LAN (HTTPS).
- La source des tours Assist est marquée `assist` : les actions **sensibles**
  (déverrouillage, désarmement) ne s'exécutent jamais par ce canal sans passer
  par la double confirmation, et **aucune proposition sensible ne s'approuve hors
  de l'interface web** — règle durcie en Phase 5B (`via != "ui"`).
- Quiconque a le jeton et l'accès LAN peut commander la domotique courante, au
  même titre qu'avec l'interface web : garde le jeton privé.
