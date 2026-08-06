# rag_pipeline.py
# d5 — MILESTONE: pipeline RAG completa
# Domanda → ChromaDB trova chunk → Claude risponde citando la sorgente

import chromadb
from anthropic import Anthropic
from dotenv import load_dotenv

# ── 1. CARICA API KEY ─────────────────────────────────────────────────────────
load_dotenv(dotenv_path=".env")
client_claude = Anthropic()

# ── 2. CONNESSIONE CHROMADB ───────────────────────────────────────────────────
client_chroma = chromadb.PersistentClient(path="./chroma_db")
collection = client_chroma.get_collection(name="weldaim_linee_guida")

# ── 3. FUNZIONE: recupera chunk rilevanti ─────────────────────────────────────
def recupera_contesto(domanda: str, n_risultati: int = 4) -> str:
    risultati = collection.query(
        query_texts=[domanda],
        n_results=n_risultati
    )
    blocchi = []
    for chunk, meta in zip(
        risultati["documents"][0],
        risultati["metadatas"][0]
    ):
        sorgente = meta.get("source", "sconosciuta")
        idx = meta.get("chunk_index", "?")
        blocchi.append(f"[Sorgente: {sorgente} | Chunk {idx}]\n{chunk}")
    return "\n\n---\n\n".join(blocchi)

# ── 4. SYSTEM PROMPT ──────────────────────────────────────────────────────────
SYSTEM_PROMPT = """Sei un esperto di coordinamento della saldatura con profonda conoscenza 
delle normative ISO, EN e dei requisiti contrattuali Geismar Italia / Deutsche Bahn.

Rispondi SEMPRE in italiano. Basa le tue risposte ESCLUSIVAMENTE sul contesto normativo 
fornito. Se l'informazione non è nel contesto, dillo esplicitamente.

Per ogni affermazione tecnica, cita la sorgente esatta usando il formato:
[Fonte: nome_documento, Chunk N]

Sii preciso, tecnico e diretto. Non inventare requisiti non presenti nel contesto."""

# ── 5. FUNZIONE: chiama Claude con contesto RAG ───────────────────────────────
def chiedi_a_claude(domanda: str) -> str:
    contesto = recupera_contesto(domanda)
    messaggio_utente = f"""Contesto normativo estratto dai documenti:

{contesto}

---

Domanda: {domanda}"""

    risposta = client_claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[
            {"role": "user", "content": messaggio_utente}
        ]
    )
    return risposta.content[0].text

# ── 6. TEST ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    domande_test = [
        "Quali sono i requisiti per il mock-up e le macrografie?",
        "Cosa deve contenere il certificato 3.1 per il materiale base?",
    ]

    for domanda in domande_test:
        print(f"\n{'='*60}")
        print(f"❓ {domanda}")
        print('='*60)
        risposta = chiedi_a_claude(domanda)
        print(risposta)
        print()