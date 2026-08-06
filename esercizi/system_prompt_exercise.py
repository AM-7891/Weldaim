# ── Importazioni ────────────────────────────────────────────────────────────
import anthropic          # libreria ufficiale Anthropic
from dotenv import load_dotenv  # per leggere il file .env

# ── Carica la chiave API dal file .env ──────────────────────────────────────
load_dotenv()             # legge .env nella cartella del progetto principale

# ── Crea il client Anthropic ─────────────────────────────────────────────────
client = anthropic.Anthropic()  # usa automaticamente la variabile ANTHROPIC_API_KEY

# ── Funzione helper: aggiunge un messaggio utente alla lista ────────────────
def add_user_message(messages, user_message):
    # Aggiunge un dizionario con ruolo "user" e il testo alla lista messaggi
    messages.append({"role": "user", "content": user_message})

# ── Funzione helper: invia la lista messaggi a Claude con un system prompt ──
def chat(messages, system_prompt=""):
    response = client.messages.create(
        model="claude-sonnet-4-6",          # modello da usare
        max_tokens=1024,                   # lunghezza massima risposta
        system=system_prompt,              # <-- qui va il system prompt
        messages=messages                  # lista dei messaggi
    )
    # Restituisce solo il testo della risposta
    return response.content[0].text

# ════════════════════════════════════════════════════════════════════════════
# ESERCIZIO: System Prompt
# Obiettivo: vedere come cambia la risposta con e senza system prompt
# ════════════════════════════════════════════════════════════════════════════

# ── TEST 1: senza system prompt ──────────────────────────────────────────────
print("=" * 60)
print("TEST 1 — Nessun system prompt")
print("=" * 60)

messages = []   # lista vuota — nuova conversazione
add_user_message(
    messages,
    "Write a Python function that checks a string for duplicate characters.",
)
answer = chat(messages)   # chiamata senza system prompt
print(answer)

# ── TEST 2: con system prompt da esperto conciso ─────────────────────────────
print("\n" + "=" * 60)
print("TEST 2 — Con system prompt")
print("=" * 60)

system = "You are a senior Python developer. \
Reply with clean code only, no explanations, no markdown."

messages = []   # reset — nuova conversazione
add_user_message(
    messages,
    "Write a Python function that checks a string for duplicate characters.",
)
answer = chat(messages, system_prompt=system)   # stessa domanda, system prompt diverso
print(answer)