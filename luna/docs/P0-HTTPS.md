# Luna — P0 : HTTPS en local

**Statut : procédure arrêtée et relue, reste à exécuter sur Nova.**
Contexte connu au 8 septembre 2026 : Nova tourne bien Home Assistant OS (H1
close), et Guillaume ne possède **aucun nom de domaine** — seulement l'adresse
Nabu Casa.

*Relue après P3 à P6 : l'option `ipv4` de l'add-on Duck DNS est confirmée
(§4.4), `trusted_proxies` s'est révélé porter la barrière biométrique de §6
(§4.6), et le même contexte sécurisé conditionne la caméra de P6 autant que le
micro de P2.*

§4 du cahier des charges : « À régler **avant** la phase voix. » Ce document dit
comment, sans acheter de domaine.

---

## 1. Le problème, précisément

`getUserMedia()` — l'appel qui ouvre le micro **et la caméra** — est refusé par
tous les navigateurs hors **contexte sécurisé** : `https://`, ou
`http://localhost`. Pas `http://192.168.0.251:8123`.

C'est donc P2 **et** P6 qui butent sur le même appel : la voix aujourd'hui, le
visage le jour où tu le décideras. Une seule pièce à monter pour les deux.

```
Companion iOS/Android, en Wi-Fi maison
   └─► URL interne  http://192.168.0.251:8123
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
2. Créer un sous-domaine, par exemple `guillaume-sentinel`. Il devient
   `guillaume-sentinel.duckdns.org`.
3. Noter le **token** affiché en haut de la page.

Un seul sous-domaine suffit : le distant continue de passer par Nabu Casa, ce
nom-ci ne sert qu'à la maison.

### 4.2 — Le nom résout-il vers Nova ? (à faire *avant* le certificat)

C'est la question qui décide si tout ce qui suit est possible, elle se répond en
cinq minutes, et elle ne demande **ni add-on, ni certificat**. La poser en
premier évite de découvrir le mur après vingt minutes de montage.

```bash
curl "https://www.duckdns.org/update?domains=guillaume-sentinel&token=<TOKEN>&ip=192.168.0.251"
# → OK
nslookup guillaume-sentinel.duckdns.org
```

**L'IP privée revient** → feu vert, passe au 4.3.

> Regarde **quel serveur** `nslookup` a interrogé, ligne « Serveur ». S'il
> répond `one.one.one.one` ou `dns.google`, cette machine parle à un résolveur
> public qui, lui, ne filtre jamais — le test ne dit alors rien des appareils en
> DHCP normal, qui passent par la box. Le contre-test ne demande aucun terminal :
> ouvrir `http://guillaume-sentinel.duckdns.org:8123` depuis le téléphone en
> Wi-Fi maison. La page de connexion Home Assistant s'affiche, le nom résout
> depuis cet appareil aussi.
>
> **Le port fait partie du test.** Sans `:8123`, le navigateur tente le port 80,
> où rien n'écoute : il rend `ERR_CONNECTION_REFUSED`, « Ce site est
> inaccessible ». Ça ressemble à un échec et c'en est l'inverse — pour refuser
> une connexion, il a fallu résoudre le nom, joindre Nova, et se faire fermer la
> porte au nez. Le vrai échec de résolution porte un autre nom :
> `ERR_NAME_NOT_RESOLVED`. Lire lequel des deux s'affiche répond à la question
> plus sûrement que la page elle-même.

**Rien ne revient, ou une autre IP revient** → *DNS rebinding protection* : ton
routeur ou ton résolveur efface silencieusement les réponses pointant vers une
adresse privée. Beaucoup le font (Freebox, Livebox, AdGuard Home, Pi-hole,
pfSense). L'issue est un **enregistrement DNS local sur la passerelle**, décrit
en §4.4 : la passerelle répond elle-même, il n'y a donc plus de réponse externe
à filtrer. C'est la cause numéro un d'échec de ce montage, et elle ne se
manifeste par aucun message clair.

Rien ne presse pour autant : le certificat de §4.3 et le proxy de §4.5 ne
dépendent pas de la résolution locale — seul leur *usage* en dépend. Les deux
chantiers avancent en parallèle.

### 4.3 — Le certificat

**Paramètres → Modules complémentaires → Boutique → Duck DNS → Installer.**

Passer en **⋮ → Éditer en YAML**, et écrire le bloc **en entier** :

```yaml
domains:
  - guillaume-sentinel.duckdns.org
token: "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
aliases: []
lets_encrypt:
  accept_terms: true
  algo: secp384r1
  certfile: fullchain.pem
  keyfile: privkey.pem
seconds: 300
ipv4: "192.168.0.251"
```

⚠️ **`algo` est obligatoire.** L'éditeur YAML **remplace** les options, il ne
les fusionne pas : omettre une clé du bloc `lets_encrypt` la supprime, et le
Superviseur refuse l'enregistrement avec `Missing option 'algo' in lets_encrypt`.
Le schéma de l'add-on est `list(rsa|prime256v1|secp384r1)`, sa valeur par défaut
`secp384r1` — une courbe elliptique, plus légère à négocier qu'une clé RSA sur
un N95.

⚠️ **Ne pas remplir `aliases`.** Le nom trompe : ce n'est pas un libellé, c'est
une **délégation de domaine**, faite pour valider un certificat en posant le
`TXT` du défi sous *un autre domaine DuckDNS qu'on possède*. Le hook en tire le
domaine qu'il interroge :

```bash
ALIAS="$(jq -r "[.aliases[]|{(.domain):.alias}]|add.\"$DOMAIN\"" $CONFIG_PATH)" || ALIAS="$DOMAIN"
```

Y écrire un joli nom — `alias: Luna` — envoie le défi sur un domaine qui
n'existe pas : DuckDNS répond `KO`, le `dig` cherche `_acme-challenge.Luna`
pendant cent vingt secondes, et dehydrated abandonne.

La clé, elle, est **obligatoire** — l'omettre rend `Missing option 'aliases' in
root`, exactement comme `algo`. Ce qui doit être vide, c'est la **liste** :
`aliases: []`. Le `jq` ne trouve alors aucune correspondance, sort en erreur, et
le `||` fait retomber `ALIAS` sur le domaine lui-même. Trois clés, trois raisons
différentes de ne pas se laisser guider par l'interface : `algo` obligatoire et
facile à effacer, `ipv4` facultative donc jamais proposée, `aliases` obligatoire
mais qui doit rester vide.

C'est vrai de `ipv4` aussi, dans l'autre sens : `str?` le rend facultatif au
schéma, donc absent des options par défaut, donc invisible dans l'interface
graphique. Le mode YAML est le seul endroit d'où on peut le poser. Sans lui,
l'add-on réécrit l'enregistrement `A` avec l'IP **publique** de la maison au
premier rafraîchissement, et le §4.2 est défait en cinq minutes.

Démarrer, puis **lire le journal de l'add-on**. La première émission prend
quelques minutes — le temps que le `TXT` se propage. Elle est réussie quand
`/ssl/fullchain.pem` existe. Le renouvellement est ensuite automatique.

> Cette étape ne dépend **pas** du §4.2 : le défi DNS-01 passe par l'API
> DuckDNS, pas par la résolution locale. Un nom qui ne résout pas encore depuis
> le téléphone n'empêche ni le certificat ni le proxy — seulement leur usage.

### 4.4 — Vérifier que l'IP épinglée tient

`ipv4` a été posé en §4.3. Attendre dix minutes — un cycle de rafraîchissement —
puis :

```bash
nslookup guillaume-sentinel.duckdns.org
```

Si l'IP publique est revenue malgré `ipv4`, ou si le §4.2 avait viré au rouge,
le repli est un **enregistrement DNS local sur la passerelle**.

Sur une **UniFi** (UCG, UDM, UDR), c'est natif depuis UniFi Network 8.2 :
*Local DNS Records*, un enregistrement **Host (A)** —
`guillaume-sentinel.duckdns.org → 192.168.0.251`. Chercher « DNS » dans la
recherche des réglages ; selon la version, la page vit sous *Routing* ou sous
les réglages globaux de réseau.

C'est plus qu'un repli, c'est plus robuste que l'enregistrement public :

- la passerelle répond elle-même, donc **aucune protection contre le rebinding
  ne s'applique** — il n'y a pas de réponse externe à filtrer ;
- tous les appareils en DHCP reçoivent la bonne réponse sans configuration ;
- le nom continue de résoudre à la maison **même si DuckDNS tombe**.

Contrepartie : les appareils qui court-circuitent le résolveur de la passerelle
— un PC réglé sur 1.1.1.1, un téléphone avec un DNS chiffré — ne le voient pas.
Pour eux, l'enregistrement public reste la réponse, d'où l'intérêt de garder les
deux justes.

> ⚠️ **Cet enregistrement peut tuer le renouvellement du certificat.** Le hook du
> §4.3 pose le `TXT` du défi chez DuckDNS, puis attend de le relire lui-même :
>
> ```bash
> curl -s "https://www.duckdns.org/update?domains=$ALIAS&token=$SYS_TOKEN&txt=$TOKEN_VALUE"
> timeout 120s bash -c -- "while ! dig -t txt \"_acme-challenge.$ALIAS\" | grep -F -- \"$TOKEN_VALUE\"; do sleep 5; done"
> ```
>
> Ce `dig` n'a **pas de `@serveur`** : il part vers le résolveur de HAOS, donc
> vers la passerelle de la maison. Si la passerelle se déclare autoritaire pour
> le domaine **et ses sous-domaines**, elle répond elle-même pour
> `_acme-challenge.…` — sans `TXT`, puisqu'elle n'en connaît aucun. La requête ne
> sort jamais, la boucle tourne 120 s, et dehydrated abandonne :
> `ERROR: deploy_challenge hook returned with non-zero exit code`.
>
> Ce n'est pas seulement la première émission : le renouvellement automatique
> tombe dans le même trou tous les soixante jours, et rien n'avertit.
>
> Le test qui tranche, avec un sous-domaine qui n'existe pas :
>
> ```bash
> nslookup zzz.guillaume-sentinel.duckdns.org 1.1.1.1      # doit être NXDOMAIN
> nslookup zzz.guillaume-sentinel.duckdns.org <IP passerelle>
> ```
>
> Si la passerelle répond `192.168.0.251` là où Cloudflare dit « domaine
> inexistant », elle couvre tout le domaine, et il faut sortir le certificat de
> son chemin : donner à Nova un résolveur public (**Paramètres → Système →
> Réseau**, serveurs DNS `1.1.1.1`).
>
> **Mesuré sur la UCG Max de la maison : ce n'est pas le cas.** Un
> enregistrement *Local DNS Record* UniFi ne vaut que pour le nom exact ; la
> passerelle transmet `_acme-challenge.…` comme n'importe quelle autre requête et
> rend le `TXT` à l'identique de Cloudflare. L'avertissement reste écrit parce
> qu'il dépend de l'implémentation de chaque passerelle — dnsmasq offre les deux
> formes, `host-record` pour le nom seul et `address=/domaine/` pour le domaine
> entier — et que le test coûte deux commandes.

### 4.5 — Servir le certificat sur le 443

**Boutique → NGINX Home Assistant SSL proxy → Installer.**

```yaml
domain: guillaume-sentinel.duckdns.org
certfile: fullchain.pem
keyfile: privkey.pem
hsts: max-age=31536000; includeSubDomains
```

Il écoute sur le 443 de Nova et relaie vers Home Assistant en clair sur le 8123.
Le 8123 reste donc joignable comme avant, pour tout ce qui lui parle déjà.

La montée en WebSocket est gérée nativement : c'est vital, la carte Luna ne
passe que par là.

> **Ne pas démarrer avant que `/ssl/fullchain.pem` existe.** L'add-on ne sait pas
> attendre : il lit le certificat au démarrage, ne le trouve pas, et s'arrête —
> `stat: can't stat '/ssl/fullchain.pem': No such file or directory`, puis
> `Service nginx exited with code 1`. Ce n'est pas un défaut de configuration,
> c'est le §4.3 qui n'est pas fini. Rien à corriger ici : finir le certificat,
> puis démarrer.
>
> L'autre cause d'un refus de démarrer, une fois le certificat en place : quelque
> chose occupe déjà le port 443 de Nova.

### 4.6 — Configuration de Home Assistant

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
  internal_url: "https://guillaume-sentinel.duckdns.org"
  external_url: "https://xxxxxxxx.ui.nabu.casa"
```

Sans `trusted_proxies`, Home Assistant voit toutes les requêtes venir de l'IP du
proxy : les journaux deviennent illisibles, la protection contre la force brute
bannit tout le monde d'un coup, et les automatisations basées sur l'IP source
mentent. **Ce n'est pas optionnel.**

### Et depuis P3, ça porte plus que les journaux

Ce document a été écrit avant la phase identité. Depuis, l'intégration Luna
contient la barrière de §6 — « la biométrie n'est active que sur le réseau
local » — et elle décide en lisant l'IP du client :

```python
requete = http.current_request.get()
return util_reseau.is_local(ip_address(requete.remote))
```

Avec un proxy devant et **sans** `trusted_proxies`, `requete.remote` vaut l'IP
de NGINX — `172.30.32.x`, une adresse privée. Toute requête paraîtrait donc
locale, et la barrière ne distinguerait plus rien.

Aujourd'hui le risque reste théorique : l'accès distant passe par Nabu Casa, et
`is_cloud_connection()` l'écarte avant même de regarder l'IP. Mais le jour où
tu ouvrirais le 443 depuis internet — ce montage ne le demande pas, beaucoup de
gens le font ensuite —, `trusted_proxies` serait la **seule** chose empêchant
la reconnaissance vocale de fonctionner depuis n'importe où. Autant le poser
correctement maintenant.

Ne **pas** mettre `ssl_certificate` dans le bloc `http:` — le proxy s'en charge,
et le faire ici couperait le 8123 en clair, ce qu'on cherche justement à éviter.

Redémarrer Home Assistant.

### 4.7 — Application Companion

Sur **chaque** appareil — ton téléphone, celui de Clara, la tablette murale :

1. **Paramètres → Companion app → Serveurs → Nova**
2. **URL interne** : `https://guillaume-sentinel.duckdns.org`
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

### Et une septième vérification, pour `trusted_proxies`

Elle ne se voit pas depuis le navigateur. **Paramètres → Système → Journaux**,
puis regarde une ligne de connexion : elle doit citer l'IP réelle de l'appareil
(`192.168.0.x`), **pas** celle du proxy (`172.30.32.x`).

Si tu vois `172.30.32.x`, `trusted_proxies` n'est pas pris en compte — et la
barrière de §6 ne distingue plus rien (voir §4.6).

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
| 2 | Poser le `A` vers l'IP privée **et le résoudre** (§4.2) | **Feu vert ou feu rouge** : dit tout de suite si le montage est possible | 5 min |
| 3 | Add-on Duck DNS en YAML : `algo`, `accept_terms`, `ipv4` | Certificat valide, aucun port ouvert | 10 min |
| 4 | Add-on NGINX SSL proxy | HTTPS sur le 443, 8123 intact | 5 min |
| 5 | `trusted_proxies` + `internal_url`, redémarrer HA | Journaux, bannissements — **et la barrière biométrique de §6** | 5 min |
| 6 | URL interne + SSID dans Companion, permission micro | Le local repasse en local | 5 min/appareil |
| 7 | Les six combinaisons de §7, plus la vérification des journaux | **P0 finie, P2 débloquée — et P6 possible le jour venu** | 10 min |

L'étape 0 est indépendante des autres : elle te débloque ce soir, et tu peux
faire le reste quand tu veux sans rien casser.
