# Courriel de Luna (Phase 3) — Gmail en lecture seule

Luna peut te **résumer tes courriels non lus** (expéditeur, objet, importance,
court aperçu). Deux garanties, par construction :

- **Lecture seule absolue.** L'autorisation demandée à Google est la portée
  `gmail.readonly`. Sentinel ne peut **jamais** envoyer, supprimer, ni même
  marquer un message comme lu — Google le refuse au niveau du jeton.
- **Pour toi seul.** Le relevé est réservé au **propriétaire** : Luna te le donne
  si elle reconnaît ta voix (Phase 2) ou à l'écrit dans le cockpit. La maisonnée
  et les invités n'y accèdent jamais.

Rien n'est stocké : Luna interroge Gmail à la demande et n'en garde aucune copie.

## Mise en place (une fois)

### 1) Créer l'accès côté Google (~10 min)

1. Va sur <https://console.cloud.google.com/> et crée un **projet** (ex. « Sentinel »).
2. **API et services › Bibliothèque** : cherche **Gmail API** et clique **Activer**.
3. **API et services › Écran de consentement OAuth** :
   - Type **Externe**, renseigne un nom d'app et ton adresse.
   - À l'étape **Utilisateurs de test**, **ajoute ta propre adresse Gmail** (sinon
     l'accès sera bloqué tant que l'app n'est pas « publiée »).
4. **API et services › Identifiants › Créer des identifiants › ID client OAuth** :
   - Type d'application : **Application de bureau**.
   - Tu obtiens un **ID client** et un **code secret du client** — garde-les.

### 2) Renseigner l'ID et le secret

Dans `.env` sur Nebula :

```
GMAIL_CLIENT_ID=xxxxxxxx.apps.googleusercontent.com
GMAIL_CLIENT_SECRET=xxxxxxxx
```

### 3) Obtenir le jeton de rafraîchissement (sur TON PC)

Le consentement Google a besoin d'un navigateur : lance le script **sur ton PC**
(pas sur Nebula). Il n'a besoin que de Python 3, aucune installation.

```
python mail/authorize.py --client-id "TON_ID" --client-secret "TON_SECRET"
```

Une fenêtre Google s'ouvre → autorise l'accès en **lecture** à Gmail. Le script
affiche alors une ligne :

```
GMAIL_REFRESH_TOKEN=1//0g....
```

Colle-la dans le `.env` de Nebula, sous les deux autres.

> Google affichera un avertissement « application non validée » (normal pour une
> app perso non publiée) : choisis **Paramètres avancés › Continuer**. Comme tu es
> l'utilisateur de test, l'accès fonctionne.

### 4) Redémarrer

```
docker compose up -d sentinel-core
```

## Utilisation

- **À l'écrit** : bouton **✉** dans la barre du cockpit → le tiroir liste tes
  non-lus (bouton ↻ pour rafraîchir).
- **À la voix** (si tu es reconnu) : « Luna, relève mes mails », « J'ai des
  messages importants ? ». Elle en fait un résumé parlé.

## Réglages (`.env`)

| Variable | Rôle |
|---|---|
| `GMAIL_CLIENT_ID` / `GMAIL_CLIENT_SECRET` | Identifiant OAuth « Application de bureau ». |
| `GMAIL_REFRESH_TOKEN` | Jeton obtenu via `mail/authorize.py`. Les trois vides = désactivé. |
| `MAIL_MAX` | Nombre de non-lus détaillés dans un résumé (défaut 10 ; le total est toujours donné). |

## Révoquer / dépanner

- **Tout couper** : vide les trois `GMAIL_*` dans `.env` puis redémarre.
- **Retirer l'accès côté Google** : <https://myaccount.google.com/permissions>.
- **« autorisation refusée »** dans Luna : le jeton a expiré ou été révoqué
  (ex. mot de passe changé) → relance `mail/authorize.py` pour en régénérer un.
- Autre boîte qu'un compte perso (Workspace) : même procédure, mais un
  administrateur peut devoir autoriser l'app.

## Sous le capot

- `core/app/mail/client.py` — `GmailClient` : rafraîchit un jeton d'accès court
  (mis en cache) et appelle l'API REST Gmail (`messages.list` + `messages.get`
  format `metadata`). Aucune dépendance lourde (httpx uniquement).
- `mail/authorize.py` — flux OAuth « application de bureau » en local (stdlib).
- Outil LLM `resume_mails` (réservé au **propriétaire** via le contrôle d'accès
  de la Phase 2) ; côté écrit, requête WebSocket `mail` servie au seul demandeur.
- Aucune route d'écriture n'existe : la lecture seule est garantie par la portée.
