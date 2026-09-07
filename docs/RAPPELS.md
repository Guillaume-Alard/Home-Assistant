# Minuteurs & rappels vocaux (Phase 10)

Luna sait poser des **minuteurs** et des **rappels datés** — et te prévenir à
l'heure dite en **carillonnant** et en le disant. **Tout est local** : rien ne
quitte Nebula, aucun service tiers.

## Ce que tu peux dire

**Minuteurs (compte à rebours) :**
- « **Luna, minuteur 10 minutes** » · « **minuteur 5 minutes pour les pâtes** »
- « **minuteur 1 h 30** »

**Rappels (à une échéance) :**
- « **rappelle-moi dans 20 minutes de sortir le plat** »
- « **rappelle-moi à 18h d'appeler le garage** »
- « **rappelle-moi ce soir de lancer la machine** » · « **demain matin de…** »

**Gérer :**
- « **qu'est-ce que j'ai comme rappels ?** » → Luna les liste avec leur échéance.
- « **annule le minuteur des pâtes** ».

À l'échéance, Luna **carillonne** sur les appareils connectés et **annonce** :
« ⏰ Minuteur terminé — pâtes » / « ⏰ Rappel : appeler le garage ». Un rappel que
tu as demandé se dit **toujours** (même la nuit — c'est toi qui l'as posé).

## La tuile ⏰

Le bouton **⏰** ouvre un tiroir qui liste tes minuteurs et rappels en cours,
avec un **décompte en direct** (« dans 8:42 ») pour les proches et l'heure pour
les plus lointains. Un **✕** pour annuler.

## Bon à savoir

- **100% local** : les rappels sont rangés dans la base de Sentinel (ils
  **survivent à un redémarrage**) ; la boucle qui les déclenche tourne côté
  serveur, à ~5 s près.
- **Réservé aux personnes reconnues** (toi ou la maisonnée), comme la domotique
  courante — un invité non reconnu ne peut pas en poser.
- **Indépendant de Nova** : ça marche même si Home Assistant n'est pas là.

## Sous le capot

- `core/app/reminders.py` : `compute_due_iso` (relatif / absolu → échéance UTC)
  et `ReminderScheduler` (boucle de fond qui déclenche les échus).
- `core/app/store/db.py` : table `reminders` (`kind` timer/reminder, `due_at`
  UTC, `status` active/fired/cancelled).
- `core/app/brain/toolbox.py` : `minuteur` / `rappel` / `lister_rappels` /
  `annuler_rappel` (personne reconnue).
- `core/app/main.py` : WebSocket `reminders` (liste) / `reminder_cancel` ;
  diffusion `reminder_fired` (carillon) à l'échéance.
- `ui/` : tuile ⏰ avec décompte en direct.

## Réglages

`.env` : `SENTINEL_REMINDERS` (on/off). Aucune autre dépendance.

## Limites & évolutions

- Un rappel se **dit** (et carillonne) ; pas encore de rappels **récurrents**
  (« tous les mardis »). Piste : récurrence, et — le jour où l'agenda sera
  branché — des rappels tirés de ton calendrier.
