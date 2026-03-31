#!/usr/bin/env python3
"""
run_pipeline.py — versione aggiornata con fix anti-timeout.

Uso: python run_pipeline.py

Se i siti bloccano lo scraping, usa invece:
    python seed_db.py   ← dati mock realistici, funziona sempre
"""

import json, os, re, sys, unicodedata
from datetime import date, timedelta
from typing import Optional

import google.generativeai as genai
from playwright.sync_api import sync_playwright
from sqlalchemy import text

sys.path.insert(0, os.path.dirname(__file__))
from app.database import engine, SessionLocal, init_db, Supermercato, Offerta

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "LA_TUA_API_KEY_QUI")

CATENE = [
    {
        "catena":   "Esselunga",
        "url":      "https://www.esselunga.it/area-pubblica/negozi-e-volantini/volantino.html",
        "lat":      40.8518,   # ← cambia con le tue coordinate
        "lon":      14.2681,
        "logo_url": "https://upload.wikimedia.org/wikipedia/it/thumb/0/06/Esselunga_logo.svg/200px-Esselunga_logo.svg.png",
    },
    {
        "catena":   "Lidl",
        "url":      "https://www.lidl.it/offerte-settimanali",
        "lat":      40.8500,
        "lon":      14.2550,
        "logo_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/9/91/Lidl-Logo.svg/200px-Lidl-Logo.svg.png",
    },
]


# ─── STEP 1: SCRAPING ────────────────────────────────────────────────────────

def scrape_testo_volantino(url: str, catena: str) -> str:
    print(f"  🌐  Apro {url} …")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="it-IT",
            viewport={"width": 1366, "height": 768},
        )

        # Rimuovi firma webdriver che i siti anti-bot rilevano
        ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
        )

        # Blocca solo media, non JS/XHR/CSS
        ctx.route(
            "**/*",
            lambda route, req: route.abort()
            if req.resource_type in ("image", "font", "media")
            else route.continue_()
        )

        page = ctx.new_page()
        testo = ""

        try:
            # FIX CHIAVE: "domcontentloaded" invece di "networkidle"
            # networkidle va in timeout su siti con polling continuo
            page.goto(url, wait_until="domcontentloaded", timeout=45_000)

            # Aspetta testo significativo (max 8s)
            try:
                page.wait_for_function("document.body.innerText.length > 500", timeout=8_000)
            except Exception:
                pass

            # Cookie banner — prova vari selettori comuni
            for sel in [
                "button:has-text('Accetta tutto')",
                "button:has-text('Accetta')",
                "button:has-text('Accetto')",
                "#onetrust-accept-btn-handler",
                ".cookie-accept",
            ]:
                try:
                    page.locator(sel).first.click(timeout=2_000)
                    page.wait_for_timeout(1_000)
                    break
                except Exception:
                    continue

            # Scroll per contenuto lazy
            page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
            page.wait_for_timeout(1_500)
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(1_500)

            testo = page.inner_text("body")

        except Exception as e:
            print(f"  ⚠️  Errore Playwright: {e}")
            # Screenshot di debug per capire cosa ha caricato
            try:
                debug = f"debug_{catena.lower()}.png"
                page.screenshot(path=debug, full_page=False)
                print(f"  📸  Screenshot salvato: {debug}")
                print(f"       Aprilo per vedere se il sito ha risposto o bloccato")
            except Exception:
                pass
        finally:
            browser.close()

    righe = [r.strip() for r in testo.splitlines() if r.strip()]
    testo = "\n".join(righe)[:15_000]
    print(f"  📄  Estratti {len(testo)} caratteri")
    return testo


# ─── STEP 2: PARSING GEMINI ──────────────────────────────────────────────────

SYSTEM_PROMPT = """
Sei un assistente specializzato nell'estrazione di dati strutturati da testi
di volantini promozionali di supermercati italiani.

OBIETTIVO: Estrai OGNI prodotto in offerta e restituisci un array JSON valido.

SCHEMA per ogni prodotto:
{
  "nome_prodotto":    "stringa senza marca",
  "marca":            "stringa oppure null",
  "quantita":         "es: 70cl, 1kg, conf. 4x100g oppure null",
  "prezzo":           9.90,
  "prezzo_originale": 13.50,
  "categoria":        "es: Spirits, Birra, Latticini oppure null",
  "data_inizio":      "YYYY-MM-DD",
  "data_fine":        "YYYY-MM-DD"
}

REGOLE:
1. Rispondi SOLO con il JSON array, zero testo aggiuntivo.
2. Prezzo come numero decimale con punto (2.99 non "2,99€").
3. Se le date non ci sono nel testo, usa quelle del contesto.
4. Ignora indirizzi, orari, numero verde, disclaimer.
"""

def _normalizza(nome: str, marca: Optional[str]) -> str:
    t = f"{marca} {nome}" if marca else nome
    t = t.lower()
    t = "".join(c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn")
    t = re.sub(r"[''`']", "", t)
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def parsa_con_gemini(testo: str, catena: str, d_inizio: date, d_fine: date) -> list[dict]:
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel(
        model_name="gemini-1.5-flash",
        system_instruction=SYSTEM_PROMPT,
        generation_config=genai.GenerationConfig(
            response_mime_type="application/json",
            temperature=0.1,
        ),
    )

    prompt = f"""
CATENA: {catena}
PERIODO: dal {d_inizio.strftime("%d/%m/%Y")} al {d_fine.strftime("%d/%m/%Y")}

TESTO VOLANTINO:
---
{testo}
---

Estrai tutti i prodotti. Rispondi SOLO con il JSON array.
"""
    print(f"  🤖  Invio a Gemini…")
    try:
        raw = model.generate_content(prompt).text.strip()
        raw = re.sub(r"```(?:json)?", "", raw).strip()
    except Exception as e:
        print(f"  ❌  Gemini errore: {e}")
        return []

    start, end = raw.find("["), raw.rfind("]")
    if start == -1:
        print(f"  ⚠️  Risposta non è un JSON array")
        return []

    try:
        items = json.loads(raw[start:end + 1])
    except json.JSONDecodeError as e:
        print(f"  ⚠️  JSON non valido: {e}")
        return []

    valide = []
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

        orig = item.get("prezzo_originale")
        try:
            orig = float(str(orig).replace(",", ".")) if orig else None
        except (ValueError, TypeError):
            orig = None

        def pd(v, default):
            try: return date.fromisoformat(str(v)[:10])
            except: return default

        marca = str(item.get("marca", "")).strip() or None
        valide.append({
            "nome_prodotto":     nome,
            "marca":             marca,
            "quantita":          item.get("quantita"),
            "prezzo":            round(prezzo, 2),
            "prezzo_originale":  round(orig, 2) if orig else None,
            "categoria":         item.get("categoria"),
            "data_inizio":       pd(item.get("data_inizio"), d_inizio),
            "data_fine":         pd(item.get("data_fine"), d_fine),
            "nome_normalizzato": _normalizza(nome, marca),
        })

    print(f"  ✅  {len(valide)} offerte valide")
    return valide


# ─── STEP 3: INSERT DB ───────────────────────────────────────────────────────

def get_o_crea_supermercato(catena, lat, lon, logo_url):
    db = SessionLocal()
    try:
        sup = db.query(Supermercato).filter(Supermercato.catena == catena).first()
        if not sup:
            sup = Supermercato(
                catena=catena, nome=catena,
                indirizzo="Da aggiornare", citta="Da aggiornare",
                logo_url=logo_url, attivo=True,
                location=f"SRID=4326;POINT({lon} {lat})",
            )
            db.add(sup)
            db.commit()
            db.refresh(sup)
            print(f"  🏪  Supermercato '{catena}' creato")
        else:
            print(f"  🏪  Supermercato '{catena}' trovato")
        return sup
    finally:
        db.close()


def inserisci_offerte(sup, offerte):
    if not offerte:
        return 0
    db = SessionLocal()
    try:
        n = db.query(Offerta).filter(Offerta.supermercato_id == sup.id).delete()
        if n:
            print(f"  🗑️   Rimosse {n} offerte precedenti")
        db.bulk_save_objects([
            Offerta(
                supermercato_id=sup.id,
                nome_prodotto=o["nome_prodotto"],
                marca=o.get("marca"),
                quantita=o.get("quantita"),
                prezzo=o["prezzo"],
                prezzo_originale=o.get("prezzo_originale"),
                categoria=o.get("categoria"),
                nome_normalizzato=o.get("nome_normalizzato"),
                data_inizio=o["data_inizio"],
                data_fine=o["data_fine"],
            ) for o in offerte
        ])
        db.commit()
        print(f"  💾  Inserite {len(offerte)} offerte")
        return len(offerte)
    except Exception as e:
        db.rollback()
        print(f"  ❌  Errore DB: {e}")
        raise
    finally:
        db.close()


# ─── MAIN ────────────────────────────────────────────────────────────────────

def main():
    print("=" * 55)
    print("  PrezzoRadar — Pipeline scraping")
    print("=" * 55)

    init_db()
    oggi, fine = date.today(), date.today() + timedelta(days=6)
    totale = 0

    for cfg in CATENE:
        catena = cfg["catena"]
        print(f"\n{'─'*45}\n  {catena.upper()}\n{'─'*45}")

        testo = scrape_testo_volantino(cfg["url"], catena)
        if not testo.strip():
            print(f"  ⚠️  Testo vuoto — vedi debug_{catena.lower()}.png")
            print(f"  💡  Alternativa rapida: python seed_db.py")
            continue

        offerte = parsa_con_gemini(testo, catena, oggi, fine)
        if not offerte:
            continue

        sup = get_o_crea_supermercato(catena, cfg["lat"], cfg["lon"], cfg.get("logo_url"))
        totale += inserisci_offerte(sup, offerte)

    print(f"\n{'='*55}")
    print(f"  Totale offerte inserite: {totale}")
    if totale == 0:
        print(f"\n  💡  I siti stanno bloccando lo scraping.")
        print(f"      Per testare Flutter usa i dati mock:")
        print(f"      python seed_db.py")
    print(f"{'='*55}\n")


if __name__ == "__main__":
    main()