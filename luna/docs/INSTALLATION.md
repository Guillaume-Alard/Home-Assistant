# Luna — installation sur Nova, en partant de rien

**Pour une machine où Home Assistant OS tourne et où rien d'autre n'est
installé.** Compter une heure, dont vingt minutes d'attente pendant que
l'add-on se construit.

Rien ici ne demande P0 : Luna s'installe et fonctionne en HTTP. Seuls le micro
et la caméra attendent le HTTPS ([`P0-HTTPS.md`](P0-HTTPS.md)) — l'écrit marche
tout de suite.

---

## 0. Le problème de la poule et de l'œuf

Home Assistant OS n'est pas un Linux ordinaire : pas de `ssh` par défaut, pas
d'accès au disque, pas de moyen évident de déposer un fichier. Or les trois
artefacts de Luna sont des fichiers à déposer à trois endroits.

La sortie, c'est qu'un add-on officiel donne cet accès. On installe donc
**d'abord un moyen de copier**, ensuite Luna.

| Ce qu'on dépose | Où | Ce que c'est |
|---|---|---|
| `addon/` | `/addons/luna/` | Le cerveau. HAOS le construira en conteneur |
| `integration/custom_components/luna/` | `/config/custom_components/luna/` | Le nerf. Chargé par Home Assistant au démarrage |
| `card/luna-card.js` | `/config/www/luna-card.js` | Le visage. Servi au navigateur sous `/local/` |

---

## 1. Préparer Home Assistant

### 1.1 — Activer le mode avancé

**Clique sur ton nom, en bas à gauche → onglet du haut → Mode avancé.**

Sans lui, les ressources Lovelace et une partie de la boutique restent
invisibles. C'est la cause d'une bonne moitié des « je ne trouve pas ce menu ».

### 1.2 — Un moyen de copier des fichiers

Deux chemins. Prends le premier si tu es sur un Mac ou un PC ; le second si tu
préfères la ligne de commande et que tu comptes redéployer souvent.

**a. Samba share — le plus simple pour un premier coup**

**Paramètres → Modules complémentaires → Boutique → Samba share → Installer.**
Officiel, présent d'origine dans la boutique.

```yaml
workgroup: WORKGROUP
username: nova
password: "un-mot-de-passe-a-toi"
allow_hosts:
  - 192.168.0.0/16
  - 172.16.0.0/12
  - 10.0.0.0/8
```

Démarre-le, active **Démarrer au démarrage**. Nova apparaît alors dans le
voisinage réseau, avec des partages `addons`, `config`, `share`, `ssl`. Copier
un dossier devient un glisser-déposer.

> Sur macOS : Finder → Aller → Se connecter au serveur → `smb://nova.local`.
> Sur Windows : `\\nova.local` dans l'explorateur.

**b. Advanced SSH & Web Terminal — pour redéployer ensuite**

C'est celui dont `ops/deploy.sh` a besoin. Il n'est **pas** dans la boutique
d'origine : il faut d'abord ajouter son dépôt.

**Boutique → ⋮ (en haut à droite) → Dépôts →** ajouter :

```
https://github.com/hassio-addons/repository
```

Puis installer **Advanced SSH & Web Terminal**, y déposer ta clé publique, et —
c'est le point qu'on oublie — **désactiver `protection_mode`** dans l'onglet
Configuration. Sans ça, `/addons` reste inaccessible et le déploiement échoue
sans dire pourquoi.

> Ne confonds pas avec **Terminal & SSH**, l'add-on officiel : celui-là ne voit
> que `/config`, jamais `/addons`. Il ne suffit pas.

---

## 2. Récupérer les fichiers, et comprendre ce qu'on voit

### 2.1 — Télécharger depuis GitHub

Sur **github.com/Guillaume-Alard/Home-Assistant** :

1. Bouton **branches** (au-dessus de la liste des fichiers) → choisir
   **`claude/new-session-3gvhdx`**.
2. Bouton vert **`<> Code`** → **Download ZIP**.
3. Décompresser. Tu obtiens un dossier nommé
   **`Home-Assistant-claude-new-session-3gvhdx`**.

### 2.2 — Pourquoi il y a dix dossiers et pas trois

C'est le point qui prête à confusion, et c'est normal : **ce dépôt n'est pas
celui de Luna.** C'est ton ancien dépôt Home Assistant, celui de *Sentinelle*.
Luna y a été ajoutée dans un sous-dossier, sans rien toucher au reste (décision
prise au tout début du projet : ne rien casser de l'existant).

Voilà exactement ce que tu vois après décompression :

```
Home-Assistant-claude-new-session-3gvhdx/
│
├── agenda/              ┐
├── config/              │
├── core/                │
├── custom_components/   │  Sentinelle — l'ancien projet.
├── docs/                ├  RIEN À FAIRE AVEC. N'y touche pas,
├── mail/                │  ne le copie pas, ne le lis pas.
├── speaker/             │
├── ui/                  │
├── worker/              │
├── docker-compose.yml   │
├── LICENSE              │
├── README.md            ┘
│
└── luna/            ★ ★ ★  TOUT CE QUI NOUS INTÉRESSE EST ICI  ★ ★ ★
```

**Tu peux ignorer les onze premières lignes.** Ouvre `luna/`, et oublie le reste.

### 2.3 — Ce qu'il y a dans `luna/`

```
luna/
├── addon/           ★ ARTEFACT 1 — le cerveau
├── integration/
│   └── custom_components/
│       └── luna/    ★ ARTEFACT 2 — le nerf
├── card/
│   └── luna-card.js ★ ARTEFACT 3 — le visage (un seul fichier)
│
├── docs/            documentation (dont ce fichier). Ne se copie pas.
├── ops/             script de déploiement. Ne se copie pas.
├── .github/         intégration continue. Ne se copie pas.
└── README.md        ne se copie pas.
```

Trois artefacts à copier, quatre choses à laisser sur ton PC.

### 2.4 — Les trois copies, exactement

Connecte-toi au partage Samba (§1.2). Tu vois des dossiers `addons`, `config`,
`share`, `ssl`. Puis, **dans cet ordre** :

---

**Copie 1 — le cerveau**

| | |
|---|---|
| **Depuis** | `luna/addon/` — le dossier entier |
| **Vers** | le partage `addons`, dans un dossier que tu nommes **`luna`** |
| **Résultat** | `addons/luna/config.yaml` doit exister |

⚠️ **Le piège.** Tu copies le dossier `addon` (singulier, sans « s ») et tu le
renommes `luna`. Le plus simple : crée d'abord un dossier vide `luna` dans
`addons`, puis copie **le contenu** de `luna/addon/` dedans.

Après la copie, `addons/luna/` doit contenir exactement ceci :

```
addons/luna/
├── config.yaml          ← ce fichier doit être ICI, à la racine
├── Dockerfile
├── requirements.txt
├── pyproject.toml
├── README.md
├── .dockerignore
├── .importlinter
├── luna/                ← oui, un dossier « luna » dans « luna ». C'est normal :
│                          c'est le code Python. Ne le remonte pas d'un cran.
└── tests/               ← inutile sur Nova, mais inoffensif. Tu peux le supprimer.
```

Si `addons/luna/addon/config.yaml` existe au lieu de `addons/luna/config.yaml`,
tu as copié un niveau de trop. C'est **la** cause du « Luna n'apparaît pas dans
les add-ons locaux ».

---

**Copie 2 — le nerf**

| | |
|---|---|
| **Depuis** | `luna/integration/custom_components/luna/` — le dossier `luna` tout au fond |
| **Vers** | `config/custom_components/luna/` |
| **Résultat** | `config/custom_components/luna/manifest.json` doit exister |

⚠️ **Ne copie pas `luna/integration/`** — tu emporterais `tests/` et
`pyproject.toml` dans la configuration de Home Assistant. Descends jusqu'au
dossier `luna` qui se trouve dans `custom_components`.

Il contient onze fichiers, ni plus ni moins :

```
config/custom_components/luna/
├── manifest.json
├── __init__.py
├── binary_sensor.py
├── client.py
├── config_flow.py
├── const.py
├── conversation.py
├── strings.json
├── websocket.py
└── translations/
    ├── en.json
    └── fr.json
```

> Si `config/custom_components/` n'existe pas, crée-le.

---

**Copie 3 — le visage**

| | |
|---|---|
| **Depuis** | `luna/card/luna-card.js` — **un seul fichier** |
| **Vers** | `config/www/luna-card.js` |

⚠️ **`config/www/` n'existe presque jamais.** Crée le dossier `www` dans
`config` avant de copier. C'est ce dossier que Home Assistant sert au navigateur
sous l'adresse `/local/`.

Ne copie pas le reste de `luna/card/` : les tests et le `pyproject.toml` n'ont
rien à faire sur Nova.

---

### 2.5 — Vérifier avant d'aller plus loin

Trois chemins doivent exister. Vérifie-les un par un dans le partage :

```
addons/luna/config.yaml
config/custom_components/luna/manifest.json
config/www/luna-card.js
```

Si les trois sont là, la partie fastidieuse est finie.

### 2.6 — Plus tard, par SSH

Une fois **Advanced SSH & Web Terminal** installé (§1.2b), tout ce qui précède
tient en une commande, depuis ton PC, dans le dépôt cloné :

```bash
./ops/deploy.sh root@nova.local
```

Il fait les trois copies aux bons endroits, exclut les tests et les caches. À
utiliser pour les mises à jour ; pour la première fois, le glisser-déposer est
plus sûr parce que tu vois ce qui se passe.

## 3. Deux secrets à préparer

**La clé API Anthropic.** Sur `console.anthropic.com` → API Keys → Create key.
Elle commence par `sk-ant-`. Vérifie qu'il y a du crédit sur le compte : sans
lui, Luna démarre, répond une erreur explicite à chaque tour, et le dit
franchement — mais elle ne converse pas.

**Le secret du relais.** C'est le mot de passe partagé entre l'add-on et
l'intégration. Il ne sort jamais de Nova. Fabrique-en un vrai :

```bash
openssl rand -hex 32
```

Garde-le sous la main : tu vas le taper **deux fois**, à l'identique — une fois
dans l'add-on, une fois dans l'intégration. C'est la cause d'échec numéro un de
l'étape 5.

---

## 4. Installer l'add-on

**Paramètres → Modules complémentaires → Boutique → ⋮ → Vérifier les mises à
jour.**

Une section **« Add-ons locaux »** apparaît en haut, avec **Luna** dedans. Si
elle n'apparaît pas, saute au §8.

**Installer.** La construction prend **cinq à quinze minutes sur un N95** : elle
télécharge Python, puis installe les dépendances. C'est normal, et ça n'arrive
qu'une fois — les reconstructions suivantes réutilisent la couche des
dépendances.

Onglet **Configuration**, avant de démarrer :

```yaml
anthropic_api_key: "sk-ant-…"
modele: "claude-sonnet-5"
effort: "low"
relay_secret: "le-secret-fabriqué-au-§3"
fuseau: "Europe/Paris"
journal: "info"
profils:
  - utilisateur_ha: "Guillaume"
    profil: "guillaume"
    presence: ""
profil_par_defaut: "guest"
```

`utilisateur_ha` doit correspondre **exactement** au nom de ton utilisateur
Home Assistant — c'est lui qui fait qu'elle sait à qui elle parle.

Laisse tout le reste par défaut : la voix, l'identité, la veille et la gardienne
s'allument plus tard, chacune quand tu lui donneras de quoi travailler.

**Démarrer**, puis onglet **Journal**. Tu dois y lire :

```
Luna démarre — réglages : {...}
Base ouverte : /data/luna.db (schéma v4)
Home Assistant 2026.x.x — NNN entités
Relais à l'écoute sur le port 8099
Gardienne : seuil 30 min, Loggia oui, journal système oui
Veille : aucune règle déclarée — rien à surveiller.
```

Les deux dernières lignes sont normales : la veille n'a pas encore de capteurs
([`P4-CAPTEURS.md`](P4-CAPTEURS.md)).

Active **Démarrer au démarrage** et **Surveillance**.

---

## 5. Installer l'intégration

**Redémarre Home Assistant** — Paramètres → Système → ⋮ → Redémarrer. C'est ce
qui lui fait découvrir `custom_components/luna`. Un rechargement de
configuration ne suffit pas pour une intégration neuve.

Puis **Paramètres → Appareils et services → Ajouter une intégration → Luna**.

| Champ | Valeur |
|---|---|
| Hôte | `local-luna` — laisse-le tel quel |
| Port | `8099` |
| Secret | **exactement** celui de l'add-on |

`local-luna` est le nom que le Supervisor donne à un add-on local sur le réseau
interne des add-ons. Ce n'est ni `localhost` ni `nova.local` : l'intégration
tourne dans le conteneur de Home Assistant, pas sur l'hôte.

Le formulaire **ouvre vraiment le relais avant d'enregistrer**. S'il accepte,
c'est que ça communique. S'il répond « injoignable », va au §8.

Vérifie ensuite que `binary_sensor.luna_en_ligne` existe et vaut **on**
(Outils de développement → États).

---

## 6. Déclarer la carte

**Paramètres → Tableaux de bord → ⋮ → Ressources → Ajouter une ressource.**

| Champ | Valeur |
|---|---|
| URL | `/local/luna-card.js` |
| Type | **Module JavaScript** |

> Le menu « Ressources » n'apparaît qu'en **mode avancé** (§1.1).

Puis, dans le tableau de bord Loggia : **⋮ → Modifier → + Ajouter une carte →
Manuel**, et colle :

```yaml
type: custom:luna-card
height: 620
```

Recharge la page en vidant le cache (**Ctrl/Cmd + Maj + R**). La console du
navigateur doit afficher une ligne `LUNA-CARD 0.1.0` : c'est la preuve que le
fichier servi est bien le tien.

---

## 7. Vérifier que tout marche

Dans l'ordre, chaque étape dépendant de la précédente :

| # | Vérification | Où |
|---|---|---|
| 1 | Le journal de l'add-on dit « Relais à l'écoute » | Add-on → Journal |
| 2 | `binary_sensor.luna_en_ligne` vaut `on` | Outils de développement → États |
| 3 | La carte s'affiche, l'orbe respire, le badge dit « Guillaume » | Loggia |
| 4 | « il est quelle heure ? » → une réponse en français | La carte |
| 5 | « allume le salon » → la lumière s'allume, une ligne d'outil apparaît | La carte |
| 6 | « ferme les volets » → un refus motivé, pas une erreur | La carte |

Le point 6 est celui que je regarderais en premier : c'est l'échelle d'autonomie
de §9 qui répond, et un refus clair prouve que l'arbitre est bien sur le chemin.

Le micro reste grisé tant que P0 n'est pas fait, avec un message qui le dit —
c'est voulu.

---

## 8. Quand ça ne marche pas

Par ordre de fréquence réelle.

**« Luna » n'apparaît pas dans les add-ons locaux.**
Dans neuf cas sur dix, c'est un niveau de dossier en trop. Le chemin exact doit
être `addons/luna/config.yaml`, **pas** `addons/luna/addon/config.yaml` ni
`addons/addon/config.yaml`. Va vérifier dans le partage avant toute autre chose.
Ensuite seulement, **⋮ → Vérifier les mises à jour** — un simple rechargement de
page ne suffit pas.

**La construction de l'add-on échoue.**
Lis le journal de construction. `no matching distribution` sur `onnxruntime`
voudrait dire que quelqu'un a remis l'image en Alpine — le `Dockerfile` explique
en tête pourquoi c'est du Debian, et la CI construit l'image à chaque envoi
précisément pour que ça ne revienne pas.

**L'intégration répond « injoignable ».**
Dans cet ordre : l'add-on est-il **démarré** ? Le secret est-il **identique des
deux côtés** (pas d'espace en fin de ligne) ? L'hôte est-il bien `local-luna` ?
Le journal de l'add-on montre-t-il une tentative de connexion refusée — auquel
cas c'est le secret, et lui seul.

**`binary_sensor.luna_en_ligne` est `off`.**
L'add-on s'est arrêté, ou n'a jamais joint Home Assistant. Son journal le dit.
La carte bascule d'elle-même en mode dégradé et l'affiche : elle ne fait jamais
semblant.

**La carte affiche « Custom element doesn't exist ».**
Le fichier n'est pas servi. Vérifie qu'il est bien en `/config/www/` (et pas
`/config/`), que la ressource est déclarée en **Module** et non en feuille de
style, puis vide le cache. Ouvrir `https://…/local/luna-card.js` directement
dans un onglet tranche en deux secondes : soit tu vois du JavaScript, soit tu
vois une erreur 404.

**Luna répond une erreur à chaque tour.**
Le message vient de l'API Anthropic et il est affiché tel quel : crédit épuisé,
clé invalide, saturation. C'est délibéré — §8 interdit les échecs silencieux, et
un « je n'arrive pas à répondre » sans motif ne se diagnostique pas.

---

## 9. Mettre à jour, ensuite

```bash
./ops/deploy.sh root@nova.local
```

Puis, selon ce qui a bougé :

| Ce qui a changé | Ce qu'il faut faire |
|---|---|
| `addon/` | Reconstruire l'add-on (⋮ → Reconstruire), puis le redémarrer |
| `integration/` | Redémarrer Home Assistant |
| `card/luna-card.js` | Vider le cache du navigateur. La version en console confirme |

Rien de tout ça ne touche à `/data` : la base — conversations, journal des
actions, empreintes, faits, incidents — survit à toutes les reconstructions.
C'est le seul chemin persistant, et HAOS le sauvegarde avec le reste.

---

## 10. Et ensuite

Luna converse et pilote la maison. Le reste s'allume pièce par pièce, dans
l'ordre de la liste du [README](../README.md) :

- **P0** débloque le micro (et la caméra, le jour venu) — [`P0-HTTPS.md`](P0-HTTPS.md)
- **P2** demande Whisper, Piper et un pipeline Assist
- **P3** demande un modèle d'empreinte de locuteur dans `/share`
- **P4** demande les capteurs de veille — [`P4-CAPTEURS.md`](P4-CAPTEURS.md)
- **P5** tourne déjà : la gardienne surveille dès le premier démarrage
