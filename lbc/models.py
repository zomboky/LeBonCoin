"""Le type `Listing` et sa normalisation depuis les payloads bruts.

L'API interne et le `__NEXT_DATA__` de la page web servent le même objet annonce,
donc `Listing.from_raw` est le point d'entrée unique des deux transports. Tout est
lu défensivement : quand leboncoin renomme un champ, on veut une annonce
incomplète, pas une exception.
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from typing import Any

_DATE_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ")


def _parse_date(value: Any) -> _dt.datetime | None:
    if not isinstance(value, str):
        return None
    raw = value.strip()
    for fmt in _DATE_FORMATS:
        try:
            parsed = _dt.datetime.strptime(raw, fmt)
        except ValueError:
            continue
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=_dt.timezone.utc)
    return None


def _parse_price(value: Any) -> int | None:
    """Le prix arrive tantôt en liste `[120]`, tantôt en scalaire, tantôt en texte."""
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value) if value > 0 else None
    if isinstance(value, str):
        digits = re.sub(r"[^\d]", "", value)
        if digits:
            price = int(digits)
            return price if price > 0 else None
    return None


@dataclass
class Listing:
    """Une annonce, normalisée. Les champs `deal_*` sont remplis par score.py."""

    id: str
    title: str = ""
    body: str = ""
    price: int | None = None
    url: str = ""
    category_id: str = ""
    city: str = ""
    zipcode: str = ""
    department: str = ""
    region: str = ""
    seller_type: str = ""           # "private" | "pro" | ""
    published: _dt.datetime | None = None
    images: list[str] = field(default_factory=list)
    nb_images: int = 0
    attributes: dict[str, str] = field(default_factory=dict)

    # Renseignés par extract.py
    brand: str = ""
    model: str = ""
    model_source: str = ""          # "title" | "body" | "attributes" | ""

    # Renseignés par pricing.py / score.py
    cohort_key: str = ""
    cohort_size: int = 0
    reference_price: float | None = None
    reference_source: str = ""      # "cohort" | "band" | ""
    deal_score: float = 0.0
    signals: dict[str, float] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)

    # -- Construction ---------------------------------------------------------

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "Listing":
        ad_id = str(raw.get("list_id") or raw.get("ad_id") or raw.get("id") or "")

        images_block = raw.get("images") or {}
        urls = (
            images_block.get("urls_large")
            or images_block.get("urls")
            or images_block.get("urls_thumb")
            or []
        )
        if isinstance(urls, str):
            urls = [urls]
        images = [u for u in urls if isinstance(u, str)]

        attributes: dict[str, str] = {}
        for attr in raw.get("attributes") or []:
            if not isinstance(attr, dict):
                continue
            key = attr.get("key") or attr.get("key_label")
            if not key:
                continue
            attributes[str(key)] = str(
                attr.get("value_label") or attr.get("value") or ""
            )

        location = raw.get("location") or {}
        owner = raw.get("owner") or {}

        url = raw.get("url") or ""
        if url and not url.startswith("http"):
            url = "https://www.leboncoin.fr" + url
        if not url and ad_id:
            url = f"https://www.leboncoin.fr/ad/{ad_id}"

        return cls(
            id=ad_id,
            title=(raw.get("subject") or raw.get("title") or "").strip(),
            body=(raw.get("body") or raw.get("description") or "").strip(),
            price=_parse_price(raw.get("price")),
            url=url,
            category_id=str(raw.get("category_id") or ""),
            city=str(location.get("city") or ""),
            zipcode=str(location.get("zipcode") or ""),
            department=str(
                location.get("department_name") or location.get("department_id") or ""
            ),
            region=str(location.get("region_name") or ""),
            seller_type=str(owner.get("type") or raw.get("ad_type") or ""),
            published=_parse_date(
                raw.get("first_publication_date") or raw.get("index_date")
            ),
            images=images,
            nb_images=int(images_block.get("nb_images") or len(images) or 0),
            attributes=attributes,
        )

    # -- Commodités -----------------------------------------------------------

    @property
    def text(self) -> str:
        """Titre + description + attributs, pour l'extraction de modèle."""
        parts = [self.title, self.body, *self.attributes.values()]
        return " ".join(p for p in parts if p)

    @property
    def age_hours(self) -> float | None:
        if self.published is None:
            return None
        now = _dt.datetime.now(_dt.timezone.utc)
        return max(0.0, (now - self.published).total_seconds() / 3600.0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "price": self.price,
            "url": self.url,
            "city": self.city,
            "zipcode": self.zipcode,
            "seller_type": self.seller_type,
            "published": self.published.isoformat() if self.published else None,
            "nb_images": self.nb_images,
            "images": self.images,
            "brand": self.brand,
            "model": self.model,
            "model_source": self.model_source,
            "cohort_key": self.cohort_key,
            "cohort_size": self.cohort_size,
            "reference_price": self.reference_price,
            "deal_score": round(self.deal_score, 1),
            "signals": {k: round(v, 3) for k, v in self.signals.items()},
            "reasons": self.reasons,
        }
