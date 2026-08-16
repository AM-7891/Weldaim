"""
WELDAIM - test_supervisor_check4_determinismo.py
Script di test isolato — verifica se check_copertura_wq() (cross-check #4
del Supervisore: Agente 1 <-> Agente 2 <-> Agente 3) produce non conformita'
o giudizi diversi tra due chiamate identiche, con INPUT CONGELATI su disco.

Percorso: C:/Users/angma/Desktop/weldaim/agents/test_supervisor_check4_determinismo.py

PERCHE' QUESTO TEST (2026-08-16, seguito del blind-testing su CHECK1/CHECK2):
Stesso protocollo diagnosi-prima gia' applicato ai check precedenti. Test di
blindatura preventiva prima della validazione completa (Opzione B).

DIFFERENZE STRUTTURALI RISPETTO A CHECK1/CHECK2 (importanti per il confronto):
- 3 report in input (Agent 1, 2, 3), non 2.
- Niente flag booleani per-elemento paragonabili a mockup_analizzati/
  wpqr_analizzati. Qui confrontiamo:
  (a) i due CONTATORI INTERI saldatori_mockup_distinti e saldatori_wq_distinti
  (b) per ciascun WQ in wq_analizzati: il campo compatibile_con_wps (bool)
  (c) la lista wps_compatibili di ciascun WQ - confrontata come INSIEME
      (set), non come lista ordinata: il modello potrebbe restituire gli
      stessi WPS compatibili in ordine diverso tra run senza che questo
      sia un vero disaccordo di merito.
- Il prompt vieta esplicitamente di generare NC per condizioni soddisfatte
  (fix bug SUP4-01 del 2/8) e fissa la soglia STOP sulla Condizione B come
  non derogabile (aggiornamento 2/8, sostituisce vecchia soglia ATTENZIONE) -
  entrambi i vincoli gia' molto espliciti nel prompt, aspettativa di
  partenza: alta probabilita' di stabilita' gia' al primo giro (come CHECK2).

ATTENZIONE - PREREQUISITO: richiede report_agent1.json, report_agent2.json
e report_agent3.json gia' presenti in report_agents/ (run pipeline completo
precedente).

COSTO: 2 chiamate a check_copertura_wq() (max_tokens=3000 ciascuna) -
frazione minima del costo di un run pipeline completo.

USO (da dentro agents/, stesso motivo import relativo spiegato negli altri
script di questa serie):
    cd C:\\Users\\angma\\Desktop\\weldaim\\agents
    python test_supervisor_check4_determinismo.py
"""

import os
import json

import anthropic
from supervisor_agent import (
    carica_report,
    check_copertura_wq,
    MODEL,
    REPORT_DIR,
)


def riassumi_wq_analizzati(risultato_check):
    """
    Estrae {nome_wq: {"compatibile": bool, "wps_compatibili": frozenset}}.
    wps_compatibili come frozenset per ignorare differenze di solo ordine
    tra run (vedi nota in testa al file).
    """
    riassunto = {}
    for w in (risultato_check or {}).get("wq_analizzati", []):
        nome = w.get("wq", "<SENZA NOME>")
        riassunto[nome] = {
            "compatibile": w.get("compatibile_con_wps"),
            "wps_compatibili": frozenset(w.get("wps_compatibili") or []),
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
    report2 = carica_report("agent2")
    report3 = carica_report("agent3")
    if report1 is None or report2 is None or report3 is None:
        print("\n[ERRORE] Manca report_agent1.json, report_agent2.json o report_agent3.json in report_agents/.")
        print("Esegui prima un run completo della pipeline da app.py.")
        return
    print("  -> report_agent1.json, report_agent2.json, report_agent3.json caricati.\n")

    client = anthropic.Anthropic()

    print("=" * 60)
    print("  RUN 1 - check_copertura_wq()")
    print("=" * 60)
    risultato_run1 = check_copertura_wq(report1, report2, report3, client, MODEL)

    print("\n" + "=" * 60)
    print("  RUN 2 - check_copertura_wq() - STESSO INPUT del run 1")
    print("=" * 60)
    risultato_run2 = check_copertura_wq(report1, report2, report3, client, MODEL)

    # Salva entrambi i risultati grezzi per ispezione manuale
    path_run1 = os.path.join(REPORT_DIR, "test_supervisor_check4_run1.json")
    path_run2 = os.path.join(REPORT_DIR, "test_supervisor_check4_run2.json")
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

    # Confronto dei due contatori interi
    print("\n" + "-" * 60)
    print("CONFRONTO STRUTTURATO")
    print("-" * 60)

    smd1 = (risultato_run1 or {}).get("saldatori_mockup_distinti")
    smd2 = (risultato_run2 or {}).get("saldatori_mockup_distinti")
    swd1 = (risultato_run1 or {}).get("saldatori_wq_distinti")
    swd2 = (risultato_run2 or {}).get("saldatori_wq_distinti")
    print(f"\n  --- Contatori ---")
    print(f"  [{'OK' if smd1 == smd2 else 'DIVERSO'}] saldatori_mockup_distinti: run1={smd1}  run2={smd2}")
    print(f"  [{'OK' if swd1 == swd2 else 'DIVERSO'}] saldatori_wq_distinti: run1={swd1}  run2={swd2}")
    diff_contatori = (smd1 != smd2) + (swd1 != swd2)

    riassunto_wq_1 = riassumi_wq_analizzati(risultato_run1)
    riassunto_wq_2 = riassumi_wq_analizzati(risultato_run2)
    diff_wq = confronta_dizionari(riassunto_wq_1, riassunto_wq_2, "wq_analizzati (compatibile + wps_compatibili come insieme)")

    riassunto_nc_1 = riassumi_non_conformita(risultato_run1)
    riassunto_nc_2 = riassumi_non_conformita(risultato_run2)
    diff_nc = confronta_dizionari(riassunto_nc_1, riassunto_nc_2, "non_conformita (codice -> severita)")

    print("\n" + "=" * 60)
    if diff_contatori == 0 and diff_wq == 0 and diff_nc == 0:
        print("  ESITO: nessuna differenza rilevata. check_copertura_wq() e' stabile")
        print("  su questo input congelato con temperature=0.")
    else:
        print("  ESITO: variabilita' residua rilevata con INPUT IDENTICO.")
        if diff_contatori > 0:
            print(f"  -> divergenza nei contatori saldatori distinti (mockup e/o WQ).")
        if diff_wq > 0:
            print(f"  -> {diff_wq} divergenza/e nella compatibilita' WQ<->WPS.")
        if diff_nc > 0:
            print(f"  -> {diff_nc} divergenza/e nelle non conformita' finali (codice/severita').")
        print("  Prossimo passo: esaminare PROMPT_CHECK4 per individuare quale")
        print("  criterio lascia margine discrezionale al modello.")
    print("=" * 60)


if __name__ == "__main__":
    main()
