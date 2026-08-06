# rag_query.py
# d4 — Query semantica: trova i chunk più rilevanti nel DB vettoriale

import chromadb

# ── 1. CONNESSIONE AL DATABASE ─────────────────────────────────────────────────
client = chromadb.PersistentClient(path="./chroma_db")

# ── 2. APERTURA DELLA COLLECTION ───────────────────────────────────────────────
# Non specifichiamo embedding_function — usiamo quello già salvato nel DB
collection = client.get_collection(name="weldaim_linee_guida")

# ── 3. DOMANDA DA CERCARE ──────────────────────────────────────────────────────
domanda = "Quali sono i requisiti per il mock-up?"

# ── 4. QUERY AL DATABASE ───────────────────────────────────────────────────────
risultati = collection.query(
    query_texts=[domanda],
    n_results=3
)

# ── 5. STAMPA RISULTATI ────────────────────────────────────────────────────────
print(f"\n🔍 Domanda: {domanda}")
print("=" * 60)

for i, (chunk, meta, dist) in enumerate(zip(
    risultati["documents"][0],
    risultati["metadatas"][0],
    risultati["distances"][0]
)):
    print(f"\n📄 Risultato {i+1} — Rilevanza: {1 - dist:.2%}")
    print(f"   Sorgente: {meta['source']} | Chunk #{meta['chunk_index']}")
    print(f"   Testo:\n{chunk[:400]}...")
    print("-" * 60)