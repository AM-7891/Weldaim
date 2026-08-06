import json          # per leggere il file chunks.json
import chromadb       # il database vettoriale locale

# ── STEP 1: leggi i chunk dal file JSON ──────────────────────────────────────
print("WeldAIM — Caricamento chunk in ChromaDB")
print("=" * 60)

# Apre il file chunks.json creato da rag_chunker.py (task d2)
with open("chunks.json", "r", encoding="utf-8") as f:
    chunks = json.load(f)

print(f"Chunk trovati nel file JSON: {len(chunks)}")

# ── STEP 2: connettiti a ChromaDB (locale, sul tuo PC) ───────────────────────

# Crea (o riapre) il database nella cartella "chroma_db" dentro weldaim/
client = chromadb.PersistentClient(path="./chroma_db")

# Crea (o riapre) la collezione — è come una "tabella" nel database
# Se la collezione esiste già, la cancella e la ricrea da zero
try:
    client.delete_collection("weldaim_linee_guida")
    print("Collezione esistente rimossa — ricreo da zero")
except:
    pass  # se non esiste, non fa nulla

# Crea la collezione con nome descrittivo
collection = client.create_collection(
    name="weldaim_linee_guida",   # nome della collezione
    metadata={"description": "Linee guida contrattuali Geismar/Deutsche Bahn"}
)

print("Collezione 'weldaim_linee_guida' creata in ChromaDB")

# ── STEP 3: carica i chunk nel database ──────────────────────────────────────
print("\nCaricamento chunk in corso...")

# Prepara le tre liste che ChromaDB richiede:
documents = []   # testo di ogni chunk
metadatas = []   # informazioni aggiuntive (fonte, numero chunk)
ids = []         # identificatore unico per ogni chunk

for chunk in chunks:
    documents.append(chunk["testo"])          # il testo del chunk
    metadatas.append({
        "source": chunk["documento"],           # es. "QT.6495.022_..."
        "chunk_index": chunk["chunk_index"]  # numero progressivo del chunk
    })
    ids.append(chunk["id"])                  # id univoco, es. "QT.6495.022_chunk_0"

# Inserisce tutto nel database in un colpo solo
collection.add(
    documents=documents,
    metadatas=metadatas,
    ids=ids
)

# ── STEP 4: verifica che sia andato tutto bene ───────────────────────────────
count = collection.count()  # conta quanti chunk sono nel database

print(f"\nRISULTATO:")
print(f"  Chunk caricati nel database: {count}")
print(f"  Posizione database:          ./chroma_db/")
print(f"\nProssimo passo (d4): interroga il database con una domanda")
