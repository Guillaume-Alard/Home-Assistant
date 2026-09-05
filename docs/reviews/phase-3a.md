# Brief de revue externe — Phase 3A (le copilote qui observe)

> Pour un avis extérieur. Contexte : [PLAN.md](../PLAN.md), [ARCHITECTURE.md](../ARCHITECTURE.md),
> briefs précédents : [phase-1.md](phase-1.md), [phase-2.md](phase-2.md).

## Ce qui a été construit

- **Proxy Docker** (`sentinel-dockerproxy`, tecnativa) : lecture + `ALLOW_RESTARTS`
  uniquement, aucun port LAN, socket jamais monté dans core.
- **Moniteurs** (`core/app/monitors/`) : Docker (conteneurs, stats, logs
  démultiplexés, restart), système (/proc), Nova (indisponibles, mises à jour,
  version), Atrium (HTTP), agrégés par `HealthService`.
- **Audit déterministe** : constats classés critique/attention/info avec actions
  suggérées (conteneurs en panne/gourmands, charge, RAM, mises à jour, entités
  mortes, ports publiés).
- **Rapport quotidien** : planificateur asyncio, publication dans le fil.
- **Première action non-domotique** : `docker.restart`, `direct=False` — un
  redémarrage n'existe que comme proposition approuvée ; invariant statique
  étendu aux écritures Docker.
- **Outils LLM** : `sante_systemes`, `logs_conteneur`, `audit_systemes`,
  `redemarrer_conteneur` ; intent local « comment vont les systèmes » sans LLM.
- **Dégradations propres** : chaque brique (Nova, Docker, Atrium) optionnelle.
- Tests : 90 (11 nouveaux, dont un faux proxy Docker HTTP avec logs multiplexés).

## Points de doute — avis sollicité

1. **ALLOW_RESTARTS** du proxy autorise aussi start/stop/kill — Sentinel n'expose
   que restart, mais la surface du proxy est plus large que l'usage. Acceptable,
   ou vaut-il un durcissement (HAProxy rules custom) ?
2. **Stats mémoire** : un appel `stats?one-shot` par conteneur (plafonné à 20,
   semaphore 5) à chaque snapshot/audit — coût acceptable ou faut-il un cache TTL ?
3. **Audit** : les seuils (charge > cœurs, RAM ≥ 90 %, mémoire conteneur) sont
   fixes/simples — assez utiles en v1, ou faux positifs prévisibles gênants ?
4. **Rapport quotidien** : boucle sleep-until (recalcule après chaque tir, +61 s
   d'anti-rebond) — des pièges (DST, dérive) qui justifieraient une lib de cron ?
5. **`/proc` de l'hôte depuis le conteneur** : charge et meminfo reflètent bien
   l'hôte, mais pas les disques — la limite est documentée ; une meilleure source
   simple (sans privilèges) t'évoque-t-elle quelque chose sur Unraid ?

## Comment tester

```bash
cd core && pip install -r requirements-dev.txt && pytest -q   # 90 tests
```

Réel : README → « Surveillance de Nebula (Phase 3A) ».

## Hors périmètre

Le modèle d'autorisation (validé), la Phase 3B (worker Claude Code, Loggia),
l'UI finale (Phase 4).
