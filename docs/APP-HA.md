# Luna dans Home Assistant — la carte « visage »

En plus du cockpit autonome (l'app web de Sentinel), Luna a désormais un
**visage natif dans Home Assistant** : une **carte Lovelace** que tu poses sur
un tableau de bord. Elle affiche **l'orbe** de Luna (repris de la branche
« visage ») et un **fil de conversation** — tu lui parles depuis n'importe quel
tableau de bord HA, sur le téléphone comme sur une tablette murale.

## Ce que c'est (et ce que ce n'est pas)

- Une **carte** (`custom:luna-card`), pas une page à part : tu l'ajoutes où tu
  veux, seule sur un tableau épinglé au menu latéral pour un effet « app », ou à
  côté de tes autres cartes.
- **L'orbe** réagit à la conversation : il respire au repos, l'anneau tourne
  quand Luna réfléchit, le noyau pulse quand elle répond, il vire à l'ambre en
  alerte. SVG animé en CSS, **aucun asset externe**.
- Ce **n'est pas** un second cerveau : la carte ne fait que **parler à Luna**.

## Comment elle parle à Luna — et pourquoi c'est sûr

La carte **ne parle qu'à Home Assistant**, par sa WebSocket interne
(`hass.connection`). Elle n'ouvre **jamais** de connexion vers un hôte externe,
et **aucun jeton** ne se trouve dans la page.

Par défaut, la réponse arrive **mot à mot** : la carte s'abonne à une commande
WebSocket de l'intégration (`sentinel_assist/converse`), qui relaie le **flux**
de Sentinel (SSE) et repousse chaque fragment à la carte. Si cette commande
n'est pas disponible (intégration plus ancienne, ou `stream: false`), la carte
**retombe** sur le pipeline de conversation d'HA (`conversation/process`),
réponse en un bloc. Dans les deux cas, la phrase est routée vers l'**agent
Sentinel** (l'intégration `sentinel_assist`). Donc :

- **Même cerveau** que partout ailleurs — y compris le **choix du modèle**
  (multi-LLM) : ce que tu règles dans le cockpit vaut aussi ici.
- **Même sécurité, jamais contournée.** Une action reste une **proposition à
  valider** ; le déverrouillage et le désarmement restent réservés à
  l'interface. La carte n'a **aucun pouvoir d'action propre** : elle envoie du
  texte, elle affiche une réponse.

```
 carte luna  ──(WebSocket HA, local)──▶  agent sentinel_assist
                                              │  /v1/chat/completions
                                              ▼
                                    cerveau de Sentinel (Nebula)
                                    → outils → moteur « propose puis approuve »
```

## Installation

1. **L'intégration** `sentinel_assist` doit être installée (Paramètres →
   Appareils et services → Ajouter → « Sentinel ») : renseigne l'URL de Sentinel
   et le jeton `SENTINEL_ASSIST_TOKEN`. C'est elle qui fournit l'agent de
   conversation **et** qui sert la carte.
2. **Recharge la page** de HA une fois (la carte s'enregistre au démarrage de
   l'intégration ; un rechargement du navigateur charge le nouveau module).
3. **Ajoute la carte** à un tableau de bord : « Ajouter une carte » → cherche
   **Luna**. Elle apparaît dans le sélecteur, avec un aperçu.

> La carte est servie par l'intégration et **déclarée d'office** comme module
> frontend : rien à enregistrer à la main dans les *ressources* Lovelace.

## Réglages de la carte (optionnels)

```yaml
type: custom:luna-card
title: Luna                    # titre affiché
subtitle: Ton intendante numérique
height: 460                    # hauteur en pixels
stream: true                   # rendu « mot à mot » (défaut) ; false = un bloc
agent: conversation.sentinel   # agent de conversation (chemin non-streamé / repli)
voice: true                    # bouton micro (défaut) ; false = clavier seul
pipeline: ""                   # id d'un pipeline Assist ; vide = pipeline préféré
```

- `agent` : par défaut, la carte **détecte** l'agent Sentinel (une entité
  `conversation.*` nommée « Sentinel » ou « Luna ») ; sinon elle utilise l'agent
  **par défaut** d'HA. Renseigne-le si tu as plusieurs agents.
- Pour un effet **« app plein écran »** : crée un tableau de bord ne contenant
  que cette carte, et épingle-le au menu latéral (Modifier le tableau de bord →
  Paramètres → afficher dans la barre latérale).

## La voix — parler à Luna

Le bouton **micro** (🎙) lance le **pipeline Assist de Home Assistant** : HA
transcrit ta voix (STT), la fait traiter par l'agent Sentinel, puis **prononce**
la réponse (TTS) — le tout avec les moteurs vocaux que tu as déjà configurés
dans HA. La carte capture le micro et streame l'audio à HA par sa WebSocket
interne ; **rien ne sort du réseau local par la carte**. L'orbe suit : elle
**écoute** (halo ouvert, micro rose), **réfléchit**, puis **parle** pendant que
la voix joue.

Pendant que tu parles, une **bulle « en écoute »** apparaît côté toi et **réagit
à ta voix** — un niveau sonore vivant — puis se **résout en tes mots** dès que la
transcription arrive. En toute honnêteté : une transcription de type *whisper*
rend la phrase **d'un bloc, à la fin** ; voir tes mots s'écrire au fur et à mesure
demanderait un moteur STT **en flux**, que le pipeline standard ne fournit pas. Si
le tien en fournit un jour, la carte affiche le texte partiel automatiquement.

Un appui lance l'écoute ; un second appui dit « j'ai fini de parler » (sinon la
détection de silence d'HA s'en charge, si ton pipeline l'active).

Trois conditions, sinon le micro reste grisé (le clavier, lui, marche partout) :

1. **HTTPS.** Le navigateur n'autorise le micro qu'en **contexte sécurisé** —
   HA doit être ouvert en `https://…` (ou en `localhost`). En `http://` sur le
   LAN, le micro est refusé par le navigateur, pas par la carte.
2. **La permission micro**, accordée à HA sur l'appareil (l'app companion la
   demande la première fois).
3. **Un pipeline Assist** configuré avec **STT + TTS + l'agent Sentinel**
   (Paramètres → Voix). Par défaut la carte prend ton pipeline **préféré** ;
   `pipeline:` en force un autre.

> À valider chez toi : le chemin audio (capture, format, lecture TTS) dépend de
> l'appareil et du pipeline. La machine à états de la carte est testée (voir
> plus bas), mais le micro réel et le rendu de la voix se vérifient sur ton HA —
> surtout dans l'app iOS. Si le micro ne s'active pas, c'est presque toujours le
> point 1 (HTTPS).

## L'orbe vient de la branche « visage »

L'orbe (halo, anneau, noyau ; cinq états) est **repris tel quel** de
`luna/card/luna-card.js` de la branche `new-session-3gvhdx`, dans la palette
rose de Luna. C'est un composant autonome : pas d'image, pas de police externe,
pas de WebGL — il tient dans quelques lignes de SVG et de CSS, et respecte
`prefers-reduced-motion`.

## Éprouver la carte (navigateur)

La carte est du frontend : on la teste dans un vrai Chromium, hors de la suite
`core/tests`.

```bash
pip install playwright && playwright install chromium
python custom_components/sentinel_assist/tests/test_carte.py
```

Le test ouvre la carte avec un faux `hass` et vérifie les trois chemins :
**streaming** (la bulle de Luna se remplit — on observe un préfixe strict du
texte final, l'orbe « parle » pendant le remplissage, puis revient au repos, via
un abonnement à `sentinel_assist/converse`, jamais un `fetch` externe) ; **repli**
(si la commande de streaming manque, la carte bascule sur `conversation/process`
et affiche quand même la réponse) ; **erreur** (une bulle, l'orbe au repos) ; et
la **voix** (le micro lance le pipeline Assist simulé — une bulle « en écoute »
apparaît et son niveau réagit, puis se résout en transcription, réponse, voix
jouée, retour au repos ; le micro et l'audio sont stubés, on éprouve la machine à
états). Le banc manuel est `custom_components/sentinel_assist/tests/banc.html`.

## La suite (incréments futurs, indépendants)

Le **streaming mot à mot**, la **voix** et la **bulle d'écoute vivante** sont
faits. Viendront ensuite, si tu le veux :

- Le **mot d'éveil** (« Luna… ») pour parler sans toucher l'écran.
- Les **propositions** à valider directement dans la carte (aujourd'hui : dans
  le cockpit).
