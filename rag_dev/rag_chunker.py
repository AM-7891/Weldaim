# ============================================================
# WeldAIM — rag_chunker.py
# Task d2: Estrazione testo PDF + suddivisione in chunk
# ============================================================

import fitz    # PyMuPDF
import json
import os

CARTELLA_PDF = r"C:\Users\angma\Desktop\weldaim\linee_guida"

PDF_FILES = [
    "QT.6495.022_05-Requisiti carpenterie commessa DB_IT.pdf",
    "QT.6495.023_04-Requisiti  Report Mock-up Commessa DB_IT.pdf",
    "QT.6495.024_04-Requisiti dei Welding Book Commessa DB_IT.pdf",
]

CHUNK_SIZE = 400
CHUNK_OVERLAP = 50
OUTPUT_FILE = r"C:\Users\angma\Desktop\weldaim\chunks.json"


def estrai_testo_pdf(percorso_pdf):
    """Apre un PDF editabile e restituisce il testo completo."""
    print(f"  -> Apertura: {os.path.basename(percorso_pdf)}")
    doc = fitz.open(percorso_pdf)
    testo_totale = ""
    for numero_pagina in range(len(doc)):
        pagina = doc[numero_pagina]
        # Estrazione diretta — nessun OCR, il file e' editabile
        testo_pagina = pagina.get_text()
        testo_totale += f"\n\n[PAGINA {numero_pagina + 1}]\n" + testo_pagina
    parole_totali = len(testo_totale.split())
    print(f"  OK {len(doc)} pagine — {parole_totali} parole estratte")
    doc.close()
    return testo_totale


def crea_chunk(testo, nome_documento, chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    """Divide il testo in blocchi di chunk_size parole con sovrapposizione."""
    parole = testo.split()
    chunks = []
    indice = 0
    numero_chunk = 0
    while indice < len(parole):
        parole_chunk = parole[indice : indice + chunk_size]
        testo_chunk = " ".join(parole_chunk)
        chunk = {
            "id": f"{nome_documento}_chunk_{numero_chunk:04d}",
            "testo": testo_chunk,
            "documento": nome_documento,
            "chunk_index": numero_chunk,
            "parole": len(parole_chunk),
        }
        chunks.append(chunk)
        numero_chunk += 1
        # Overlap: il prossimo chunk riparte indietro di 'overlap' parole
        indice += chunk_size - overlap
    print(f"  OK {len(chunks)} chunk creati per '{nome_documento}'")
    return chunks


def main():
    print("=" * 60)
    print("WeldAIM — Estrazione testo e chunking linee guida")
    print("=" * 60)
    tutti_i_chunk = []
    for nome_file in PDF_FILES:
        percorso_completo = os.path.join(CARTELLA_PDF, nome_file)
        if not os.path.exists(percorso_completo):
            print(f"\nATTENZIONE: File non trovato: {percorso_completo}")
            continue
        print(f"\nDocumento: {nome_file}")
        testo = estrai_testo_pdf(percorso_completo)
        nome_doc = os.path.splitext(nome_file)[0]
        chunks = crea_chunk(testo, nome_doc)
        tutti_i_chunk.extend(chunks)
    print(f"\nSalvataggio in: {OUTPUT_FILE}")
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(tutti_i_chunk, f, ensure_ascii=False, indent=2)
    print(f"\nCOMPLETATO")
    print(f"  Chunk totali: {len(tutti_i_chunk)}")
    print(f"  File output:  {OUTPUT_FILE}")
    print(f"\nProssimo passo (d3): carica chunks.json in ChromaDB")


if __name__ == "__main__":
    main()
