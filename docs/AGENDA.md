# Agenda de Luna (Phase 12) — Google Agenda en lecture seule

Luna peut lire ton **Google Agenda** : tes rendez-vous du **jour** et de la
**semaine à venir** (titre, heure, lieu). Elle les cite dans le **briefing du
matin** (Phase 11), répond à « qu'est-ce que j'ai aujourd'hui ? », et les affiche
dans le tiroir **📅** du cockpit. Deux garanties, par construction :

- **Lecture seule absolue.** L'autorisation demandée à Google est la portée
  `calendar.readonly`. Sentinel ne peut **jamais** créer, déplacer ni supprimer
  un événement — Google le refuse au niveau du jeton.
- **Pour toi seul.** L'agenda est réservé au **propriétaire** : Luna le consulte
  si elle reconnaît ta voix (Phase 2) ou à l'écrit dans le cockpit. La maisonnée
  et les invités n'y accèdent jamais (outil `agenda` de niveau *propriétaire*).

Rien n'est stocké : Luna interroge l'agenda à la demande et n'en garde aucune copie.

> **Écriture (Phase 13), en option.** Par défaut l'agenda est en **lecture seule**.
> Tu peux autoriser Luna à **préparer des rendez-vous** : même dans ce mode, elle
> ne crée **jamais** rien elle-même — elle dépose une **proposition** que tu
> approuves dans le cockpit, exactement comme les autres écritures. Voir la
> section « Écriture par proposition » plus bas.

## Mise en place (une fois)

Le plus simple : **réutilise le projet Google et l'identifiant OAuth déjà créés
pour Gmail** (Phase 3, voir [EMAIL.md](EMAIL.md)). Il ne reste qu'à activer l'API
Agenda et à obtenir un jeton portant la portée agenda.

### 1) Activer l'API Google Calendar (~2 min)

1. Va sur <https://console.cloud.google.com/> et ouvre **le projet de Gmail**
   (ex. « Sentinel »). *(Si tu n'as pas encore fait Gmail : suis d'abord les
   étapes 1, 3 et 4 de [EMAIL.md](EMAIL.md) pour créer le projet, l'écran de
   consentement avec ta propre adresse en utilisateur de test, et l'ID client
   OAuth « Application de bureau ».)*
2. **API et services › Bibliothèque** : cherche **Google Calendar API** et clique
   **Activer**.

L'ID client et le secret sont **les mêmes que Gmail** — rien à changer dans `.env`
pour ces deux-là.

### 2) Obtenir le jeton de rafraîchissement (sur TON PC)

Le consentement Google a besoin d'un navigateur : lance le script **sur ton PC**
(pas sur Nebula). Il n'a besoin que de Python 3, aucune installation.

```
python agenda/authorize.py --client-id "TON_ID" --client-secret "TON_SECRET"
```

*(ou, si `GMAIL_CLIENT_ID` / `GMAIL_CLIENT_SECRET` sont déjà dans ton
environnement, simplement `python agenda/authorize.py`.)*

Une fenêtre Google s'ouvre → autorise l'accès en **lecture** à l'agenda. Le
script affiche alors une ligne :

```
GCAL_REFRESH_TOKEN=1//0g....
```

> Google affichera un avertissement « application non validée » (normal pour une
> app perso non publiée) : choisis **Paramètres avancés › Continuer**. Comme tu es
> l'utilisateur de test, l'accès fonctionne.

### 3) Renseigner et redémarrer

Dans `.env` sur Nebula (le client id/secret y sont déjà, hérités de Gmail) :

```
GCAL_REFRESH_TOKEN=1//0g....
GCAL_CALENDAR_ID=primary
```

Puis :

```
docker compose up -d sentinel-core
```

## Utilisation

- **À l'écrit** : bouton **📅** dans la barre du cockpit → le tiroir liste tes
  rendez-vous des sept prochains jours, groupés par jour (bouton ↻ pour
  rafraîchir).
- **À la voix** (si tu es reconnu) : « Luna, qu'est-ce que j'ai aujourd'hui ? »,
  « Mon agenda de la semaine ? », « Suis-je libre demain ? ».
- **Briefing du matin** : l'agenda du jour s'ajoute automatiquement au briefing
  (voir [BRIEFING.md](BRIEFING.md)).

## Écriture par proposition (Phase 13, en option)

En lecture seule, Luna ne fait que consulter. Tu peux l'autoriser à **préparer
des rendez-vous** — sans jamais rien créer d'elle-même : chaque ajout devient une
**proposition** que tu approuves dans le cockpit (le même moteur « propose puis
approuve » que le reste des écritures). Rien ne part vers Google tant que tu n'as
pas cliqué **Approuver**.

**Activer (une fois) :**

1. Réautorise avec la portée **écriture** (lecture + écriture d'événements) — sur
   ton PC, comme pour la lecture, mais avec `--write` :
   ```
   python agenda/authorize.py --write
   ```
   La fenêtre Google demandera cette fois de **gérer** tes événements. Le script
   affiche un nouveau `GCAL_REFRESH_TOKEN` **et** la ligne `GCAL_WRITE=1`.
2. Dans `.env` sur Nebula, **remplace** l'ancien jeton par le nouveau et ajoute le
   drapeau :
   ```
   GCAL_REFRESH_TOKEN=1//0g....   ← le nouveau (portée écriture)
   GCAL_WRITE=1
   ```
3. `docker compose up -d sentinel-core`.

**Utiliser :** « Luna, **ajoute** un rendez-vous dentiste demain à 14h », « **note**
la réunion projet lundi 10h salle Nebula ». Luna prépare l'événement (titre, date,
heure, lieu) et dépose une **proposition** — tu la vois dans le tiroir
**Propositions** (⚑) du cockpit, avec **Approuver / Refuser**. Elle n'est ajoutée à
Google Agenda qu'**après** ton approbation. Un refus n'écrit rien.

> **Garanties.** L'écriture n'existe **que** par proposition : l'action
> `agenda.creer` est marquée « jamais en ordre direct » — même à la voix, même
> reconnu, rien n'est créé sans l'étape d'approbation. Réservé au **propriétaire**.
> Un test statique verrouille le fait que seul le moteur d'actions peut écrire
> dans l'agenda. Pour tout couper : `GCAL_WRITE=` (vide) → retour en lecture seule,
> sans toucher au reste.

## Réglages (`.env`)

| Variable | Rôle |
|---|---|
| `GMAIL_CLIENT_ID` / `GMAIL_CLIENT_SECRET` | Identifiant OAuth « Application de bureau » — **partagé avec Gmail**. |
| `GCAL_REFRESH_TOKEN` | Jeton obtenu via `agenda/authorize.py` (ajoute `--write` pour l'écriture). Vide = agenda désactivé. |
| `GCAL_CALENDAR_ID` | Agenda lu/écrit : `primary` (ton agenda principal) ou un id `…@group.calendar.google.com` pour un agenda partagé précis. Défaut `primary`. |
| `GCAL_WRITE` | `1` pour autoriser Luna à **proposer** des rendez-vous (jeton `--write` requis). Vide = lecture seule. |

L'agenda est **actif** (lecture) dès que `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`
et `GCAL_REFRESH_TOKEN` sont renseignés (`calendar_enabled`). L'**écriture** exige
en plus `GCAL_WRITE=1` (`calendar_write_enabled`).

## Révoquer / dépanner

- **Tout couper** : vide `GCAL_REFRESH_TOKEN` dans `.env` puis redémarre (Gmail
  reste inchangé).
- **Retirer l'accès côté Google** : <https://myaccount.google.com/permissions>.
- **« autorisation Agenda refusée »** dans Luna : le jeton a expiré ou été révoqué
  → relance `agenda/authorize.py` pour en régénérer un.
- **Agenda Workspace (pro)** : même procédure, mais un administrateur peut devoir
  autoriser l'app.

## Sous le capot

- `core/app/agenda/client.py` — `CalendarClient` : rafraîchit un jeton d'accès
  court (mis en cache) et appelle l'API REST Calendar
  (`calendars/{id}/events` avec `singleEvents=true&orderBy=startTime`, bornée par
  `timeMin`/`timeMax`). Même motif que `mail/client.py` ; httpx uniquement.
  `today()` = de minuit à minuit local ; `upcoming(jours)` = à partir de maintenant.
- `agenda/authorize.py` — flux OAuth « application de bureau » en local (stdlib),
  portée `calendar.readonly`.
- Outil LLM `agenda` (réservé au **propriétaire** via le contrôle d'accès de la
  Phase 2) ; côté écrit, requête WebSocket `agenda` servie au seul demandeur.
- `BriefingService` reçoit le client agenda et ajoute la ligne « Agenda : … » au
  briefing du matin.
- Aucune route d'écriture n'existe : la lecture seule est garantie par la portée.
