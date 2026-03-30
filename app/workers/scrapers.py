"""
PrezzoVicinato — Step 6: Celery Workers + Scrapers
Catene supportate: Esselunga, Conad, Lidl, Grand'Etè, Solo365, Pro7

Dipendenze:
    pip install celery[redis] playwright httpx beautifulsoup4
                pypdf2 pillow tenacity
    playwright install chromium
"""

# =============================================================================
#  app/workers/celery_app.py  —  Configurazione Celery
# =============================================================================
from celery import Celery
from celery.schedules import crontab
from app.core.config_db_cache import settings

celery_app = Celery(
    "prezzovicinato",
    broker    = settings.REDIS_URL,
    backend   = settings.REDIS_URL,
    include   = ["app.workers.tasks"],
)

celery_app.conf.update(
    task_serializer        = "json",
    result_serializer      = "json",
    accept_content         = ["json"],
    timezone               = "Europe/Rome",
    enable_utc             = True,
    task_track_started     = True,
    task_acks_late         = True,          # riprova se il worker crasha
    worker_prefetch_multiplier = 1,         # un task alla volta per worker (OCR è pesante)
    task_routes = {
        "app.workers.tasks.scrape_catena_task": {"queue": "scraping"},
        "app.workers.tasks.parse_volantino_task": {"queue": "parsing"},
    },
)

# ── Scheduler automatico (Celery Beat) ────────────────────────────────────────
celery_app.conf.beat_schedule = {
    # Ogni giovedì alle 02:00 — i volantini italiani partono il giovedì
    "scrape-settimanale": {
        "task":     "app.workers.tasks.scrape_tutte_le_catene",
        "schedule": crontab(hour=2, minute=0, day_of_week=4),
    },
    # Ogni giorno alle 06:00 — pulizia offerte scadute
    "pulizia-offerte-scadute": {
        "task":     "app.workers.tasks.cleanup_offerte_scadute",
        "schedule": crontab(hour=6, minute=0),
    },
}


# =============================================================================
#  app/workers/base_scraper.py  —  Classe base per tutti gli scraper
# =============================================================================
import abc
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

import httpx
from playwright.async_api import async_playwright, Browser, Page

logger = logging.getLogger(__name__)

DOWNLOAD_DIR = Path("/tmp/prezzovicinato/volantini")
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class VolantinoRaw:
    catena:        str
    url_originale: str
    data_inizio:   date
    data_fine:     date
    pdf_path:      Optional[Path] = None
    raw_images:    list[Path]     = field(default_factory=list)
    raw_text:      str            = ""


class BaseScraper(abc.ABC):
    """
    Classe base per tutti gli scraper di catene GDO.
    Ogni catena eredita questa classe e implementa `fetch_volantino_url`
    e (opzionalmente) `parse_date_range`.
    """
    CATENA: str = ""
    BASE_URL: str = ""

    def __init__(self):
        self._browser: Optional[Browser] = None

    async def __aenter__(self):
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        return self

    async def __aexit__(self, *_):
        if self._browser:
            await self._browser.close()
        await self._pw.stop()

    @abc.abstractmethod
    async def fetch_volantino_info(self) -> list[VolantinoRaw]:
        """Recupera URL e date dei volantini attivi. Deve restituire VolantinoRaw senza contenuto."""

    async def download_pdf(self, url: str, filename: str) -> Path:
        """Scarica un PDF e lo salva localmente (o su S3 in produzione)."""
        dest = DOWNLOAD_DIR / filename
        if dest.exists():
            return dest
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            dest.write_bytes(resp.content)
        return dest

    async def new_page(self) -> Page:
        ctx = await self._browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 900},
            locale="it-IT",
        )
        page = await ctx.new_page()
        await page.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
        )
        return page


# =============================================================================
#  SCRAPER ESSELUNGA
# =============================================================================
from bs4 import BeautifulSoup
import re
from datetime import datetime


class EsselungaScraper(BaseScraper):
    CATENA   = "Esselunga"
    BASE_URL = "https://www.esselunga.it/area-pubblica/negozi-e-volantini/volantino.html"

    async def fetch_volantino_info(self) -> list[VolantinoRaw]:
        page = await self.new_page()
        try:
            await page.goto(self.BASE_URL, wait_until="networkidle", timeout=30000)
            await page.wait_for_selector("[class*='volantino']", timeout=10000)
            content = await page.content()
        finally:
            await page.close()

        soup   = BeautifulSoup(content, "html.parser")
        result = []

        # Cerca i link PDF dei volantini nella pagina
        for link in soup.find_all("a", href=re.compile(r"\.pdf", re.I)):
            href = link.get("href", "")
            if not href.startswith("http"):
                href = "https://www.esselunga.it" + href

            # Estrai date dal testo vicino al link
            parent_text = link.parent.get_text(" ", strip=True) if link.parent else ""
            d_inizio, d_fine = self._parse_date_range_it(parent_text)

            result.append(VolantinoRaw(
                catena        = self.CATENA,
                url_originale = href,
                data_inizio   = d_inizio,
                data_fine     = d_fine,
            ))

        logger.info(f"[Esselunga] Trovati {len(result)} volantini")
        return result

    def _parse_date_range_it(self, text: str) -> tuple[date, date]:
        """Estrae 'dal GG/MM al GG/MM/YYYY' o pattern simili."""
        oggi   = date.today()
        # Pattern: "dal 14 al 20 luglio 2025" o "14/07 - 20/07/2025"
        mesi = {"gennaio":1,"febbraio":2,"marzo":3,"aprile":4,"maggio":5,"giugno":6,
                "luglio":7,"agosto":8,"settembre":9,"ottobre":10,"novembre":11,"dicembre":12}

        m = re.search(
            r"dal\s+(\d{1,2})\s+al\s+(\d{1,2})\s+(\w+)\s*(\d{4})?",
            text, re.I
        )
        if m:
            anno   = int(m.group(4)) if m.group(4) else oggi.year
            mese   = mesi.get(m.group(3).lower(), oggi.month)
            return date(anno, mese, int(m.group(1))), date(anno, mese, int(m.group(2)))

        m2 = re.search(r"(\d{1,2})[/.-](\d{1,2})[/.-]?(\d{2,4})?\s*[-–]\s*(\d{1,2})[/.-](\d{1,2})[/.-]?(\d{2,4})?", text)
        if m2:
            anno_fine  = int(m2.group(6) or oggi.year)
            if anno_fine < 100: anno_fine += 2000
            return (
                date(anno_fine, int(m2.group(2)), int(m2.group(1))),
                date(anno_fine, int(m2.group(5)), int(m2.group(4))),
            )

        # Fallback: settimana corrente
        from datetime import timedelta
        return oggi, oggi + timedelta(days=6)


# =============================================================================
#  SCRAPER CONAD
# =============================================================================
class ConadScraper(BaseScraper):
    CATENA   = "Conad"
    BASE_URL = "https://www.conad.it/promozioni/volantini.html"

    async def fetch_volantino_info(self) -> list[VolantinoRaw]:
        page = await self.new_page()
        try:
            await page.goto(self.BASE_URL, wait_until="networkidle", timeout=30000)
            # Conad usa un widget JS — aspetta che si carichi
            await page.wait_for_selector(".flyer-list, .volantino-card", timeout=15000)

            # Intercetta chiamate XHR per ottenere PDF direttamente
            pdf_urls = await page.evaluate("""
                () => Array.from(document.querySelectorAll('a[href*=".pdf"], a[href*="volantino"]'))
                          .map(a => a.href)
                          .filter(h => h.length > 10)
            """)
            content = await page.content()
        finally:
            await page.close()

        soup   = BeautifulSoup(content, "html.parser")
        result = []
        oggi   = date.today()
        from datetime import timedelta

        for url in pdf_urls[:3]:  # Limitiamo a 3 volantini attivi
            result.append(VolantinoRaw(
                catena        = self.CATENA,
                url_originale = url,
                data_inizio   = oggi,
                data_fine     = oggi + timedelta(days=6),
            ))

        logger.info(f"[Conad] Trovati {len(result)} volantini")
        return result


# =============================================================================
#  SCRAPER LIDL  (usa API pubblica JSON)
# =============================================================================
class LidlScraper(BaseScraper):
    CATENA    = "Lidl"
    API_URL   = "https://www.lidl.it/api/dynamic/proxy?url=https://www.lidl.it/api/folder/category/51"

    async def fetch_volantino_info(self) -> list[VolantinoRaw]:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            # Lidl espone le offerte come API REST pubblica
            resp = await client.get(
                "https://www.lidl.it/api/dynamic/proxy",
                params={"url": "https://api.lidl.it/it/it/folders"},
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Accept":     "application/json",
                },
            )

        result  = []
        oggi    = date.today()
        from datetime import timedelta

        try:
            data = resp.json()
            folders = data.get("grids", [{}])[0].get("folders", [])
        except Exception:
            folders = []

        for folder in folders[:2]:
            start_str = folder.get("StartDate", "")
            end_str   = folder.get("EndDate",   "")
            try:
                d_i = date.fromisoformat(start_str[:10])
                d_f = date.fromisoformat(end_str[:10])
            except Exception:
                d_i, d_f = oggi, oggi + timedelta(days=6)

            pdf_url = folder.get("PdfUri", "") or folder.get("ThumbUri", "")
            result.append(VolantinoRaw(
                catena        = self.CATENA,
                url_originale = pdf_url,
                data_inizio   = d_i,
                data_fine     = d_f,
            ))

        logger.info(f"[Lidl] Trovati {len(result)} volantini")
        return result


# =============================================================================
#  SCRAPER GRAND'ETÈ
# =============================================================================
class GrandEteScraper(BaseScraper):
    CATENA   = "Grand'Etè"
    BASE_URL = "https://www.grandete.it/volantino"

    async def fetch_volantino_info(self) -> list[VolantinoRaw]:
        page = await self.new_page()
        try:
            await page.goto(self.BASE_URL, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(2)  # JS render

            # Grand'Etè mostra il volantino come immagini slideshow
            # Intercettiamo le URL delle immagini pagina
            img_urls = await page.evaluate("""
                () => Array.from(document.querySelectorAll('img[src*="volantino"], img[data-src*="volantino"]'))
                          .map(img => img.src || img.dataset.src)
                          .filter(s => s && s.startsWith('http'))
            """)

            # Cerca range date nel testo della pagina
            body_text = await page.inner_text("body")
        finally:
            await page.close()

        oggi = date.today()
        from datetime import timedelta

        # Estrai date con regex
        mesi_it = {"gen":1,"feb":2,"mar":3,"apr":4,"mag":5,"giu":6,
                   "lug":7,"ago":8,"set":9,"ott":10,"nov":11,"dic":12}
        d_i, d_f = oggi, oggi + timedelta(days=6)
        m = re.search(r"(\d{1,2})\s+(\w{3}).*?(\d{1,2})\s+(\w{3})\s+(\d{4})", body_text, re.I)
        if m:
            try:
                anno  = int(m.group(5))
                mese_i = mesi_it.get(m.group(2).lower()[:3], oggi.month)
                mese_f = mesi_it.get(m.group(4).lower()[:3], oggi.month)
                d_i = date(anno, mese_i, int(m.group(1)))
                d_f = date(anno, mese_f, int(m.group(3)))
            except Exception:
                pass

        result = []
        if img_urls:
            # Ogni pagina è un'immagine: le raggruppiamo in un unico VolantinoRaw
            result.append(VolantinoRaw(
                catena        = self.CATENA,
                url_originale = self.BASE_URL,
                data_inizio   = d_i,
                data_fine     = d_f,
                raw_images    = img_urls[:30],  # max 30 pagine
            ))
        logger.info(f"[Grand'Etè] Trovate {len(img_urls)} pagine immagine")
        return result


# =============================================================================
#  SCRAPER SOLO365
# =============================================================================
class Solo365Scraper(BaseScraper):
    CATENA   = "Solo365"
    BASE_URL = "https://www.solo365.it/volantino"

    async def fetch_volantino_info(self) -> list[VolantinoRaw]:
        page = await self.new_page()
        try:
            await page.goto(self.BASE_URL, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(2)

            # Solo365 carica il volantino tramite iframe o widget esterno (es. Publitalia)
            # Intercettiamo le richieste di rete per trovare PDF/immagini
            pdf_links = await page.evaluate("""
                () => {
                    const links = [];
                    document.querySelectorAll('a[href], iframe[src]').forEach(el => {
                        const url = el.href || el.src || '';
                        if (url.match(/\\.pdf|volantino|flyer/i)) links.push(url);
                    });
                    return links;
                }
            """)

            body_text  = await page.inner_text("body")
            screenshot = await page.screenshot(full_page=False)  # fallback
        finally:
            await page.close()

        oggi = date.today()
        from datetime import timedelta
        d_i, d_f = self._estrai_date(body_text, oggi)

        result = []
        for url in (pdf_links or [self.BASE_URL])[:2]:
            result.append(VolantinoRaw(
                catena        = self.CATENA,
                url_originale = url,
                data_inizio   = d_i,
                data_fine     = d_f,
            ))

        logger.info(f"[Solo365] Trovati {len(result)} volantini")
        return result

    def _estrai_date(self, text: str, oggi: date) -> tuple[date, date]:
        from datetime import timedelta
        m = re.search(r"(\d{1,2})[/.](\d{1,2})[/.]?(\d{4})?\s*[-–al]+\s*(\d{1,2})[/.](\d{1,2})[/.]?(\d{4})?", text)
        if m:
            try:
                anno = int(m.group(6) or m.group(3) or oggi.year)
                if anno < 100: anno += 2000
                return (
                    date(anno, int(m.group(2)), int(m.group(1))),
                    date(anno, int(m.group(5)), int(m.group(4))),
                )
            except Exception:
                pass
        return oggi, oggi + timedelta(days=6)


# =============================================================================
#  SCRAPER PRO7 (Gruppo VéGé)
# =============================================================================
class Pro7Scraper(BaseScraper):
    CATENA   = "Pro7"
    # Pro7 usa spesso il circuito Tiendeo / VolantinoFacile per i volantini digitali
    TIENDEO_URL = "https://www.tiendeo.it/Negozi/italia/pro7"

    async def fetch_volantino_info(self) -> list[VolantinoRaw]:
        # Pro7 non ha sempre un sito unico nazionale — usiamo Tiendeo come aggregatore
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            resp = await client.get(
                "https://www.tiendeo.it/api/v2/catalogs",
                params={"countryCode": "it", "chain": "pro7", "limit": 3},
                headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
            )

        result = []
        oggi   = date.today()
        from datetime import timedelta

        try:
            catalogs = resp.json().get("data", [])
        except Exception:
            catalogs = []

        for cat in catalogs:
            try:
                d_i = date.fromisoformat(cat.get("valid_from", "")[:10])
                d_f = date.fromisoformat(cat.get("valid_to",   "")[:10])
            except Exception:
                d_i, d_f = oggi, oggi + timedelta(days=6)

            pdf_url = cat.get("pdf_url") or cat.get("thumbnail") or ""
            result.append(VolantinoRaw(
                catena        = self.CATENA,
                url_originale = pdf_url,
                data_inizio   = d_i,
                data_fine     = d_f,
            ))

        # Fallback: scraping diretto sito locale Pro7
        if not result:
            result = await self._fallback_scraping()

        logger.info(f"[Pro7] Trovati {len(result)} volantini")
        return result

    async def _fallback_scraping(self) -> list[VolantinoRaw]:
        """Scraping diretto del sito Pro7 se l'API Tiendeo non risponde."""
        page = await self.new_page()
        try:
            await page.goto("https://www.pro7supermercati.it/volantino",
                            wait_until="networkidle", timeout=25000)
            await asyncio.sleep(2)
            imgs = await page.evaluate("""
                () => Array.from(document.images)
                      .map(i=>i.src)
                      .filter(s=>s.includes('volantino') || s.includes('offerta'))
            """)
            body = await page.inner_text("body")
        except Exception as e:
            logger.warning(f"[Pro7] Fallback scraping fallito: {e}")
            return []
        finally:
            await page.close()

        oggi = date.today()
        from datetime import timedelta
        return [VolantinoRaw(
            catena        = self.CATENA,
            url_originale = "https://www.pro7supermercati.it/volantino",
            data_inizio   = oggi,
            data_fine     = oggi + timedelta(days=6),
            raw_images    = imgs[:20],
        )]


# =============================================================================
#  REGISTRY degli scraper — aggiungi nuove catene qui
# =============================================================================
SCRAPER_REGISTRY: dict[str, type[BaseScraper]] = {
    "esselunga": EsselungaScraper,
    "conad":     ConadScraper,
    "lidl":      LidlScraper,
    "grand ete": GrandEteScraper,
    "solo365":   Solo365Scraper,
    "pro7":      Pro7Scraper,
}