"""
WELDAIM - test_supervisor_check2_determinismo.py
Script di test isolato — verifica se check_wpqr_vs_en15085() (cross-check #2
del Supervisore: Agente 1 <-> Agente 5) produce non conformita' o giudizi
diversi tra due chiamate identiche, con INPUT CONGELATI su disco.

Percorso: C:/Users/angma/Desktop/weldaim/agents/test_supervisor_check2_determinismo.py
(stessa cartella di supervisor_agent.py e degli altri test di determinismo)

PERCHE' QUESTO TEST (2026-08-16, seguito del blind-testing su CHECK1):
Stesso protocollo diagnosi-prima gia' applicato a CHECK3 (13/8) e CHECK1
(16/8). Test di blindatura preventiva prima della validazione completa
(Opzione B), non caccia a un bug gia' osservato.

Nota di scope: questo script NON sostituisce test_check2_wpqr_en15085.py
(che resta valido come test "a colpo singolo" per validare il comportamento
del check dopo l'aggiornamento delle mappe di copertura gruppo materiale,
correzione #023-BIS del 10/8). Questo script aggiunge la dimensione mancante:
la RIPETIZIONE con input identico, per isolare non-determinismo dal modello
stesso.

COSA CONFRONTA:
1. I 3 flag booleani per ciascun WPQR analizzato: processo_coerente,
   materiale_coerente, dimensione_coerente (target di confronto piu'
   preciso del solo conteggio NC - dice ESATTAMENTE su quale parametro
   una divergenza si manifesta).
2. Le non conformita' prodotte: codici e severita'.
3. L'esito complessivo (GO/ATTENZIONE/STOP).

ATTENZIONE - PREREQUISITO: richiede report_agent1.json e report_agent5.json
gia' presenti in report_agents/ (run pipeline completo precedente).

COSTO: 2 chiamate a check_wpqr_vs_en15085() (max_tokens=6000 ciascuna) -
frazione minima del costo di un run pipeline completo.

USO (da dentro agents/, stesso motivo spiegato in test_check2_wpqr_en15085.py -
import relativo, non "python -m agents...."):
    cd C:\\Users\\angma\\Desktop\\weldaim\\agents
    python test_supervisor_check2_determinismo.py
"""

import os
import json

import anthropic
from supervisor_agent import (
    carica_report,
    check_wpqr_vs_en15085,
    MODEL,
    REPORT_DIR,
)

_CAMPI_BOOLEANI_WPQR = [
    "processo_coerente",
    "materiale_coerente",
    "dimensione_coerente",
]


def riassumi_wpqr_analizzati(risultato_check):
    """Estrae {nome_wpqr: (processo_coerente, materiale_coerente, dimensione_coerente)}."""
    riassunto = {}
    for w in (risultato_check or {}).get("wpqr_analizzati", []):
        nome = w.get("wpqr", "<SENZA NOME>")
        valori = tuple(w.get(campo, "?") for campo in _CAMPI_BOOLEANI_WPQR)
        riassunto[nome] = valori
    return riassunto


def riassumi_non_conformita(risultato_check):
    """Estrae {codice: severita} dal risultato del check."""
    riassunto = {}
    for nc in (risultato_check or {}).get("non_conformita", []):
        codice = nc.get("codice", "SENZA-CODICE")
        severita = nc.get("severita", "?")
        riassunto[codice] = severita
    return riassunto


def confronta_dizionari(run_a, run_b, etichetta):
    """Stampa un confronto leggibile tra due dizionari chiave->valore e ritorna il numero di differenze."""
    tutte_chiavi = sorted(set(run_a.keys()) | set(run_b.keys()))
    differenze = 0
    print(f"\n  --- {etichetta} ---")
    if not tutte_chiavi:
        print("  (nessuna voce in nessuno dei due run)")
        return 0
    for chiave in tutte_chiavi:
        val_a = run_a.get(chiave, "<ASSENTE NEL RUN 1>")
        val_b = run_b.get(chiave, "<ASSENTE NEL RUN 2>")
        stato = "OK" if val_a == val_b else "DIVERSO"
        if stato == "DIVERSO":
            differenze += 1
        print(f"  [{stato}] {chiave}")
        print(f"      run 1: {val_a}")
        print(f"      run 2: {val_b}")
    print(f"\n  Totale differenze su {etichetta}: {differenze}/{len(tutte_chiavi)}")
    return differenze


def main():
    print("Caricamento report congelati da disco (nessuna nuova estrazione)...")
    report1 = carica_report("agent1")
    report5 = carica_report("agent5")
    if report1 is None or report5 is None:
        print("\n[ERRORE] Manca report_agent1.json o report_agent5.json in report_agents/.")
        print("Esegui prima un run completo della pipeline da app.py.")
        return
    print("  -> report_agent1.json, report_agent5.json caricati.\n")

    client = anthropic.Anthropic()

    print("=" * 60)
    print("  RUN 1 - check_wpqr_vs_en15085()")
    print("=" * 60)
    risultato_run1 = check_wpqr_vs_en15085(report1, report5, client, MODEL)

    print("\n" + "=" * 60)
    print("  RUN 2 - check_wpqr_vs_en15085() - STESSO INPUT del run 1")
    print("=" * 60)
    risultato_run2 = check_wpqr_vs_en15085(report1, report5, client, MODEL)

    # Salva entrambi i risultati grezzi per ispezione manuale
    path_run1 = os.path.join(REPORT_DIR, "test_supervisor_check2_run1.json")
    path_run2 = os.path.join(REPORT_DIR, "test_supervisor_check2_run2.json")
    with open(path_run1, "w", encoding="utf-8") as f:
        json.dump(risultato_run1, f, ensure_ascii=False, indent=2)
    with open(path_run2, "w", encoding="utf-8") as f:
        json.dump(risultato_run2, f, ensure_ascii=False, indent=2)
    print(f"\nRisultati grezzi salvati in:\n  {path_run1}\n  {path_run2}")

    n1 = len((risultato_run1 or {}).get("non_conformita", []))
    n2 = len((risultato_run2 or {}).get("non_conformita", []))
    print(f"\nNumero di non conformita': run1={n1}  run2={n2}  {'OK' if n1 == n2 else 'DIVERSO'}")

    esito1 = (risultato_run1 or {}).get("esito", "?")
    esito2 = (risultato_run2 or {}).get("esito", "?")
    print(f"Esito complessivo del check: run1={esito1}  run2={esito2}  {'OK' if esito1 == esito2 else 'DIVERSO'}")

    print("\n" + "-" * 60)
    print("CONFRONTO STRUTTURATO")
    print("-" * 60)

    riassunto_wpqr_1 = riassumi_wpqr_analizzati(risultato_run1)
    riassunto_wpqr_2 = riassumi_wpqr_analizzati(risultato_run2)
    diff_wpqr = confronta_dizionari(
        riassunto_wpqr_1, riassunto_wpqr_2,
        f"wpqr_analizzati (ordine campi: {', '.join(_CAMPI_BOOLEANI_WPQR)})"
    )

    riassunto_nc_1 = riassumi_non_conformita(risultato_run1)
    riassunto_nc_2 = riassumi_non_conformita(risultato_run2)
    diff_nc = confronta_dizionari(riassunto_nc_1, riassunto_nc_2, "non_conformita (codice -> severita)")

    print("\n" + "=" * 60)
    if diff_wpqr == 0 and diff_nc == 0:
        print("  ESITO: nessuna differenza rilevata. check_wpqr_vs_en15085() e' stabile")
        print("  su questo input congelato con temperature=0.")
    else:
        print("  ESITO: variabilita' residua rilevata con INPUT IDENTICO.")
        if diff_wpqr > 0:
            print(f"  -> {diff_wpqr} divergenza/e nei flag booleani di wpqr_analizzati:")
            print("     il modello valuta diversamente la stessa coerenza WPQR/certificato")
            print("     a parita' di dati in ingresso.")
        if diff_nc > 0:
            print(f"  -> {diff_nc} divergenza/e nelle non conformita' finali (codice/severita').")
        print("  Prossimo passo: esaminare PROMPT_CHECK2 per individuare quale")
        print("  criterio lascia margine discrezionale al modello.")
    print("=" * 60)


if __name__ == "__main__":
    main()
