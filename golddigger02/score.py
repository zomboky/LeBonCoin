"""Le score de pépite : combinaison pondérée des signaux.

Une pépite n'est pas seulement « pas chère ». C'est le plus souvent une annonce
que **personne ne voit** : modèle absent du titre, marque mal orthographiée,
photos ratées, vendeur qui liquide un grenier. Le prix bas en est la conséquence,
pas la cause — d'où plusieurs signaux et pas un seul.
"""

from __future__ import annotations

from . import config
from .domains import Domain
from .extract import (
    detect_typo,
    find_markers,
    is_model_hidden,
    listing_quality,
    normalize,
)
from .pricing import assign_reference_prices, is_suspiciously_cheap, price_gap

URGENCY_KEYWORDS = [
    "demenagement", "déménagement", "succession", "debarras", "débarras",
    "cause depart", "cause départ", "urgent", "liquidation", "heritage",
    "héritage", "vide maison", "vide grenier", "grenier", "a debarrasser",
    "à débarrasser", "avant destruction", "depart etranger", "départ étranger",
    "rapidement", "premier arrive", "premier arrivé",
]

# Un vendeur qui écrit ça ne sait pas ce qu'il vend — c'est une invitation.
IGNORANCE_KEYWORDS = [
    "je ne sais pas", "sais pas ce que c'est", "aucune idee", "aucune idée",
    "trouve dans", "trouvé dans", "appartenait a mon", "appartenait à mon",
    "vieux", "je connais pas", "sans garantie", "en l'etat", "en l'état",
    "a identifier", "à identifier", "non teste", "non testé",
]


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _freshness(listing) -> float:
    """1.0 pour une annonce de moins d'une heure, 0 au-delà de 72 h."""
    age = listing.age_hours
    if age is None:
        return 0.0
    if age <= 1:
        return 1.0
    if age >= 72:
        return 0.0
    return _clamp(1.0 - (age - 1) / 71.0)


def score_listing(listing, domain: Domain | None = None) -> float:
    """Calcule `deal_score` (0-100), remplit `signals` et `reasons`."""
    signals: dict[str, float] = {}
    reasons: list[str] = []
    text = listing.text

    # --- Prix -----------------------------------------------------------------
    gap = price_gap(listing)
    signals["price_gap"] = gap
    if gap > 0.15 and listing.reference_price:
        pct = round((1 - listing.price / listing.reference_price) * 100)
        source = "cohorte" if listing.reference_source == "cohort" else "barème"
        reasons.append(f"-{pct}% vs {source} ({int(listing.reference_price)}€)")

    # --- Modèle caché ---------------------------------------------------------
    hidden = is_model_hidden(listing)
    signals["hidden_model"] = 1.0 if hidden else 0.0
    if hidden:
        reasons.append(f"modèle « {listing.model} » absent du titre")

    # --- Authenticité ---------------------------------------------------------
    repro_found: list[str] = []
    if domain is not None:
        origin = find_markers(text, domain.origin_markers)
        repro_found = find_markers(text, domain.repro_markers)
        signals["authenticity"] = _clamp(len(origin) / 2.0) if origin else 0.0
        if origin:
            reasons.append("marqueurs d'origine : " + ", ".join(origin[:3]))
    else:
        signals["authenticity"] = 0.0

    # --- Marque haut de gamme au prix du tout-venant --------------------------
    tier = 0.0
    if domain is not None and domain.premium:
        premium_hit = find_markers(text, domain.premium)
        if premium_hit and gap > 0.2:
            tier = _clamp(len(premium_hit) / 2.0)
            reasons.append(f"pièce recherchée : {premium_hit[0]}")
    signals["brand_tier"] = tier

    # --- Faute d'orthographe sur la marque ------------------------------------
    typo = detect_typo(text, domain.typos) if domain is not None else None
    signals["typo_brand"] = 1.0 if typo else 0.0
    if typo:
        reasons.append(f"« {typo[0]} » pour « {typo[1]} » — invisible en recherche")

    # --- Annonce bâclée -------------------------------------------------------
    quality = listing_quality(listing)
    signals["weak_listing"] = _clamp(1.0 - quality)
    if quality < 0.35:
        reasons.append("annonce bâclée (peu vue)")

    # --- Urgence et ignorance du vendeur --------------------------------------
    haystack = normalize(text)
    urgency_hits = [k for k in URGENCY_KEYWORDS if normalize(k) in haystack]
    ignorance_hits = [k for k in IGNORANCE_KEYWORDS if normalize(k) in haystack]
    signals["urgency"] = _clamp((len(urgency_hits) + len(ignorance_hits)) / 2.0)
    if urgency_hits:
        reasons.append(f"vente pressée ({urgency_hits[0]})")
    if ignorance_hits:
        reasons.append("vendeur ne l'identifie pas")

    signals["private_seller"] = 1.0 if listing.seller_type == "private" else 0.0
    signals["freshness"] = _freshness(listing)

    # --- Agrégation -----------------------------------------------------------
    total_weight = sum(config.WEIGHTS.values())
    weighted = sum(
        config.WEIGHTS.get(name, 0.0) * value for name, value in signals.items()
    )
    score = 100.0 * weighted / total_weight if total_weight else 0.0

    # --- Garde-fous -----------------------------------------------------------
    # Une reproduction annoncée n'est pas une pépite, quel que soit son prix.
    if repro_found:
        score *= 0.15
        reasons = [f"REPRO ({repro_found[0]})"] + reasons

    # Un prix dérisoire face à la référence signale une arnaque ou une pièce
    # détachée, pas une affaire.
    if is_suspiciously_cheap(listing):
        score *= 0.4
        reasons.insert(0, "prix invraisemblable — à vérifier")

    if listing.price is None:
        score *= 0.5
        reasons.append("prix non affiché")

    listing.signals = signals
    listing.reasons = reasons
    listing.deal_score = round(score, 2)
    return listing.deal_score


def rank(
    listings: list,
    domain: Domain | None = None,
    min_cohort: int | None = None,
    min_score: float = 0.0,
    top: int | None = None,
) -> list:
    """Enrichit, score et classe. C'est le point d'entrée du mode `deals`."""
    from .extract import extract_model  # import tardif : évite un cycle

    for listing in listings:
        brand, model, source = extract_model(listing, domain)
        listing.brand, listing.model, listing.model_source = brand, model, source

    assign_reference_prices(listings, domain, min_cohort)

    for listing in listings:
        score_listing(listing, domain)

    ranked = sorted(listings, key=lambda l: l.deal_score, reverse=True)
    if min_score > 0:
        ranked = [l for l in ranked if l.deal_score >= min_score]
    return ranked[:top] if top else ranked
