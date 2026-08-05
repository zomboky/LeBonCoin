"""Prix de référence : cohortes comparables, statistiques robustes, bandes curées.

Sur du courant, la médiane des annonces comparables est le meilleur juge. Sur du
rare — et les domaines visés le sont — il n'y a souvent aucun comparable en
ligne : c'est là que les `value_bands` des packs prennent le relais. Sans ce
repli, l'outil serait aveugle précisément là où les pépites se trouvent.
"""

from __future__ import annotations

from statistics import median as _median

from . import config
from .domains import Domain
from .extract import cohort_signature


def median(values: list[float]) -> float:
    return float(_median(values)) if values else 0.0


def mad(values: list[float], center: float | None = None) -> float:
    """Écart absolu médian. Insensible aux annonces aberrantes, contrairement à σ.

    Les recherches leboncoin ramènent régulièrement des prix à 1 € et des lots
    entiers ; un écart-type classique serait dominé par ce bruit.
    """
    if not values:
        return 0.0
    center = median(values) if center is None else center
    return float(_median([abs(v - center) for v in values]))


def robust_z(value: float, center: float, dispersion: float) -> float:
    """Z-score robuste. 0.6745 ramène le MAD à l'échelle d'un écart-type."""
    if dispersion <= 0:
        return 0.0
    return 0.6745 * (value - center) / dispersion


def build_cohorts(listings: list, domain: Domain | None = None) -> dict[str, list]:
    cohorts: dict[str, list] = {}
    for listing in listings:
        key = cohort_signature(listing, domain)
        listing.cohort_key = key
        cohorts.setdefault(key, []).append(listing)
    return cohorts


def assign_reference_prices(
    listings: list,
    domain: Domain | None = None,
    min_cohort: int | None = None,
) -> None:
    """Renseigne `cohort_size` et `reference_price` sur chaque annonce.

    Modifie les annonces en place — elles traversent ensuite le scoring.
    """
    min_cohort = config.MIN_COHORT if min_cohort is None else min_cohort
    cohorts = build_cohorts(listings, domain)

    for key, members in cohorts.items():
        priced = [m for m in members if m.price and m.price > 0]
        # La médiane doit être calculée sur les *autres* annonces, sinon une
        # cohorte de deux pépites se déclare mutuellement au prix du marché.
        reference = None
        if len(priced) >= min_cohort:
            values = [float(m.price) for m in priced]
            reference = median(values)

        for listing in members:
            listing.cohort_size = len(priced)
            if reference is not None:
                listing.reference_price = reference
                listing.reference_source = "cohort"
            elif domain is not None:
                band = domain.band_for(listing.text)
                listing.reference_price = band.reference if band else None
                listing.reference_source = "band" if band else ""
            else:
                listing.reference_price = None
                listing.reference_source = ""


def cohort_dispersion(members: list) -> float:
    values = [float(m.price) for m in members if m.price and m.price > 0]
    if len(values) < 2:
        return 0.0
    return mad(values)


def price_gap(listing) -> float:
    """Écart relatif sous la référence, dans [0,1].

    0 = au prix de référence ou au-dessus. 1 = donné (90 % sous la référence).
    La saturation à 0.9 évite qu'une annonce à 1 € — souvent une erreur de
    saisie ou un appât — écrase tout le classement.
    """
    if not listing.price or not listing.reference_price:
        return 0.0
    if listing.reference_price <= 0:
        return 0.0
    ratio = listing.price / listing.reference_price
    if ratio >= 1.0:
        return 0.0
    discount = 1.0 - ratio
    return min(1.0, discount / 0.9)


def is_suspiciously_cheap(listing, floor_ratio: float = 0.05) -> bool:
    """Trop beau pour être vrai : sous 5 % de la référence, c'est une arnaque
    ou une pièce détachée, pas une pépite."""
    if not listing.price or not listing.reference_price:
        return False
    return listing.price < listing.reference_price * floor_ratio
