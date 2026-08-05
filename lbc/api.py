"""Construction des requêtes, pagination, et normalisation en `Listing`.

C'est la frontière : au-dessus on parle de `SearchParams` et de `Listing`, en
dessous on parle du JSON de leboncoin. Rien du format brut ne doit fuir plus haut.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator
from urllib.parse import urlencode

from . import cache, config
from .models import Listing
from .taxonomy import resolve_category, resolve_location
from .transport import Blocked, Transport, TransportError

SORTS = {
    "date": ("time", "desc"),
    "recent": ("time", "desc"),
    "price": ("price", "asc"),
    "price_desc": ("price", "desc"),
    "relevance": ("relevance", "desc"),
}


@dataclass
class SearchParams:
    text: str = ""
    category: str | None = None
    min_price: int | None = None
    max_price: int | None = None
    where: str | None = None
    radius_km: int | None = None
    sort: str = "date"
    seller: str | None = None      # "private" | "pro"
    pages: int = 2
    page_size: int = config.PAGE_SIZE

    # Rempli à la construction du payload, utile pour le repli navigateur.
    _web_url: str = field(default="", repr=False)

    def filters(self) -> dict:
        filters: dict = {"enums": {"ad_type": ["offer"]}}

        if self.text:
            filters["keywords"] = {"text": self.text}

        category_id = resolve_category(self.category)
        if category_id and category_id != "0":
            filters["category"] = {"id": category_id}

        ranges: dict = {}
        price: dict = {}
        if self.min_price is not None:
            price["min"] = int(self.min_price)
        if self.max_price is not None:
            price["max"] = int(self.max_price)
        if price:
            ranges["price"] = price
        if ranges:
            filters["ranges"] = ranges

        location = resolve_location(self.where)
        if location:
            if self.radius_km:
                location = dict(location, radius=int(self.radius_km) * 1000)
            filters["location"] = {"locations": [location]}

        if self.seller in ("private", "pro"):
            filters["owner_type"] = self.seller

        return filters

    def payload(self, offset: int = 0) -> dict:
        sort_by, sort_order = SORTS.get(self.sort, SORTS["date"])
        return {
            "filters": self.filters(),
            "limit": int(self.page_size),
            "limit_alu": 3,
            "offset": int(offset),
            "sort_by": sort_by,
            "sort_order": sort_order,
        }

    def web_url(self, page: int = 1) -> str:
        """URL de la page de recherche équivalente, pour le repli navigateur."""
        query: dict[str, str] = {}
        if self.text:
            query["text"] = self.text
        category_id = resolve_category(self.category)
        if category_id and category_id != "0":
            query["category"] = category_id
        if self.min_price is not None or self.max_price is not None:
            low = self.min_price if self.min_price is not None else "min"
            high = self.max_price if self.max_price is not None else "max"
            query["price"] = f"{low}-{high}"
        location = resolve_location(self.where)
        if location:
            key = location.get("department_id") or location.get("region_id")
            if key:
                query["locations"] = str(key)
        if self.seller in ("private", "pro"):
            query["owner_type"] = self.seller
        if page > 1:
            query["page"] = str(page)
        sort_by, sort_order = SORTS.get(self.sort, SORTS["date"])
        query["sort"] = sort_by
        query["order"] = sort_order
        return f"{config.WEB_BASE}{config.SEARCH_PATH}?{urlencode(query)}"


def fetch(
    params: SearchParams,
    transport: Transport | None = None,
    store: "cache.Store | None" = None,
    use_cache: bool = True,
) -> list[Listing]:
    """Récupère `params.pages` pages et renvoie des annonces dédupliquées."""
    transport = transport or Transport()
    listings: list[Listing] = []
    seen_ids: set[str] = set()

    for page in range(int(params.pages)):
        offset = page * params.page_size
        payload = params.payload(offset)

        block = None
        key = cache.cache_key("search", payload)
        if use_cache and store is not None:
            block = store.get_response(key)

        if block is None:
            try:
                block = transport.search(payload, web_url=params.web_url(page + 1))
            except Blocked as exc:
                # Sur la première page, c'est fatal ; ensuite on garde l'acquis.
                if page == 0:
                    raise
                print(f"lbc: arrêt à la page {page + 1} — {exc}")
                break
            except TransportError as exc:
                if page == 0:
                    raise
                print(f"lbc: arrêt à la page {page + 1} — {exc}")
                break
            if store is not None:
                store.put_response(key, block)

        ads = block.get("ads") or []
        if not ads:
            break

        for raw in ads:
            if not isinstance(raw, dict):
                continue
            listing = Listing.from_raw(raw)
            if listing.id and listing.id not in seen_ids:
                seen_ids.add(listing.id)
                listings.append(listing)

        # Dernière page atteinte.
        if len(ads) < params.page_size:
            break

    return listings


def fetch_many(
    queries: list[str],
    base: SearchParams,
    transport: Transport | None = None,
    store: "cache.Store | None" = None,
    use_cache: bool = True,
    on_query=None,
) -> list[Listing]:
    """Lance plusieurs requêtes-sources (cas d'un pack) et fusionne le résultat."""
    transport = transport or Transport()
    merged: dict[str, Listing] = {}
    for query in queries:
        params = SearchParams(**{**base.__dict__, "text": query})
        params._web_url = ""
        if on_query:
            on_query(query)
        try:
            for listing in fetch(params, transport, store, use_cache):
                merged.setdefault(listing.id, listing)
        except (Blocked, TransportError) as exc:
            print(f"lbc: requête « {query} » abandonnée — {exc}")
            continue
    return list(merged.values())


def iter_pages(params: SearchParams) -> Iterator[dict]:
    """Payloads page par page — pratique pour inspecter ce qui part réellement."""
    for page in range(int(params.pages)):
        yield params.payload(page * params.page_size)
