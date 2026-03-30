#!/usr/bin/env python3
"""
run_pipeline.py — Script standalone MVP
Uso: python run_pipeline.py

Fa tutto in sequenza:
  1. Playwright → scarica il testo del volantino di Esselunga (e/o Lidl)
  2. Gemini     → struttura il testo in JSON offerte
  3. PostgreSQL → inserisce le offerte nel DB

Dipendenze:
  pip install playwright google-generativeai sqlalchemy psycopg2-binary geoalchemy2
  playwright install chromium
"""

import json
import os
import re
import sys
import unicodedata
from datetime import date, timedelta
from typing import Optional

from google import genai
from google.genai.types import GenerateContentConfig
from playwright.sync_api import sync_playwright
from sqlalchemy import text, types

# Importa engine e modelli dal nostro database.py
# Se lanci lo script dalla root del progetto: python run_pipeline.py
sys.path.insert(0, os.path.dirname(__file__))
from app.database import engine, SessionLocal, init_db, Supermercato, Offerta

# ─── Configurazione ───────────────────────────────────────────────────────────

GEMINI_API_KEY = os.getenv("GOOGLE_API_KEY")

# Catene da processare in questa run.
# Ogni entry: nome catena, URL volantino, lat/lon di un punto vendita di esempio.
# ⚠️  Sostituisci lat/lon con coordinate reali dei tuoi punti vendita test.
CATENE = [
    {
        "catena":    "Esselunga",
        "url":       "https://www.esselunga.it/area-pubblica/negozi-e-volantini/volantino.html",
        "lat":       45.4654,   # Milano centro — cambia con il tuo punto vendita
        "lon":       9.1859,
        "logo_url":  "https://upload.wikimedia.org/wikipedia/it/thumb/0/06/Esselunga_logo.svg/200px-Esselunga_logo.svg.png",
    },
    {
        "catena":    "Lidl",
        "url":       "https://www.lidl.it/offerte-settimanali",
        "lat":       45.4700,
        "lon":       9.2000,
        "logo_url":  "https://upload.wikimedia.org/wikipedia/commons/thumb/9/91/Lidl-Logo.svg/200px-Lidl-Logo.svg.png",
    },
]


# ─────────────────────────────────────────────────────────────────────────────
#  STEP 1 — SCRAPING con Playwright
# ─────────────────────────────────────────────────────────────────────────────

def scrape_testo_volantino(url: str, catena: str) -> str:
    """
    Apre la pagina con Playwright, attende il caricamento JS,
    ed estrae tutto il testo visibile sulla pagina.
    Ritorna una stringa grezza (max 15.000 caratteri per non sforare il contesto Gemini).
    """
    print(f"  🌐  Apro {url} …")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx     = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="it-IT",
            viewport={"width": 1280, "height": 900},
        )
        # Blocca immagini e font per velocizzare il caricamento
        ctx.route(
            "**/*.{png,jpg,jpeg,gif,webp,svg,woff,woff2,ttf}",
            lambda route, _: route.abort()
        )
        page = ctx.new_page()

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            # Aspetta che ci sia del contenuto testuale significativo
            page.wait_for_timeout(3000)

            # Prova a cliccare su "Accetta cookie" se presente
            for selector in [
                "button:has-text('Accetta')",
                "button:has-text('Accetto')",
                "button:has-text('Accept')",
                "#cookieAccept",
                ".cookie-accept",
            ]:
                try:
                    page.click(selector, timeout=2000)
                    page.wait_for_timeout(1000)
                    break
                except Exception:
                    pass

            # Scorri la pagina per caricare il contenuto lazy
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(2000)

            # Estrai tutto il testo visibile
            testo = page.inner_text("body")

        except Exception as e:
            print(f"  ⚠️  Playwright errore su {catena}: {e}")
            testo = ""
        finally:
            browser.close()

    # Pulizia: rimuovi righe vuote consecutive
    righe  = [r.strip() for r in testo.splitlines() if r.strip()]
    testo  = "\n".join(righe)

    # Tronca a 15.000 caratteri — Gemini Flash ha un context window enorme
    # ma più testo = più token = più lento. 15k caratteri è sufficiente per
    # coprire ~2-3 pagine di volantino.
    testo = testo[:15_000]

    print(f"  📄  Estratti {len(testo)} caratteri di testo")
    return testo


# ─────────────────────────────────────────────────────────────────────────────
#  STEP 2 — PARSING con Gemini
# ─────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """
Sei un assistente specializzato nell'estrazione di dati strutturati da testi
di volantini promozionali di supermercati italiani.

OBIETTIVO: Estrai OGNI prodotto in offerta e restituisci un array JSON valido.

SCHEMA per ogni prodotto (tutti i campi sono stringhe o numeri, MAI oggetti annidati):
{
  "nome_prodotto":    "stringa — nome del prodotto senza marca, es: Gin Dry",
  "marca":            "stringa oppure null",
  "quantita":         "stringa oppure null — es: 70cl, 1kg, conf. 4x100g",
  "prezzo":           numero decimale con punto, es: 9.90,
  "prezzo_originale": numero decimale oppure null — solo se presente il prezzo barrato,
  "categoria":        "stringa oppure null — es: Spirits, Birra, Latticini, Salumi",
  "data_inizio":      "YYYY-MM-DD",
  "data_fine":        "YYYY-MM-DD"
}

REGOLE CRITICHE:
1. Restituisci SOLO il JSON array [ {...}, {...} ], zero testo prima o dopo.
2. Prezzo: numero puro senza €. Virgola italiana → punto decimale: "2,99" → 2.99
3. Se le date non sono esplicite nel testo, usa quelle che ti fornisco nel contesto.
4. Ignora testi non-prodotto: indirizzi, orari, disclaimer, numero verde.
5. Prodotti diversi = righe separate. Non raggruppare.
"""

def _normalizza_nome(nome: str, marca: Optional[str] = None) -> str:
    """
    Produce il nome normalizzato per il fuzzy search nel DB.
    Esempio: "Gin Gordon's Dry 70cl" → "gin gordons dry 70cl"
    """
    testo = f"{marca} {nome}" if marca else nome
    # lowercase
    testo = testo.lower()
    # rimuovi accenti
    testo = "".join(
        c for c in unicodedata.normalize("NFD", testo)
        if unicodedata.category(c) != "Mn"
    )
    # rimuovi apostrofi e caratteri speciali
    testo = re.sub(r"[''`']", "", testo)
    testo = re.sub(r"[^a-z0-9\s]", " ", testo)
    # comprimi spazi
    return re.sub(r"\s+", " ", testo).strip()


def parsa_con_gemini(
    testo_ocr: str,
    catena: str,
    data_inizio: date,
    data_fine: date,
) -> list[dict]:
    
    client = genai.Client(api_key=GEMINI_API_KEY)

    prompt_utente = f"""
CATENA: {catena}
PERIODO VALIDITÀ: dal {data_inizio.strftime("%d/%m/%Y")} al {data_fine.strftime("%d/%m/%Y")}

TESTO VOLANTINO:
---
{testo_ocr}
---

Estrai tutti i prodotti in offerta. Rispondi SOLO con il JSON array.
"""

    print(f"  🤖  Invio a Gemini ({len(testo_ocr)} caratteri)…")
    try:
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt_utente,
            config=GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                response_mime_type="application/json",
                temperature=0.1,
            )
        )
        raw = response.text.strip()
    except Exception as e:
        print(f"  ❌  Gemini errore: {e}")
        return []

    raw = re.sub(r"http://googleusercontent.com/immersive_entry_chip/0", "", raw)
    start = raw.find("[")
    end   = raw.rfind("]")
    if start == -1 or end == -1:
        print(f"  ⚠️  Gemini non ha restituito un array JSON valido")
        print(f"      Risposta raw (primi 300 char): {raw[:300]}")
        return []

    try:
        items = json.loads(raw[start:end + 1])
    except json.JSONDecodeError as e:
        print(f"  ⚠️  JSON non valido: {e}")
        return []

    # Validazione e normalizzazione di ogni offerta
    offerte_valide = []
    for item in items:
        nome = str(item.get("nome_prodotto", "")).strip()
        if not nome:
            continue

        try:
            prezzo = float(str(item.get("prezzo", 0)).replace(",", "."))
        except (ValueError, TypeError):
            continue
        if prezzo <= 0:
            continue

        prezzo_orig = item.get("prezzo_originale")
        try:
            prezzo_orig = float(str(prezzo_orig).replace(",", ".")) if prezzo_orig else None
        except (ValueError, TypeError):
            prezzo_orig = None

        def _parse_date(val, default: date) -> date:
            if not val:
                return default
            try:
                return date.fromisoformat(str(val)[:10])
            except ValueError:
                return default

        d_i = _parse_date(item.get("data_inizio"), data_inizio)
        d_f = _parse_date(item.get("data_fine"),   data_fine)
        if d_f < d_i:
            d_f = d_i + timedelta(days=6)

        marca = str(item.get("marca", "")).strip() or None

        offerte_valide.append({
            "nome_prodotto":     nome,
            "marca":             marca,
            "quantita":          item.get("quantita"),
            "prezzo":            round(prezzo, 2),
            "prezzo_originale":  round(prezzo_orig, 2) if prezzo_orig else None,
            "categoria":         item.get("categoria"),
            "data_inizio":       d_i,
            "data_fine":         d_f,
            "nome_normalizzato": _normalizza_nome(nome, marca),
        })

    print(f"  ✅  Estratte {len(offerte_valide)} offerte valide")
    return offerte_valide


# ─────────────────────────────────────────────────────────────────────────────
#  STEP 3 — INSERT NEL DB
# ─────────────────────────────────────────────────────────────────────────────

def _get_o_crea_supermercato(
    db,
    catena: str,
    lat: float,
    lon: float,
    logo_url: Optional[str],
) -> Supermercato:
    """
    Cerca il supermercato nel DB per nome catena.
    Se non esiste lo crea con le coordinate fornite.
    In una versione più avanzata ci sarebbero più punti vendita per catena;
    per l'MVP usiamo un record unico per catena.
    """
    sup = db.query(Supermercato).filter(
        Supermercato.catena == catena
    ).first()

    if not sup:
        print(f"  🏪  Creo supermercato '{catena}' nel DB…")
        sup = Supermercato(
            catena   = catena,
            nome     = catena,
            indirizzo= "Indirizzo di esempio — aggiorna dal DB",
            citta    = "Milano",
            logo_url = logo_url,
            attivo   = True,
            # ST_MakePoint(lon, lat) — nota l'ordine: longitudine PRIMA
            location = f"SRID=4326;POINT({lon} {lat})",
        )
        db.add(sup)
        db.commit()
        db.refresh(sup)
        print(f"  ✅  Supermercato creato con ID: {sup.id}")
    else:
        print(f"  ✅  Supermercato '{catena}' trovato: {sup.id}")

    return sup


def inserisci_offerte(
    supermercato: Supermercato,
    offerte: list[dict],
) -> int:
    """
    Inserisce le offerte nel DB.
    Prima cancella le offerte scadute della stessa catena per evitare duplicati
    tra run successive dello stesso volantino.
    Ritorna il numero di offerte inserite.
    """
    if not offerte:
        return 0

    db = SessionLocal()
    try:
        # Elimina offerte precedenti della stessa catena (stesso supermercato)
        # in modo da poter ri-lanciare lo script senza duplicati
        cancellate = (
            db.query(Offerta)
            .filter(Offerta.supermercato_id == supermercato.id)
            .delete()
        )
        if cancellate:
            print(f"  🗑️   Rimosse {cancellate} offerte precedenti di '{supermercato.catena}'")

        # Bulk insert
        offerte_orm = [
            Offerta(
                supermercato_id   = supermercato.id,
                nome_prodotto     = o["nome_prodotto"],
                marca             = o.get("marca"),
                quantita          = o.get("quantita"),
                prezzo            = o["prezzo"],
                prezzo_originale  = o.get("prezzo_originale"),
                categoria         = o.get("categoria"),
                nome_normalizzato = o.get("nome_normalizzato"),
                data_inizio       = o["data_inizio"],
                data_fine         = o["data_fine"],
            )
            for o in offerte
        ]
        db.bulk_save_objects(offerte_orm)
        db.commit()

    except Exception as e:
        db.rollback()
        print(f"  ❌  Errore inserimento DB: {e}")
        raise
    finally:
        db.close()

    print(f"  💾  Inserite {len(offerte_orm)} offerte nel DB")
    return len(offerte_orm)


# ─────────────────────────────────────────────────────────────────────────────
#  MAIN — pipeline completa
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  PrezzoRadar — Pipeline scraping MVP")
    print("=" * 60)

    # Inizializza DB (crea tabelle se non esistono)
    print("\n📦  Inizializzo database…")
    init_db()

    oggi     = date.today()
    fine     = oggi + timedelta(days=6)
    totale   = 0

    for config in CATENE:
        catena = config["catena"]
        print(f"\n{'─'*50}")
        print(f"  Catena: {catena.upper()}")
        print(f"{'─'*50}")

        # ── Step 1: scraping ──────────────────────────────────────────────────
        testo = scrape_testo_volantino(config["url"], catena)
        if not testo.strip():
            print(f"  ⚠️  Testo vuoto per {catena}, salto.")
            continue

        # ── Step 2: parsing con Gemini ────────────────────────────────────────
        offerte = parsa_con_gemini(
            testo_ocr   = testo,
            catena      = catena,
            data_inizio = oggi,
            data_fine   = fine,
        )
        if not offerte:
            print(f"  ⚠️  Nessuna offerta estratta per {catena}, salto.")
            continue

        # ── Step 3: insert nel DB ─────────────────────────────────────────────
        db = SessionLocal()
        try:
            sup = _get_o_crea_supermercato(
                db       = db,
                catena   = catena,
                lat      = config["lat"],
                lon      = config["lon"],
                logo_url = config.get("logo_url"),
            )
        finally:
            db.close()

        n = inserisci_offerte(sup, offerte)
        totale += n

    print(f"\n{'='*60}")
    print(f"  ✅  Pipeline completata. Totale offerte inserite: {totale}")
    print(f"{'='*60}\n")

    # ── Anteprima di 5 record per verifica ───────────────────────────────────
    print("  Anteprima ultime offerte nel DB:")
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT s.catena, o.nome_prodotto, o.marca, o.quantita, o.prezzo, o.data_fine
            FROM offerte o
            JOIN supermercati s ON s.id = o.supermercato_id
            ORDER BY o.created_at DESC
            LIMIT 5
        """)).fetchall()
    if rows:
        print(f"  {'Catena':<14} {'Prodotto':<30} {'Marca':<15} {'Qt':<8} {'€':>6}  Scade")
        print(f"  {'-'*80}")
        for r in rows:
            print(f"  {str(r[0]):<14} {str(r[1]):<30} {str(r[2] or ''):<15} "
                  f"{str(r[3] or ''):<8} {float(r[4]):>6.2f}  {r[5]}")
    else:
        print("  (nessun record trovato — controlla la configurazione)")


if __name__ == "__main__":
    main()