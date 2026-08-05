"""Configuration : chemins, endpoints, en-têtes, poids de scoring.

Tout ce qui est susceptible de casser quand leboncoin change quelque chose est
regroupé ici et surchargeable par variable d'environnement, pour qu'une panne se
répare sans toucher au code.
"""

from __future__ import annotations

import os
from pathlib import Path

# --- Chemins -----------------------------------------------------------------

PKG_DIR = Path(__file__).resolve().parent

# Packs thématiques de l'utilisateur. Ceux qui portent le nom d'un pack livré le
# remplacent, ce qui permet d'ajuster un domaine sans modifier le dépôt.
_default_conf = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "lbc"
USER_DOMAIN_DIR = Path(os.environ.get("LBC_DOMAIN_DIR", _default_conf / "domains"))

_default_home = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "lbc"
CACHE_DIR = Path(os.environ.get("LBC_CACHE_DIR", _default_home))
CACHE_DB = CACHE_DIR / "cache.sqlite3"
STATE_DIR = CACHE_DIR / "state"

# --- Endpoints ---------------------------------------------------------------

# L'API interne du site web. La clé ci-dessous est celle que le front public
# envoie ; elle change de temps en temps, d'où la surcharge par env.
API_URL = os.environ.get("LBC_API_URL", "https://api.leboncoin.fr/finder/search")
API_KEY = os.environ.get("LBC_API_KEY", "ba0c2dad52b3ec")
WEB_BASE = "https://www.leboncoin.fr"
SEARCH_PATH = "/recherche"

USER_AGENT = os.environ.get(
    "LBC_USER_AGENT",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
)

# Chromium fourni par l'environnement, sinon celui que Playwright résout seul.
CHROMIUM_PATH = os.environ.get("LBC_CHROMIUM_PATH") or None
PROXY = os.environ.get("LBC_PROXY") or None

# Nombre d'annonces par page côté API. 35 est ce que demande le front.
PAGE_SIZE = int(os.environ.get("LBC_PAGE_SIZE", "35"))

# Pause entre deux requêtes, en secondes. 0 = pas de throttle (défaut).
DEFAULT_DELAY = float(os.environ.get("LBC_DELAY", "0"))

# Durée de vie du cache disque, en secondes.
CACHE_TTL = int(os.environ.get("LBC_CACHE_TTL", str(6 * 3600)))


def api_headers(referer: str = WEB_BASE + "/") -> dict[str, str]:
    """En-têtes attendus par l'API interne."""
    return {
        "api_key": API_KEY,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
        "Origin": WEB_BASE,
        "Referer": referer,
        "User-Agent": USER_AGENT,
    }


# --- Poids du score de pépite ------------------------------------------------
#
# Chaque signal produit une contribution dans [0, 1] ; le score final est la
# somme pondérée ramenée sur 100. Ajuster ici plutôt que dans score.py.

WEIGHTS: dict[str, float] = {
    "price_gap": 3.0,      # écart au prix médian de la cohorte — moteur principal
    "hidden_model": 2.0,   # modèle présent dans la description mais absent du titre
    "brand_tier": 1.4,     # marque/modèle haut de gamme vendu au prix du bas de gamme
    "typo_brand": 1.2,     # marque mal orthographiée : l'annonce sort de peu de recherches
    "weak_listing": 0.8,   # annonce bâclée : peu de photos, description courte
    "urgency": 0.6,        # « déménagement », « succession », « urgent »
    "private_seller": 0.4, # un particulier price moins juste qu'un pro
    "freshness": 0.5,      # annonce récente : il faut être le premier
}

# En dessous de ce nombre d'annonces comparables, on ne fait pas confiance à la
# médiane et le signal prix est neutralisé.
MIN_COHORT = int(os.environ.get("LBC_MIN_COHORT", "5"))


def ensure_dirs() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
