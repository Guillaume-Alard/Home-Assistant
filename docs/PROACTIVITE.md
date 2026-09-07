# La proactivité de Luna (Phase 7)

Luna ne se contente plus de répondre : elle **observe** l'état de la maison et
l'heure, et te **suggère** quand quelque chose mérite ton attention — un volet
resté ouvert le soir, des lumières allumées tard, personne à la maison alors que
tout tourne. Mais elle **n'agit jamais seule**.

## Le principe : observer, suggérer — jamais exécuter

Une suggestion actionnable ne devient une action qu'en **deux temps, tous deux
humains** :

1. Luna **suggère** (« le volet du garage est encore ouvert, il est 22h — je
   prépare une proposition pour le fermer ? »).
2. Tu dis **oui** (à la voix, ou « Préparer la proposition » dans le cockpit) →
   une **proposition** est créée.
3. Tu **approuves** la proposition → l'action s'exécute.

Le veilleur proactif ne fait, au plus, que **créer une proposition** ; il
n'exécute jamais rien lui-même. C'est **verrouillé par des tests** : le paquet
`proactive` n'appelle aucune écriture (ni `run_direct`, ni `call_service`) — voir
`core/tests/test_invariant.py`. Et, comme toujours, les actions sensibles
(serrures, alarme) restent barrées à la voix : elles ne s'approuvent que dans
l'interface.

## Ce que Luna surveille (v1)

| Règle | Ce qu'elle observe | Suggestion |
|---|---|---|
| `ouverture_nuit` | Porte / fenêtre ouverte le soir | Constat (sécurité) |
| `volet_nuit` | Volet / porte de garage ouvert le soir | Propose de **fermer** |
| `absence_appareils` | Personne à la maison + lumières / musique | Propose d'**éteindre** |
| `fenetre_chauffage` | Fenêtre ouverte alors que le chauffage tourne | Constat (énergie) |
| `lumiere_tard` | Lumières encore allumées tard | Propose d'**éteindre** |
| `temperature` | Pièce trop chaude / trop fraîche | Constat (confort) |

Les **alertes de sécurité** (volet/porte ouverte la nuit) sont **dites à voix
haute**. Les **simples constats** restent silencieux pendant les **heures
calmes** (ils t'attendent dans le tiroir ✦ Suggestions).

## Tu gardes la main

- **Tiroir ✦ Suggestions** (barre du haut) : chaque suggestion propose
  *Préparer la proposition* / *Plus tard* / *Ignorer* / *Ne plus me suggérer ça*.
- **« Ne plus me suggérer ça »** tait la règle : Luna apprend de tes retours.
  Tu réactives une règle tue dans **Paramètres › Proactivité**.
- **Anti-répétition** : une même situation n'est pas resuggérée avant un délai
  (par défaut 3 h).
- **Rythme réglable** : `config/proactive.yml` (heures calmes, heure « nuit »,
  seuils de température, délai). Luna peut même te **proposer** de le modifier
  (Phase 6, Paramètres › Évolutions).
- **Coupe-circuit** : `SENTINEL_PROACTIVE=off` désactive tout le veilleur.

## Quand ça tourne

Le veilleur évalue le contexte **périodiquement** (par défaut toutes les 150 s)
et, de façon amortie, **à chaque changement d'état** de Nova (une porte qui
s'ouvre déclenche une réévaluation, au plus une fois toutes les 20 s). Il faut
donc **Nova connectée** (`HA_URL`) — sans domotique, il n'y a rien à observer.

## Sous le capot

- `core/app/proactive/rules.py` : le `Context` (état maison + heure) et les
  règles — des fonctions pures, faciles à tester. Chaque `Suggestion` porte une
  `key` (anti-répétition), une `rule` (pour « ne plus suggérer ça ») et, si elle
  est actionnable, une **action proposable**.
- `core/app/proactive/engine.py` : le `ProactiveEngine` — boucle de fond,
  anti-répétition, heures calmes, escalade d'une suggestion en **proposition**
  (`propose`, jamais d'exécution), report / rejet / mise en sourdine.
- `core/app/store/db.py` : tables `proactive` (suggestions) et
  `proactive_mutes` (règles tues).
- `core/app/main.py` : WebSocket `proactive` / `proactive_make_proposal` /
  `proactive_snooze` / `proactive_dismiss` / `proactive_mute` / `proactive_unmute`.
- `config/proactive.yml` : réglages (facultatif — valeurs par défaut sinon).
- `ui/` : tiroir ✦ Suggestions + Paramètres › Proactivité.

## Limites & évolutions

- Règles **état + heure** pour l'instant. Pistes : apprentissage plus fin de tes
  habitudes (heures réelles de coucher/lever), suggestions liées à la météo
  (« il va pleuvoir, je ferme le velux ? »), à l'agenda. Rien de tout cela ne
  changerait la règle d'or : **Luna suggère, tu décides**.
