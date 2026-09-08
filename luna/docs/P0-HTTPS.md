# Luna — P0 : HTTPS en local

**Statut : en attente de validation.** Deux questions ouvertes à la fin, sans
lesquelles je ne peux pas figer la procédure.

§4 du cahier des charges : « À régler **avant** la phase voix. » Ce document
dit comment, ce que ça coûte, et où le cahier des charges se contredit.

---

## 1. Le problème, précisément

`getUserMedia()` — l'appel qui ouvre le micro et la caméra — est refusé par tous
les navigateurs hors **contexte sécurisé**. Un contexte sécurisé, c'est `https://`
ou `http://localhost`. Pas `http://192.168.1.42:8123`.

En distant, Nabu Casa fournit du HTTPS : rien à faire. Le trou est en local :

```
Companion iOS/Android, en Wi-Fi maison
   └─► URL interne  http://192.168.1.42:8123
         └─► window.isSecureContext === false
               └─► navigator.mediaDevices === undefined
                     └─► le bouton micro de la carte Luna ne peut rien faire
```

Et c'est précisément là que Luna sera le plus utilisée : la tablette murale, le
téléphone dans le salon.

Ce n'est pas contournable côté code. Il n'y a pas de repli, pas de polyfill, pas
de drapeau. Il faut du vrai HTTPS sur le réseau local.

---

## 2. Où tourne le proxy — et pourquoi ça compte

§4 recommande « Caddy en reverse proxy **sur le réseau local** ». §2 pose la
règle d'indépendance : « Si Nebula et Orion sont éteintes, Luna fonctionne
normalement. »

L'endroit naturel pour Caddy, c'est Nebula : il y a déjà Docker, déjà des
conteneurs, déjà l'habitude. **Ce serait une erreur.** Éteindre le NAS couperait
le HTTPS, donc le micro, donc la moitié de Luna — la règle d'indépendance serait
violée par le prérequis censé la servir.

**Donc : le HTTPS local est produit sur Nova, et nulle part ailleurs.** Nova est
en HAOS, on n'y installe pas de logiciel arbitraire — mais on y installe des
add-ons. Deux routes, ci-dessous.

---

## 3. Deux routes, un critère de décision

### Le critère

> **Est-ce qu'un appareil ou un service du réseau local parle à Home Assistant
> en `http://` simple sur le port 8123 ?**

Candidats typiques : un ESPHome mal configuré, un script sur le NAS, une caméra
qui pousse un webhook, un Node-RED, un tableau de bord tiers, un
`curl http://nova:8123/api/…` dans un cron.

- **Oui, ou tu n'es pas sûr** → route A (Caddy). Le port 8123 reste en clair
  pour eux, le 443 sert le HTTPS aux navigateurs.
- **Non, avec certitude** → route B, deux fois plus simple.

### Route A — Caddy en add-on (recommandée par §4)

```
navigateur ──https://nova.…:443──► add-on Caddy sur Nova ──http──► homeassistant:8123
appareils legacy ─────────────────http://192.168.1.42:8123────────────────┘
```

| | |
|---|---|
| **Pour** | Le 8123 en clair survit. URL propre sans port. Un seul endroit pour ajouter d'autres services plus tard. Ce que §4 recommande. |
| **Contre** | Un add-on de plus. Le certificat DNS-01 demande un module Caddy spécifique — **tous les add-ons Caddy ne l'embarquent pas**, à vérifier au moment de l'installation. |

### Route B — add-on Let's Encrypt + TLS natif de HA

L'add-on officiel *Let's Encrypt* obtient le certificat par défi DNS et l'écrit
dans `/ssl/`. Home Assistant le sert lui-même sur le 8123. Pas de proxy du tout.

```yaml
# configuration.yaml
http:
  ssl_certificate: /ssl/fullchain.pem
  ssl_key: /ssl/privkey.pem
```

| | |
|---|---|
| **Pour** | Un add-on officiel, maintenu. Aucun proxy, aucun en-tête à régler, aucune classe de bugs liée au proxy. Renouvellement automatique. |
| **Contre** | **Le 8123 devient HTTPS uniquement.** Tout ce qui y parlait en clair casse le jour du basculement. L'URL garde son port : `https://nova.…:8123`. |

**Ma recommandation :** route A, parce que §4 la recommande *et* parce que la
réponse honnête au critère est presque toujours « je ne suis pas sûr ». Mais si
tu sais que rien ne parle en clair au 8123, la route B est deux fois moins de
pièces mobiles pour le même résultat — et moins de pièces, c'est ce qui a
manqué à Sentinelle.

---

## 4. Le certificat

Un certificat valide exige un nom de domaine que tu contrôles. Trois façons de
le valider ; une seule tient ici.

| Défi | Verdict |
|---|---|
| **HTTP-01** | Exige d'ouvrir le port 80 depuis internet vers Nova. Non. |
| **TLS-ALPN-01** | Même problème, port 443. Non. |
| **DNS-01** | Un jeton d'API chez ton hébergeur DNS, un enregistrement `TXT` posé et retiré automatiquement. **Aucun port ouvert.** C'est celui-là. |

DNS-01 valide aussi les certificats **génériques** (`*.maison.tondomaine.fr`),
ce qui évite de refaire l'opération pour chaque service futur.

### Faire résoudre le nom en local

Le certificat couvre `nova.maison.tondomaine.fr` ; encore faut-il que ce nom
donne l'IP privée de Nova sur le réseau maison.

**Le plus simple** : poser l'enregistrement `A` en DNS **public**, pointant vers
l'IP privée.

```
nova.maison.tondomaine.fr.  A  192.168.1.42
```

Rien ne fuit d'exploitable — une IP privée ne mène nulle part depuis internet —
et il n'y a aucun DNS local à maintenir.

> ⚠️ **Le piège.** Beaucoup de routeurs et de résolveurs (Freebox, AdGuard Home,
> Pi-hole, pfSense) appliquent une *protection contre le DNS rebinding* : ils
> effacent silencieusement les réponses pointant vers une IP privée. Le nom ne
> résout alors nulle part, sans message d'erreur utile. Il faut mettre le
> domaine en exception dans le résolveur. **À vérifier en premier**, avant même
> de demander le certificat : c'est la cause numéro un d'échec de cette
> approche.

**L'alternative** : DNS à double horizon — une entrée locale dans le résolveur
de la maison. Plus robuste, une pièce de plus à maintenir.

---

## 5. Caddyfile

```caddyfile
{
    email guillaume@exemple.fr
    # Aucun port ouvert vers l'extérieur : tout passe par le DNS.
}

nova.maison.tondomaine.fr {
    tls {
        # Le nom du fournisseur dépend de ton hébergeur DNS (question Q1).
        dns cloudflare {env.CF_API_TOKEN}
    }

    encode zstd gzip

    reverse_proxy homeassistant:8123 {
        header_up Host              {host}
        header_up X-Real-IP         {remote_host}
        header_up X-Forwarded-For   {remote_host}
        header_up X-Forwarded-Proto {scheme}
    }
}
```

Notes :

- **Caddy 2 gère la montée en WebSocket toute seule.** Aucune directive
  `Upgrade`/`Connection` à ajouter : c'est une habitude nginx qui n'a pas cours
  ici. Et c'est vital — l'API WebSocket de HA est ce que la carte Luna utilise
  pour absolument tout.
- `homeassistant:8123` est le nom d'hôte du cœur de HA sur le réseau des
  add-ons. Si l'add-on Caddy retenu ne le résout pas, replier sur
  `172.30.32.1:8123`.
- Le jeton DNS se met dans les options de l'add-on, jamais dans le dépôt.
- Le certificat se renouvelle seul, sans intervention.

---

## 6. Configuration de Home Assistant

**Obligatoire dès qu'un proxy est devant HA** (route A uniquement) :

```yaml
# configuration.yaml
http:
  use_x_forwarded_for: true
  trusted_proxies:
    - 172.30.32.0/23        # réseau des add-ons HAOS — c'est de là que vient Caddy
    - 127.0.0.1
    - ::1

homeassistant:
  internal_url: "https://nova.maison.tondomaine.fr"
  external_url: "https://xxxxxxxx.ui.nabu.casa"
```

Sans `trusted_proxies`, HA voit toutes les requêtes venir de l'IP du proxy :
les journaux deviennent illisibles, la protection contre les attaques par force
brute bannit tout le monde d'un coup, et les automatisations basées sur l'IP
source mentent. **Ce n'est pas optionnel.**

Redémarrer HA après cette modification.

---

## 7. Application Companion

Sur **chaque** appareil — le téléphone de Guillaume, celui de Clara, la tablette
murale :

1. **Paramètres → Companion app → Serveurs → Nova**
2. **URL interne** : `https://nova.maison.tondomaine.fr`
   *(route B : ajouter `:8123`)*
3. **Se connecter en Wi-Fi (SSID)** : cocher le SSID de la maison. C'est ce qui
   dit à l'app d'utiliser l'URL interne à la maison et l'URL Nabu Casa ailleurs.
4. **Vider le champ « URL externe »** seulement si tu veux forcer le passage par
   Nabu Casa — voir le repli 2 ci-dessous.
5. Autoriser le micro pour l'application au niveau du système (iOS : Réglages →
   Home Assistant → Microphone ; Android : Paramètres → Applications → Home
   Assistant → Autorisations).

Le point 5 est distinct du HTTPS et souvent oublié : le contexte sécurisé rend
`getUserMedia()` *appelable*, la permission système le rend *fructueux*. Les
deux sont nécessaires.

---

## 8. Vérification — la sortie testable de P0

§11 : « Micro autorisé en local et en distant ». Voici comment le prouver, avant
d'écrire la moindre ligne de la carte.

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

Les six au vert : P0 est fini, P2 est débloquée. Une seule au rouge et la phase
voix bute dessus plus tard, dans un contexte où ce sera bien plus dur à
diagnostiquer.

---

## 9. Les deux replis de §4

### Repli 2 — forcer l'URL Nabu Casa

Vider le champ « URL interne » dans l'app Companion : tout passe par Nabu Casa,
y compris à la maison.

- **Pour** : immédiat, zéro configuration, zéro certificat.
- **Contre** : chaque commande de lumière fait un aller-retour par internet.
  Latence en hausse, et **Luna cesse de fonctionner quand la fibre tombe** —
  ce qui contredit l'esprit de §2 et de §7 (« Pas de dépendance cloud pour la
  reconnaissance »).
- **Verdict** : bon pour tester le pipeline vocal cette semaine. Mauvais comme
  état final.

### Repli 3 — certificat auto-signé sur la tablette

Générer un certificat, l'approuver comme autorité de confiance sur la tablette
murale uniquement.

- **Pour** : aucun domaine, aucun DNS, aucune dépendance externe.
- **Contre** : à refaire sur chaque appareil ; iOS demande une étape
  supplémentaire (Réglages → Général → Informations → Réglages des certificats)
  peu évidente ; à refaire à chaque expiration ; les téléphones qui vont et
  viennent restent sans micro.
- **Verdict** : acceptable si l'usage vocal se limite vraiment à la tablette.
  Sinon, dette qui revient à chaque appareil.

---

## 10. Ce qu'il me faut pour figer la procédure

> **Q1 — Le nom de domaine.** Quel domaine utilises-tu, et chez quel hébergeur
> DNS est-il géré ? (Cloudflare, OVH, Gandi, Infomaniak, autre.) Le fournisseur
> détermine le module DNS du certificat, donc le choix de l'add-on Caddy — ou
> l'option `dns` de l'add-on Let's Encrypt.
> Si tu n'as pas de domaine : il en faut un. Une extension `.fr` coûte une
> dizaine d'euros par an, et un domaine chez un hébergeur avec API DNS est le
> seul chemin propre vers un certificat valide en local.

> **Q2 — Le critère de §3.** À ta connaissance, quelque chose parle-t-il à
> `http://<ip-de-nova>:8123` en clair sur le réseau ? Si tu ne sais pas, la
> réponse est « oui » et on part sur Caddy.

Deux réponses, et j'écris la procédure exacte — add-on nommé, options remplies,
ordre des opérations — plutôt que le canevas ci-dessus.
