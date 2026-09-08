"""L0 — les erreurs de Luna.

Chaque erreur porte un `code` machine, consommé par la carte pour choisir quoi
afficher, et un `message` français destiné à être montré tel quel. §8 du cahier
des charges : « Jamais d'échec silencieux ».

La taxonomie complète est dans docs/P1-CONTRATS.md §8. Les codes `insecure_context`
et `mic_denied` n'apparaissent pas ici : la carte les fabrique elle-même, ils ne
viennent jamais du serveur.
"""

from __future__ import annotations


class LunaError(Exception):
    """Erreur affichable. Le message est en français et sort tel quel."""

    code = "internal"

    def __init__(self, message: str, *, detail: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail

    def charge_utile(self) -> dict[str, str | None]:
        return {"code": self.code, "message": self.message, "detail": self.detail}


class MaisonIndisponible(LunaError):
    code = "ha_unavailable"

    def __init__(self, message: str | None = None, **kw: str | None) -> None:
        super().__init__(
            message or "Je n'arrive pas à joindre Home Assistant pour l'instant.", **kw
        )


class CerveauIndisponible(LunaError):
    code = "claude_unavailable"


class CreditEpuise(LunaError):
    code = "claude_no_credit"

    def __init__(self, message: str | None = None, **kw: str | None) -> None:
        super().__init__(
            message
            or "Le crédit de la clé API Anthropic est épuisé — recharge le compte "
            "sur console.anthropic.com, puis réessaie.",
            **kw,
        )


class TropDeRequetes(LunaError):
    code = "claude_rate_limited"

    def __init__(self, message: str | None = None, **kw: str | None) -> None:
        super().__init__(
            message or "L'API Anthropic est saturée. Je réessaie dans un instant.", **kw
        )


class RefusDuModele(LunaError):
    """Le modèle a décliné la requête (`stop_reason: "refusal"`)."""

    code = "claude_refused"

    def __init__(self, message: str | None = None, **kw: str | None) -> None:
        super().__init__(message or "Je préfère ne pas répondre à cette demande.", **kw)


class NonAutorise(LunaError):
    """Le profil actif n'a pas le scope requis (§3, F3)."""

    code = "not_allowed"


class HorsPerimetreV1(LunaError):
    """Action de niveau 5 (§9). Ouvrants, alarme, suppression, envoi externe."""

    code = "out_of_scope_v1"


class PropositionExpiree(LunaError):
    code = "proposal_expired"

    def __init__(self, message: str | None = None, **kw: str | None) -> None:
        super().__init__(
            message or "Cette proposition a expiré. Redemande-moi si tu la veux encore.",
            **kw,
        )


class PropositionInconnue(LunaError):
    code = "proposal_expired"

    def __init__(self, message: str | None = None, **kw: str | None) -> None:
        super().__init__(message or "Je ne retrouve plus cette proposition.", **kw)


class BiometrieDistante(LunaError):
    """§6 : « La biométrie n'est active que sur le réseau local. »

    Levée par sécurité côté add-on ; l'intégration refuse déjà en amont, pour
    qu'aucun octet d'audio ne traverse le relais Nabu Casa (décision C3).
    """

    code = "remote_biometrics"

    def __init__(self, message: str | None = None, **kw: str | None) -> None:
        super().__init__(
            message
            or "Je ne reconnais les voix que sur le réseau de la maison. "
            "À distance, passe par le PIN de Loggia.",
            **kw,
        )


class ModeleVoixIndisponible(LunaError):
    code = "model_unavailable"

    def __init__(self, message: str | None = None, **kw: str | None) -> None:
        super().__init__(
            message
            or "Le modèle de reconnaissance de voix n'est pas chargé. "
            "Vérifie l'option « modele_voix » de l'add-on.",
            **kw,
        )


class AudioTropCourt(LunaError):
    code = "audio_too_short"

    def __init__(self, message: str | None = None, **kw: str | None) -> None:
        super().__init__(
            message or "C'était trop court pour que je reconnaisse la voix.", **kw
        )


class ProfilNonInscrit(LunaError):
    code = "not_enrolled"


class PasEncoreImplemente(LunaError):
    """Commande d'une phase future. Documentée en P1, vivante plus tard."""

    code = "not_implemented"


class ErreurInterne(LunaError):
    code = "internal"

    def __init__(self, message: str | None = None, **kw: str | None) -> None:
        super().__init__(
            message or "Quelque chose a cassé de mon côté. Regarde mes journaux.", **kw
        )
