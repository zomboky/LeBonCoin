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
from .pricing import (
    assign_reference_prices,
    is_suspiciously_cheap,
    price_gap,
    price_signal,
)

# Indices forts : rares en dehors du motif recherché (vendeur pressé, ou qui
# ignore explicitement ce qu'il vend). Deux indices forts saturent le signal.
STRONG_URGENCY = [
    "succession", "heritage", "héritage", "debarras", "débarras",
    "vide maison", "vide grenier", "avant destruction", "liquidation",
    "demenagement", "déménagement", "cause depart", "cause départ",
    "depart etranger", "départ étranger",
]
STRONG_IGNORANCE = [
    "je ne sais pas", "aucune idee", "aucune idée", "je connais pas",
    "a identifier", "à identifier", "trouve dans", "trouvé dans",
    "appartenait a mon", "appartenait à mon",
]

# Indices faibles : omniprésents sur leboncoin (« vieux », « en l'état »...),
# donc plafonnés — ils ne doivent jamais, seuls, saturer le signal (cf. C1).
WEAK_TELLS = [
    "vieux", "vieille", "en l'etat", "en l'état", "sans garantie",
    "non teste", "non testé", "urgent", "rapidement",
    "premier arrive", "premier arrivé", "grenier",
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


def _price_drop(listing) -> float:
    """Baisse depuis un passage précédent, dans [0,1]. 50% de rabais = signal
    plein — un vendeur qui casse son prix de moitié est aussi motivé qu'il
    peut l'être. Un premier passage sans historique n'est pas pénalisé : 0.0
    est neutre, pas une punition pour absence de passé."""
    prev = listing.previous_price
    if not prev or not listing.price or prev <= 0 or listing.price >= prev:
        return 0.0
    return _clamp((1.0 - listing.price / prev) / 0.5)


def score_listing(listing, domain: Domain | None = None) -> float:
    """Calcule `deal_score` (0-100), remplit `signals` et `reasons`."""
    signals: dict[str, float] = {}
    reasons: list[str] = []
    text = listing.text

    # --- Prix -----------------------------------------------------------------
    # `raw_gap` sert aux raisons affichées et à la porte `brand_tier` : elles
    # doivent rester véridiques (un pourcentage réel), indépendamment de la
    # confiance accordée par la dispersion de la cohorte.
    raw_gap = price_gap(listing)
    gap = price_signal(listing)
    signals["price_gap"] = gap
    if raw_gap > 0.15 and listing.reference_price:
        pct = round((1 - listing.price / listing.reference_price) * 100)
        source = {"cohort": "cohorte", "history": "historique"}.get(
            listing.reference_source, "barème"
        )
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
        if premium_hit and raw_gap > 0.2:
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
    urgency_hits = find_markers(text, STRONG_URGENCY)
    ignorance_hits = find_markers(text, STRONG_IGNORANCE)
    weak_hits = find_markers(text, WEAK_TELLS)
    strong_count = len(urgency_hits) + len(ignorance_hits)
    signals["urgency"] = _clamp(0.5 * strong_count + min(0.45, 0.15 * len(weak_hits)))
    if urgency_hits:
        reasons.append(f"vente pressée ({urgency_hits[0]})")
    if ignorance_hits:
        reasons.append("vendeur ne l'identifie pas")

    signals["private_seller"] = 1.0 if listing.seller_type == "private" else 0.0
    signals["freshness"] = _freshness(listing)

    # --- Baisse de prix (D1) ---------------------------------------------------
    signals["price_drop"] = _price_drop(listing)
    if signals["price_drop"] > 0:
        pct = round((1 - listing.price / listing.previous_price) * 100)
        reasons.append(f"prix baissé de {pct}% (était {int(listing.previous_price)}€)")

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
    cohort_stats: dict[str, float] | None = None,
) -> list:
    """Enrichit, score et classe. C'est le point d'entrée du mode `deals`.

    `cohort_stats` (optionnel) : médianes de cohorte persistées d'un run
    précédent (D2), données pures — `rank`/`score.py` n'importent jamais
    `cache.py`, cf. `test_score_ne_depend_pas_du_cache`.
    """
    from .extract import extract_model  # import tardif : évite un cycle

    for listing in listings:
        brand, model, source = extract_model(listing, domain)
        listing.brand, listing.model, listing.model_source = brand, model, source

    assign_reference_prices(listings, domain, min_cohort, cohort_stats)

    for listing in listings:
        score_listing(listing, domain)

    ranked = sorted(listings, key=lambda l: l.deal_score, reverse=True)
    if min_score > 0:
        ranked = [l for l in ranked if l.deal_score >= min_score]
    return ranked[:top] if top else ranked
