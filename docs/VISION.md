# Vision en lecture — Luna regarde, elle n'agit pas

Luna peut **regarder une caméra de Nova et décrire ce qu'elle voit**. C'est la
première brique de perception : un **capteur**, jamais un bras. Elle observe et
raconte ; pour intervenir sur ce qu'elle voit, elle passe par le moteur
« propose puis approuve ». La reconnaissance de locuteur ne débloque rien de
plus : regarder est réservé aux personnes reconnues (une caméra touche à
l'intimité), mais reste une **lecture**, jamais une action.

> « Luna, qui est à la porte ? » — « Une personne en manteau sombre attend
> devant l'entrée, un colis à la main. »

## Principe

- **Local d'abord.** Le modèle de vision tourne sur Nebula (la RTX 2070),
  derrière une API **compatible OpenAI** — le même dialecte que les
  fournisseurs LLM alternatifs et la voix clonée. **Aucune image ne quitte le
  réseau local.**
- **Lecture seule.** L'instantané de caméra est lu depuis Nova via l'API REST
  `camera_proxy` (une requête `GET`, jamais `call_service`). L'invariant de
  sécurité reste vert : `regarder` n'écrit rien.
- **Jamais une action.** L'outil renvoie une description. Agir sur ce qu'elle
  voit (« du coup, verrouille la porte ») repasse toujours par une proposition
  que Guillaume valide.

## Activer

1. **Faire tourner un service de vision local** exposant une API compatible
   OpenAI (`/v1/chat/completions`) avec un modèle multimodal. Quelques options
   sur la RTX 2070 :
   - **Ollama** : `ollama run qwen2.5-vl` puis exposer `http://nebula:11434/v1` ;
   - **vLLM** : `vllm serve Qwen/Qwen2.5-VL-7B-Instruct` (endpoint `/v1`) ;
   - **llama.cpp** (`llama-server`) avec un modèle VLM (Qwen-VL, LLaVA…).
2. **Déclarer le service dans `.env`** (voir `.env.example`) :
   ```ini
   SENTINEL_VISION=on
   VISION_BASE_URL=http://sentinel-vision:8000/v1
   VISION_MODEL=qwen2.5-vl-7b
   # VISION_API_KEY=            # optionnel (service local sans authentification)
   # VISION_MAX_TOKENS=512
   # VISION_TIMEOUT=60
   ```
3. **Avoir au moins une caméra dans Nova** (`camera.*`). L'outil `regarder`
   n'apparaît que si le service ET une caméra sont présents.

Tant que `VISION_BASE_URL`/`VISION_MODEL` ne sont pas posés, la vision est
simplement absente : l'outil ne s'affiche pas et rien ne casse.

## Usage

- « Regarde la caméra de l'entrée. »
- « Y a-t-il une voiture dans l'allée ? »
- « Le portail est-il bien fermé ? »

Luna choisit la caméra par la **pièce** (`zone`) ou un **entity_id** précis
(`camera`). S'il n'y a qu'une seule caméra, elle est prise par défaut ; s'il y
en a plusieurs et qu'aucune n'est précisée, Luna demande laquelle.

## Ce que ça ne fait pas (encore)

- Pas de **flux continu** ni de détection en temps réel : Luna regarde **sur
  demande**, une image à la fois.
- Pas d'**action déclenchée par la vision** : c'est un capteur. Toute
  conséquence passe par une proposition.
- Pas de **reconnaissance faciale** ni d'identification nominative : Luna décrit
  ce qu'elle voit, sans prétendre reconnaître qui que ce soit.

## Sécurité

| Garde-fou | Comment |
|---|---|
| Aucune écriture | `camera_snapshot` est un `GET` ; l'invariant statique interdit tout `call_service` hors des exécuteurs. |
| Images locales | Le service de vision est sur Nebula ; l'URL pointe le réseau local. |
| Personnes reconnues | `regarder` exige `known` (comme la domotique) ; un invité ne peut pas regarder. |
| Jamais élévateur | La reconnaissance ne débloque aucune action ; regarder reste une lecture. |
| Dégradé propre | Service absent ou éteint → message clair, jamais d'erreur opaque. |
