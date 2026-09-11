"""Prix indicatifs des modèles LLM — pour **estimer** un coût à partir des tokens
comptés localement (table `llm_usage`).

Honnêteté : ce sont des tarifs publics **au moment de l'écriture** (début 2026), en
**USD par million de tokens**, à **titre indicatif**. Les grilles bougent souvent —
édite ce tableau au besoin. La facture réelle se lit sur la **console** de chaque
fournisseur ; Sentinel ne connaît que les tokens que l'API lui renvoie.
"""

from __future__ import annotations

# (prix entrée, prix sortie) en USD / 1 000 000 de tokens.
# Correspondance par PRÉFIXE d'identifiant de modèle (le plus long l'emporte) —
# couvre les variantes datées (claude-opus-5-20260101, gpt-4o-2024-…, etc.).
PRICES: dict[str, tuple[float, float]] = {
    # Anthropic (Claude)
    "claude-opus": (15.0, 75.0),
    "claude-sonnet": (3.0, 15.0),
    "claude-haiku": (0.80, 4.0),
    "claude-3-opus": (15.0, 75.0),
    "claude-3-5-sonnet": (3.0, 15.0),
    "claude-3-5-haiku": (0.80, 4.0),
    # OpenAI
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.0),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1": (2.0, 8.0),
    "o1-mini": (1.10, 4.40),
    "o1": (15.0, 60.0),
    # Google Gemini (tarifs payants ; l'offre gratuite = 0 côté facture)
    "gemini-2.5-pro": (1.25, 10.0),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-1.5-pro": (1.25, 5.0),
    "gemini-1.5-flash": (0.075, 0.30),
    # Groq — gratuit à ce jour
    "llama-3.3-70b": (0.0, 0.0),
    "llama-3.1-8b": (0.0, 0.0),
}

# Fournisseurs sans facturation à ce jour (indice affiché, jamais un « inconnu »).
FREE_PROVIDERS = {"groq"}


def price_for(model: str) -> tuple[float, float] | None:
    """(prix entrée, prix sortie) pour un modèle — préfixe le plus long. `None` si inconnu."""
    m = (model or "").strip().lower()
    if not m:
        return None
    best_prefix = ""
    best_price: tuple[float, float] | None = None
    for prefix, price in PRICES.items():
        if m.startswith(prefix) and len(prefix) > len(best_prefix):
            best_prefix, best_price = prefix, price
    return best_price


def estimate_usd(model: str, in_tok: int, out_tok: int) -> float | None:
    """Coût estimé en USD, ou `None` si le prix du modèle est inconnu."""
    price = price_for(model)
    if price is None:
        return None
    pin, pout = price
    return round((in_tok or 0) / 1e6 * pin + (out_tok or 0) / 1e6 * pout, 6)
