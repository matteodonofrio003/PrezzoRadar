-- =============================================================
--  PrezzoVicinato — Database Schema
--  PostgreSQL 15+ con PostGIS 3 e pg_trgm
-- =============================================================

-- ── Estensioni ─────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS postgis;       -- georeferenziazione
CREATE EXTENSION IF NOT EXISTS pg_trgm;       -- fuzzy matching (LIKE veloci + similarity)
CREATE EXTENSION IF NOT EXISTS unaccent;      -- ricerca accent-insensitive
-- CREATE EXTENSION IF NOT EXISTS vector;     -- pgvector (opzionale, per semantic search)


-- ── Enum ───────────────────────────────────────────────────
CREATE TYPE stato_volantino AS ENUM (
    'pending',      -- scaricato, in attesa di OCR
    'processing',   -- OCR + LLM in corso
    'completed',    -- offerte estratte e inserite
    'failed'        -- errore nel parsing
);


-- =============================================================
--  SUPERMERCATI
--  Ogni riga = un punto vendita fisico, con coordinate GPS.
-- =============================================================
CREATE TABLE supermercati (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    catena              VARCHAR(100) NOT NULL,               -- "Esselunga", "Conad", "Lidl" …
    nome_punto_vendita  VARCHAR(200),                        -- "Esselunga Bari Murat"
    indirizzo           TEXT        NOT NULL,
    citta               VARCHAR(100) NOT NULL,
    cap                 VARCHAR(10),
    location            GEOMETRY(POINT, 4326),               -- lon/lat WGS84
    attivo              BOOLEAN     NOT NULL DEFAULT TRUE,
    logo_url            TEXT,                                -- CDN URL logo catena
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Indice spaziale GIST — obbligatorio per ST_DWithin veloce
CREATE INDEX idx_supermercati_location  ON supermercati USING GIST(location);
CREATE INDEX idx_supermercati_catena    ON supermercati(catena);
CREATE INDEX idx_supermercati_citta     ON supermercati(citta);


-- =============================================================
--  VOLANTINI
--  Ogni riga = un volantino promozionale scaricato (PDF/Immagine).
-- =============================================================
CREATE TABLE volantini (
    id              UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    supermercato_id UUID            NOT NULL
                        REFERENCES supermercati(id) ON DELETE CASCADE,
    data_inizio     DATE            NOT NULL,
    data_fine       DATE            NOT NULL,
    url_originale   TEXT,                       -- URL sorgente sul sito della catena
    url_pdf         TEXT,                       -- path su S3/R2 dove l'abbiamo salvato
    stato           stato_volantino NOT NULL DEFAULT 'pending',
    raw_text        TEXT,                       -- testo grezzo estratto dall'OCR
    pagine          SMALLINT,
    errore          TEXT,                       -- dettaglio errore se stato = 'failed'
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_volantino_date CHECK (data_fine >= data_inizio)
);

CREATE INDEX idx_volantini_supermercato ON volantini(supermercato_id);
-- Indice parziale: solo volantini completati con date future/correnti
CREATE INDEX idx_volantini_attivi ON volantini(data_inizio, data_fine)
    WHERE stato = 'completed';


-- =============================================================
--  OFFERTE
--  Ogni riga = un singolo prodotto in offerta estratto dal volantino.
-- =============================================================
CREATE TABLE offerte (
    id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    volantino_id      UUID        NOT NULL
                          REFERENCES volantini(id) ON DELETE CASCADE,
    supermercato_id   UUID        NOT NULL
                          REFERENCES supermercati(id) ON DELETE CASCADE,

    -- Dati estratti dall'LLM
    nome_prodotto     VARCHAR(300) NOT NULL,
    marca             VARCHAR(100),
    quantita          VARCHAR(50),             -- "1L", "500g", "conf. 6x33cl"
    prezzo            NUMERIC(8, 2) NOT NULL,
    prezzo_per_unita  NUMERIC(10, 4),          -- calcolato, per confronto omogeneo
    categoria         VARCHAR(100),            -- "Spirits", "Birra", "Latticini" …

    -- Normalizzazione per fuzzy matching
    nome_normalizzato VARCHAR(300),            -- es: "gin gordons 70cl"
    -- embedding       VECTOR(768),            -- pgvector (fase 2)

    data_inizio       DATE        NOT NULL,
    data_fine         DATE        NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_offerta_prezzo   CHECK (prezzo > 0),
    CONSTRAINT chk_offerta_date     CHECK (data_fine >= data_inizio)
);

-- Indice B-Tree sul nome normalizzato
CREATE INDEX idx_offerte_nome_norm ON offerte(nome_normalizzato);

-- Indice GIN trigramma — abilita la ricerca fuzzy con similarity()
-- es: WHERE nome_normalizzato % 'gin gordons' → ordina per similarità
CREATE INDEX idx_offerte_trgm ON offerte
    USING GIN(nome_normalizzato gin_trgm_ops);

-- Indice composito per velocizzare la ricerca per nome, negozio e data
CREATE INDEX idx_offerte_attive ON offerte(nome_normalizzato, supermercato_id, data_fine);

CREATE INDEX idx_offerte_supermercato ON offerte(supermercato_id);
CREATE INDEX idx_offerte_categoria    ON offerte(categoria);


-- =============================================================
--  PRODOTTI (catalogo normalizzato)
--  Permette di raggruppare varianti dello stesso prodotto,
--  es: "Gin Gordon's 70cl" = "GORDON S GIN CL70" = "gordons gin".
-- =============================================================
CREATE TABLE prodotti (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    nome_canonico   VARCHAR(300) NOT NULL UNIQUE,   -- nome "master" del prodotto
    marca           VARCHAR(100),
    categoria       VARCHAR(100),
    varianti        JSONB       NOT NULL DEFAULT '[]',
    -- Esempio varianti: ["gin gordons 70cl", "gordon s gin lt 0.7", "gin gordons"]
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_prodotti_trgm ON prodotti
    USING GIN(nome_canonico gin_trgm_ops);
CREATE INDEX idx_prodotti_varianti ON prodotti USING GIN(varianti);


-- =============================================================
--  QUERY PRINCIPALE — Ricerca per prodotto + posizione GPS
--  Parametri: :query, :lat, :lon, :raggio_m (es. 5000 = 5km)
-- =============================================================
/*
SELECT
    s.catena,
    s.nome_punto_vendita,
    s.indirizzo,
    s.logo_url,
    o.nome_prodotto,
    o.marca,
    o.quantita,
    o.prezzo,
    ROUND(
        ST_Distance(
            s.location::geography,
            ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography
        ) / 1000, 2
    ) AS distanza_km
FROM offerte o
JOIN supermercati s ON s.id = o.supermercato_id
WHERE
    o.nome_normalizzato % :query           -- fuzzy match con pg_trgm (soglia 0.3)
    AND o.data_fine >= CURRENT_DATE
    AND s.attivo = TRUE
    AND ST_DWithin(
        s.location::geography,
        ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography,
        :raggio_m
    )
ORDER BY
    similarity(o.nome_normalizzato, :query) DESC,
    o.prezzo ASC,
    distanza_km ASC
LIMIT 50;
*/