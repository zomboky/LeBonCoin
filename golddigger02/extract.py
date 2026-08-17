"""Analyse du texte des annonces : marque, modèle, marqueurs, signature de cohorte.

Tout est lexical et déterministe — aucun appel à un modèle. C'est le principe :
le travail d'analyse coûte du CPU local, pas des tokens.
"""

from __future__ import annotations

import re
import unicodedata

from .domains import Domain

# Mots qui ne disent rien sur *ce que c'est*, et qui pollueraient les cohortes.
_STOPWORDS = {
    "a", "au", "aux", "avec", "ce", "ces", "dans", "de", "des", "du", "en", "et",
    "la", "le", "les", "lot", "ma", "mes", "mon", "ou", "par", "pour", "sa", "se",
    "ses", "son", "sur", "un", "une", "vend", "vends", "vendu", "cause", "tres",
    "tbe", "be", "bon", "bonne", "etat", "neuf", "neuve", "occasion", "ancien",
    "ancienne", "vintage", "rare", "collection", "original", "originale", "piece",
    "pieces", "prix", "euros", "eur", "negociable", "urgent", "cede", "donne",
    "taille", "couleur", "noir", "noire", "blanc", "blanche", "rouge", "bleu",
    "bleue", "vert", "verte", "gris", "grise", "jaune", "marron", "beige",
    "grand", "grande", "petit", "petite", "moyen", "moyenne", "xs", "s", "m",
    "l", "xl", "xxl", "cm", "mm", "kg", "g",
}

_WORD_RX = re.compile(r"[a-z0-9]+")

# Incrémenté à chaque changement de la manière dont `cohort_signature` calcule
# une clé — les statistiques persistées (cache.py, table `cohort_stats`) sont
# indexées par clé versionnée, pour qu'un changement de logique invalide
# automatiquement les anciennes lignes plutôt que d'exiger un effacement manuel.
SIGNATURE_VERSION = 2


def normalize(text: str) -> str:
    """Minuscules, sans accents. Base de toutes les comparaisons."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return stripped.lower()


def tokens(text: str) -> list[str]:
    return _WORD_RX.findall(normalize(text))


def content_tokens(text: str) -> list[str]:
    """Tokens porteurs de sens : ni mots vides, ni tailles, ni bruit d'un caractère."""
    out = []
    for token in tokens(text):
        if token in _STOPWORDS or len(token) < 2:
            continue
        # Un nombre isolé est presque toujours un prix, une taille ou une année.
        if token.isdigit() and len(token) < 3:
            continue
        out.append(token)
    return out


def _token_rank(token: str, domain: Domain | None) -> tuple:
    """Plus le tuple est petit, plus le token discrimine.

    Une marque ou un modèle du pack l'emporte sur tout ; à défaut, un token
    contenant un chiffre (référence, P/N, millésime) ; à défaut, le mot le
    plus long.
    """
    if domain is not None:
        norm = normalize(token)
        if any(norm == normalize(b) for b in domain.brands):
            return (0, 0, token)
        if any(rule.search(token) for rule in domain.models):
            return (0, 1, token)
    if any(c.isdigit() for c in token):
        return (1, -len(token), token)
    return (2, -len(token), token)


def cohort_signature(listing, domain: Domain | None = None) -> str:
    """Clé de regroupement des annonces comparables.

    Le modèle extrait prime : deux annonces du même modèle sont comparables même
    si les titres n'ont rien à voir. Sinon on retombe sur les tokens les plus
    discriminants du titre (classés, puis tronqués, puis retriés pour la
    jointure) — l'ordre des mots dans le titre original n'a pas d'importance.

    Le préfixe est la catégorie de l'annonce, sauf si le pack déclare plusieurs
    catégories : il affirme alors qu'elles contiennent le même genre de biens
    (ex. un altimètre listé en Collection ou en Décoration), et prendre la
    catégorie comme préfixe fragmenterait la cohorte d'un même objet.
    """
    if domain is not None and len(domain.category_ids) > 1:
        prefix = domain.name
    else:
        prefix = listing.category_id

    if listing.model:
        return f"{prefix}:{normalize(listing.model)}"

    ranked = sorted(set(content_tokens(listing.title)), key=lambda t: _token_rank(t, domain))
    words = ranked[:4]
    if not words:
        return f"{prefix}:?"
    return f"{prefix}:" + "_".join(sorted(words))


def find_markers(text: str, markers: list[str]) -> list[str]:
    """Marqueurs présents dans le texte, en comparaison insensible aux accents."""
    haystack = normalize(text)
    return [m for m in markers if normalize(m) in haystack]


def detect_typo(text: str, typos: dict[str, str]) -> tuple[str, str] | None:
    """Repère une marque mal orthographiée.

    C'est un signal fort : une annonce qui écrit « Kolsman » ne remonte dans
    aucune recherche « Kollsman », donc presque personne ne la voit.
    """
    words = set(tokens(text))
    for wrong, right in typos.items():
        wrong_norm = normalize(wrong)
        if wrong_norm in words:
            # On ne signale pas si l'orthographe correcte figure aussi.
            if normalize(right) not in normalize(text):
                return wrong, right
    return None


def extract_model(listing, domain: Domain | None) -> tuple[str, str, str]:
    """Renvoie `(marque, modèle, source)` où source ∈ title | body | attributes.

    La source est ce qui compte pour la détection de pépite : un modèle trouvé
    dans le corps mais absent du titre signale une annonce que peu de gens
    voient passer.
    """
    if domain is None:
        return "", "", ""

    title_norm = normalize(listing.title)
    body_norm = normalize(listing.body)
    attrs_norm = normalize(" ".join(listing.attributes.values()))

    brand = ""
    for candidate in domain.brands:
        candidate_norm = normalize(candidate)
        if _contains_word(title_norm, candidate_norm):
            brand = candidate
            break
        if not brand and (
            _contains_word(body_norm, candidate_norm)
            or _contains_word(attrs_norm, candidate_norm)
        ):
            brand = candidate

    model = ""
    source = ""
    for rule in domain.models:
        if rule.search(listing.title):
            model, source = rule.name, "title"
            break
        if not model and rule.search(listing.body):
            model, source = rule.name, "body"
        if not model and rule.search(" ".join(listing.attributes.values())):
            model, source = rule.name, "attributes"

    # Une marque trouvée seulement dans le corps compte comme un modèle caché.
    if not model and brand:
        model = brand
        brand_norm = normalize(brand)
        if _contains_word(title_norm, brand_norm):
            source = "title"
        elif _contains_word(body_norm, brand_norm):
            source = "body"
        else:
            source = "attributes"

    return brand, model, source


def _contains_word(haystack: str, needle: str) -> bool:
    """Sous-chaîne bornée par des non-alphanumériques.

    Évite que « lee » matche « collee » ou que « ge » matche « garage ».
    """
    if not needle or not haystack:
        return False
    pattern = r"(?<![a-z0-9])" + re.escape(needle) + r"(?![a-z0-9])"
    return re.search(pattern, haystack) is not None


def is_model_hidden(listing) -> bool:
    """Le modèle est identifié, mais pas dans le titre."""
    return bool(listing.model) and listing.model_source in ("body", "attributes")


def listing_quality(listing) -> float:
    """Qualité perçue de l'annonce, dans [0,1]. 0 = bâclée.

    Une annonce bâclée attire moins d'acheteurs, donc se brade plus souvent —
    et trahit souvent un vendeur qui ne sait pas ce qu'il a.
    """
    score = 0.0
    if len(listing.body) >= 150:
        score += 0.4
    elif len(listing.body) >= 50:
        score += 0.2
    if listing.nb_images >= 4:
        score += 0.35
    elif listing.nb_images >= 2:
        score += 0.2
    if len(listing.title) >= 25:
        score += 0.15
    if listing.attributes:
        score += 0.1
    return min(1.0, score)
