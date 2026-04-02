# app/main.py
# FastAPI MVP — un solo file, zero magia.
# Dipendenze: pip install fastapi uvicorn[standard] sqlalchemy psycopg2-binary geoalchemy2

from fastapi import FastAPI, Depends, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import text
from pydantic import BaseModel
from typing import Optional
from datetime import date
import uuid

from app.database import get_db, init_db

# ─── App ──────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="PrezzoRadar API",
    version="1.0.0-mvp",
    description="Cerca prezzi nei supermercati vicini a te",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # in produzione restringi ai tuoi domini
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
    """Crea tabelle e indici al primo avvio se non esistono già."""
    init_db()


# ─── Schema risposta (quello che Flutter si aspetta) ─────────────────────────
class OfferResult(BaseModel):
    id:                 str
    supermercato_id:    str
    catena:             str
    nome_punto_vendita: str
    indirizzo:          Optional[str]
    logo_url:           Optional[str]
    nome_prodotto:      str
    marca:              Optional[str]
    quantita:           Optional[str]
    prezzo:             float
    prezzo_originale:   Optional[float]
    sconto_percent:     Optional[int]
    distanza_km:        float
    data_fine:          Optional[str]   # ISO "YYYY-MM-DD" — più semplice per Flutter


class SearchResponse(BaseModel):
    query:   str
    total:   int
    raggio:  int
    results: list[OfferResult]


# ─── Endpoint /search ─────────────────────────────────────────────────────────
@app.get("/search", response_model=SearchResponse)
def search(
    q:      str   = Query(..., min_length=2, description="Termine di ricerca, es: 'Gin Gordon\\'s'"),
    lat:    float = Query(..., description="Latitudine GPS dell'utente"),
    lon:    float = Query(..., description="Longitudine GPS dell'utente"),
    raggio: int   = Query(5000, ge=500, le=1000000, description="Raggio in metri"),
    limit:  int   = Query(50,   ge=1,   le=100),
    db:     Session = Depends(get_db),
):
    """
    Cerca offerte attive per il termine `q` nei supermercati entro `raggio` metri
    dalla posizione (lat, lon), ordinate per similarità e poi prezzo crescente.
    """
    # Normalizzazione minimale della query (minuscolo + rimuovi accenti via unaccent)
    # unaccent è già abilitato nel DB — la funzione è disponibile in SQL
    q_clean = q.strip().lower()

    # ── Query SQL con PostGIS + pg_trgm ──────────────────────────────────────
    # Usiamo SQL nativo perché le funzioni geografiche PostGIS non hanno
    # un mapping ORM conveniente senza geoalchemy2 expression API.
    sql = text("""
        SELECT
            o.id::text                                          AS id,
            s.id::text                                          AS supermercato_id,
            s.catena,
            COALESCE(s.nome, s.catena)                          AS nome_punto_vendita,
            s.indirizzo,
            s.logo_url,
            o.nome_prodotto,
            o.marca,
            o.quantita,
            o.prezzo::float,
            o.prezzo_originale::float,
            o.data_fine::text,
            ROUND(
                (ST_Distance(
                    s.location::geography,
                    ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography
                ) / 1000.0)::numeric,
            2)::float                                           AS distanza_km
        FROM offerte o
        JOIN supermercati s ON s.id = o.supermercato_id
        WHERE
            -- Fuzzy match sul nome normalizzato (soglia 0.20 = permissiva)
            similarity(
                unaccent(o.nome_normalizzato),
                unaccent(:q)
            ) > 0.20
            -- Solo offerte ancora valide
            AND o.data_fine >= CURRENT_DATE
            -- Solo supermercati attivi
            AND s.attivo = TRUE
            -- Raggio geografico (opera in metri su sferoide)
            AND ST_DWithin(
                s.location::geography,
                ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography,
                :raggio
            )
        ORDER BY
            similarity(unaccent(o.nome_normalizzato), unaccent(:q)) DESC,
            o.prezzo ASC,
            distanza_km ASC
        LIMIT  :limit
    """)

    params = dict(q=q_clean, lat=lat, lon=lon, raggio=raggio, limit=limit)

    try:
        rows = db.execute(sql, params).mappings().all()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Errore DB: {e}")

    # ── Costruisci risultati con sconto calcolato ─────────────────────────────
    results = []
    for r in rows:
        sconto = None
        if r["prezzo_originale"] and r["prezzo_originale"] > r["prezzo"]:
            sconto = round(
                (r["prezzo_originale"] - r["prezzo"]) / r["prezzo_originale"] * 100
            )

        results.append(OfferResult(
            id                 = r["id"],
            supermercato_id    = r["supermercato_id"],
            catena             = r["catena"],
            nome_punto_vendita = r["nome_punto_vendita"],
            indirizzo          = r["indirizzo"],
            logo_url           = r["logo_url"],
            nome_prodotto      = r["nome_prodotto"],
            marca              = r["marca"],
            quantita           = r["quantita"],
            prezzo             = r["prezzo"],
            prezzo_originale   = r["prezzo_originale"],
            sconto_percent     = sconto,
            distanza_km        = r["distanza_km"],
            data_fine          = r["data_fine"],
        ))

    return SearchResponse(
        query   = q,
        total   = len(results),
        raggio  = raggio,
        results = results,
    )


# ─── Endpoint di salute (utile per testare che l'API è up) ───────────────────
@app.get("/health")
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        return {"status": "ok", "db": "connected"}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"DB non raggiungibile: {e}")


# ─── Avvio diretto (opzionale, utile per debug) ───────────────────────────────
# python app/main.py
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)