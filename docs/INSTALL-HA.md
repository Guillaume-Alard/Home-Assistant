# Installer Luna dans Home Assistant (carte + voix + mot d'éveil)

Deux choses à mettre en place, indépendantes :

1. **L'intégration `sentinel_assist`** — l'agent de conversation Sentinel **et**
   la carte Lovelace « Luna ». Suffit pour **écrire** à Luna.
2. **Un pipeline Assist avec voix** (STT + TTS) et, pour le mains libres, un
   **moteur de mot d'éveil (openWakeWord)**. Nécessaire pour **parler** et pour
   le **mot d'éveil**.

> **Le micro exige HTTPS.** Le navigateur (et l'app companion) n'ouvrent le micro
> qu'en **contexte sécurisé** : Home Assistant doit être joint en `https://…`
> (Nabu Casa, ou un reverse proxy / DuckDNS + Let's Encrypt en local), ou en
> `localhost`. En `http://<ip>:8123`, **le texte marche, pas la voix**. C'est une
> règle du navigateur, pas de la carte.

---

## 1. L'intégration + la carte

### a) Copier le dossier dans `/config`

Sur **Home Assistant OS / Supervised**, le plus simple est l'add-on **Samba share**
(ou **Advanced SSH & Web Terminal**, ou **Studio Code Server**) :

1. Add-on **Samba share** → installer, démarrer. Un partage `\\<IP-de-HA>\config`
   apparaît sur le réseau.
2. Copie **tout le dossier** `custom_components/sentinel_assist/` du dépôt dans
   `\\<IP-de-HA>\config\custom_components\` (crée `custom_components` s'il
   n'existe pas). Important : copie le dossier **entier** — il contient `www/`
   (la carte), `websocket.py` (le streaming), etc.
3. **Redémarre** Home Assistant (Paramètres → Système → ⋮ → Redémarrer).

(En **HA Container / Core**, dépose le dossier dans le `config/custom_components/`
monté par ton conteneur, puis redémarre.)

### b) Ajouter l'intégration

1. **Paramètres → Appareils et services → Ajouter une intégration →** cherche
   **« Sentinel »**.
2. Renseigne :
   - **URL de Sentinel** : `https://192.168.0.212:8443` (sans `/v1`)
   - **Jeton** : la valeur de `SENTINEL_ASSIST_TOKEN` (côté Sentinel, `.env`)
   Le certificat auto-signé est accepté automatiquement.

### c) Ajouter la carte

1. **Recharge la page** de HA une fois (Ctrl/Cmd + Maj + R) — l'intégration
   déclare la carte comme module frontend au démarrage ; le rechargement la charge.
2. Sur un tableau de bord : **Modifier → Ajouter une carte →** cherche **Luna**.
   Elle apparaît dans le sélecteur, avec un aperçu. (Rien à enregistrer dans les
   *ressources* Lovelace : l'intégration s'en charge.)
3. Pour un effet « app » : un tableau de bord ne contenant que cette carte,
   épinglé au menu latéral.

À ce stade, **écrire** à Luna fonctionne partout. Pour la voix, passe au §2.

---

## 2. La voix et le mot d'éveil (pipeline Assist)

La carte utilise le **pipeline Assist de HA**. Il lui faut donc un moteur de
**transcription (STT)**, de **synthèse (TTS)**, et — pour le mains libres — de
**mot d'éveil**.

### a) Installer les moteurs (add-ons, sur HAOS/Supervised)

**Paramètres → Modules complémentaires → Boutique**, installe puis **démarre** :

- **Whisper** (transcription) — ou faster-whisper.
- **Piper** (synthèse, voix française dispo).
- **openWakeWord** (mot d'éveil).

HA découvre ces services **Wyoming** tout seul : Paramètres → Appareils et
services → « Découverts » → **Configurer** chacun.

> **HA Container (pas d'add-ons)** : lance ces services en conteneurs
> (`rhasspy/wyoming-whisper`, `rhasspy/wyoming-piper`,
> `rhasspy/wyoming-openwakeword`) et ajoute l'intégration **Wyoming Protocol**
> pointant sur chacun. (Tu as déjà whisper/piper/openwakeword côté **Sentinel**,
> mais ils ne sont pas publiés sur le LAN — le plus simple reste des services
> dédiés à HA.)

### b) Construire l'assistant

**Paramètres → Assistants vocaux → Ajouter un assistant** (ou modifier celui par
défaut) :

- **Agent conversationnel** : **Sentinel** (fourni par l'intégration).
- **Langue** : Français.
- **Speech-to-text** : Whisper (modèle FR).
- **Text-to-speech** : Piper (voix FR).
- **Mot d'éveil** : **openWakeWord** → choisis un mot.

Puis **mets cet assistant en PRÉFÉRÉ** (l'étoile). La carte utilise le pipeline
**préféré** sans réglage ; sinon, précise son id dans la carte (`pipeline:`).

### c) Utiliser

Recharge la page, ouvre la carte : le **🎙 parle** (appui = écoute, second appui =
« j'ai fini »), le **👂 arme la veille** (mot d'éveil, mains libres, ré-armement
auto). À la première utilisation, le navigateur/companion demande l'autorisation
du **micro** : accepte.

---

## Le mot d'éveil : « hey jarvis » par défaut, « Luna » sur mesure

openWakeWord est livré avec quelques mots **pré-entraînés** — dont **« hey
jarvis »** (parfait pour l'esprit Jarvis), « ok nabu », « alexa »… **Il n'y a pas
de « Luna » prêt à l'emploi.**

- **Tout de suite** : choisis **« hey jarvis »** dans le pipeline.
- **« Luna » sur mesure** : entraîne un modèle openWakeWord (l'outil / le Colab
  officiel openWakeWord produit un `.tflite`), dépose-le dans le dossier des
  modèles personnalisés de l'add-on, puis sélectionne-le dans le pipeline. C'est
  la même logique que le `WAKEWORD_MODEL` de Sentinel (voir
  `config/wakewords/README.md`).

> **Deux veilles distinctes.** Le 👂 de la **carte** utilise openWakeWord **de
> HA** (ce doc). Le 👂 du **cockpit** autonome utilise l'openWakeWord **de
> Sentinel** (sur Nebula). Les deux sont séparés ; règle celui de HA pour la carte.

> **Choisir le mot depuis le cockpit.** Pour la veille **du cockpit**, le mot se
> règle désormais **dans l'app** : Paramètres › **Voix & réveil** › *Mot de
> réveil*. La liste montre les modèles **réellement chargés** par ton serveur
> openWakeWord (marqués « chargé ») plus les mots pré-entraînés courants ; un champ
> **« perso »** accepte le nom d'un `.tflite` maison (p.ex. « luna ») une fois
> déposé sur le serveur. Le changement est **immédiat** et mémorisé. (Ceci ne
> touche pas la carte HA : son mot reste celui de son pipeline Assist.) Rappel : si
> tu **dis « Luna » alors que le mot réglé est « hey jarvis »**, rien ne se
> déclenche — c'est la cause la plus fréquente d'une veille « qui n'entend pas ».

---

## Ça ne marche pas ?

- **Le 🎙 est grisé** → HTTPS manquant (le plus fréquent), micro refusé, ou
  navigateur sans micro. Le clavier, lui, marche toujours.
- **Le 👂 dit une erreur et se désarme** → le pipeline **préféré** n'a pas de
  **mot d'éveil** configuré (§2b), ou openWakeWord n'est pas démarré.
- **La carte n'apparaît pas** dans le sélecteur → recharge la page (Ctrl/Cmd +
  Maj + R) ; vérifie que `www/luna-card.js` est bien dans le dossier copié.
- **« Sentinel injoignable »** → l'URL / le jeton de l'intégration, ou Sentinel
  (Nebula) hors ligne.
