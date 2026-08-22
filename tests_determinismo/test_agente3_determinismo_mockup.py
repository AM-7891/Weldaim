"""
WELDAIM - test_agente3_determinismo_mockup.py
Script di test isolato — verifica se Agent 3 (check_mockup) produce digest
diversi tra due chiamate identiche sullo stesso set di documenti, ISOLANDO
il problema dal resto della pipeline (Agente 1, altri agenti, Supervisore).

Percorso consigliato: C:/Users/angma/Desktop/weldaim/agents/test_agente3_determinismo_mockup.py
(stessa cartella di app.py e supervisor_agent.py, cosi' gli import relativi funzionano)

DIAGNOSI CHE VERIFICA (2026-08-14): dopo il fix della contaminazione tra run
(il Supervisore rileggeva i report da disco - ora li riceve in memoria), due
run puliti consecutivi sullo stesso identico set di documenti hanno comunque
prodotto NC diverse lato Agent 3: SUP1-01/SUP3-01 (mismatch tipo giunto +
scarto macrografico 11,97mm vs nominale 15mm, entrambi confermati fondati
da IWE) presenti nel run 1, assenti dal run 2 - sostituiti da NC su
argomenti diversi (tracciabilita' materiale). 15 NC totali in entrambi i
run, ma la composizione e' sostanzialmente diversa. Sospetto gia'
documentato in utils.py (changelog 19/07/2026): il chunking taglia per
conteggio caratteri/pagine senza consapevolezza della struttura del
documento - puo' produrre digest diversi tra chiamate identiche.

COSA FA QUESTO SCRIPT (pattern diagnosis-first, non rilancia la pipeline):
1. Carica UNA SOLA VOLTA i digest WPS/WPQR (Agente 1) - cosi' l'input a
   check_mockup() e' identico byte-per-byte in entrambe le chiamate, ed
   eventuale variabilita' osservata e' imputabile SOLO a check_mockup(),
   non a monte (Agente 1 ha gia' TEMPERATURA_ESTRAZIONE=0 e non e' sotto
   sospetto in questo test).
2. Chiama agente3.check_mockup() DUE VOLTE sullo stesso PDF mock-up, con
   esattamente lo stesso testo WPS/WPQR.
3. Salva entrambi i digest grezzi in JSON per ispezione manuale.
4. Confronta automaticamente: (a) numero di voci prodotte, (b) il
   "Giudizio complessivo: ..." estratto da ciascuna voce, (c) tutti i
   valori numerici in mm citati nel testo libero (il punto piu' sensibile
   osservato nei due run di pipeline completa: proprio una misura in mm
   e' comparsa in un run e sparita nell'altro).

COSTO: 2 chiamate a check_mockup() (che puo' fare piu' chiamate interne se
il PDF e' scansionato/lungo) - molto meno costoso di due run completi della
pipeline a 5 agenti + Supervisore.

USO:
    python test_agente3_determinismo_mockup.py

I documenti devono essere gia' presenti in test_docs/07_MOCKUP, 01_WPS e
02_WPQR (stessa struttura usata da app.py) - lo script non gestisce upload,
legge direttamente da disco come fa l'esecuzione diretta degli altri agenti.
"""

import os
import sys
import re
import json
import anthropic

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'agents'))

import agent_wps_wpqr as agente1
import agent_mockup_vt as agente3
from utils import BASE_DIR

TEST_DIR = str(BASE_DIR / "test_docs")
CARTELLA_MOCKUP = os.path.join(TEST_DIR, "07_MOCKUP")
OUTPUT_DIR = str(BASE_DIR / "report_agents")  # stessa cartella dei report, per comodita'

_PATTERN_GIUDIZIO = re.compile(r"Giudizio complessivo:\s*(GO|ATTENZIONE|STOP)", re.IGNORECASE)
_PATTERN_MM = re.compile(r"\b\d{1,3}(?:[.,]\d{1,2})?\s*mm\b")


def carica_contesto_wps_wpqr(client):
    """
    Carica WPS e WPQR UNA SOLA VOLTA (stesso identico input per entrambe le
    chiamate a check_mockup() sotto). Riproduce esattamente la stessa logica
    di app.py per costruire wps_testo_combinato/wpqr_testo_combinato, cosi'
    il contesto passato ad Agent 3 e' identico a quello di un run reale.
    """
    print("Caricamento WPS/WPQR (Agente 1) - una sola volta per questo test...")
    wps_docs = agente1.carica_documenti(agente1.WPS_DIR, "WPS")
    wpqr_digests = agente1.carica_wpqr_chunked(agente1.WPQR_DIR, client, agente1.MODEL)

    wps_testo_combinato = "\n\n---\n\n".join(
        [f"[WPS: {d['nome']}]\n{d['testo']}" for d in wps_docs]
    ) if wps_docs else ""
    wpqr_testo_combinato = "\n\n---\n\n".join(
        [f"[WPQR: {d.get('_nome_file', '?')}]\n{json.dumps(d, ensure_ascii=False, indent=2)}"
         for d in wpqr_digests]
    ) if wpqr_digests else ""

    print(f"  -> {len(wps_docs)} WPS, {len(wpqr_digests)} WPQR caricati.")
    return wps_testo_combinato, wpqr_testo_combinato


def estrai_giudizi(risultati_mockup):
    """Estrae {chiave_voce: giudizio} da una lista risultati_mockup."""
    giudizi = {}
    for i, voce in enumerate(risultati_mockup or []):
        testo = voce.get("analisi", "") if isinstance(voce, dict) else ""
        chiave = voce.get("mockup") or voce.get("file") or f"voce_{i}"
        match = _PATTERN_GIUDIZIO.search(testo)
        giudizi[chiave] = match.group(1).upper() if match else "NON TROVATO"
    return giudizi


def estrai_misure_mm(risultati_mockup):
    """Estrae tutti i valori 'NN mm' trovati nel testo libero di ogni voce."""
    misure = {}
    for i, voce in enumerate(risultati_mockup or []):
        testo = voce.get("analisi", "") if isinstance(voce, dict) else ""
        chiave = voce.get("mockup") or voce.get("file") or f"voce_{i}"
        misure[chiave] = sorted(set(_PATTERN_MM.findall(testo)))
    return misure


def confronta(run_a, run_b, etichetta):
    """Stampa un confronto leggibile tra due dizionari chiave->valore e ritorna il numero di differenze."""
    tutte_chiavi = sorted(set(run_a.keys()) | set(run_b.keys()))
    differenze = 0
    for chiave in tutte_chiavi:
        val_a = run_a.get(chiave, "<ASSENTE NEL RUN 1>")
        val_b = run_b.get(chiave, "<ASSENTE NEL RUN 2>")
        stato = "OK" if val_a == val_b else "DIVERSO"
        if stato == "DIVERSO":
            differenze += 1
        print(f"  [{stato}] {chiave}")
        print(f"      run 1: {val_a}")
        print(f"      run 2: {val_b}")
    print(f"\n  Totale differenze su {etichetta}: {differenze}/{len(tutte_chiavi)}\n")
    return differenze


def main():
    client = anthropic.Anthropic()

    wps_testo_combinato, wpqr_testo_combinato = carica_contesto_wps_wpqr(client)

    pdf_mockup = agente3.trova_file_per_estensione(CARTELLA_MOCKUP, [".pdf"])
    if not pdf_mockup:
        print("ERRORE: nessun PDF trovato in 07_MOCKUP.")
        print(f"Verifica che i mock-up siano presenti in: {CARTELLA_MOCKUP}")
        return

    print(f"\nPDF mock-up trovati: {[os.path.basename(p) for p in pdf_mockup]}")

    print("\n" + "=" * 60)
    print("  RUN 1 - check_mockup()")
    print("=" * 60)
    risultati_run1 = agente3.check_mockup(pdf_mockup, wps_testo_combinato, wpqr_testo_combinato)

    print("\n" + "=" * 60)
    print("  RUN 2 - check_mockup() - STESSO INPUT del run 1")
    print("=" * 60)
    risultati_run2 = agente3.check_mockup(pdf_mockup, wps_testo_combinato, wpqr_testo_combinato)

    # Salva entrambi i digest grezzi per ispezione manuale
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(os.path.join(OUTPUT_DIR, "test_agente3_run1.json"), "w", encoding="utf-8") as f:
        json.dump(risultati_run1, f, ensure_ascii=False, indent=2)
    with open(os.path.join(OUTPUT_DIR, "test_agente3_run2.json"), "w", encoding="utf-8") as f:
        json.dump(risultati_run2, f, ensure_ascii=False, indent=2)
    print(f"\nDigest grezzi salvati in:\n  {OUTPUT_DIR}\\test_agente3_run1.json\n  {OUTPUT_DIR}\\test_agente3_run2.json")

    # Confronto 1: numero di voci prodotte
    n1, n2 = len(risultati_run1 or []), len(risultati_run2 or [])
    print(f"\nNumero di voci in risultati_mockup: run1={n1}  run2={n2}  {'OK' if n1 == n2 else 'DIVERSO'}")

    # Confronto 2: giudizio complessivo per voce
    print("\n" + "-" * 60)
    print("CONFRONTO GIUDIZI COMPLESSIVI (GO/ATTENZIONE/STOP)")
    print("-" * 60)
    giudizi1 = estrai_giudizi(risultati_run1)
    giudizi2 = estrai_giudizi(risultati_run2)
    diff_giudizi = confronta(giudizi1, giudizi2, "giudizi complessivi")

    # Confronto 3: misure in mm citate nel testo (il punto piu' sensibile,
    # visto che una misura in mm e' proprio quella comparsa/sparita tra i
    # due run di pipeline completa)
    print("-" * 60)
    print("CONFRONTO MISURE IN MM CITATE NEL TESTO LIBERO")
    print("-" * 60)
    misure1 = estrai_misure_mm(risultati_run1)
    misure2 = estrai_misure_mm(risultati_run2)
    diff_misure = confronta(misure1, misure2, "misure in mm")

    print("=" * 60)
    if diff_giudizi == 0 and diff_misure == 0 and n1 == n2:
        print("  ESITO: nessuna differenza rilevata tra i due run.")
        print("  Il chunking/estrazione di check_mockup() sembra stabile su questo documento.")
        print("  Se la pipeline completa continua a oscillare, il sospetto si sposta su")
        print("  un altro punto (es. aggregazione nel Supervisore, non ancora escluso).")
    else:
        print("  ESITO: rilevate differenze - confermata variabilita' di estrazione")
        print("  in check_mockup() (Agente 3), indipendente dal Supervisore e dalla")
        print("  contaminazione da disco (gia' risolta separatamente il 2026-08-14).")
        print("  Prossimo passo: ispezionare i due JSON salvati per capire SE la")
        print("  differenza nasce nel chunking (numero/confini dei chunk diversi tra")
        print("  le due chiamate) o nell'aggregazione finale (_aggrega_risultati).")
    print("=" * 60)


if __name__ == "__main__":
    main()
