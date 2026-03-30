"""
app/workers/tasks.py  —  Task Celery: scraping → OCR → parsing → DB insert

Pipeline completa per ogni catena:
  1. Scraper  → scarica PDF/immagini
  2. OCR      → estrae testo grezzo
  3. LLM      → struttura le offerte in JSON
  4. DB       → inserisce/aggiorna su PostgreSQL
"""
import asyncio
import logging
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from celery import shared_task, chord, chain as celery_chain
from celery.utils.log import get_task_logger

from app.workers.celery_app import celery_app
from app.workers.scrapers import SCRAPER_REGISTRY, VolantinoRaw

logger = get_task_logger(__name__)


# =============================================================================
#  OCR ENGINE  —  PaddleOCR (locale) con fallback Google Vision
# =============================================================================

def ocr_pdf(pdf_path: Path, max_pages: int = 20) -> str:
    """
    Estrae testo da un PDF usando PaddleOCR.
    Prima prova estrazione diretta del testo (per PDF nativi),
    poi rasterizza e usa OCR (per PDF scansionati / immagine).
    """
    import fitz  # PyMuPDF
    doc  = fitz.open(str(pdf_path))
    testo_totale = []

    for i, page in enumerate(doc):
        if i >= max_pages:
            break
        # 1. Prova estrazione testo nativo
        testo = page.get_text("text").strip()
        if len(testo) > 50:
            testo_totale.append(testo)
        else:
            # 2. Rasterizza e applica OCR
            testo_totale.append(_ocr_pagina_immagine(page))

    doc.close()
    return "\n\n---PAGINA---\n\n".join(testo_totale)


def ocr_images(image_paths: list[str], max_images: int = 20) -> str:
    """Applica OCR a una lista di URL immagine (usato per Grand'Etè, Pro7)."""
    import httpx, io
    from PIL import Image

    testi = []
    for url in image_paths[:max_images]:
        try:
            data  = httpx.get(url, timeout=15).content
            img   = Image.open(io.BytesIO(data))
            testo = _ocr_immagine_pil(img)
            if testo.strip():
                testi.append(testo)
        except Exception as e:
            logger.warning(f"OCR immagine fallita ({url[:60]}): {e}")

    return "\n\n---PAGINA---\n\n".join(testi)


def _ocr_pagina_immagine(page) -> str:
    """Rasterizza una pagina fitz e applica PaddleOCR."""
    try:
        from paddleocr import PaddleOCR
        import numpy as np
        from PIL import Image
        import io

        _ocr_engine = _get_paddle_ocr()
        mat   = page.get_pixmap(dpi=200)
        img   = Image.open(io.BytesIO(mat.tobytes("png")))
        arr   = np.array(img)
        result = _ocr_engine.ocr(arr, cls=True)
        lines = [line[1][0] for block in (result or []) for line in block if line]
        return "\n".join(lines)
    except ImportError:
        return _ocr_with_google_vision(page)


def _ocr_immagine_pil(img) -> str:
    try:
        from paddleocr import PaddleOCR
        import numpy as np
        engine = _get_paddle_ocr()
        arr    = np.array(img)
        result = engine.ocr(arr, cls=True)
        lines  = [line[1][0] for block in (result or []) for line in block if line]
        return "\n".join(lines)
    except ImportError:
        return ""


_paddle_instance: Optional[object] = None

def _get_paddle_ocr():
    """Singleton PaddleOCR — il modello si carica una sola volta."""
    global _paddle_instance
    if _paddle_instance is None:
        from paddleocr import PaddleOCR
        _paddle_instance = PaddleOCR(use_angle_cls=True, lang="it", show_log=False)
    return _paddle_instance


def _ocr_with_google_vision(page) -> str:
    """Fallback: Google Vision API per pagine difficili."""
    import os, io, base64
    import httpx
    from PIL import Image

    api_key = os.getenv("GOOGLE_VISION_API_KEY", "")
    if not api_key:
        return ""

    mat = page.get_pixmap(dpi=200)
    img_bytes = mat.tobytes("png")
    b64 = base64.b64encode(img_bytes).decode()

    payload = {
        "requests": [{
            "image": {"content": b64},
            "features": [{"type": "DOCUMENT_TEXT_DETECTION"}],
            "imageContext": {"languageHints": ["it"]},
        }]
    }
    resp = httpx.post(
        f"https://vision.googleapis.com/v1/images:annotate?key={api_key}",
        json=payload, timeout=20
    )
    return resp.json().get("responses", [{}])[0].get("fullTextAnnotation", {}).get("text", "")


# =============================================================================
#  TASK CELERY
# =============================================================================

@celery_app.task(
    name      = "app.workers.tasks.scrape_catena_task",
    bind      = True,
    max_retries = 3,
    default_retry_delay = 120,
    queue     = "scraping",
)
def scrape_catena_task(self, catena: str, force: bool = False):
    """
    Task principale per una singola catena.
    Orchestrates: fetch → download → OCR → parse → insert
    """
    logger.info(f"[{catena.upper()}] Avvio pipeline scraping")
    catena_key = catena.lower().strip()

    scraper_class = SCRAPER_REGISTRY.get(catena_key)
    if not scraper_class:
        logger.error(f"Scraper non trovato per catena: {catena}")
        return {"error": f"Catena non supportata: {catena}"}

    try:
        # Esegui lo scraper asincrono in un event loop dedicato
        volantini_raw = asyncio.get_event_loop().run_until_complete(
            _run_scraper(scraper_class)
        )
    except Exception as exc:
        logger.error(f"[{catena.upper()}] Scraping fallito: {exc}")
        raise self.retry(exc=exc, countdown=120)

    risultati = []
    for vol_raw in volantini_raw:
        task_id = process_volantino_task.delay(
            catena        = vol_raw.catena,
            url_originale = vol_raw.url_originale,
            data_inizio   = vol_raw.data_inizio.isoformat(),
            data_fine     = vol_raw.data_fine.isoformat(),
            pdf_path      = str(vol_raw.pdf_path) if vol_raw.pdf_path else None,
            raw_images    = vol_raw.raw_images,
            force         = force,
        )
        risultati.append(str(task_id))

    logger.info(f"[{catena.upper()}] Avviati {len(risultati)} task di processing")
    return {"catena": catena, "volantini_avviati": len(risultati), "task_ids": risultati}


async def _run_scraper(scraper_class) -> list[VolantinoRaw]:
    async with scraper_class() as scraper:
        return await scraper.fetch_volantino_info()


@celery_app.task(
    name      = "app.workers.tasks.process_volantino_task",
    bind      = True,
    max_retries = 2,
    default_retry_delay = 60,
    queue     = "parsing",
    time_limit= 600,   # 10 minuti max per volantino
)
def process_volantino_task(
    self,
    catena:        str,
    url_originale: str,
    data_inizio:   str,
    data_fine:     str,
    pdf_path:      Optional[str] = None,
    raw_images:    list[str]     = None,
    force:         bool          = False,
):
    """
    Pipeline completa per un singolo volantino:
      1. Aggiorna stato → 'processing'
      2. OCR del PDF o delle immagini
      3. LLM parsing → lista offerte JSON
      4. Insert nel DB (upsert)
      5. Aggiorna stato → 'completed'
    """
    from app.core.database import AsyncSessionLocal
    from app.models import Volantino, Offerta, Supermercato
    from sqlalchemy import select

    d_inizio = date.fromisoformat(data_inizio)
    d_fine   = date.fromisoformat(data_fine)

    # ── Step 1: OCR ──────────────────────────────────────────────────────────
    logger.info(f"[{catena}] OCR in corso…")
    raw_text = ""

    if pdf_path and Path(pdf_path).exists():
        raw_text = ocr_pdf(Path(pdf_path))
    elif raw_images:
        raw_text = ocr_images(raw_images)
    else:
        # Scarica prima il PDF se non è stato salvato localmente
        try:
            import httpx
            pdf_bytes = httpx.get(url_originale, timeout=30, follow_redirects=True).content
            tmp_path  = Path(f"/tmp/prezzovicinato/{uuid.uuid4().hex}.pdf")
            tmp_path.write_bytes(pdf_bytes)
            raw_text  = ocr_pdf(tmp_path)
            tmp_path.unlink(missing_ok=True)
        except Exception as e:
            logger.error(f"[{catena}] Download PDF fallito: {e}")
            raw_text = ""

    if not raw_text.strip():
        logger.warning(f"[{catena}] OCR ha prodotto testo vuoto")
        return {"status": "skip", "reason": "empty_ocr"}

    logger.info(f"[{catena}] OCR completato: {len(raw_text)} caratteri")

    # ── Step 2: LLM Parsing ──────────────────────────────────────────────────
    logger.info(f"[{catena}] LLM parsing in corso…")
    try:
        from app.workers.parser import parse_volantino
        offerte_json = parse_volantino(
            raw_text    = raw_text,
            catena      = catena,
            data_inizio = d_inizio,
            data_fine   = d_fine,
        )
    except Exception as exc:
        logger.error(f"[{catena}] LLM parsing fallito: {exc}")
        raise self.retry(exc=exc, countdown=60)

    logger.info(f"[{catena}] Estratte {len(offerte_json)} offerte")

    # ── Step 3: Inserimento DB ───────────────────────────────────────────────
    inserted = asyncio.get_event_loop().run_until_complete(
        _insert_offerte(catena, url_originale, d_inizio, d_fine, raw_text, offerte_json)
    )

    return {
        "status":    "completed",
        "catena":    catena,
        "offerte":   inserted,
    }


async def _insert_offerte(
    catena: str,
    url_originale: str,
    d_inizio: date,
    d_fine: date,
    raw_text: str,
    offerte_json: list[dict],
) -> int:
    """Inserisce volantino e offerte nel DB in una singola transazione."""
    from app.core.database import AsyncSessionLocal
    from app.models import Volantino, Offerta, Supermercato
    from sqlalchemy import select

    async with AsyncSessionLocal() as session:
        async with session.begin():
            # Trova (o crea) il supermercato per questa catena
            # In produzione andresti a cercare il punto vendita specifico;
            # qui gestiamo il caso generico "catena nazionale"
            stmt = select(Supermercato).where(
                Supermercato.catena == catena,
                Supermercato.attivo == True,
            ).limit(1)
            result      = await session.execute(stmt)
            supermercato = result.scalar_one_or_none()

            if not supermercato:
                logger.warning(f"Nessun supermercato trovato per catena '{catena}' — skip insert")
                return 0

            # Crea record Volantino
            volantino = Volantino(
                supermercato_id = supermercato.id,
                data_inizio     = d_inizio,
                data_fine       = d_fine,
                url_originale   = url_originale,
                stato           = "completed",
                raw_text        = raw_text[:50_000],  # limita a 50k chars
            )
            session.add(volantino)
            await session.flush()  # ottieni l'id

            # Bulk insert offerte
            offerte_orm = [
                Offerta(
                    volantino_id      = volantino.id,
                    supermercato_id   = supermercato.id,
                    nome_prodotto     = o["nome_prodotto"],
                    marca             = o.get("marca"),
                    quantita          = o.get("quantita"),
                    prezzo            = o["prezzo"],
                    prezzo_originale  = o.get("prezzo_originale"),
                    categoria         = o.get("categoria"),
                    nome_normalizzato = o.get("nome_normalizzato"),
                    data_inizio       = date.fromisoformat(o["data_inizio"]),
                    data_fine         = date.fromisoformat(o["data_fine"]),
                )
                for o in offerte_json
            ]
            session.add_all(offerte_orm)

    return len(offerte_json)


# =============================================================================
#  TASK DI MANUTENZIONE
# =============================================================================

@celery_app.task(name="app.workers.tasks.scrape_tutte_le_catene")
def scrape_tutte_le_catene():
    """Avviato da Celery Beat ogni giovedì alle 02:00."""
    from app.workers.scrapers import SCRAPER_REGISTRY
    logger.info("▶ Avvio scraping settimanale per tutte le catene")
    for catena in SCRAPER_REGISTRY:
        scrape_catena_task.delay(catena=catena)
    logger.info(f"  Schedulati {len(SCRAPER_REGISTRY)} job")


@celery_app.task(name="app.workers.tasks.cleanup_offerte_scadute")
def cleanup_offerte_scadute():
    """Elimina offerte e volantini scaduti da più di 30 giorni."""
    async def _do_cleanup():
        from app.core.database import AsyncSessionLocal
        from sqlalchemy import text
        async with AsyncSessionLocal() as session:
            result = await session.execute(text("""
                WITH deleted_offerte AS (
                    DELETE FROM offerte
                    WHERE data_fine < CURRENT_DATE - INTERVAL '30 days'
                    RETURNING id
                ),
                deleted_volantini AS (
                    DELETE FROM volantini
                    WHERE data_fine < CURRENT_DATE - INTERVAL '30 days'
                    RETURNING id
                )
                SELECT
                    (SELECT COUNT(*) FROM deleted_offerte)  AS offerte_eliminate,
                    (SELECT COUNT(*) FROM deleted_volantini) AS volantini_eliminati
            """))
            row = result.one()
            await session.commit()
            return dict(row._mapping)

    stats = asyncio.get_event_loop().run_until_complete(_do_cleanup())
    logger.info(f"Cleanup completato: {stats}")
    return stats