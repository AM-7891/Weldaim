# ── Importazioni ─────────────────────────────────────────────
import anthropic          # libreria Anthropic
import json               # libreria per gestire il formato JSON
from dotenv import load_dotenv  # per leggere la API key dal file .env

# ── Carica la chiave API ──────────────────────────────────────
load_dotenv()

# ── Crea il client ────────────────────────────────────────────
client = anthropic.Anthropic()

# ── Dati di input (simula un testo da un documento WPS/WPQR) ──
testo_documento = """
WPS numero: WPS-001
Processo di saldatura: 111 (MMA)
Materiale base: S355J2 (acciaio strutturale)
Spessore: 10mm
Posizione di saldatura: PA
Materiale d'apporto: E7018
Preciscaldamento: 80°C
"""

# ── Prompt che chiede a Claude di rispondere SOLO in JSON ──────
prompt = f"""
Estrai le informazioni tecniche dal seguente documento di saldatura
e restituisci SOLO un oggetto JSON valido, senza testo aggiuntivo,
senza commenti, senza backtick. Solo JSON puro.

Il JSON deve avere questa struttura:
{{
  "numero_wps": "...",
  "processo": "...",
  "materiale_base": "...",
  "spessore_mm": 0,
  "posizione": "...",
  "materiale_apporto": "...",
  "preciscaldamento_celsius": 0
}}

DOCUMENTO:
{testo_documento}
"""

# ── Chiamata API ──────────────────────────────────────────────
print("Invio richiesta a Claude...")

risposta = client.messages.create(
    model="claude-sonnet-4-6",   # sempre questo modello
    max_tokens=1024,
    messages=[
        {"role": "user", "content": prompt}
    ]
)

# ── Estrai il testo della risposta ────────────────────────────
testo_risposta = risposta.content[0].text

print("\n── RISPOSTA RAW DI CLAUDE ───────────────────────────")
print(testo_risposta)

# ── Converti il testo JSON in un dizionario Python ────────────
try:
    dati = json.loads(testo_risposta)  # trasforma il testo in dati usabili
    print("\n── DATI STRUTTURATI ESTRATTI ────────────────────────")
    print(f"Numero WPS: {dati['numero_wps']}")
    print(f"Processo: {dati['processo']}")
    print(f"Materiale base: {dati['materiale_base']}")
    print(f"Spessore: {dati['spessore_mm']} mm")
    print(f"Posizione: {dati['posizione']}")
    print(f"Materiale apporto: {dati['materiale_apporto']}")
    print(f"Preciscaldamento: {dati['preciscaldamento_celsius']} °C")
except json.JSONDecodeError:
    # Se Claude non risponde in JSON puro, questo blocco gestisce l'errore
    print("ERRORE: Claude non ha risposto in formato JSON valido.")
    print("Risposta ricevuta:", testo_risposta)