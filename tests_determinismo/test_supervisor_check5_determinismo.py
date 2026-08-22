"""
WELDAIM - test_supervisor_check5_determinismo.py
Script di test isolato — verifica se check_materiale_apporto() (cross-check #5
del Supervisore: Agente 1 <-> Agente 4, ULTIMO dei 5 cross-check da blindare)
produce non conformita' o giudizi diversi tra due chiamate identiche, con
INPUT CONGELATI su disco.

Percorso: C:/Users/angma/Desktop/weldaim/agents/test_supervisor_check5_determinismo.py

PERCHE' QUESTO TEST (2026-08-16, chiusura sprint blind-testing):
Stesso protocollo diagnosi-prima gia' applicato ai 4 check precedenti
(CHECK3, CHECK1, CHECK2, CHECK4 - tutti gia' confermati stabili). Questo
e' l'ultimo tassello prima della validazione completa (Opzione B).

DIFFERENZA STRUTTURALE IMPORTANTE RISPETTO AGLI ALTRI CHECK:
Il prompt di CHECK5 impone esplicitamente una "REGOLA DI SINTETICITA'":
gli array controllo_a_wps_vs_wpqr e controllo_b_certificati_vs_wpqr NON
devono contenere voci per elementi coerenti/senza problemi - solo per
quelli con un problema reale (NC o dato non leggibile). Questo significa
che, a differenza di mockup_analizzati (CHECK1) o wpqr_analizzati (CHECK2),
NON possiamo aspettarci un elenco completo e fisso di elementi da
confrontare a ogni run: il confronto va fatto sull'UNIONE degli elementi
comparsi in almeno uno dei due run, verificando che le stesse chiavi
compaiano (o non compaiano) in entrambi.

Due sotto-controlli distinti, confrontati separatamente:
- CONTROLLO A (WPS vs WPQR): chiave = numero WPS
- CONTROLLO B (Certificati vs WPQR): chiave = nome file certificato

ATTENZIONE - PREREQUISITO: richiede report_agent1.json e report_agent4.json
gia' presenti in report_agents/ (run pipeline completo precedente).

COSTO: 2 chiamate a check_materiale_apporto() (max_tokens=6000 ciascuna) -
frazione minima del costo di un run pipeline completo.

USO (da dentro agents/, stesso motivo import relativo spiegato negli altri
script di questa serie):
    cd C:\\Users\\angma\\Desktop\\weldaim\\agents
    python test_supervisor_check5_determinismo.py
"""

import os
import sys
import json

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'agents'))

import anthropic
from supervisor_agent import (
    carica_report,
    check_materiale_apporto,
    MODEL,
    REPORT_DIR,
)


def riassumi_controllo_a(risultato_check):
    """Estrae {numero_wps: {"coerente": ..., "materiale_wps": ..., "materiale_wpqr": ...}}."""
    riassunto = {}
    for voce in (risultato_check or {}).get("controllo_a_wps_vs_wpqr", []):
        chiave = voce.get("wps", "<SENZA NOME>")
        riassunto[chiave] = {
            "coerente": voce.get("coerente"),
            "materiale_apporto_wps": voce.get("materiale_apporto_wps"),
            "materiale_apporto_wpqr": voce.get("materiale_apporto_wpqr"),
        }
    return riassunto


def riassumi_controllo_b(risultato_check):
    """Estrae {nome_certificato: {"trovato_in_wpqr": ..., "wpqr_corrispondente": ..., "certificati_completi": ...}}."""
    riassunto = {}
    for voce in (risultato_check or {}).get("controllo_b_certificati_vs_wpqr", []):
        chiave = voce.get("certificato", "<SENZA NOME>")
        riassunto[chiave] = {
            "trovato_in_wpqr": voce.get("trovato_in_wpqr"),
            "wpqr_corrispondente": voce.get("wpqr_corrispondente"),
            "certificati_completi": voce.get("certificati_completi"),
        }
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
        print("  (nessuna voce in nessuno dei due run - coerente con la regola di sinteticita' se tutto era a posto)")
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
    report4 = carica_report("agent4")
    if report1 is None or report4 is None:
        print("\n[ERRORE] Manca report_agent1.json o report_agent4.json in report_agents/.")
        print("Esegui prima un run completo della pipeline da app.py.")
        return
    print("  -> report_agent1.json, report_agent4.json caricati.\n")

    client = anthropic.Anthropic()

    print("=" * 60)
    print("  RUN 1 - check_materiale_apporto()")
    print("=" * 60)
    risultato_run1 = check_materiale_apporto(report1, report4, client, MODEL)

    print("\n" + "=" * 60)
    print("  RUN 2 - check_materiale_apporto() - STESSO INPUT del run 1")
    print("=" * 60)
    risultato_run2 = check_materiale_apporto(report1, report4, client, MODEL)

    # Salva entrambi i risultati grezzi per ispezione manuale
    path_run1 = os.path.join(REPORT_DIR, "test_supervisor_check5_run1.json")
    path_run2 = os.path.join(REPORT_DIR, "test_supervisor_check5_run2.json")
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

    riassunto_a_1 = riassumi_controllo_a(risultato_run1)
    riassunto_a_2 = riassumi_controllo_a(risultato_run2)
    diff_a = confronta_dizionari(riassunto_a_1, riassunto_a_2, "controllo_a_wps_vs_wpqr (solo voci con problema, per regola di sinteticita')")

    riassunto_b_1 = riassumi_controllo_b(risultato_run1)
    riassunto_b_2 = riassumi_controllo_b(risultato_run2)
    diff_b = confronta_dizionari(riassunto_b_1, riassunto_b_2, "controllo_b_certificati_vs_wpqr (solo voci con problema)")

    riassunto_nc_1 = riassumi_non_conformita(risultato_run1)
    riassunto_nc_2 = riassumi_non_conformita(risultato_run2)
    diff_nc = confronta_dizionari(riassunto_nc_1, riassunto_nc_2, "non_conformita (codice -> severita)")

    print("\n" + "=" * 60)
    if diff_a == 0 and diff_b == 0 and diff_nc == 0:
        print("  ESITO: nessuna differenza rilevata. check_materiale_apporto() e' stabile")
        print("  su questo input congelato con temperature=0.")
        print("\n  *** SPRINT BLIND-TESTING COMPLETATO: tutti e 5 i cross-check ***")
        print("  *** del Supervisore sono ora confermati deterministici.       ***")
    else:
        print("  ESITO: variabilita' residua rilevata con INPUT IDENTICO.")
        if diff_a > 0:
            print(f"  -> {diff_a} divergenza/e nel Controllo A (WPS vs WPQR).")
        if diff_b > 0:
            print(f"  -> {diff_b} divergenza/e nel Controllo B (certificati vs WPQR).")
        if diff_nc > 0:
            print(f"  -> {diff_nc} divergenza/e nelle non conformita' finali (codice/severita').")
        print("  Prossimo passo: esaminare PROMPT_CHECK5 per individuare quale")
        print("  criterio lascia margine discrezionale al modello.")
    print("=" * 60)


if __name__ == "__main__":
    main()
