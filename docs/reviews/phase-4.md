# Brief de revue externe — Phase 4 (l'interface aboutie)

> Pour un avis extérieur. Contexte : [PLAN.md](../PLAN.md), [ARCHITECTURE.md](../ARCHITECTURE.md),
> briefs précédents : [phase-1](phase-1.md), [phase-2](phase-2.md),
> [phase-3a](phase-3a.md), [phase-3b](phase-3b.md).

## Ce qui a été construit

- **Console de l'atelier en direct** (demande explicite de Guillaume) :
  - le worker passe de `--output-format json` (boîte noire) à **`stream-json`** ;
    chaque événement du CLI est traduit en français (`worker/streamlog.py`,
    pur stdlib : « ▸ modifie README.md », texte de l'assistant, résultat final)
    dans un tampon mémoire borné par tâche, servi par
    `GET /tasks/{id}/log?after=N` (lecture incrémentale, index absolus stables
    même après troncature).
  - lecture streaming robuste : stdout/stderr séparés, `limit=8 Mo` par ligne
    (les résultats d'outils peuvent être énormes), timeout global avec kill du
    groupe de processus, secrets purgés ligne à ligne.
- **Cinq requêtes WS de lecture pure** (`dev_tasks`, `dev_log`, `dev_diff`,
  `sante`, `historique`) répondues au seul demandeur ; `dev_status` diffusé par
  le veilleur quand une tâche démarre/finit (pastille ⚒ qui pulse).
- **Quatre panneaux UI** : propositions (existant, refactoré en gestionnaire
  commun — un seul ouvert à la fois, Échap referme), atelier (liste des tâches,
  journal sondé toutes les 2 s pendant l'exécution, diff colorée), santé
  (tuiles Nova/Nebula/Docker/Atrium, sondage 30 s panneau ouvert), historique
  (journal des actions = piste d'audit du moteur, propositions passées).
- `TZ` transmis au worker (horodatages du journal en heure locale).
- Tests : 112 (10 nouveaux — traduction du flux, `/log` incrémental, protocole
  WS des panneaux de bout en bout sur les faux serveurs).

## Points de doute — avis sollicité

1. **Sondage plutôt que push** : le journal en direct est tiré (2 s) par le
   client pendant qu'une tâche tourne, uniquement panneau ouvert. Un vrai push
   serveur (le worker n'a pas de canal vers core hors HTTP) exigerait un flux
   SSE/WS worker→core→UI — complexité justifiée ou non pour un usage mono-foyer ?
2. **Tampon de journal en mémoire seulement** (perdu au redémarrage du worker,
   résumé/diff persistés eux) : acceptable, ou faut-il persister la traîne dans
   `tasks.json` ?
3. **`translate()` face aux évolutions du CLI** : tolérant par construction
   (ligne non-JSON montrée brute, type inconnu ignoré) — angle mort restant ?
4. **Périmètre lecture seule des panneaux** : aucune écriture possible par les
   nouveaux types WS (l'invariant du moteur d'actions est inchangé). La console
   n'offre volontairement ni « lancer une tâche » ni « pousser » — bon choix, ou
   frustrant à l'usage ?

## Comment tester

```bash
cd core && pip install -r requirements-dev.txt && pytest -q   # 112 tests
```

Réel : `git pull && docker compose up -d --build`, ouvrir l'UI → ⚒, puis
« Lance une tâche de dev sur loggia : … » et regarder le journal défiler.

## Hors périmètre

Mot d'éveil et satellites (Phase 5), lancement de tâches depuis la console
(volontairement exclu : la conversation et les propositions restent le seul
chemin d'écriture).
