# Définir un protocole

Un **protocole** est une séquence d'actions Home Assistant nommée, déclenchable :
à la voix (« Sentinel, protocole forteresse »), à l'écrit, ou par le cerveau LLM.
Tout est déclaratif dans [`config/protocols.yml`](../config/protocols.yml) — zéro code.

## La recette

1. Ouvre `config/protocols.yml` et ajoute une entrée :

   ```yaml
   nuit:
     nom: "Nuit"
     risque: moyen              # faible | moyen | sensible
     annonce: "Protocole Nuit. Bonne nuit, Guillaume."
     phrases:                   # déclencheurs en plus de « protocole nuit » / « mode nuit »
       - "bonne nuit sentinel"
     actions:
       - service: light.turn_off
         target: { entity_id: all }
       - service: cover.close_cover
         target: { area_id: [chambre] }
       - service: alarm_control_panel.alarm_arm_night
         target: { entity_id: alarm_control_panel.maison }
   ```

2. Redémarre le cœur : `docker compose restart sentinel-core`.
   Le journal de démarrage liste les protocoles chargés (et les erreurs de format).

3. Teste : « Sentinel, protocole nuit ». La phrase `annonce` est prononcée en retour.

## Ce qu'il faut savoir

- **Cibles** : `target` utilise les identifiants natifs HA — `entity_id`,
  `area_id` (l'identifiant d'area, pas le nom affiché), `device_id`.
  Où les trouver : Nova → Outils de développement → États / Zones.
- **Risque** :
  - `faible` / `moyen` → exécution immédiate sur ordre direct, journalisée ;
  - `sensible` → Sentinel exige une **double confirmation** (« Sentinel,
    confirme ») avant d'exécuter. À utiliser pour tout ce qui désarme, ouvre
    ou supprime.
- **Étapes en échec** : une étape ratée (entité inexistante, service refusé)
  n'interrompt pas les suivantes ; Sentinel le signale dans sa réponse et tout
  est journalisé.
- **Déclencheurs implicites** : « protocole X » et « mode X » fonctionnent
  toujours, `phrases` ajoute des formulations naturelles. Les phrases sont
  insensibles aux accents/majuscules.
- **Sécurité** : l'exécution passe par le moteur d'actions (`protocol.run`),
  comme toute écriture — un protocole ne peut pas être déclenché par une règle
  d'alerte ni par une initiative de Sentinel sans passer par une proposition.

## Exemples fournis

- `test` : une simple notification dans Nova — pour valider la chaîne sans risque.
- `forteresse` : livré minimal (notification) avec les étapes réelles en
  commentaires, à adapter avec tes entités puis décommenter.
