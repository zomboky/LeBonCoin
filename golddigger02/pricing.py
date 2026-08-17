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


def assign_cohort_keys(listings: list, domain: Domain | None = None) -> list[str]:
    """Clés de cohorte triées et dédupliquées, sans calculer de référence.

    Permet à l'appelant (`cli`) de connaître les clés à interroger dans la
    persistance *avant* que `assign_reference_prices` ne tourne — les clés se
    calculent de la même façon des deux côtés (`build_cohorts`), donc pas de
    second calcul incohérent.
    """
    cohorts = build_cohorts(listings, domain)
    return sorted(cohorts)


def assign_reference_prices(
    listings: list,
    domain: Domain | None = None,
    min_cohort: int | None = None,
    cohort_stats: dict[str, float] | None = None,
) -> None:
    """Renseigne `cohort_size`, `cohort_median` et `reference_price` sur chaque
    annonce. Précédence : cohorte vivante (leave-one-out) > historique persisté
    (`cohort_stats`) > barème du pack > rien.

    Modifie les annonces en place — elles traversent ensuite le scoring.
    """
    min_cohort = config.MIN_COHORT if min_cohort is None else min_cohort
    cohorts = build_cohorts(listings, domain)

    for key, members in cohorts.items():
        priced = [m for m in members if m.price and m.price > 0]
        full_median = median([float(m.price) for m in priced]) if priced else None
        trusted = len(priced) >= min_cohort

        for listing in members:
            listing.cohort_size = len(priced)
            listing.cohort_median = full_median if trusted else None
            listing.reference_dispersion = None

            # La référence doit être calculée sur les *autres* annonces, sinon
            # une cohorte de deux pépites se déclare mutuellement au prix du
            # marché. Le plancher max(2, min_cohort - 1) referme ce cas même
            # à min_cohort=2 : il faut au moins deux "autres" pour trancher.
            others = [float(m.price) for m in priced if m is not listing]
            if trusted and len(others) >= max(2, min_cohort - 1):
                listing.reference_price = median(others)
                listing.reference_dispersion = mad(others)
                listing.reference_source = "cohort"
                continue

            persisted = cohort_stats.get(key) if cohort_stats else None
            if persisted is not None:
                # Corridor anti-dérive : le barème (a priori humain) borne la
                # valeur apprise. Les barèmes sont pessimistes par construction
                # (cf. ValueBand.reference), donc promouvoir l'historique
                # au-dessus d'eux relève globalement les références — c'est
                # l'effet recherché sur le rare, mais aucune dérive ne peut
                # sortir de ce qu'un humain a jugé plausible.
                band = domain.band_for(listing.text) if domain is not None else None
                if band is not None:
                    persisted = min(max(persisted, band.low), band.high)
                listing.reference_price = persisted
                listing.reference_source = "history"
                continue

            if domain is not None:
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


def gap_confidence(listing) -> float:
    """Confiance dans l'écart de prix, dans [MIN_GAP_CONFIDENCE, 1].

    Un rabais de 40 % dans une cohorte qui va de 30 à 400 € est du bruit ; le
    même rabais dans une cohorte serrée est une vraie anomalie de prix. Une
    dispersion nulle ou absente (barème, historique, cohorte parfaitement
    uniforme) signifie « pas d'information » : confiance pleine, pas amortie.
    """
    dispersion = listing.reference_dispersion
    if not dispersion or dispersion <= 0:
        return 1.0
    if not listing.price or not listing.reference_price:
        return 1.0
    z = abs(robust_z(float(listing.price), float(listing.reference_price), dispersion))
    return max(config.MIN_GAP_CONFIDENCE, min(1.0, z / config.GAP_CONFIDENCE_Z))


def price_signal(listing) -> float:
    """`price_gap` pondéré par la confiance que la dispersion de la cohorte
    autorise à lui accorder."""
    return price_gap(listing) * gap_confidence(listing)


def is_suspiciously_cheap(listing, floor_ratio: float = 0.05) -> bool:
    """Trop beau pour être vrai : sous 5 % de la référence, c'est une arnaque
    ou une pièce détachée, pas une pépite."""
    if not listing.price or not listing.reference_price:
        return False
    return listing.price < listing.reference_price * floor_ratio
