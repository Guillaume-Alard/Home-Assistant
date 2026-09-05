# Brief de revue externe — Phase 3B (le copilote qui agit)

> Pour un avis extérieur. Contexte : [PLAN.md](../PLAN.md), [ARCHITECTURE.md](../ARCHITECTURE.md),
> briefs précédents : [phase-1](phase-1.md), [phase-2](phase-2.md), [phase-3a](phase-3a.md).

## Ce qui a été construit

- **Worker isolé** (`worker/`) : Claude Code headless + mini-API FastAPI (réseau
  interne). Ne reçoit ni jeton Nova ni socket Docker. Clone jetable, branche
  `sentinel/<id>`, une tâche à la fois, timeout, secrets purgés des sorties,
  persistance `tasks.json`, volume nommé.
- **Flux complet** : demande explicite → `dev.task` (ordre direct, risque
  faible — n'écrit que dans le bac à sable, journalisé) → annonce de fin par le
  `DevWatcher` → **proposition automatique de push** (`dev.push`,
  propositions uniquement) → approbation → branche poussée → merge/HACS par
  Guillaume.
- **Outils LLM** : `lancer_tache_dev`, `etat_taches_dev`, `lire_diff_dev`.
- Invariant statique étendu (`start_task`/`push_branch` hors exécuteurs = CI rouge).
- Tests : 101 (10 nouveaux, faux worker HTTP complet).

## Points de doute — avis sollicité

1. **`--dangerously-skip-permissions` dans le worker** : justifié par
   l'isolation du conteneur (pas de secrets, workspace jetable, réseau sortant
   seulement) — angles morts ? (ex. exfiltration du GITHUB_TOKEN par un prompt
   piégé dans le contenu d'un dépôt tiers — mitigé par la liste blanche de
   dépôts possédés par Guillaume.)
2. **`dev.task` en risque faible/direct** : lancer une tâche n'écrit que dans
   le bac à sable mais consomme du forfait et du temps — le bon niveau, ou
   faudrait-il un plafond quotidien ?
3. **Sécurité du push** : jeton PAT portée `repo` dans l'env du worker ;
   poussée uniquement de `sentinel/*` vers le dépôt d'origine de la tâche.
   Suffisant, ou restreindre côté GitHub (fine-grained PAT par dépôt) à
   recommander plus fort dans le README ?
4. **Une tâche à la fois + clone frais à chaque fois** : simplicité assumée —
   cache de clones utile ou prématuré ?
5. **Parsing de `--output-format json`** : champ `result` avec repli sur la
   sortie brute — fragile face aux évolutions du CLI ?

## Comment tester

```bash
cd core && pip install -r requirements-dev.txt && pytest -q   # 101 tests
```

Réel : README → « Atelier de développement (Phase 3B) » (setup-token, PAT), puis
« Lance une tâche de dev sur loggia : … ».

## Hors périmètre

UI finale (Phase 4), mot d'éveil/satellites (Phase 5), application automatique
sur la prod (exclue par conception : le déploiement reste manuel via GitHub/HACS).
