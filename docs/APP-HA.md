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

Chaque phrase passe par le **pipeline de conversation d'HA**
(`conversation/process`), qui la route vers l'**agent Sentinel** (l'intégration
`sentinel_assist`). Donc :

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
agent: conversation.sentinel   # agent de conversation à interroger
```

- `agent` : par défaut, la carte **détecte** l'agent Sentinel (une entité
  `conversation.*` nommée « Sentinel » ou « Luna ») ; sinon elle utilise l'agent
  **par défaut** d'HA. Renseigne-le si tu as plusieurs agents.
- Pour un effet **« app plein écran »** : crée un tableau de bord ne contenant
  que cette carte, et épingle-le au menu latéral (Modifier le tableau de bord →
  Paramètres → afficher dans la barre latérale).

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

Le test ouvre la carte avec un faux `hass`, envoie une phrase, et vérifie : ma
bulle apparaît, l'orbe passe à « réfléchit », la réponse de Luna s'affiche,
l'orbe revient au repos, et la carte a bien parlé à l'agent `conversation.*`
(jamais un `fetch` externe). Le mode erreur affiche une bulle et laisse l'orbe
au repos. Le banc manuel est `custom_components/sentinel_assist/tests/banc.html`.

## La suite (incréments futurs, indépendants)

Cette première version est volontairement resserrée — **une phase testable, qui
ne dépend pas des suivantes**. Viendront, si tu le veux :

- **Streaming** de la réponse (mot à mot) plutôt qu'en un bloc.
- **Voix** dans la carte (le micro d'Assist existe déjà côté HA).
- Les **propositions** à valider directement dans la carte (aujourd'hui : dans
  le cockpit).
