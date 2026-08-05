"""Formats de sortie. C'est ici que se joue l'économie de tokens.

Règles suivies partout :

- une annonce tient sur **une ligne** ;
- pas d'URL complète : l'identifiant suffit, le motif est rappelé une fois en
  en-tête (une URL leboncoin pèse ~50 caractères, soit plus que le reste de la
  ligne réunie) ;
- les libellés sont courts et non répétés ;
- les images ne sortent qu'en mode `vision`, et seulement pour les finalistes.
"""

from __future__ import annotations

import json

URL_HINT = "url = leboncoin.fr/ad/<id>"


def _short(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _age(listing) -> str:
    hours = listing.age_hours
    if hours is None:
        return "?"
    if hours < 1:
        return f"{int(hours * 60)}min"
    if hours < 48:
        return f"{int(hours)}h"
    return f"{int(hours / 24)}j"


def _price(value) -> str:
    return f"{int(value)}" if value else "-"


def as_tsv(listings: list, with_reasons: bool = True) -> str:
    """Tableau compact : ~15 tokens par annonce."""
    lines = [f"# score\tprix\tref\ttitre\tville\tage\tid" + ("\tpourquoi" if with_reasons else "")]
    lines.append(f"# {URL_HINT}")
    for listing in listings:
        row = [
            f"{listing.deal_score:.0f}",
            _price(listing.price),
            _price(listing.reference_price),
            _short(listing.title, 55),
            _short(listing.city, 18),
            _age(listing),
            listing.id,
        ]
        if with_reasons:
            row.append(_short(" ; ".join(listing.reasons), 90))
        lines.append("\t".join(row))
    return "\n".join(lines)


def as_ids(listings: list) -> str:
    return "\n".join(l.id for l in listings)


def as_jsonl(listings: list) -> str:
    return "\n".join(
        json.dumps(l.to_dict(), ensure_ascii=False, separators=(",", ":"))
        for l in listings
    )


def as_vision(listings: list, max_images: int = 3) -> str:
    """Bloc destiné à l'inspection visuelle des finalistes.

    On ne sort ce format que pour une poignée d'annonces : c'est le seul endroit
    où le modèle dépense des tokens de vision, pour confirmer une référence sur
    la photo (plaque constructeur, cadran, étiquette, numéro de série).
    """
    blocks = []
    for listing in listings:
        head = (
            f"[{listing.id}] {listing.deal_score:.0f}/100 — "
            f"{_short(listing.title, 80)} — {_price(listing.price)}€ "
            f"(ref {_price(listing.reference_price)}€) — {listing.city}"
        )
        parts = [head]
        if listing.reasons:
            parts.append("  pourquoi: " + _short(" ; ".join(listing.reasons), 160))
        if listing.body:
            parts.append("  desc: " + _short(listing.body, 300))
        for url in listing.images[:max_images]:
            parts.append("  img: " + url)
        parts.append("  url: https://www.leboncoin.fr/ad/" + listing.id)
        blocks.append("\n".join(parts))
    return "\n\n".join(blocks)


def as_detail(listing) -> str:
    """Vue complète d'une annonce unique (`lbc show`)."""
    lines = [
        f"{listing.title}",
        f"prix   : {_price(listing.price)}€"
        + (f"  (ref {_price(listing.reference_price)}€)" if listing.reference_price else ""),
        f"score  : {listing.deal_score:.0f}/100",
        f"lieu   : {listing.city} {listing.zipcode}",
        f"vendeur: {listing.seller_type or '?'}   publié: {_age(listing)}",
        f"url    : https://www.leboncoin.fr/ad/{listing.id}",
    ]
    if listing.model:
        lines.append(f"modèle : {listing.model} (vu dans: {listing.model_source})")
    if listing.reasons:
        lines.append("pourquoi: " + " ; ".join(listing.reasons))
    if listing.attributes:
        attrs = ", ".join(f"{k}={v}" for k, v in list(listing.attributes.items())[:10])
        lines.append("attrs  : " + _short(attrs, 200))
    if listing.body:
        lines.append("\n" + _short(listing.body, 800))
    if listing.images:
        lines.append("\nimages:")
        lines.extend("  " + u for u in listing.images[:5])
    return "\n".join(lines)


def render(listings: list, fmt: str, top: int | None = None) -> str:
    subset = listings[:top] if top else listings
    if fmt == "jsonl":
        return as_jsonl(subset)
    if fmt == "ids":
        return as_ids(subset)
    if fmt == "vision":
        return as_vision(subset)
    return as_tsv(subset)


def summary_line(total: int, kept: int, domain_name: str = "", new_only: bool = False) -> str:
    """Une ligne de contexte, pour que l'agent sache ce qu'il regarde."""
    bits = [f"{total} annonces analysées", f"{kept} retenues"]
    if domain_name:
        bits.append(f"pack={domain_name}")
    if new_only:
        bits.append("nouveautés seulement")
    return "# " + ", ".join(bits)
