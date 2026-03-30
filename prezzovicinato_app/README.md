# \# PrezzoRadar MVP — Guida rapida al setup

# 

# \## Struttura del progetto

# 

# ```

# prezzoradar/

# ├── app/

# │   ├── \_\_init\_\_.py       (file vuoto)

# │   ├── database.py       ← modelli + connessione DB

# │   └── main.py           ← FastAPI + endpoint /search

# ├── run\_pipeline.py       ← scraping + Gemini + insert DB

# ├── .env                  ← variabili d'ambiente (non committare!)

# └── requirements.txt

# ```

# 

# \---

# 

# \## 1. Prerequisiti

# 

# \### PostgreSQL con PostGIS

# ```bash

# \# macOS

# brew install postgresql@16 postgis

# brew services start postgresql@16

# 

# \# Ubuntu / WSL

# sudo apt install postgresql postgresql-contrib postgis

# sudo systemctl start postgresql

# ```

# 

# \### Crea il database

# ```sql

# \-- Connettiti a psql come superuser

# psql -U postgres

# 

# CREATE DATABASE prezzoradar;

# \\c prezzoradar

# 

# \-- PostGIS richiede superuser la prima volta

# CREATE EXTENSION IF NOT EXISTS postgis;

# CREATE EXTENSION IF NOT EXISTS pg\_trgm;

# CREATE EXTENSION IF NOT EXISTS unaccent;

# \\q

# ```

# 

# \---

# 

# \## 2. Installa dipendenze Python

# ```bash

# python -m venv venv

# source venv/bin/activate     # Windows: venv\\Scripts\\activate

# 

# pip install fastapi uvicorn\[standard] sqlalchemy psycopg2-binary \\

# &#x20;           geoalchemy2 google-generativeai playwright pydantic-settings

# 

# playwright install chromium

# ```

# 

# \---

# 

# \## 3. Configura le variabili d'ambiente

# Crea il file `.env` nella root del progetto:

# ```env

# DATABASE\_URL=postgresql://postgres:tua\_password@localhost:5432/prezzoradar

# GEMINI\_API\_KEY=AIza...la\_tua\_chiave\_google

# ```

# 

# Ottieni la chiave Gemini gratis su: https://aistudio.google.com/app/apikey

# 

# \---

# 

# \## 4. Lancia la pipeline (popola il DB)

# ```bash

# \# Prima modifica run\_pipeline.py:

# \# - imposta lat/lon reali dei tuoi supermercati test

# \# - verifica che GEMINI\_API\_KEY sia nell'env

# 

# python run\_pipeline.py

# ```

# 

# Output atteso:

# ```

# ============================================================

# &#x20; PrezzoRadar — Pipeline scraping MVP

# ============================================================

# 

# 📦  Inizializzo database…

# ✅  DB inizializzato correttamente.

# 

# ──────────────────────────────────────────────────────────

# &#x20; Catena: ESSELUNGA

# ──────────────────────────────────────────────────────────

# &#x20; 🌐  Apro https://www.esselunga.it/...

# &#x20; 📄  Estratti 12.450 caratteri di testo

# &#x20; 🤖  Invio a Gemini (12.450 caratteri)…

# &#x20; ✅  Estratte 47 offerte valide

# &#x20; ✅  Supermercato 'Esselunga' trovato: uuid...

# &#x20; 💾  Inserite 47 offerte nel DB

# ...

# ```

# 

# \---

# 

# \## 5. Avvia il backend FastAPI

# ```bash

# uvicorn app.main:app --reload --port 8000

# ```

# 

# Testa nel browser: http://localhost:8000/docs

# 

# \---

# 

# \## 6. Testa l'endpoint /search

# ```bash

# \# Cerca "gin gordons" entro 10km da Milano centro

# curl "http://localhost:8000/search?q=gin+gordons\&lat=45.4654\&lon=9.1859\&raggio=10000"

# ```

# 

# Risposta JSON attesa:

# ```json

# {

# &#x20; "query": "gin gordons",

# &#x20; "total": 2,

# &#x20; "raggio": 10000,

# &#x20; "results": \[

# &#x20;   {

# &#x20;     "id": "uuid...",

# &#x20;     "catena": "Esselunga",

# &#x20;     "nome\_prodotto": "Gin Dry",

# &#x20;     "marca": "Gordon's",

# &#x20;     "quantita": "70cl",

# &#x20;     "prezzo": 9.90,

# &#x20;     "prezzo\_originale": 13.50,

# &#x20;     "sconto\_percent": 27,

# &#x20;     "distanza\_km": 0.8,

# &#x20;     "data\_fine": "2025-07-20"

# &#x20;   }

# &#x20; ]

# }

# ```

# 

# \---

# 

# \## 7. Configura Flutter

# 

# In Flutter, il base URL dell'API è:

# \- Simulatore iOS:   `http://localhost:8000`

# \- Emulatore Android: `http://10.0.2.2:8000`

# \- Dispositivo fisico: `http://IP\_DEL\_TUO\_MAC:8000`

# 

# \---

# 

# \## Troubleshooting

# 

# | Problema | Soluzione |

# |---|---|

# | `could not connect to server` | Verifica che PostgreSQL sia avviato e che DATABASE\_URL sia corretta |

# | `extension "postgis" does not exist` | Esegui `CREATE EXTENSION postgis;` come superuser in psql |

# | `playwright: browser not found` | Esegui `playwright install chromium` |

# | Gemini restituisce testo vuoto | Controlla GEMINI\_API\_KEY e quota API |

# | Offerte con prezzo 0 | Normale — vengono filtrate nella validazione del parser |

# | Risultati /search vuoti | Controlla che `data\_fine >= today` e che le coordinate siano nel raggio |

# 

# \---

# 

# \## requirements.txt

# ```

# fastapi>=0.110.0

# uvicorn\[standard]>=0.27.0

# sqlalchemy>=2.0.0

# psycopg2-binary>=2.9.9

# geoalchemy2>=0.14.0

# google-generativeai>=0.5.0

# playwright>=1.42.0

# pydantic>=2.0.0

# pydantic-settings>=2.0.0

# ```

