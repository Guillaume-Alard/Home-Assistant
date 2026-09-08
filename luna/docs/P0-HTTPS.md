# Luna — P0 : HTTPS en local

**Statut : procédure arrêtée, reste à exécuter sur Nova.**
Contexte connu au 8 septembre 2026 : Nova tourne bien Home Assistant OS (H1
close), et Guillaume ne possède **aucun nom de domaine** — seulement l'adresse
Nabu Casa.

§4 du cahier des charges : « À régler **avant** la phase voix. » Ce document dit
comment, sans acheter de domaine.

---

## 1. Le problème, précisément

`getUserMedia()` — l'appel qui ouvre le micro — est refusé par tous les
navigateurs hors **contexte sécurisé** : `https://`, ou `http://localhost`. Pas
`http://192.168.1.42:8123`.

```
Companion iOS/Android, en Wi-Fi maison
   └─► URL interne  http://192.168.1.42:8123
         └─► window.isSecureContext === false
               └─► navigator.mediaDevices === undefined
                     └─► le bouton micro de la carte Luna ne peut rien faire
```

Ce n'est pas contournable côté code : pas de repli, pas de polyfill, pas de
drapeau. Il faut du vrai HTTPS.

---

## 2. D'abord, une correction : l'adresse Nabu Casa marche en local

`https://xxxxxxxx.ui.nabu.casa` est une adresse **publique** en HTTPS valide.
Depuis un téléphone posé sur le canapé, le navigateur sort sur internet,
atteint les serveurs Nabu Casa, et redescend par le tunnel que Home Assistant
maintient en permanence. **La page s'ouvre, le contexte est sécurisé, le micro
fonctionne.** C'est d'ailleurs la solution 2 de ton §4, qualifiée
d'« immédiat ».

Ce qu'elle ne fait pas, c'est *rester* locale :

- chaque commande de lumière fait un aller-retour par la fibre ;
- **Luna se tait quand la connexion tombe** — ce qui contredit l'esprit de §2 et
  la lettre de §7 (« Pas de dépendance cloud pour la reconnaissance »).

**Donc : c'est une bonne béquille, pas une destination.** Elle débloque P2 ce
soir. Elle ne doit pas rester l'état final.

### La faire, en deux minutes

Sur chaque appareil : **Paramètres → Companion app → Serveurs → Nova**, vider
le champ **URL interne**. L'app utilisera l'adresse Nabu Casa partout, y compris
à la maison.

Vérifier ensuite avec le test de §7. Si les six lignes passent au vert, la phase
voix n'est plus bloquée pendant que tu montes la suite.

---

## 3. Le vrai chemin, gratuit : DuckDNS

Un certificat valide exige un nom que tu contrôles. Tu n'as pas de domaine —
mais **DuckDNS en donne un gratuitement, et Home Assistant a un add-on officiel
pour ça.** C'est le montage le plus répandu de l'écosystème, précisément pour
cette raison.

Le problème se coupe en deux, et les deux morceaux se règlent séparément :

| Sous-problème | Réponse |
|---|---|
| **a. Obtenir un certificat valide** sans ouvrir de port | Add-on **Duck DNS**, défi DNS-01. Il pose et retire un `TXT` par l'API DuckDNS. Aucun port ouvert vers internet. |
| **b. Faire résoudre le nom vers Nova sur le réseau maison** | L'enregistrement `A` de ton sous-domaine pointe vers l'**IP privée** de Nova. Repli : une entrée dans le résolveur DNS de la maison. |

Le (a) marche quel que soit le contenu de l'enregistrement `A` : le défi DNS-01
ne regarde que le `TXT`. C'est ce qui rend ce montage possible sans rien exposer.

### Écart assumé avec le §4 du cahier des charges

Le cahier recommande **Caddy**. Je propose deux add-ons officiels à la place :
**Duck DNS** pour le certificat, **NGINX Home Assistant SSL proxy** pour le
servir sur le 443.

La raison : §4 recommandait Caddy parce qu'il sait *aussi* obtenir le
certificat. Ici c'est Duck DNS qui s'en charge — et un add-on Caddy capable de
faire du DNS-01 demande un module compilé pour ton hébergeur DNS, donc une image
personnalisée à maintenir. Les deux add-ons officiels font le même travail, sont
maintenus par le projet Home Assistant, et ne demandent aucune construction.

Le bénéfice de §4 est conservé intégralement : **le port 8123 reste en clair**
pour tout ce qui lui parle déjà, et les navigateurs passent par le 443 en HTTPS.
Ça règle au passage la question que je t'avais posée (« quelque chose parle-t-il
en HTTP clair au 8123 ? ») : avec un proxy, la réponse n'a plus d'importance.

---

## 4. La procédure

### 4.1 — Le sous-domaine

1. Aller sur **duckdns.org**, se connecter (GitHub, Google — gratuit, sans
   carte).
2. Créer un sous-domaine, par exemple `nova-alard`. Il devient
   `nova-alard.duckdns.org`.
3. Noter le **token** affiché en haut de la page.

Un seul sous-domaine suffit : le distant continue de passer par Nabu Casa, ce
nom-ci ne sert qu'à la maison.

### 4.2 — Le certificat

**Paramètres → Modules complémentaires → Boutique → Duck DNS → Installer.**

```yaml
domains:
  - nova-alard.duckdns.org
token: "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
lets_encrypt:
  accept_terms: true
  certfile: fullchain.pem
  keyfile: privkey.pem
seconds: 300
```

Démarrer, puis **lire le journal de l'add-on**. La première émission prend
quelques minutes — le temps que le `TXT` se propage. Elle est réussie quand
`/ssl/fullchain.pem` existe. Le renouvellement est ensuite automatique.

### 4.3 — Faire pointer le nom vers Nova (le point délicat)

Par défaut, l'add-on Duck DNS met à jour l'enregistrement `A` avec l'**IP
publique** de la maison. Or on veut l'IP **privée** de Nova. Trois manières, par
ordre de préférence :

**a. Épingler l'IP dans l'add-on.** Regarder si le schéma de configuration
expose une option `ipv4`. Si oui :

```yaml
ipv4: "192.168.1.42"
```

C'est le plus propre : l'add-on continue de rafraîchir, mais avec la bonne
valeur. *À vérifier sur place — je ne peux pas confirmer d'ici la présence de
cette option dans la version que tu installeras.*

**b. Poser l'enregistrement à la main, puis surveiller.**

```bash
curl "https://www.duckdns.org/update?domains=nova-alard&token=<TOKEN>&ip=192.168.1.42"
```

Attendre dix minutes, puis vérifier que l'add-on ne l'a pas réécrit :

```bash
nslookup nova-alard.duckdns.org
```

S'il répond l'IP privée, c'est réglé. S'il est repassé sur l'IP publique,
passer en (c).

**c. Une entrée dans le résolveur DNS de la maison.** Routeur, AdGuard Home,
Pi-hole : `nova-alard.duckdns.org → 192.168.1.42`. Plus robuste, une pièce de
plus à maintenir, et il faut que tous les appareils utilisent bien ce résolveur.

> ⚠️ **Le piège, quelle que soit la manière.** Beaucoup de routeurs et de
> résolveurs (Freebox, Livebox, AdGuard Home, Pi-hole, pfSense) appliquent une
> *protection contre le DNS rebinding* : ils effacent silencieusement les
> réponses pointant vers une IP privée. Le nom ne résout alors nulle part, sans
> message d'erreur utile. **À tester en premier**, avant même de configurer quoi
> que ce soit :
>
> ```bash
> nslookup nova-alard.duckdns.org
> ```
>
> Si l'IP privée ne revient pas alors que tu viens de la poser, il faut mettre
> `duckdns.org` en exception dans le résolveur. C'est la cause numéro un
> d'échec de cette approche, et elle ne se manifeste par aucun message clair.

### 4.4 — Servir le certificat sur le 443

**Boutique → NGINX Home Assistant SSL proxy → Installer.**

```yaml
domain: nova-alard.duckdns.org
certfile: fullchain.pem
keyfile: privkey.pem
hsts: max-age=31536000; includeSubDomains
```

Il écoute sur le 443 de Nova et relaie vers Home Assistant en clair sur le 8123.
Le 8123 reste donc joignable comme avant, pour tout ce qui lui parle déjà.

La montée en WebSocket est gérée nativement : c'est vital, la carte Luna ne
passe que par là.

> Vérifier que rien d'autre n'occupe le port 443 de Nova. Si l'add-on refuse de
> démarrer, c'est presque toujours ça.

### 4.5 — Configuration de Home Assistant

**Obligatoire dès qu'un proxy est devant HA :**

```yaml
# configuration.yaml
http:
  use_x_forwarded_for: true
  trusted_proxies:
    - 172.30.32.0/23        # réseau des add-ons HAOS — c'est de là que vient NGINX
    - 127.0.0.1
    - ::1

homeassistant:
  internal_url: "https://nova-alard.duckdns.org"
  external_url: "https://xxxxxxxx.ui.nabu.casa"
```

Sans `trusted_proxies`, Home Assistant voit toutes les requêtes venir de l'IP du
proxy : les journaux deviennent illisibles, la protection contre la force brute
bannit tout le monde d'un coup, et les automatisations basées sur l'IP source
mentent. **Ce n'est pas optionnel.**

Ne **pas** mettre `ssl_certificate` dans le bloc `http:` — le proxy s'en charge,
et le faire ici couperait le 8123 en clair, ce qu'on cherche justement à éviter.

Redémarrer Home Assistant.

### 4.6 — Application Companion

Sur **chaque** appareil — ton téléphone, celui de Clara, la tablette murale :

1. **Paramètres → Companion app → Serveurs → Nova**
2. **URL interne** : `https://nova-alard.duckdns.org`
3. **Se connecter en Wi-Fi (SSID)** : cocher le SSID de la maison. C'est ce qui
   dit à l'app d'utiliser l'URL interne à la maison, et Nabu Casa ailleurs.
4. Autoriser le micro pour l'application au niveau du système
   (iOS : Réglages → Home Assistant → Microphone ;
   Android : Paramètres → Applications → Home Assistant → Autorisations).

Le point 4 est distinct du HTTPS et souvent oublié : le contexte sécurisé rend
`getUserMedia()` *appelable*, la permission système le rend *fructueux*. Les
deux sont nécessaires.

---

## 5. Où tourne le proxy — et pourquoi ça compte

Les deux add-ons tournent **sur Nova**, et c'est structurant.

L'endroit naturel pour un reverse proxy, c'est Nebula : il y a déjà Docker, déjà
des conteneurs, déjà l'habitude. **Ce serait une erreur.** §2 pose la règle
d'indépendance : « Si Nebula et Orion sont éteintes, Luna fonctionne
normalement. » Éteindre le NAS couperait le HTTPS, donc le micro, donc la moitié
de Luna — la règle serait violée par le prérequis censé la servir.

Sur Nova, la chaîne complète — certificat, proxy, Home Assistant, add-on Luna —
tient sur une seule machine.

---

## 6. Plus tard : un vrai domaine

Une extension `.fr` coûte une dizaine d'euros par an. Ce que ça apporte, le jour
où tu voudras :

- un nom qui t'appartient, indépendant d'un service gratuit tiers ;
- des certificats **génériques** (`*.maison.tondomaine.fr`), donc plus rien à
  refaire pour chaque service futur ;
- pas de dépendance à la disponibilité de DuckDNS.

La procédure ne change quasiment pas : l'add-on **Let's Encrypt** remplace Duck
DNS (avec le fournisseur DNS de ton registrar), le reste — NGINX, la
configuration HA, l'app Companion — est identique. Rien de ce que tu montes
aujourd'hui n'est perdu.

---

## 7. Vérification — la sortie testable de P0

§11 : « Micro autorisé en local et en distant ». Voici comment le prouver, avant
d'écrire la moindre ligne de la phase voix.

Dans les outils de développement du navigateur, ou dans une carte Markdown
temporaire :

```js
console.log("contexte sécurisé :", window.isSecureContext);
console.log("mediaDevices :", !!navigator.mediaDevices);
navigator.mediaDevices.getUserMedia({ audio: true })
  .then(s => { console.log("✅ micro OK"); s.getTracks().forEach(t => t.stop()); })
  .catch(e => console.log("❌", e.name, e.message));
```

À passer sur **six combinaisons** :

| Appareil | Réseau | Attendu |
|---|---|---|
| Companion iOS | Wi-Fi maison | ✅ |
| Companion iOS | 4G / distant | ✅ (déjà acquis via Nabu Casa) |
| Companion Android | Wi-Fi maison | ✅ |
| Companion Android | 4G / distant | ✅ |
| Tablette murale | Wi-Fi maison | ✅ |
| Navigateur de bureau | Wi-Fi maison | ✅ |

Les six au vert : P0 est finie, P2 est débloquée. Une seule au rouge et la phase
voix bute dessus plus tard, dans un contexte où ce sera bien plus dur à
diagnostiquer.

La carte Luna fabrique déjà les messages qui vont avec : cliquer le micro hors
contexte sécurisé renvoie vers ce document, et un refus de permission explique
comment le rétablir (§8 : « Jamais d'échec silencieux »).

---

## 8. Le repli, si DuckDNS coince

Le certificat **auto-signé**, approuvé comme autorité de confiance sur la
tablette murale uniquement (solution 3 de §4).

- **Pour** : aucun domaine, aucun DNS, aucune dépendance externe.
- **Contre** : à refaire sur chaque appareil ; iOS demande une étape
  supplémentaire peu évidente (Réglages → Général → Informations → Réglages des
  certificats) ; à refaire à chaque expiration ; les téléphones qui vont et
  viennent restent sans micro.
- **Verdict** : acceptable si l'usage vocal se limite vraiment à la tablette.
  Sinon, une dette qui revient à chaque appareil.

---

## 9. Récapitulatif — quoi faire, dans quel ordre

| # | Action | Effet | Durée |
|---|---|---|---|
| 0 | Vider l'URL interne dans l'app Companion | **Micro débloqué tout de suite**, tout passe par internet | 2 min |
| 1 | Créer un sous-domaine DuckDNS | Un nom à toi, gratuit | 5 min |
| 2 | Add-on Duck DNS, `lets_encrypt` activé | Certificat valide, aucun port ouvert | 10 min |
| 3 | Faire pointer le `A` vers l'IP privée de Nova | Le nom résout à la maison | 5 min, ou plus si rebinding |
| 4 | Add-on NGINX SSL proxy | HTTPS sur le 443, 8123 intact | 5 min |
| 5 | `trusted_proxies` + `internal_url`, redémarrer HA | Journaux et bannissements corrects | 5 min |
| 6 | URL interne + SSID dans Companion, permission micro | Le local repasse en local | 5 min/appareil |
| 7 | Les six combinaisons de §7 | **P0 finie, P2 débloquée** | 10 min |

L'étape 0 est indépendante des autres : elle te débloque ce soir, et tu peux
faire le reste quand tu veux sans rien casser.
