# Brief de revue externe — Phase 5B (Sentinel, agent conversationnel d'Assist)

> Pour un avis extérieur. Contexte : [PLAN.md](../PLAN.md), [ARCHITECTURE.md](../ARCHITECTURE.md),
> [ASSIST.md](../ASSIST.md), briefs précédents : [phase-1](phase-1.md) … [phase-5a](phase-5a.md).

## Ce qui a été construit

La seconde moitié de la Phase 5. Objectif de Guillaume : « Sentinel = agent
conversationnel » — parler à Sentinel depuis l'app HA.

- **API compatible OpenAI** dans `sentinel-core` : `POST /v1/chat/completions`
  (non-streaming) et `GET /v1/models`, protégées par un jeton porteur
  (`SENTINEL_ASSIST_TOKEN`, vide → 404). Comparaison de jeton en temps constant.
- **`run_assist_reply`** : même cerveau que le chat écrit (intents → LLM + outils),
  source `assist`, réponse en une fois, **tour diffusé au hub** → l'app et le web
  partagent le fil.
- **Sécurité durcie** : l'approbation d'une proposition sensible passe de
  « refusée à la voix » (`via == "voice"`) à « **interface uniquement** »
  (`via != "ui"`) — ferme aussi le texte et Assist. Les actions sensibles directes
  gardent la double confirmation.
- **Intégration** documentée (`ASSIST.md`) : Extended OpenAI Conversation (HACS) →
  `https://<nebula>:8443/v1`, pipeline Assist, app HA. Réutilisation possible des
  conteneurs Wyoming de Sentinel comme STT/TTS de Nova.
- **Honnêteté matériel** : les Echo Dot (Alexa) ne peuvent pas être des satellites
  de Sentinel ; documenté, avec les seules options réalistes (annonces via Alexa
  Media Player). Le vrai satellite est l'app HA (et un futur Voice PE, sans code).
- Tests : 125 (6 nouveaux — désactivé sans jeton, jeton exigé, complétion +
  miroir dans le fil, message vide refusé, bloc malformé sans 500, `/v1/models` ;
  + durcissement sensible vérifié pour `text`/`assist`).

## Points de doute — avis sollicité

1. **Non-streaming** : Extended OpenAI Conversation attend la réponse complète —
   choisi pour la simplicité et la robustesse. Faut-il prévoir le SSE `stream:true`
   pour d'autres clients Assist, ou est-ce prématuré ?
2. **Tour Assist hors machinerie `start_turn`** : une requête HTTP Assist ne prend
   pas le verrou de tour du WebSocket (pas de barge-in). En mono-foyer, risque de
   chevauchement négligeable — ou faut-il sérialiser web et Assist ?
3. **Historique** : on ignore l'historique fourni par Nova et on reconstruit depuis
   le store de Sentinel (continuité du fil unique). Bon compromis, ou surprenant si
   Nova gère aussi un contexte de son côté ?
4. **TLS auto-signé** : Extended OpenAI Conversation doit accepter le certificat de
   Sentinel (option à désactiver, ou vrai cert). Documenté — piège récurrent ?
5. **Durcissement `via != "ui"`** : bien fondé (le modèle de sécurité dit sensible =
   approbation UI seule), mais change le comportement du texte tapé. Acceptable ?

## Addendum — revue interne du 06/09 (4 constats, tous corrigés)

1. **Tour Assist hors machinerie de tour (le plus grave)** : `run_assist_reply`
   ne prenait pas le verrou de tour → un appel Assist chevauchant un tour web
   corrompait le fil partagé (deux flux dans une bulle), et deux appels Assist
   simultanés pouvaient exécuter un outil / créer une proposition en double →
   le tour passe désormais par le verrou + barge-in (`_cancel_locked`), comme le
   WebSocket ; jamais deux tours en parallèle.
2. Awaits d'avant-flux hors `try` → une panne transitoire donnait un 500 et une
   bulle utilisateur orpheline → tout le corps est en `try/finally`, `assistant_end`
   toujours émis, `error` diffusé, jamais de 500.
3. Extraction d'un contenu à blocs : un `text` non-`str` (`null`) levait un
   `TypeError` (→ 500) → coercition `str(... or "")`, 400 propre si vide.
4. Duplication avec `run_reply_turn` : réduite (le corps Assist partage la
   machinerie de tour) ; le reste diverge volontairement (pas de TTS, renvoie le
   texte).

125 tests après correctifs. Les points de doute restent ouverts.

## Comment tester

```bash
cd core && pip install -r requirements-dev.txt && pytest -q   # 125 tests
```

Réel : `SENTINEL_ASSIST_TOKEN=...` dans `.env`, `docker compose up -d`, puis
`curl -k https://<nebula>:8443/v1/models -H "Authorization: Bearer ..."`, enfin le
parcours HACS + app HA de `ASSIST.md`.

## Hors périmètre

Streaming SSE, satellites matériels dédiés (Voice PE / ESP32 — marcheront via le
même pipeline sans code Sentinel), passerelle Alexa (hors projet).
