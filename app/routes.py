"""
app/api/v1/search.py  —  Endpoint di ricerca con PostGIS + fuzzy matching
"""
import hashlib, json
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from app.core.database import get_db
from app.core.config_db_cache import get_redis, settings
from app.schemas.schemas import SearchParams, SearchResponse, OfferResult

router = APIRouter()

# ── SQL nativo con PostGIS + pg_trgm ─────────────────────────────────────────
# Usiamo SQL raw perché le funzioni PostGIS non hanno un mapping SQLAlchemy
# comodo senza geoalchemy2 expression API, e raw è più leggibile.

_SEARCH_SQL = text("""
    SELECT
        o.id,
        o.supermercato_id,
        s.catena,
        COALESCE(s.nome_punto_vendita, s.catena)  AS nome_punto_vendita,
        s.indirizzo,
        s.logo_url,
        o.nome_prodotto,
        o.marca,
        o.quantita,
        o.prezzo::float,
        o.prezzo_originale::float,
        o.categoria,
        o.data_fine,
        ROUND(
            (ST_Distance(
                s.location::geography,
                ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography
            ) / 1000)::numeric, 2
        )::float AS distanza_km
    FROM offerte o
    JOIN supermercati s ON s.id = o.supermercato_id
    WHERE
        o.nome_normalizzato %  :query
        AND o.data_fine        >= CURRENT_DATE
        AND s.attivo            = TRUE
        AND ST_DWithin(
            s.location::geography,
            ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography,
            :raggio
        )
    ORDER BY
        similarity(o.nome_normalizzato, :query) DESC,
        o.prezzo ASC,
        distanza_km ASC
    LIMIT  :limit
    OFFSET :offset
""")

_COUNT_SQL = text("""
    SELECT COUNT(*) FROM offerte o
    JOIN supermercati s ON s.id = o.supermercato_id
    WHERE
        o.nome_normalizzato % :query
        AND o.data_fine     >= CURRENT_DATE
        AND s.attivo         = TRUE
        AND ST_DWithin(
            s.location::geography,
            ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography,
            :raggio
        )
""")


@router.get("/search", response_model=SearchResponse, summary="Cerca prodotto nei supermercati vicini")
async def search_products(
    q:      str   = Query(..., min_length=2, max_length=100),
    lat:    float = Query(..., ge=-90,  le=90),
    lon:    float = Query(..., ge=-180, le=180),
    raggio: int   = Query(5000, ge=500, le=20000),
    limit:  int   = Query(50, ge=1, le=100),
    offset: int   = Query(0, ge=0),
    db:     AsyncSession = Depends(get_db),
):
    """
    Cerca un prodotto per nome e restituisce le offerte nei supermercati
    entro il raggio specificato, ordinate per rilevanza e prezzo crescente.

    - **q**: termine di ricerca (es: "gin gordons", "parmigiano reggiano")
    - **lat/lon**: coordinate GPS dell'utente
    - **raggio**: raggio di ricerca in metri (default 5km, max 20km)
    """
    # Normalizza la query come il DB (lowercase, senza accenti)
    import unicodedata, re
    q_norm = re.sub(r"[^a-z0-9\s]", " ",
        "".join(c for c in unicodedata.normalize("NFD", q.lower())
                if unicodedata.category(c) != "Mn")
    ).strip()

    # Cache Redis
    redis = await get_redis()
    cache_key = f"search:{hashlib.md5(f'{q_norm}{lat}{lon}{raggio}{limit}{offset}'.encode()).hexdigest()}"
    cached = await redis.get(cache_key)
    if cached:
        return SearchResponse(**json.loads(cached))

    # Imposta soglia similarity pg_trgm (default 0.3, abbassiamo per nomi corti)
    await db.execute(text("SET pg_trgm.similarity_threshold = 0.25"))

    params = dict(query=q_norm, lat=lat, lon=lon, raggio=raggio, limit=limit, offset=offset)

    rows  = (await db.execute(_SEARCH_SQL, params)).mappings().all()
    total = (await db.execute(_COUNT_SQL, {k: v for k, v in params.items()
                                            if k != "limit" and k != "offset"})).scalar()

    results = [OfferResult.model_validate(dict(r)) for r in rows]
    response = SearchResponse(total=total or 0, results=results, query=q, raggio=raggio)

    await redis.setex(cache_key, settings.CACHE_TTL_SEARCH, response.model_dump_json())
    return response


# =============================================================================
"""
app/api/v1/volantini.py  —  Trigger scraping manuale + lista volantini
"""
from fastapi import APIRouter, Depends, BackgroundTasks, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from app.core.database import get_db
# from app.models import Volantino  # importa dal tuo models.py
from app.schemas.schemas import VolantinoTriggerRequest, ScrapeJobResponse, VolantinoResponse

router_vol = APIRouter(prefix="/volantini")

CATENE_DEFAULT = ["esselunga", "conad", "lidl", "grand ete", "solo365", "pro7"]


@router_vol.post(
    "/trigger",
    response_model=ScrapeJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Avvia scraping manuale volantini",
)
async def trigger_scraping(
    body: VolantinoTriggerRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """
    Avvia il job di scraping in background.
    Risponde immediatamente con il task_id Celery.
    """
    from app.workers.tasks import scrape_catena_task  # import lazy per evitare circular

    catene = [body.catena] if body.catena else CATENE_DEFAULT
    task_ids = []

    for catena in catene:
        task = scrape_catena_task.delay(catena=catena, force=body.force)
        task_ids.append(task.id)

    return ScrapeJobResponse(
        task_id  = task_ids[0] if len(task_ids) == 1 else ",".join(task_ids),
        catene   = catene,
        message  = f"Avviato scraping per {len(catene)} catena/e",
    )


@router_vol.get("/", response_model=list[VolantinoResponse], summary="Lista volantini recenti")
async def list_volantini(
    limit:  int = 20,
    offset: int = 0,
    db:     AsyncSession = Depends(get_db),
):
    # Circular import risolto con import lazy del modello
    from app.models import Volantino
    result = await db.execute(
        select(Volantino).order_by(desc(Volantino.created_at)).limit(limit).offset(offset)
    )
    return result.scalars().all()


# =============================================================================
"""
app/api/v1/supermercati.py  —  CRUD supermercati
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from geoalchemy2.functions import ST_SetSRID, ST_MakePoint
import uuid

from app.core.database import get_db
from app.schemas.schemas import SupermercatoCreate, SupermercatoResponse

router_sup = APIRouter(prefix="/supermercati")


@router_sup.post(
    "/",
    response_model=SupermercatoResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Registra un nuovo punto vendita",
)
async def create_supermercato(
    body: SupermercatoCreate,
    db:   AsyncSession = Depends(get_db),
):
    from app.models import Supermercato
    sup = Supermercato(
        catena            = body.catena,
        nome_punto_vendita= body.nome_punto_vendita,
        indirizzo         = body.indirizzo,
        citta             = body.citta,
        cap               = body.cap,
        logo_url          = body.logo_url,
        location          = ST_SetSRID(ST_MakePoint(body.lon, body.lat), 4326),
    )
    db.add(sup)
    await db.flush()
    await db.refresh(sup)
    return sup


@router_sup.get(
    "/vicini",
    response_model=list[SupermercatoResponse],
    summary="Supermercati entro un raggio GPS",
)
async def supermercati_vicini(
    lat:    float = Query(..., ge=-90,  le=90),
    lon:    float = Query(..., ge=-180, le=180),
    raggio: int   = Query(5000, ge=500, le=20000),
    db:     AsyncSession = Depends(get_db),
):
    rows = await db.execute(text("""
        SELECT id, catena, nome_punto_vendita, indirizzo, citta, cap, logo_url, attivo
        FROM supermercati
        WHERE attivo = TRUE
          AND ST_DWithin(
              location::geography,
              ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography,
              :raggio
          )
        ORDER BY location::geography <-> ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography
        LIMIT 50
    """), dict(lat=lat, lon=lon, raggio=raggio))
    return rows.mappings().all()