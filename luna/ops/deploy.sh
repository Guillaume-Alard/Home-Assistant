#!/usr/bin/env bash
# Déploie Luna sur Nova.
#
# Trois artefacts, trois destinations. Pas de HACS (décision A4) : c'est une
# installation à un exemplaire, sur une seule machine.
#
#   ./ops/deploy.sh [utilisateur@hôte]
#
# Prérequis : l'add-on « Advanced SSH & Web Terminal » sur Nova, avec l'option
# `protection_mode` désactivée pour que /addons soit accessible.
set -euo pipefail

CIBLE="${1:-root@nova.local}"
RACINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "→ Add-on (le cerveau) vers ${CIBLE}:/addons/luna/"
rsync -az --delete \
  --exclude tests/ --exclude __pycache__/ --exclude .pytest_cache/ \
  --exclude .ruff_cache/ \
  "${RACINE}/addon/" "${CIBLE}:/addons/luna/"

echo "→ Intégration (le nerf) vers ${CIBLE}:/config/custom_components/luna/"
rsync -az --delete --exclude __pycache__/ \
  "${RACINE}/integration/custom_components/luna/" \
  "${CIBLE}:/config/custom_components/luna/"

echo "→ Carte (le visage) vers ${CIBLE}:/config/www/luna-card.js"
rsync -az "${RACINE}/card/luna-card.js" "${CIBLE}:/config/www/luna-card.js"

cat <<'FIN'

Déposé. Ce qui reste à faire à la main, et une seule fois :

  1. Modules complémentaires → Boutique → ⋮ → Vérifier les mises à jour,
     puis installer « Luna » (add-ons locaux). Renseigner la clé API Anthropic
     et le secret du relais avant de démarrer.
  2. Redémarrer Home Assistant pour qu'il découvre l'intégration.
  3. Appareils et services → Ajouter une intégration → Luna. Même secret.
  4. Tableaux de bord → Ressources → /local/luna-card.js, type Module.

Ensuite, un `deploy.sh` suffit : redémarre l'add-on, recharge l'intégration,
et vide le cache du navigateur pour la carte (la version est affichée dans la
console).
FIN
