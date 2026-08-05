"""Chargement et validation des packs thématiques.

Un pack est un YAML décrivant un terrain de chasse : où chercher, quelles marques
comptent, comment reconnaître une pièce d'origine d'une reproduction, et quelles
fourchettes de prix font foi quand il n'y a pas assez de comparables.

Ajouter un domaine = déposer un `.yml` dans `golddigger02/domains/`. Aucun code à toucher.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import config
from .taxonomy import resolve_category, slug

DOMAIN_DIR = Path(__file__).resolve().parent / "domains"


@dataclass
class ValueBand:
    """Fourchette de prix de référence pour un motif donné."""

    match: str
    low: float
    high: float
    _rx: re.Pattern | None = field(default=None, repr=False)

    def matches(self, text: str) -> bool:
        if self._rx is None:
            self._rx = re.compile(self.match, re.IGNORECASE)
        return bool(self._rx.search(text))

    @property
    def reference(self) -> float:
        """Le point de référence : le bas de fourchette majoré, pas la moyenne.

        On vise le prix d'une pièce correcte, pas d'une pièce exceptionnelle ;
        prendre la moyenne ferait passer pour pépite toute annonce ordinaire.
        """
        return self.low + (self.high - self.low) * 0.35


@dataclass
class ModelRule:
    pattern: str
    name: str
    tier: str = ""
    _rx: re.Pattern | None = field(default=None, repr=False)

    def search(self, text: str) -> bool:
        if self._rx is None:
            self._rx = re.compile(self.pattern, re.IGNORECASE)
        return bool(self._rx.search(text))


@dataclass
class Domain:
    name: str
    label: str = ""
    categories: list[str] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    brands: list[str] = field(default_factory=list)
    models: list[ModelRule] = field(default_factory=list)
    typos: dict[str, str] = field(default_factory=dict)
    origin_markers: list[str] = field(default_factory=list)
    repro_markers: list[str] = field(default_factory=list)
    value_bands: list[ValueBand] = field(default_factory=list)
    premium: list[str] = field(default_factory=list)

    @property
    def category_ids(self) -> list[str]:
        ids = [resolve_category(c) for c in self.categories]
        return [c for c in ids if c]

    def band_for(self, text: str) -> ValueBand | None:
        """Première fourchette dont le motif apparaît dans le texte.

        Les packs listent les motifs du plus spécifique au plus générique, donc
        le premier trouvé est le bon.
        """
        for band in self.value_bands:
            if band.matches(text):
                return band
        return None


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value if v is not None]


def _parse(data: dict, fallback_name: str) -> Domain:
    models = []
    for entry in data.get("models") or []:
        if isinstance(entry, dict) and entry.get("pattern"):
            models.append(
                ModelRule(
                    pattern=str(entry["pattern"]),
                    name=str(entry.get("name") or entry["pattern"]),
                    tier=str(entry.get("tier") or ""),
                )
            )
        elif isinstance(entry, str):
            models.append(ModelRule(pattern=re.escape(entry), name=entry))

    bands = []
    for entry in data.get("value_bands") or []:
        if not isinstance(entry, dict) or "match" not in entry:
            continue
        try:
            low = float(entry.get("low", 0))
            high = float(entry.get("high", 0))
        except (TypeError, ValueError):
            continue
        if high < low:
            low, high = high, low
        bands.append(ValueBand(match=str(entry["match"]), low=low, high=high))

    typos = {}
    for wrong, right in (data.get("typos") or {}).items():
        typos[str(wrong).lower()] = str(right)

    return Domain(
        name=slug(str(data.get("name") or fallback_name)),
        label=str(data.get("label") or data.get("name") or fallback_name),
        categories=_as_list(data.get("categories")),
        queries=_as_list(data.get("queries")),
        brands=_as_list(data.get("brands")),
        models=models,
        typos=typos,
        origin_markers=_as_list(data.get("origin_markers")),
        repro_markers=_as_list(data.get("repro_markers")),
        value_bands=bands,
        premium=_as_list(data.get("premium")),
    )


def load_domain(path: Path) -> Domain:
    with open(path, "r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} : le YAML doit être un mapping")
    return _parse(data, path.stem)


def _search_dirs() -> list[Path]:
    """Packs livrés, puis packs utilisateur — ces derniers l'emportent."""
    dirs = [DOMAIN_DIR]
    extra = Path(config.USER_DOMAIN_DIR)
    if extra != DOMAIN_DIR and extra.is_dir():
        dirs.append(extra)
    return dirs


def load_all() -> dict[str, Domain]:
    domains: dict[str, Domain] = {}
    for directory in _search_dirs():
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.y*ml")):
            try:
                domain = load_domain(path)
            except (yaml.YAMLError, ValueError, OSError) as exc:
                # Un pack cassé ne doit pas empêcher les autres de fonctionner.
                print(f"golddigger02: pack ignoré ({path.name}) : {exc}")
                continue
            domains[domain.name] = domain
    return domains


def get(name: str) -> Domain | None:
    if not name:
        return None
    return load_all().get(slug(name))


def merged(names: list[str]) -> Domain:
    """Fusionne plusieurs packs en un seul, pour chasser sur plusieurs terrains."""
    all_domains = load_all()
    picked = [all_domains[slug(n)] for n in names if slug(n) in all_domains]
    if len(picked) == 1:
        return picked[0]
    out = Domain(name="+".join(d.name for d in picked), label="Packs fusionnés")
    for d in picked:
        out.categories += [c for c in d.categories if c not in out.categories]
        out.queries += d.queries
        out.brands += [b for b in d.brands if b not in out.brands]
        out.models += d.models
        out.typos.update(d.typos)
        out.origin_markers += [m for m in d.origin_markers if m not in out.origin_markers]
        out.repro_markers += [m for m in d.repro_markers if m not in out.repro_markers]
        out.value_bands += d.value_bands
        out.premium += [p for p in d.premium if p not in out.premium]
    return out
