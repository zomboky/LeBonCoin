"""Accès réseau : API interne d'abord, navigateur en repli.

leboncoin est derrière DataDome. En pratique :

- une requête HTTP simple passe tant qu'on a un cookie `datadome` valide et des
  en-têtes crédibles — c'est le chemin rapide, et de loin le moins coûteux ;
- quand elle prend un 403, on ouvre Chromium, on laisse DataDome poser son
  cookie, on le récupère et on **repasse en HTTP**. Le navigateur ne sert qu'à
  débloquer, pas à scraper en continu.
- si l'API reste fermée, on lit le `__NEXT_DATA__` de la page de recherche, qui
  contient les mêmes objets annonce.

Les cookies sont persistés sur disque : un seul passage navigateur suffit
généralement pour toute une session de travail.

Note : depuis une IP de datacenter, DataDome bloque quoi qu'il arrive. Cet outil
est prévu pour tourner depuis une connexion résidentielle.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

import requests

from . import config

COOKIE_FILE = config.STATE_DIR / "cookies.json"

_NEXT_DATA_RX = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.DOTALL
)


class TransportError(RuntimeError):
    pass


class Blocked(TransportError):
    """DataDome a refusé la requête et le repli n'a rien donné."""


class Transport:
    def __init__(
        self,
        delay: float | None = None,
        use_browser: bool = True,
        verbose: bool = False,
    ):
        config.ensure_dirs()
        self.delay = config.DEFAULT_DELAY if delay is None else delay
        self.use_browser = use_browser
        self.verbose = verbose
        self.session = requests.Session()
        self.session.headers.update(config.api_headers())
        self._load_cookies()
        self._browser_tried = False
        self._last_request = 0.0

    # -- Cookies --------------------------------------------------------------

    def _load_cookies(self) -> None:
        if not COOKIE_FILE.exists():
            return
        try:
            data = json.loads(COOKIE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        for name, value in (data or {}).items():
            self.session.cookies.set(name, value, domain=".leboncoin.fr")

    def _save_cookies(self) -> None:
        try:
            data = {c.name: c.value for c in self.session.cookies}
            COOKIE_FILE.write_text(json.dumps(data), encoding="utf-8")
        except OSError:
            pass

    # -- Politesse ------------------------------------------------------------

    def _wait(self) -> None:
        if self.delay <= 0:
            return
        elapsed = time.time() - self._last_request
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)

    def _log(self, message: str) -> None:
        if self.verbose:
            print(f"golddigger02: {message}")

    # -- API ------------------------------------------------------------------

    def search(self, payload: dict, web_url: str | None = None) -> dict:
        """Renvoie le bloc de résultats brut (`{"total": N, "ads": [...]}`)."""
        try:
            return self._post(payload)
        except Blocked:
            if not self.use_browser:
                raise
            self._log("403 — passage par le navigateur pour débloquer")
            if self._unblock_with_browser(web_url):
                try:
                    return self._post(payload)
                except Blocked:
                    self._log("API toujours fermée après déblocage")
            if web_url:
                self._log("lecture directe de la page de recherche")
                return self._search_via_page(web_url)
            raise

    def _post(self, payload: dict) -> dict:
        self._wait()
        try:
            response = self.session.post(config.API_URL, json=payload, timeout=30)
        except requests.RequestException as exc:
            raise TransportError(f"réseau indisponible : {exc}") from exc
        finally:
            self._last_request = time.time()

        if response.status_code in (401, 403, 429):
            raise Blocked(f"HTTP {response.status_code} sur l'API")
        if response.status_code >= 400:
            raise TransportError(f"HTTP {response.status_code} sur l'API")

        self._save_cookies()
        try:
            return response.json()
        except ValueError as exc:
            raise TransportError("réponse API illisible (pas du JSON)") from exc

    def get_ad(self, ad_id: str) -> dict:
        """Récupère une annonce isolée par son identifiant."""
        url = f"https://api.leboncoin.fr/finder/classified/{ad_id}"
        self._wait()
        try:
            response = self.session.get(url, timeout=30)
        except requests.RequestException as exc:
            raise TransportError(f"réseau indisponible : {exc}") from exc
        finally:
            self._last_request = time.time()

        if response.status_code in (401, 403, 429):
            if self.use_browser and self._unblock_with_browser(
                f"{config.WEB_BASE}/ad/{ad_id}"
            ):
                response = self.session.get(url, timeout=30)
            if response.status_code in (401, 403, 429):
                raise Blocked(f"HTTP {response.status_code} sur l'annonce {ad_id}")
        if response.status_code == 404:
            raise TransportError(f"annonce {ad_id} introuvable (supprimée ?)")
        if response.status_code >= 400:
            raise TransportError(f"HTTP {response.status_code} sur l'annonce {ad_id}")

        self._save_cookies()
        try:
            data = response.json()
        except ValueError as exc:
            raise TransportError("réponse illisible pour cette annonce") from exc
        # Selon les versions, l'annonce est à la racine ou sous une enveloppe.
        return data.get("ad") if isinstance(data.get("ad"), dict) else data

    # -- Navigateur -----------------------------------------------------------

    def _launch(self, playwright):
        kwargs: dict[str, Any] = {
            "headless": True,
            "args": [
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
            ],
        }
        if config.CHROMIUM_PATH and Path(config.CHROMIUM_PATH).exists():
            kwargs["executable_path"] = config.CHROMIUM_PATH
        if config.PROXY:
            kwargs["proxy"] = {"server": config.PROXY}
        return playwright.chromium.launch(**kwargs)

    def _new_context(self, browser):
        return browser.new_context(
            locale="fr-FR",
            timezone_id="Europe/Paris",
            user_agent=config.USER_AGENT,
            viewport={"width": 1440, "height": 900},
        )

    def _unblock_with_browser(self, url: str | None) -> bool:
        """Ouvre une page pour récolter un cookie `datadome` frais."""
        if self._browser_tried:
            return False
        self._browser_tried = True
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self._log("playwright absent — `pip install playwright` pour le repli")
            return False

        target = url or (config.WEB_BASE + "/")
        try:
            with sync_playwright() as playwright:
                browser = self._launch(playwright)
                try:
                    context = self._new_context(browser)
                    page = context.new_page()
                    page.goto(target, wait_until="domcontentloaded", timeout=45000)
                    page.wait_for_timeout(2500)
                    harvested = 0
                    for cookie in context.cookies():
                        self.session.cookies.set(
                            cookie["name"], cookie["value"], domain=".leboncoin.fr"
                        )
                        harvested += 1
                    self._save_cookies()
                    self._log(f"{harvested} cookies récupérés")
                    return harvested > 0
                finally:
                    browser.close()
        except Exception as exc:  # playwright lève des types très variés
            self._log(f"navigateur indisponible : {str(exc)[:160]}")
            return False

    def _search_via_page(self, url: str) -> dict:
        """Dernier recours : extraire les annonces du `__NEXT_DATA__` de la page."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise Blocked("API bloquée et playwright absent") from exc

        try:
            with sync_playwright() as playwright:
                browser = self._launch(playwright)
                try:
                    page = self._new_context(browser).new_page()
                    page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    page.wait_for_timeout(2000)
                    html = page.content()
                finally:
                    browser.close()
        except Exception as exc:
            raise Blocked(f"navigateur en échec : {str(exc)[:160]}") from exc

        return extract_next_data(html)


def extract_next_data(html: str) -> dict:
    """Isole le bloc d'annonces du `__NEXT_DATA__` d'une page de recherche.

    Séparé de la classe pour être testable sur du HTML figé, sans navigateur.
    """
    match = _NEXT_DATA_RX.search(html or "")
    if not match:
        raise Blocked("page servie sans __NEXT_DATA__ (probable mur DataDome)")
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise TransportError("__NEXT_DATA__ illisible") from exc

    # Le chemin exact bouge d'une refonte à l'autre ; on cherche le premier
    # noeud qui ressemble à un bloc de résultats plutôt que de coder un chemin.
    found = _find_ads(data)
    if found is None:
        raise Blocked("aucune annonce trouvée dans __NEXT_DATA__")
    return found


def _find_ads(node: Any, depth: int = 0) -> dict | None:
    if depth > 8:
        return None
    if isinstance(node, dict):
        ads = node.get("ads")
        if isinstance(ads, list) and ads and isinstance(ads[0], dict):
            return {"total": node.get("total") or len(ads), "ads": ads}
        for value in node.values():
            found = _find_ads(value, depth + 1)
            if found:
                return found
    elif isinstance(node, list):
        for value in node[:20]:
            found = _find_ads(value, depth + 1)
            if found:
                return found
    return None
