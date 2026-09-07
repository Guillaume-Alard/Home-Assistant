# Le briefing du matin (Phase 11)

Le rapport quotidien de Sentinel devient un vrai **briefing** : Luna rassemble ce
qu'elle sait déjà et t'en fait un point clair et court — **à heure fixe** le matin,
et **à la demande** dans la journée.

## Ce qu'il contient

- **Salutation + date.**
- **Météo** — via l'entité `weather.*` de Nova (condition + température). Rien à
  installer si tu as déjà une intégration météo dans Home Assistant.
- **Maison** — ce qui est ouvert (portes/fenêtres/volets), lumières allumées.
- **Courriel** — le nombre de messages **non lus** (si Gmail est configuré).
- **Rappels du jour** — tes rappels datés d'aujourd'hui (Phase 10).
- **Santé des systèmes** — les constats importants + les propositions en attente.

Exemple :

> Bonjour. Nous sommes le lundi 7 septembre.
> Dehors : pluvieux, 6°.
> Maison : ouvert — Porte d'entrée ; 1 lumière allumée.
> 3 messages non lus.
> Aujourd'hui : appeler le garage (14h).
> ⚠ Nova : 2 entités indisponibles.

## Quand ça se déclenche

- **Chaque matin**, à l'heure de `SENTINEL_DAILY_REPORT` (défaut 07:30) — Luna le
  **dit à voix haute** sur les appareils connectés.
- **À la demande** : « **fais-moi le briefing** », « **quoi de neuf ce matin ?** »,
  ou le raccourci **☀ Briefing du matin** sous la zone de saisie.

## Bon à savoir

- **Auto-suffisant** : aucune nouvelle mise en place. Tout est **local** sauf le
  courriel (déjà configuré en Phase 3).
- **Réservé aux personnes reconnues** (il contient courriel et rappels) — comme
  la domotique courante.
- **Dégradé proprement** : sans Nova, sans météo ou sans courriel, le brief se
  contente de ce qu'il a (au pire « rien de particulier à signaler »).

## Sous le capot

- `core/app/briefing.py` : `BriefingService.compose()` — assemble météo (entité
  `weather` de Nova), maison (réutilise le contexte de la Phase 7), courriel
  (compte de non-lus), rappels du jour (Phase 10), santé (audit). Déterministe,
  testable.
- `core/app/main.py` : la boucle du rapport quotidien appelle le briefing (et le
  **dit**) ; l'outil `briefing` le fournit à la demande.
- `core/app/brain/toolbox.py` : outil `briefing` (personne reconnue).
- `ui/` : raccourci **☀ Briefing du matin**.

## Réglages

`.env` : `SENTINEL_DAILY_REPORT` (heure du brief matinal), `SENTINEL_WEATHER_ENTITY`
(entité météo à utiliser ; vide = auto-détectée).

## Limites & évolutions

- La météo se limite à la **condition + température** de l'entité `weather`
  (les prévisions détaillées de Home Assistant passent désormais par un service
  dédié). Pistes : prévisions de la journée, **trafic** vers le travail (recherche
  web), et — le jour où l'agenda sera branché — les rendez-vous du jour.
