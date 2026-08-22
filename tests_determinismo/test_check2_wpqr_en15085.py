"""
WELDAIM - test_check2_wpqr_en15085.py
Script di test ISOLATO per validare solo il cross-check #2 del Supervisor
(WPQR vs certificato EN 15085), senza eseguire gli altri 4 check e senza
sovrascrivere report_supervisor.json.

Percorso di destinazione: C:/Users/angma/Desktop/weldaim/agents/test_check2_wpqr_en15085.py
(dentro agents/, accanto a supervisor_agent.py - stessa cartella, import
diretto senza manipolazioni di path)

Uso: dopo aver rigenerato report_agent1.json con lo schema BW/FW aggiornato
e dopo aver aggiornato PROMPT_CHECK2 in agents/supervisor_agent.py con le
mappe di copertura gruppo materiale (correzione dominio #023-BIS), esegui
questo script per verificare solo il comportamento del cross-check #2 su
un input reale (Abbati/DB), senza toccare gli altri check o il report
finale del Supervisor.

Output: stampa a console + salva in report_agents/test_check2_output.json
(stessa cartella degli altri report, per restare visibile facilmente).

IMPORTANTE - come lanciarlo: lancio DIRETTO da dentro la cartella agents/,
esattamente come si lanciano gli altri script agente (es. python
agent_wps_wpqr.py). NON usare "python -m agents.test_check2..." dalla
root: quel metodo cambia come Python risolve i percorsi e rompe l'import
"from utils import ..." dentro supervisor_agent.py (utils.py sta anch'esso
in agents/, e si aspetta di essere lanciato da li').

    cd C:\\Users\\angma\\Desktop\\weldaim\\agents
    python test_check2_wpqr_en15085.py
"""

import os
import sys
import json
import anthropic

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'agents'))

from supervisor_agent import (
    carica_report,
    check_wpqr_vs_en15085,
    MODEL,
    REPORT_DIR,
)

# Output salvato accanto agli altri report, per restare visibile e coerente
OUTPUT_TEST = os.path.join(REPORT_DIR, "test_check2_output.json")


def main():
    print("WeldAIM - Test isolato Cross-check #2 (WPQR vs EN 15085)")
    print(f"   Cartella report: {REPORT_DIR}\n")

    client = anthropic.Anthropic()

    print("Caricamento report_agent1.json e report_agent5.json...")
    report1 = carica_report("agent1")
    report5 = carica_report("agent5")

    if report1 is None or report5 is None:
        print("\n[ERRORE] Manca report_agent1.json o report_agent5.json in report_agents/.")
        print("Rigenera i report mancanti prima di eseguire questo test.")
        return

    print("\nEsecuzione check_wpqr_vs_en15085 (solo questo check, nessun altro)...")
    risultato = check_wpqr_vs_en15085(report1, report5, client, MODEL)

    if risultato is None:
        print("[ERRORE] check_wpqr_vs_en15085 ha ritornato None nonostante entrambi i report fossero presenti.")
        return

    # Stampa leggibile a console
    print("\n" + "=" * 60)
    print("  RISULTATO CROSS-CHECK #2")
    print("=" * 60)
    print(f"  Esito: {risultato.get('esito')}")
    print(f"  Non conformita' rilevate: {len(risultato.get('non_conformita', []))}")
    for nc in risultato.get("non_conformita", []):
        icona = {"STOP": "[STOP]", "ATTENZIONE": "[ATTENZIONE]", "APPUNTO": "[APPUNTO]"}.get(nc.get("severita"), "-")
        print(f"\n  {icona} {nc.get('codice')}")
        print(f"     {nc.get('descrizione')}")
        print(f"     Rif.: {nc.get('riferimento')}")
        if nc.get("conflitto_documentale"):
            print(f"     Conflitto documentale: {nc.get('contesto_conflitto')}")
    print("=" * 60)

    # Salva output dedicato, NON sovrascrive report_supervisor.json
    with open(OUTPUT_TEST, "w", encoding="utf-8") as f:
        json.dump(risultato, f, ensure_ascii=False, indent=2)
    print(f"\n  Output salvato in: {OUTPUT_TEST}")


if __name__ == "__main__":
    main()
