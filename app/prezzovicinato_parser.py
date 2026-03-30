"""
PrezzoVicinato — LLM Parser
Trasforma testo OCR grezzo in offerte strutturate JSON.

Dipendenze:
    pip install openai anthropic tenacity unidecode
    (sostituire il client in base al provider scelto)
"""

import json
import re
import unicodedata
import os
from datetime import date, timedelta
from typing import Optional
from dotenv import load_dotenv

from tenacity import retry, stop_after_attempt, wait_exponential

load_dotenv()  # Carica le variabili d'ambiente da .env

# ── Scegli il provider: "anthropic" | "openai" | "gemini" ────────────────────
PROVIDER = "gemini"

if PROVIDER == "anthropic":
    import anthropic
    _client = anthropic.Anthropic()          # legge ANTHROPIC_API_KEY dall'env
    LLM_MODEL = "claude-sonnet-4-20250514"
elif PROVIDER == "openai":
    from openai import OpenAI
    _client = OpenAI()                       # legge OPENAI_API_KEY
    LLM_MODEL = "gpt-4o-mini"
elif PROVIDER == "gemini":
    import google.generativeai as genai
    genai.configure()                        # legge GOOGLE_API_KEY
    LLM_MODEL = "gemini-1.5-flash"


# =============================================================================
#  PROMPT ENGINEERING
# =============================================================================

SYSTEM_PROMPT = """\
Sei un assistente specializzato nell'estrazione di dati strutturati da testi \
di volantini promozionali di supermercati italiani.

OBIETTIVO:
Estrai OGNI prodotto in offerta dal testo e restituisci un array JSON valido.

SCHEMA PER OGNI PRODOTTO:
{
  "nome_prodotto":   string,   // nome del prodotto, senza marca (es: "Gin Dry")
  "marca":           string | null,
  "quantita":        string | null,  // es: "70cl", "1kg", "conf. 4x100g"
  "prezzo":          number,   // prezzo in euro come numero decimale
  "prezzo_originale":number | null,  // prezzo barrato/precedente se presente
  "categoria":       string | null,  // es: "Spirits", "Birra", "Latticini"
  "data_inizio":     string,   // formato ISO "YYYY-MM-DD"
  "data_fine":       string    // formato ISO "YYYY-MM-DD"
}

REGOLE CRITICHE:
1. Restituisci SOLO il JSON array, niente testo prima o dopo.
2. Se un campo è assente nel testo, usa null (mai stringa vuota).
3. Il prezzo deve essere un numero (2.99), non una stringa ("2,99€").
4. Virgola decimale italiana → punto decimale JSON: "2,99" → 2.99
5. Se il testo mostra "€ 1,29 al kg" per un prodotto da 500g, calcola il
   prezzo reale: 0.645. Indica la quantita come "500g".
6. Prodotti multipli con prezzi diversi = righe separate nel JSON.
7. Ignora testi non-prodotto: indirizzi, orari, disclaimer legali.
8. Se le date di validità non sono esplicite, usa quelle fornite nel contesto.
"""

USER_PROMPT_TEMPLATE = """\
CATENA: {catena}
PERIODO VALIDITÀ VOLANTINO: dal {data_inizio} al {data_fine}

TESTO OCR ESTRATTO:
---
{raw_text}
---

Estrai tutti i prodotti e restituisci SOLO il JSON array.
"""


# =============================================================================
#  NORMALIZZAZIONE DEL NOME (per fuzzy matching in DB)
# =============================================================================

# Abbreviazioni comuni nei volantini italiani → forma standard
_ABBREVIAZIONI = {
    r"\bcl\s*(\d+)":     r"\1cl",
    r"\blt\.?\s*(\d+(?:[.,]\d+)?)": lambda m: f"{float(m.group(1).replace(',', '.')):.2g}l",
    r"\bkg\.?\s*(\d+(?:[.,]\d+)?)": lambda m: f"{float(m.group(1).replace(',', '.')):.2g}kg",
    r"\bgr?\.?\s*(\d+)": r"\1g",
    r"\bpz\.?":          "pezzi",
    r"\bconf\.?":        "confezione",
    r"\bx\s*(\d+)":      r"x\1",
}

# Parole da rimuovere nel nome normalizzato
_STOPWORDS = {
    "il", "la", "lo", "le", "gli", "i",
    "di", "da", "a", "in", "con", "su",
    "del", "della", "degli", "delle", "dei",
    "offerta", "promo", "nuovo", "nuova", "speciale",
}


def normalizza_nome(nome: str, marca: Optional[str] = None) -> str:
    """
    Produce il nome normalizzato usato per il fuzzy matching nel DB.

    Esempio:
        normalizza_nome("Gin Gordon's Dry", "Gordon's")
        → "gin gordons dry"

        normalizza_nome("BIRRA MORETTI 33CL X6 CONF.")
        → "birra moretti 33cl x6 confezione"
    """
    testo = f"{marca} {nome}" if marca else nome

    # 1. Lowercase
    testo = testo.lower()

    # 2. Rimuovi accenti (è→e, à→a …)
    testo = "".join(
        c for c in unicodedata.normalize("NFD", testo)
        if unicodedata.category(c) != "Mn"
    )

    # 3. Rimuovi apostrofi e caratteri speciali tranne spazio e cifre
    testo = re.sub(r"[''`]", "", testo)
    testo = re.sub(r"[^a-z0-9\s]", " ", testo)

    # 4. Espandi abbreviazioni
    for pattern, sostituzione in _ABBREVIAZIONI.items():
        if callable(sostituzione):
            testo = re.sub(pattern, sostituzione, testo)
        else:
            testo = re.sub(pattern, sostituzione, testo)

    # 5. Rimuovi stopword
    parole = [p for p in testo.split() if p not in _STOPWORDS]
    testo = " ".join(parole)

    # 6. Comprimi spazi multipli
    return re.sub(r"\s+", " ", testo).strip()


# =============================================================================
#  CHIAMATA ALL'LLM (con retry esponenziale)
# =============================================================================

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
def _llm_call(prompt_user: str) -> str:
    """Chiama il modello LLM e restituisce il testo grezzo della risposta."""

    if PROVIDER == "anthropic":
        msg = _client.messages.create(
            model=LLM_MODEL,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt_user}],
        )
        return msg.content[0].text

    elif PROVIDER == "openai":
        resp = _client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt_user},
            ],
            response_format={"type": "json_object"},
        )
        return resp.choices[0].message.content

    elif PROVIDER == "gemini":
        model = genai.GenerativeModel(
            model_name=LLM_MODEL,
            system_instruction=SYSTEM_PROMPT,
            generation_config={"response_mime_type": "application/json"},
        )
        resp = model.generate_content(prompt_user)
        return resp.text

    raise ValueError(f"Provider non supportato: {PROVIDER}")


# =============================================================================
#  VALIDAZIONE E PULIZIA DEL JSON
# =============================================================================

def _estrai_json_dal_testo(testo: str) -> list[dict]:
    """
    Estrae e valida il JSON dalla risposta dell'LLM.
    L'LLM a volte aggiunge backtick o testo prima/dopo il JSON.
    """
    # Rimuovi eventuali markdown code fence
    testo = re.sub(r"```(?:json)?", "", testo).strip()

    # Trova il primo '[' e l'ultimo ']'
    start = testo.find("[")
    end   = testo.rfind("]")
    if start == -1 or end == -1:
        raise ValueError(f"Nessun array JSON trovato nella risposta: {testo[:200]}")

    array = json.loads(testo[start : end + 1])

    if not isinstance(array, list):
        raise ValueError("Il JSON restituito non è un array")

    return array


def _valida_offerta(item: dict, data_inizio_default: date, data_fine_default: date) -> Optional[dict]:
    """
    Valida i campi obbligatori e normalizza i tipi di una singola offerta.
    Restituisce None se l'offerta è malformata.
    """
    # Campo obbligatorio
    if not item.get("nome_prodotto"):
        return None

    # Prezzo: deve essere un numero positivo
    try:
        prezzo = float(str(item.get("prezzo", 0)).replace(",", "."))
        if prezzo <= 0:
            return None
    except (ValueError, TypeError):
        return None

    # Date: usa i default se mancanti o malformate
    def _parse_date(val: Optional[str], default: date) -> date:
        if not val:
            return default
        try:
            return date.fromisoformat(str(val))
        except ValueError:
            return default

    data_i = _parse_date(item.get("data_inizio"), data_inizio_default)
    data_f = _parse_date(item.get("data_fine"),   data_fine_default)
    if data_f < data_i:
        data_f = data_i + timedelta(days=7)

    nome   = str(item["nome_prodotto"]).strip()
    marca  = item.get("marca")
    if isinstance(marca, str):
        marca = marca.strip() or None

    return {
        "nome_prodotto":    nome,
        "marca":            marca,
        "quantita":         item.get("quantita"),
        "prezzo":           round(prezzo, 2),
        "prezzo_originale": item.get("prezzo_originale"),
        "categoria":        item.get("categoria"),
        "data_inizio":      data_i.isoformat(),
        "data_fine":        data_f.isoformat(),
        "nome_normalizzato": normalizza_nome(nome, marca),
    }


# =============================================================================
#  FUNZIONE PUBBLICA PRINCIPALE
# =============================================================================

def parse_volantino(
    raw_text: str,
    catena: str,
    data_inizio: date,
    data_fine: date,
    chunk_size: int = 3000,
) -> list[dict]:
    """
    Trasforma il testo grezzo OCR di un volantino in una lista di offerte
    strutturate e validate, pronte per l'inserimento nel DB.

    Args:
        raw_text:   Testo estratto dall'OCR (può essere molto lungo).
        catena:     Nome della catena GDO, es. "Esselunga".
        data_inizio: Data di inizio validità del volantino.
        data_fine:   Data di fine validità del volantino.
        chunk_size:  Caratteri per chunk (i volantini lunghi vengono spezzati).

    Returns:
        Lista di dict con i campi schema offerta + nome_normalizzato.
    """
    # Spezza testi lunghi in chunk per non sforare il context window
    chunks = _split_in_chunks(raw_text, chunk_size)

    tutte_le_offerte: list[dict] = []

    for i, chunk in enumerate(chunks):
        print(f"  → Chunk {i+1}/{len(chunks)} ({len(chunk)} chars)…")

        prompt_user = USER_PROMPT_TEMPLATE.format(
            catena      = catena,
            data_inizio = data_inizio.strftime("%d/%m/%Y"),
            data_fine   = data_fine.strftime("%d/%m/%Y"),
            raw_text    = chunk,
        )

        try:
            risposta_raw = _llm_call(prompt_user)
            items_raw    = _estrai_json_dal_testo(risposta_raw)
        except (json.JSONDecodeError, ValueError) as e:
            print(f"  ⚠️  Chunk {i+1}: parsing fallito — {e}")
            continue

        for item in items_raw:
            offerta = _valida_offerta(item, data_inizio, data_fine)
            if offerta:
                tutte_le_offerte.append(offerta)

    # Deduplicazione (stesso nome normalizzato + stesso prezzo nel volantino)
    visti: set[tuple] = set()
    risultato: list[dict] = []
    for o in tutte_le_offerte:
        chiave = (o["nome_normalizzato"], o["prezzo"])
        if chiave not in visti:
            visti.add(chiave)
            risultato.append(o)

    print(f"  ✅  Estratte {len(risultato)} offerte uniche da {len(chunks)} chunk(s)")
    return risultato


def _split_in_chunks(testo: str, size: int) -> list[str]:
    """
    Divide il testo in chunk da `size` caratteri, cercando di spezzare
    su una riga vuota per non tagliare a metà un prodotto.
    """
    if len(testo) <= size:
        return [testo]

    chunks = []
    start  = 0
    while start < len(testo):
        end = start + size
        if end >= len(testo):
            chunks.append(testo[start:])
            break
        # Cerca l'ultima riga vuota nel range
        taglio = testo.rfind("\n\n", start, end)
        if taglio == -1:
            taglio = testo.rfind("\n", start, end)
        if taglio == -1:
            taglio = end
        chunks.append(testo[start:taglio])
        start = taglio + 1
    return chunks


# =============================================================================
#  ESEMPIO DI UTILIZZO
# =============================================================================

if __name__ == "__main__":
    testo_ocr_esempio = """
    VOLANTINO ESSELUNGA
    Offerte valide dal 14 al 20 luglio 2025

    SPIRITS & LIQUORI
    Gin Gordon's Dry cl 70           € 9,90   (era € 13,50)
    Vodka Absolut 70cl               € 12,99
    Rum Bacardi Carta Blanca 70 cl   11,50 €
    Aperol Aperitivo Spritz 1L       8,99

    BIRRE
    Birra Moretti bottiglia 66cl     € 1,29
    Heineken lattina 33cl x6         € 5,49   promo -20%
    Corona Extra 35,5cl              1,99€

    LATTICINI
    Parmigiano Reggiano 24 mesi 200g  € 4,29
    Burro Lurpak spalmabile 250g      3,49

    Per informazioni: numero verde 800.XXX.XXX
    Offerte valide fino ad esaurimento scorte.
    """

    offerte = parse_volantino(
        raw_text    = testo_ocr_esempio,
        catena      = "Esselunga",
        data_inizio = date(2025, 7, 14),
        data_fine   = date(2025, 7, 20),
    )

    print("\nRisultato finale:")
    print(json.dumps(offerte, ensure_ascii=False, indent=2))