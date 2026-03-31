#!/usr/bin/env python3
"""
seed_db.py — Popola il DB con dati mock realistici.
Usa questo script per testare Flutter ADESSO,
indipendentemente dai problemi di scraping.

Uso: python seed_db.py

⚠️  Cambia le coordinate (lat/lon) con quelle della tua città!
    Apri Google Maps, clicca su un punto → copia lat, lon.
"""

import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from sqlalchemy import text
from datetime import date, timedelta
from app.database import SessionLocal, init_db, Supermercato, Offerta, Base, engine

# ─── CONFIGURA QUI LE TUE COORDINATE ─────────────────────────────────────────
# Apri Google Maps → clicca sulla tua città → copia le coordinate
# Esempio sotto: Napoli centro. Cambia con la tua posizione.

SUPERMERCATI = [
    {
        "catena":    "Esselunga",
        "nome":      "Esselunga Via Roma",
        "indirizzo": "Via Roma 1",
        "citta":     "Napoli",
        "lat":       40.8518,   # ← CAMBIA
        "lon":       14.2681,   # ← CAMBIA
        "logo_url":  "https://upload.wikimedia.org/wikipedia/it/thumb/0/06/Esselunga_logo.svg/200px-Esselunga_logo.svg.png",
    },
    {
        "catena":    "Lidl",
        "nome":      "Lidl Corso Umberto",
        "indirizzo": "Corso Umberto I 120",
        "citta":     "Napoli",
        "lat":       40.8500,   # ← CAMBIA (leggermente diverso per simulare distanza)
        "lon":       14.2550,   # ← CAMBIA
        "logo_url":  "https://upload.wikimedia.org/wikipedia/commons/thumb/9/91/Lidl-Logo.svg/200px-Lidl-Logo.svg.png",
    },
    {
        "catena":    "Conad",
        "nome":      "Conad Piazza Garibaldi",
        "indirizzo": "Piazza Garibaldi 45",
        "citta":     "Napoli",
        "lat":       40.8530,   # ← CAMBIA
        "lon":       14.2730,   # ← CAMBIA
        "logo_url":  "https://upload.wikimedia.org/wikipedia/it/thumb/6/6e/Conad_logo.svg/200px-Conad_logo.svg.png",
    },
]

# ─── OFFERTE MOCK ─────────────────────────────────────────────────────────────
# Prodotti realistici con prezzi diversi per catena.
# Struttura: { catena, nome_prodotto, marca, quantita, prezzo, prezzo_orig, categoria }

OGGI  = date.today()
FINE  = OGGI + timedelta(days=6)

OFFERTE_PER_CATENA = {
    "Esselunga": [
        # Spirits
        dict(nome="Gin Dry",              marca="Gordon's",     qty="70cl",  prezzo=9.90,  orig=13.50, cat="Spirits"),
        dict(nome="Vodka",                marca="Absolut",      qty="70cl",  prezzo=12.99, orig=None,  cat="Spirits"),
        dict(nome="Rum Carta Blanca",     marca="Bacardi",      qty="70cl",  prezzo=11.50, orig=14.90, cat="Spirits"),
        dict(nome="Aperitivo Spritz",     marca="Aperol",       qty="1L",    prezzo=8.99,  orig=11.00, cat="Spirits"),
        dict(nome="Prosecco DOC",         marca="Cantine Riunite", qty="75cl", prezzo=4.49, orig=None, cat="Vini"),
        # Birre
        dict(nome="Birra",                marca="Moretti",      qty="66cl",  prezzo=1.29,  orig=None,  cat="Birra"),
        dict(nome="Birra",                marca="Heineken",     qty="33cl x6", prezzo=5.49, orig=6.99, cat="Birra"),
        dict(nome="Birra",                marca="Corona Extra", qty="35.5cl", prezzo=1.99, orig=None,  cat="Birra"),
        # Latticini
        dict(nome="Parmigiano Reggiano 24 mesi", marca=None,    qty="200g",  prezzo=4.29,  orig=5.50,  cat="Latticini"),
        dict(nome="Burro Spalmabile",     marca="Lurpak",       qty="250g",  prezzo=3.49,  orig=None,  cat="Latticini"),
        dict(nome="Latte Fresco Intero",  marca="Granarolo",    qty="1L",    prezzo=1.45,  orig=None,  cat="Latticini"),
        dict(nome="Mozzarella di Bufala", marca="Delizie del Sud", qty="125g", prezzo=1.89, orig=2.30, cat="Latticini"),
        # Pasta e riso
        dict(nome="Pasta Spaghetti n.5",  marca="Barilla",      qty="500g",  prezzo=0.99,  orig=1.29,  cat="Pasta"),
        dict(nome="Riso Carnaroli",       marca="Scotti",       qty="1kg",   prezzo=2.49,  orig=None,  cat="Riso"),
        # Carne e pesce
        dict(nome="Prosciutto Crudo",     marca="San Daniele",  qty="100g",  prezzo=2.99,  orig=3.80,  cat="Salumi"),
        dict(nome="Salmone Affumicato",   marca="Fjord",        qty="100g",  prezzo=3.29,  orig=None,  cat="Pesce"),
        # Dolci e snack
        dict(nome="Nutella",              marca="Ferrero",      qty="750g",  prezzo=4.99,  orig=6.50,  cat="Dolci"),
        dict(nome="Biscotti Digestive",   marca="McVitie's",    qty="400g",  prezzo=1.79,  orig=None,  cat="Dolci"),
        # Caffè
        dict(nome="Caffè Miscela Classic", marca="Lavazza",     qty="250g",  prezzo=2.89,  orig=3.50,  cat="Caffè"),
        dict(nome="Capsule Espresso",     marca="Nespresso",    qty="x10",   prezzo=4.99,  orig=5.90,  cat="Caffè"),
    ],
    "Lidl": [
        # Spirits
        dict(nome="Gin London Dry",       marca="Beefeater",    qty="70cl",  prezzo=11.20, orig=None,  cat="Spirits"),
        dict(nome="Whisky Blended",       marca="Glenfiddich",  qty="70cl",  prezzo=19.99, orig=24.99, cat="Spirits"),
        dict(nome="Vino Rosso Chianti",   marca="Cecchi",       qty="75cl",  prezzo=3.99,  orig=None,  cat="Vini"),
        # Birre
        dict(nome="Birra Lager",          marca="Tennent's",    qty="50cl x4", prezzo=3.79, orig=4.99, cat="Birra"),
        dict(nome="Birra Weizen",         marca="Erdinger",     qty="50cl",  prezzo=1.49,  orig=None,  cat="Birra"),
        # Latticini
        dict(nome="Grana Padano",         marca=None,           qty="200g",  prezzo=2.99,  orig=None,  cat="Latticini"),
        dict(nome="Yogurt Greco",         marca="Fage",         qty="500g",  prezzo=2.29,  orig=2.99,  cat="Latticini"),
        dict(nome="Latte Fresco",         marca="Lidl",         qty="1L",    prezzo=1.19,  orig=None,  cat="Latticini"),
        # Pasta
        dict(nome="Pasta Penne",          marca="Barilla",      qty="500g",  prezzo=0.89,  orig=1.19,  cat="Pasta"),
        dict(nome="Pasta Integrale",      marca="De Cecco",     qty="500g",  prezzo=1.19,  orig=None,  cat="Pasta"),
        # Carne
        dict(nome="Petto di Pollo",       marca=None,           qty="600g",  prezzo=3.99,  orig=None,  cat="Carne"),
        dict(nome="Würstel di Pollo",     marca="Berni",        qty="250g",  prezzo=1.59,  orig=1.99,  cat="Salumi"),
        # Snack e dolci
        dict(nome="Patatine",             marca="Pringles",     qty="165g",  prezzo=1.99,  orig=None,  cat="Snack"),
        dict(nome="Cioccolato Fondente",  marca="Lindt 70%",    qty="100g",  prezzo=1.49,  orig=1.89,  cat="Dolci"),
        dict(nome="Caffè Macinato",       marca="Kimbo",        qty="250g",  prezzo=2.49,  orig=None,  cat="Caffè"),
    ],
    "Conad": [
        # Spirits — prezzi leggermente diversi per testare ordinamento
        dict(nome="Gin Dry",              marca="Gordon's",     qty="70cl",  prezzo=10.49, orig=None,  cat="Spirits"),
        dict(nome="Birra",                marca="Peroni",       qty="66cl",  prezzo=1.09,  orig=None,  cat="Birra"),
        dict(nome="Birra Nastro Azzurro", marca="Peroni",       qty="33cl x6", prezzo=4.99, orig=6.20, cat="Birra"),
        dict(nome="Latte UHT Intero",     marca="Parmalat",     qty="1L",    prezzo=1.09,  orig=None,  cat="Latticini"),
        dict(nome="Mozzarella",           marca="Santa Lucia",  qty="125g",  prezzo=0.99,  orig=None,  cat="Latticini"),
        dict(nome="Pasta Spaghetti",      marca="De Cecco",     qty="500g",  prezzo=1.29,  orig=None,  cat="Pasta"),
        dict(nome="Prosciutto Cotto",     marca="Conad",        qty="120g",  prezzo=1.89,  orig=2.40,  cat="Salumi"),
        dict(nome="Olio EVO",             marca="Dante",        qty="750ml", prezzo=5.99,  orig=7.50,  cat="Condimenti"),
        dict(nome="Passata di Pomodoro",  marca="Mutti",        qty="700g",  prezzo=1.49,  orig=None,  cat="Conserve"),
        dict(nome="Caffè",                marca="Conad",        qty="250g",  prezzo=1.99,  orig=None,  cat="Caffè"),
        dict(nome="Acqua Naturale",       marca="Levissima",    qty="1.5L x6", prezzo=2.29, orig=None, cat="Acqua"),
        dict(nome="Succo ACE",            marca="Yoga",         qty="1L",    prezzo=1.49,  orig=1.89,  cat="Bevande"),
    ],
}


# ─── FUNZIONI ────────────────────────────────────────────────────────────────

import unicodedata, re

def normalizza(nome: str, marca: str | None) -> str:
    t = f"{marca} {nome}" if marca else nome
    t = t.lower()
    t = "".join(c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn")
    t = re.sub(r"[''`']", "", t)
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def seed():
    # Usiamo la "ruspa" SQL per distruggere le vecchie tabelle e i loro collegamenti
    with engine.connect() as conn:
        conn.execute(text("DROP TABLE IF EXISTS offerte CASCADE;"))
        conn.execute(text("DROP TABLE IF EXISTS volantini CASCADE;"))
        conn.execute(text("DROP TABLE IF EXISTS supermercati CASCADE;"))
        conn.commit()
    print("📦  Inizializzo DB...")
    init_db()

    db = SessionLocal()
    try:
        # Pulisci dati vecchi
        db.query(Offerta).delete()
        db.query(Supermercato).delete()
        db.commit()
        print("🗑️   Puliti dati precedenti\n")

        totale_offerte = 0

        for cfg in SUPERMERCATI:
            catena = cfg["catena"]

            # Crea supermercato con coordinate WKT
            sup = Supermercato(
                catena    = catena,
                nome      = cfg["nome"],
                indirizzo = cfg["indirizzo"],
                citta     = cfg["citta"],
                logo_url  = cfg["logo_url"],
                attivo    = True,
                location  = f"SRID=4326;POINT({cfg['lon']} {cfg['lat']})",
            )
            db.add(sup)
            db.flush()  # ottieni l'ID senza commit

            # Crea offerte per questa catena
            offerte_catena = OFFERTE_PER_CATENA.get(catena, [])
            for o in offerte_catena:
                db.add(Offerta(
                    supermercato_id   = sup.id,
                    nome_prodotto     = o["nome"],
                    marca             = o.get("marca"),
                    quantita          = o.get("qty"),
                    prezzo            = o["prezzo"],
                    prezzo_originale  = o.get("orig"),
                    categoria         = o.get("cat"),
                    nome_normalizzato = normalizza(o["nome"], o.get("marca")),
                    data_inizio       = OGGI,
                    data_fine         = FINE,
                ))

            db.commit()
            print(f"  ✅  {catena}: {len(offerte_catena)} offerte — "
                  f"({cfg['lat']}, {cfg['lon']})")
            totale_offerte += len(offerte_catena)

        print(f"\n🎉  Seed completato: {len(SUPERMERCATI)} supermercati, "
              f"{totale_offerte} offerte totali")
        print(f"\n  Periodo offerte: {OGGI} → {FINE}")
        print(f"\n  Testa subito:")
        print(f"  curl \"http://localhost:8000/search?q=gin&lat={SUPERMERCATI[0]['lat']}&lon={SUPERMERCATI[0]['lon']}&raggio=5000\"")

    except Exception as e:
        db.rollback()
        print(f"❌  Errore: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed()