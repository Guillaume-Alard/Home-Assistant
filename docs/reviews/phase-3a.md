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

## Addendum — revue interne du 01/09 (8 constats, tous corrigés)

1. **Bloquant (vérifié contre le haproxy.cfg upstream)** : `POST: 0` refusait
   tous les POST AVANT la règle `ALLOW_RESTARTS` → le redémarrage approuvé
   aurait toujours échoué en 403 ; et `POST: 1` aurait ouvert create/exec.
   → **Deux proxys** : lecture pure (POST refusés) + un proxy « restart »
   minimal (`CONTAINERS=0` + `ALLOW_RESTARTS` : seule cette route matche).
2. `_HEALTH_RE` détournait « désactive le rapport du matin » → seules les
   formulations de demande déclenchent le résumé ; parler DU rapport va au LLM.
   Même famille (pré-existant, débusqué par les tests) : « à quelle heure est le
   rapport ? » déclenchait l'intent *heure* — regex resserré.
3. Rapport quotidien : 4 listages Docker et ~40 stats par rapport → un seul
   snapshot partagé résumé/audit, liste passée à `memory_usage`, sections
   Docker/Atrium interrogées en parallèle.
4. `_demux_logs` rendait « » pour un flux TTY < 8 octets → cas couvert + testé.
5. `redemarrer_conteneur` sans Docker configuré → message clair (plus d'erreur
   de registre interne).
6. Garde-fou restart étendu aux proxys eux-mêmes (pas d'auto-décapitation de la
   surveillance).
7. Une stats non-JSON d'un seul conteneur faisait tomber tout le snapshot →
   tolérance par conteneur.
8. Planificateur : sleep-until remplacé par un sondage à la minute, insensible
   aux changements d'heure (DST).

91 tests après correctifs. Les points de doute ci-dessus restent ouverts
(le n°1 est résolu par le design à deux proxys — reste l'avis sur son élégance).

## Comment tester

```bash
cd core && pip install -r requirements-dev.txt && pytest -q   # 90 tests
```

Réel : README → « Surveillance de Nebula (Phase 3A) ».

## Hors périmètre

Le modèle d'autorisation (validé), la Phase 3B (worker Claude Code, Loggia),
l'UI finale (Phase 4).
