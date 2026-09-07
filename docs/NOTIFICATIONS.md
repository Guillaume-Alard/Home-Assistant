# Notifications mobiles (Phase 14) — Luna te joint sur ton téléphone

Quand tu n'es pas devant le cockpit, Luna peut **pousser sur ton téléphone** ce
qui compte, via l'app **Home Assistant** (companion) :

- **Un rappel qui sonne** — même absent, ton minuteur / rappel te suit.
- **Une alerte de sécurité** — « porte ouverte la nuit », « fenêtre restée
  ouverte »… : les suggestions proactives de gravité *avertissement* et les
  alertes de Sentinel.
- **Le briefing du matin** — en option, poussé sur le téléphone.

Deux garanties, par construction :

- **Communication, jamais pilotage.** Une notification n'agit pas sur la maison :
  c'est un message *vers toi*. La poussée passe par le **moteur d'actions**
  (chemin « système », risque faible) et le service `notify` de Nova — donc par
  l'unique exécuteur autorisé, et elle est **journalisée** comme toute écriture.
  L'invariant de sécurité reste intact.
- **La proactivité ne s'échappe pas.** Le veilleur (Phase 7) continue de ne faire
  qu'**observer, dire, et proposer** : la notification est juste un canal de plus
  pour *te dire*, pas pour agir. Aucune action n'part sans ton approbation.

## Prérequis : l'app Home Assistant sur ton téléphone

1. Installe l'app **Home Assistant** (iOS / Android) et connecte-la à Nova.
2. Cela crée un service de notification propre à ton appareil :
   `notify.mobile_app_<nom_de_ton_appareil>`.
3. Trouve son nom exact dans Nova → **Outils de développement › Actions**
   (anciennement *Services*), en tapant `notify.` : tu verras
   `notify.mobile_app_...`. Note la partie **après** `notify.`.

## Mise en place (une fois)

Dans `.env` sur Nebula :

```
SENTINEL_NOTIFY_SERVICE=mobile_app_pixel_de_guillaume
```

> ⚠️ **Sans** le préfixe `notify.` — juste `mobile_app_...`.

Puis, au choix, ce qui est poussé (valeurs par défaut indiquées) :

```
SENTINEL_NOTIFY_REMINDERS=on     # rappels & minuteurs qui sonnent
SENTINEL_NOTIFY_ALERTS=on        # alertes de sécurité (proactivité + Sentinel)
SENTINEL_NOTIFY_BRIEFING=off     # briefing du matin poussé (long : off par défaut)
```

Redémarre :

```
docker compose up -d sentinel-core
```

## Vérifier

Dans le cockpit → **Paramètres › Connexions** → carte **Notifications mobiles** →
bouton **« Envoyer un test »**. Tu dois recevoir une notification sur ton
téléphone dans la seconde. Le cockpit confirme l'envoi (ou signale l'échec).

## Réglages (`.env`)

| Variable | Rôle |
|---|---|
| `SENTINEL_NOTIFY_SERVICE` | Service Nova `mobile_app_<appareil>` (sans `notify.`). Vide = désactivé. |
| `SENTINEL_NOTIFY_REMINDERS` | Pousser les rappels / minuteurs qui sonnent. Défaut `on`. |
| `SENTINEL_NOTIFY_ALERTS` | Pousser les alertes de sécurité (proactivité *avertissement* + alertes Sentinel). Défaut `on`. |
| `SENTINEL_NOTIFY_BRIEFING` | Pousser le briefing du matin. Défaut `off`. |

## Dépanner

- **Rien ne s'affiche** : vérifie le nom exact du service (Outils de développement
  › Actions), que l'app a bien la permission de notifier (réglages du téléphone),
  et que Nova est connectée (carte HA du cockpit).
- **« Échec » au test** : le service est probablement mal orthographié, ou
  contient encore le préfixe `notify.` — retire-le.
- **Trop / pas assez** : ajuste les trois bascules `SENTINEL_NOTIFY_*`.
- **Tout couper** : `SENTINEL_NOTIFY_SERVICE=` (vide) puis redémarre.

## Sous le capot

- `core/app/notify.py` — `Notifier` : pousse via `engine.run_system("ha.notify", …)`
  (chemin système, risque faible uniquement). Ne connaît qu'un service et trois
  bascules ; renvoie `False` en silence si non configuré.
- Câblage (`core/app/main.py`) : un rappel qui sonne (`_on_reminder_fired`), une
  alerte (`announce`, gravité *warning*/*critical*), le briefing quotidien, et les
  suggestions proactives de sécurité (`ProactiveEngine`, gravité *warning*) sont
  poussés — chacun sous sa bascule.
- L'écriture Nova reste centralisée dans l'exécuteur `ha.notify` (bas risque) ;
  l'invariant statique (`tests/test_invariant.py`) garantit que seul le moteur
  d'actions parle à Nova.
- Test depuis le cockpit : message WebSocket `notify_test`.
