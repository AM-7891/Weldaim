# ── Importazioni ────────────────────────────────────────────────────────────
import pymupdf                   # libreria per leggere PDF
from dotenv import load_dotenv
import anthropic

# ── Carica la chiave API dal file .env ──────────────────────────────────────
load_dotenv()

# ── Crea il client Anthropic ─────────────────────────────────────────────────
client = anthropic.Anthropic()

# ── Percorso del PDF da leggere ───────────────────────────────────────────────
# Modifica il nome del file con uno dei tuoi PDF normativi
PDF_PATH = r"C:\Users\angma\Desktop\weldaim docs\Normative\EN_ISO\ISO 9606\BS EN ISO 9606-1_2017.pdf"

# ── Funzione che estrae il testo dal PDF ─────────────────────────────────────
def estrai_testo_pdf(percorso):
    documento = pymupdf.open(percorso)   # apre il PDF
    testo = ""
    for pagina in documento:             # scorre ogni pagina
        testo += pagina.get_text()       # estrae il testo della pagina
    return testo

# ── Estrai il testo ───────────────────────────────────────────────────────────
print("Estrazione testo in corso...")
testo_estratto = estrai_testo_pdf(PDF_PATH)

# ── Stampa le prime 500 parole per verifica ───────────────────────────────────
parole = testo_estratto.split()
print("\n── PRIME 500 PAROLE DEL PDF ──────────────────────────────")
print(" ".join(parole[:500]))
print(f"\n── TOTALE PAROLE NEL DOCUMENTO: {len(parole)} ──────────────")
# ── Domanda tecnica da fare a Claude sul documento ────────────────────────────
# Modifica questa domanda con quello che ti interessa sapere dalla norma
DOMANDA = "Quali sono le variabili essenziali che invalidano una qualifica di saldatore secondo questo documento?"

# ── Costruisce il messaggio con testo PDF + domanda ───────────────────────────
# Nota: le norme sono lunghe, quindi limitiamo a 8000 parole per non superare il limite token
testo_per_claude = " ".join(parole[:8000])

messaggio = f"""Sei un esperto ingegnere di saldatura certificato IWE.
Rispondi alla seguente domanda basandoti ESCLUSIVAMENTE sul documento fornito.
Se la risposta non è nel documento, dillo chiaramente.

DOCUMENTO:
{testo_per_claude}

DOMANDA:
{DOMANDA}"""

# ── Invia a Claude ────────────────────────────────────────────────────────────
print("\nInvio domanda a Claude...")
risposta = client.messages.create(
    model="claude-sonnet-4-6",      # sempre questo modello per WeldAIM
    max_tokens=1024,
    messages=[{"role": "user", "content": messaggio}]
)

# ── Stampa la risposta ────────────────────────────────────────────────────────
print("\n── RISPOSTA DI CLAUDE ────────────────────────────────────")
print(risposta.content[0].text)