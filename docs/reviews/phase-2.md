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

## Hors périmètre (ne pas commenter)

Le modèle d'autorisation lui-même (ordre direct/proposition/sensible) est
validé par Guillaume ; la voix reste locale ; LAN uniquement. Les phases 3+
(moniteurs, dev) ne sont pas dans ce périmètre.
