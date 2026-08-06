# ── Importazioni ────────────────────────────────────────────────────────────
import anthropic
from dotenv import load_dotenv

# ── Carica la chiave API dal file .env ──────────────────────────────────────
load_dotenv()

# ── Crea il client Anthropic ─────────────────────────────────────────────────
client = anthropic.Anthropic()

# ── Funzione che invia la conversazione a Claude ─────────────────────────────
def chat(messages):
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        messages=messages        # invia TUTTA la storia — qui sta la memoria
    )
    return response.content[0].text

# ════════════════════════════════════════════════════════════════════════════
# ESERCIZIO: Multi-turn conversation interattiva
# Scrivi le tue domande nel terminale, Claude ricorda i turni precedenti
# Scrivi "exit" per uscire
# ════════════════════════════════════════════════════════════════════════════

messages = []   # lista vuota — si accumula ad ogni turno

print("Chat avviata. Scrivi 'exit' per uscire.\n")

while True:
    # ── Leggi input utente dal terminale ─────────────────────────────────────
    user_input = input("Tu: ")

    # ── Condizione di uscita ──────────────────────────────────────────────────
    if user_input.lower() == "exit":
        print("Chat terminata.")
        break

    # ── Aggiungi messaggio utente alla storia ─────────────────────────────────
    messages.append({"role": "user", "content": user_input})

    # ── Invia tutta la storia a Claude ───────────────────────────────────────
    answer = chat(messages)
    print(f"Claude: {answer}\n")

    # ── Aggiungi risposta di Claude alla storia — qui sta la memoria ──────────
    messages.append({"role": "assistant", "content": answer})