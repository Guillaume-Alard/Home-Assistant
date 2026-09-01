# Brief de revue externe — Phase 2 (domotique & moteur d'autorisation)

> Pour un avis extérieur (Codex ou autre). Contexte : [PLAN.md](../PLAN.md) §5
> (modèle d'autorisation validé par Guillaume), [ARCHITECTURE.md](../ARCHITECTURE.md)
> (état courant), brief précédent : [phase-1.md](phase-1.md).

## Ce qui a été construit

- **Client Nova** (`core/app/ha/client.py`) : WebSocket HA (auth, états,
  registres, événements, reconnexion), cache local, résolution de pièces FR.
- **Moteur « propose puis approuve »** (`core/app/actions/`) : registre-liste
  blanche avec risques, ordres directs journalisés, double confirmation du
  sensible (TTL 60 s), file de propositions (approbation vocale interdite pour
  le sensible), chemin « système » des alertes limité au risque faible,
  journal append-only.
- **Intents locaux** (`core/app/brain/intents.py`) : lumières/volets/serrures/
  température/protocoles/propositions/confirmation/heure, sans LLM.
- **Protocoles & alertes déclaratifs** (`config/*.yml`, `core/app/ha/{protocols,alerts}.py`).
- **LLM outillé** (`core/app/brain/{llm,toolbox}.py`) : boucle tool use
  manuelle en streaming (6 tours max), lecture libre, écriture via moteur
  uniquement, déverrouillage/désarmement absents des outils.
- **UI** : pastille Nova, bannière d'alertes, centre de propositions.
- **Tests** : 72 (dont faux serveur HA WebSocket complet, invariant statique
  d'écriture, bout-en-bout intent→Nova sans LLM).

## Points de doute — avis sollicité

1. **Le verrou d'invariant** (`tests/test_invariant.py`) : scan statique de
   `call_service`/`_send_wait` hors fichiers autorisés + revue. Suffisant, ou
   vois-tu un mécanisme d'exécution (runtime) simple qui vaudrait le coût ?
2. **Double confirmation** : un seul slot global de confirmation en attente
   (TTL 60 s), sans vérification du locuteur. Sur un LAN familial, acceptable ?
   Alternatives légères ?
3. **`homeassistant.turn_on` générique** (executors) vs services par domaine :
   la validation de domaine se fait sur `entity_id` — angle mort ?
4. **Boucle tool use** : erreurs d'outils renvoyées `is_error` au modèle,
   plafond 6 tours, un seul `messages` accumulé. Cas dégradés à craindre
   (tool_use jamais clôturé, contexte gonflé) ?
5. **AlertEngine** : transition entrante + anti-rebond par (règle, entité) —
   quid des états `unavailable`→`on` au redémarrage d'une intégration
   (faux positifs) ? Faut-il ignorer `unavailable`/`unknown` comme origine ?
6. **HAClient** : futures + `wait_for` par commande, un seul reader ;
   `max_size` 8 Mo pour `get_states`. Solide sur un HA chargé ?

## Comment tester

```bash
cd core && python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt && pytest -q   # 72 tests
```

Réel : README → « Connecter Nova » puis « Tester la Phase 2 en 3 minutes ».

## Addendum — revue interne du 01/09 (10 constats, tous corrigés)

Passe adversariale interne menée avant remise, constats vérifiés puis corrigés :

1. **Contournement d'autorisation (sécurité, le plus grave)** : une proposition
   `ha.call_service` enveloppant `lock.unlock`/`alarm_disarm` restait « moyenne »
   donc approuvable à la voix → le service générique porte désormais le risque de
   sa charge utile (liste de domaines/services sensibles, extensible).
2. `decide()` non sérialisé : deux approbations simultanées pouvaient exécuter
   deux fois → verrou asyncio (testé par un gather concurrent).
3. Interruption (barge-in) pendant un appel à Nova : l'écriture partait sans
   journal → exécution + journalisation blindées contre l'annulation.
4. « Coupe la musique dans le salon » éteignait la lumière → un verbe + une pièce
   sans mot-lumière n'est accepté que si la phrase ne contient rien d'autre.
5. « Éteins toutes les lumières de la chambre » agissait sur toute la maison →
   la pièce nommée prime sur « toutes ».
6. « Mets la température à 21 » répondait la température au lieu de régler →
   verbes de réglage et météo extérieure partent au LLM.
7. Plafond de tours d'outils : la dernière fournée d'actions s'exécutait sans que
   le modèle en voie le résultat → plus rien ne s'exécute au tour final.
8. Annonce vocale : chevauchement possible avec un tour en cours + écrasement de
   l'état → annonce sautée si le tour dure, état préservé.
9. Règle d'alerte avec `condition` sans `etats` chargée mais ne se déclenchant
   jamais (piège silencieux sur une alerte de sécurité) → rejetée au chargement ;
   entité de condition inconnue signalée en log.
10. Divers : dates françaises factorisées ; apostrophes normalisées (« l'entrée »
    devient résoluble comme pièce).

79 tests après correctifs (7 ajoutés pour verrouiller les points 1, 2, 4, 5, 6, 9).
Les points de doute plus haut restent ouverts pour un second avis.

## Hors périmètre (ne pas commenter)

Le modèle d'autorisation lui-même (ordre direct/proposition/sensible) est
validé par Guillaume ; la voix reste locale ; LAN uniquement. Les phases 3+
(moniteurs, dev) ne sont pas dans ce périmètre.
