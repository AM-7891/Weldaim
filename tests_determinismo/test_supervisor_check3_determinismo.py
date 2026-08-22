"""
WELDAIM - test_supervisor_check3_determinismo.py
Script di test isolato — verifica se check_spessore_materiale() (cross-check
#3 del Supervisore: Agente 1 <-> Agente 3 <-> Agente 4) produce non
conformita' diverse tra due chiamate identiche, con INPUT CONGELATI su
disco - isolando il Supervisore dal resto della pipeline.

Percorso consigliato: C:/Users/angma/Desktop/weldaim/agents/test_supervisor_check3_determinismo.py
(stessa cartella di app.py, supervisor_agent.py e del test precedente)

PERCHE' QUESTO TEST (2026-08-14, seguito del test su check_mockup):
Il test precedente (test_agente3_determinismo_mockup.py) ha dimostrato che
check_mockup() di Agente 3 e' STABILE sul dato che conta: la misura
macrografica 11,97 mm (vs nominale 15 mm dichiarato) e' presente
identicamente in entrambe le chiamate, su entrambi i mock-up. Le uniche
differenze erano frasi di prosa derivata (es. "scarto di circa 3 mm"),
non dati mancanti. Se Agente 3 e' stabile ma la pipeline completa ha
comunque perso la NC corrispondente (SUP3-01) in un secondo run di
pipeline, il sospetto si sposta al Supervisore stesso: puo' darsi che
check_spessore_materiale(), pur ricevendo lo stesso identico digest da
Agente 3, decida in modo diverso se/come segnalarlo.

COSA FA QUESTO SCRIPT:
1. Carica report_agent1.json, report_agent3.json, report_agent4.json GIA'
   SALVATI SU DISCO dall'ultimo run di pipeline completo (report_agents/) -
   NESSUNA nuova chiamata a OCR/estrazione, input identico byte-per-byte
   in entrambe le chiamate sotto.
2. Chiama supervisore.check_spessore_materiale() DUE VOLTE con lo stesso
   identico input.
3. Confronta le non conformita' prodotte: codici, severita', e in
   particolare se il tema "scarto macrografico 11,97mm vs 15mm nominale"
   e' presente o assente in ciascun run.

ATTENZIONE - PREREQUISITO: questo script NON rilancia gli agenti. Deve
esistere report_agents/report_agent1.json, report_agent3.json e
report_agent4.json validi (cioe' devi aver gia' fatto almeno un run
completo della pipeline da app.py prima di lanciare questo test).

COSTO: 2 chiamate a check_spessore_materiale() - una frazione minima del
costo di un run pipeline completo (0 chiamate OCR/estrazione, solo 2
chiamate al Supervisore).

USO:
    python test_supervisor_check3_determinismo.py
"""

import os
import sys
import json

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'agents'))

import supervisor_agent as supervisore
import anthropic
from utils import BASE_DIR

REPORT_DIR = str(BASE_DIR / "report_agents")

_PAROLE_CHIAVE_SCARTO_MACROGRAFICO = ["11,97", "11.97", "11,95", "11.95", "11,99", "11.99", "macrografic"]


def carica_report_congelato(nome_file):
    """Carica un report JSON gia' presente su disco (input congelato, nessuna nuova chiamata API)."""
    percorso = os.path.join(REPORT_DIR, nome_file)
    if not os.path.isfile(percorso):
        raise FileNotFoundError(
            f"{nome_file} non trovato in {REPORT_DIR}. "
            f"Esegui prima un run completo della pipeline da app.py."
        )
    with open(percorso, "r", encoding="utf-8") as f:
        return json.load(f)


def riassumi_non_conformita(risultato_check):
    """Estrae {codice: (severita, presenza_tema_scarto_macrografico)} da un risultato di check_spessore_materiale."""
    riassunto = {}
    for nc in (risultato_check or {}).get("non_conformita", []):
        codice = nc.get("codice", "SENZA-CODICE")
        severita = nc.get("severita", "?")
        testo_completo = json.dumps(nc, ensure_ascii=False).lower()
        tema_presente = any(parola.lower() in testo_completo for parola in _PAROLE_CHIAVE_SCARTO_MACROGRAFICO)
        riassunto[codice] = (severita, tema_presente)
    return riassunto


def confronta(run_a, run_b):
    """Stampa un confronto leggibile tra i riassunti di due run e ritorna il numero di differenze."""
    tutti_codici = sorted(set(run_a.keys()) | set(run_b.keys()))
    differenze = 0
    for codice in tutti_codici:
        val_a = run_a.get(codice, "<ASSENTE NEL RUN 1>")
        val_b = run_b.get(codice, "<ASSENTE NEL RUN 2>")
        stato = "OK" if val_a == val_b else "DIVERSO"
        if stato == "DIVERSO":
            differenze += 1
        print(f"  [{stato}] {codice}")
        print(f"      run 1: severita'={val_a[0] if isinstance(val_a, tuple) else val_a}"
              f"{f', tema scarto macrografico={val_a[1]}' if isinstance(val_a, tuple) else ''}")
        print(f"      run 2: severita'={val_b[0] if isinstance(val_b, tuple) else val_b}"
              f"{f', tema scarto macrografico={val_b[1]}' if isinstance(val_b, tuple) else ''}")
    print(f"\n  Totale differenze sui codici NC: {differenze}/{len(tutti_codici)}\n")
    return differenze


def main():
    print("Caricamento report congelati da disco (nessuna nuova estrazione)...")
    report1 = carica_report_congelato("report_agent1.json")
    report3 = carica_report_congelato("report_agent3.json")
    report4 = carica_report_congelato("report_agent4.json")
    print("  -> report_agent1.json, report_agent3.json, report_agent4.json caricati.\n")

    client = anthropic.Anthropic()
    model = supervisore.MODEL

    print("=" * 60)
    print("  RUN 1 - check_spessore_materiale()")
    print("=" * 60)
    risultato_run1 = supervisore.check_spessore_materiale(report1, report3, report4, client, model)

    print("\n" + "=" * 60)
    print("  RUN 2 - check_spessore_materiale() - STESSO INPUT del run 1")
    print("=" * 60)
    risultato_run2 = supervisore.check_spessore_materiale(report1, report3, report4, client, model)

    # Salva entrambi i risultati grezzi per ispezione manuale
    with open(os.path.join(REPORT_DIR, "test_supervisor_check3_run1.json"), "w", encoding="utf-8") as f:
        json.dump(risultato_run1, f, ensure_ascii=False, indent=2)
    with open(os.path.join(REPORT_DIR, "test_supervisor_check3_run2.json"), "w", encoding="utf-8") as f:
        json.dump(risultato_run2, f, ensure_ascii=False, indent=2)
    print(f"\nRisultati grezzi salvati in:\n  {REPORT_DIR}\\test_supervisor_check3_run1.json\n  {REPORT_DIR}\\test_supervisor_check3_run2.json")

    n1 = len((risultato_run1 or {}).get("non_conformita", []))
    n2 = len((risultato_run2 or {}).get("non_conformita", []))
    print(f"\nNumero di non conformita': run1={n1}  run2={n2}  {'OK' if n1 == n2 else 'DIVERSO'}")

    esito1 = (risultato_run1 or {}).get("esito", "?")
    esito2 = (risultato_run2 or {}).get("esito", "?")
    print(f"Esito complessivo del check: run1={esito1}  run2={esito2}  {'OK' if esito1 == esito2 else 'DIVERSO'}")

    print("\n" + "-" * 60)
    print("CONFRONTO NON CONFORMITA' (codice / severita' / tema scarto macrografico)")
    print("-" * 60)
    riassunto1 = riassumi_non_conformita(risultato_run1)
    riassunto2 = riassumi_non_conformita(risultato_run2)
    differenze = confronta(riassunto1, riassunto2)

    tema_run1 = any(presente for _, presente in riassunto1.values())
    tema_run2 = any(presente for _, presente in riassunto2.values())

    print("=" * 60)
    print(f"  Tema 'scarto macrografico 11,97mm vs 15mm' presente in run1: {tema_run1}")
    print(f"  Tema 'scarto macrografico 11,97mm vs 15mm' presente in run2: {tema_run2}")
    print("=" * 60)
    if tema_run1 != tema_run2:
        print("  ESITO: CONFERMATO - il Supervisore (check_spessore_materiale) e' la")
        print("  fonte della variabilita', non Agente 3. Con INPUT IDENTICO, il")
        print("  Supervisore decide diversamente se segnalare lo scarto macrografico.")
        print("  Prossimo passo: rivedere PROMPT_CHECK3 in supervisor_agent.py per")
        print("  rendere non discrezionale la segnalazione di questo scarto quando")
        print("  presente nei dati (come gia' fatto per altre condizioni non")
        print("  derogabili nel prompt, es. Condizione B di check_copertura_wq).")
    elif differenze > 0:
        print("  ESITO: variabilita' residua rilevata, ma non sul tema principale.")
        print("  Il finding chiave (scarto macrografico) e' stabile; altre voci minori")
        print("  variano - impatto minore rispetto al caso precedente, da valutare.")
    else:
        print("  ESITO: nessuna differenza rilevata. Il Supervisore e' stabile su")
        print("  questo input congelato - la variabilita' vista in pipeline completa")
        print("  potrebbe dipendere da un altro fattore non ancora isolato da questi")
        print("  due test (es. variabilita' di Agente 1 o Agente 4 tra run diversi).")
    print("=" * 60)


if __name__ == "__main__":
    main()
