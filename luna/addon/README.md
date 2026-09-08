# Add-on Luna — le cerveau

Conteneur géré par HAOS sur Nova. Contient l'orchestrateur, le client Claude, le
client Home Assistant et le relais consommé par l'intégration.

## Couches

    kernel      L0  contrats, schémas, bus, réglages, erreurs, autonomie
    providers   L1  Claude, Home Assistant, SQLite          — n'importe que L0
    engine      L2  orchestrateur, arbitre, outils          — n'importe que L0
    interfaces  L3  relais, HTTP, amorçage                  — importe tout

Vérifié par `lint-imports` (voir `.importlinter`), en CI à chaque push.

## Développer

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"

ruff check . && ruff format --check .
lint-imports
pytest -q
```

Aucun test n'appelle l'API Anthropic ni une instance Home Assistant : le client
HA est testé contre un faux serveur WebSocket, le client Claude contre des
réponses enregistrées. La CI ne consomme jamais de crédit.

## Lancer hors HAOS

```bash
export LUNA_ANTHROPIC_API_KEY=sk-ant-...
export LUNA_RELAY_SECRET=un-secret
export LUNA_URL_HA=ws://192.168.1.42:8123/api/websocket
export LUNA_JETON_HA=<jeton longue durée>
export LUNA_BASE=/tmp/luna.db
python -m luna.interfaces
```

## Installer sur Nova

Copier ce dossier dans `/addons/luna` (add-on SSH ou partage Samba), puis
**Paramètres → Modules complémentaires → Boutique → ⋮ → Vérifier les mises à
jour**. Luna apparaît dans « Add-ons locaux ». Renseigner la clé API et le
secret du relais avant de démarrer.

Le port 8099 n'est pas publié sur l'hôte : seule l'intégration, sur le réseau
des add-ons, peut joindre le relais.
