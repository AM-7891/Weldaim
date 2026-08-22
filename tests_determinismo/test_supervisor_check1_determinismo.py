"""
WELDAIM - test_supervisor_check1_determinismo.py
Script di test isolato — verifica se check_wps_vs_mockup() (cross-check #1
del Supervisore: Agente 1 <-> Agente 3) produce non conformita' o giudizi
diversi tra due chiamate identiche, con INPUT CONGELATI su disco - isolando
il Supervisore dal resto della pipeline.

Percorso consigliato: C:/Users/angma/Desktop/weldaim/agents/test_supervisor_check1_determinismo.py
(stessa cartella di app.py, supervisor_agent.py e degli altri test)

AGGIORNAMENTO 2026-08-20:
Il confronto delle non conformita' NON avviene piu' per "codice" (SUP1-01,
SUP1-02, ...). Il modello puo' rinumerare le stesse NC in ordine diverso
tra un run e l'altro (stesso identikit di non conformita', codice diverso),
generando falsi "DIVERSO" nel confronto posizionale. Il confronto ora si
basa sul CONTENUTO: per ciascuna NC si estrae l'insieme dei nomi mock-up
citati esplicitamente nella descrizione (i nomi file sono sempre riportati
in modo letterale dal modello) e si confronta severita' + insieme mock-up
coinvolti, indipendentemente dal codice assegnato o dall'ordine.

PERCHE' QUESTO TEST (2026-08-15, seguito del test su check_spessore_materiale):
Stesso protocollo diagnosi-prima applicato ieri a CHECK3. Qui non abbiamo
ancora un sospetto specifico come lo scarto macrografico di CHECK3 - questo
e' un test di blindatura preventiva prima della validazione completa
(Opzione B), non la caccia a un bug gia' osservato.

A differenza di CHECK3, il report di check_wps_vs_mockup() contiene un
campo strutturato "mockup_analizzati" con 6 flag booleani per ciascun
mock-up (wps_trovato, materiale_coerente, spessore_coerente,
processo_coerente, posizione_coerente, giunto_coerente). Questo e' un
target di confronto piu' preciso del semplice conteggio di non conformita':
ci dice ESATTAMENTE su quale variabile un'eventuale divergenza tra run
si manifesta, anche se il numero finale di NC risultasse identico.

COSA FA QUESTO SCRIPT:
1. Carica report_agent1.json e report_agent3.json GIA' SALVATI SU DISCO
   dall'ultimo run di pipeline completo (report_agents/) - NESSUNA nuova
   chiamata a OCR/estrazione, input identico byte-per-byte in entrambe le
   chiamate sotto.
2. Chiama supervisore.check_wps_vs_mockup() DUE VOLTE con lo stesso
   identico input.
3. Confronta:
   a) i 6 flag booleani di mockup_analizzati per ciascun mock-up (nome
      usato come chiave di confronto)
   b) le non conformita' prodotte: severita' + insieme dei mock-up
      coinvolti (confronto per contenuto, non per codice posizionale)
   c) l'esito complessivo (GO/ATTENZIONE/STOP)

ATTENZIONE - PREREQUISITO: questo script NON rilancia gli agenti. Deve
esistere report_agents/report_agent1.json e report_agent3.json validi
(cioe' devi aver gia' fatto almeno un run completo della pipeline da
app.py prima di lanciare questo test).

COSTO: 2 chiamate a check_wps_vs_mockup() - una frazione minima del costo
di un run pipeline completo (0 chiamate OCR/estrazione, solo 2 chiamate
al Supervisore).

USO:
    python test_supervisor_check1_determinismo.py
"""

import os
import sys
import json

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'agents'))

import supervisor_agent as supervisore
import anthropic
from utils import BASE_DIR

REPORT_DIR = str(BASE_DIR / "report_agents")

_CAMPI_BOOLEANI_MOCKUP = [
    "wps_trovato",
    "materiale_coerente",
    "spessore_coerente",
    "processo_coerente",
    "posizione_coerente",
    "giunto_coerente",
]


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


def riassumi_mockup_analizzati(risultato_check):
    """Estrae {nome_mockup: (wps_trovato, materiale_coerente, ...)} dal risultato del check."""
    riassunto = {}
    for m in (risultato_check or {}).get("mockup_analizzati", []):
        nome = m.get("mockup", "<SENZA NOME>")
        valori = tuple(m.get(campo, "?") for campo in _CAMPI_BOOLEANI_MOCKUP)
        riassunto[nome] = valori
    return riassunto


def estrai_nomi_mockup_noti(risultato_check):
    """Ritorna la lista dei nomi file mock-up presenti in mockup_analizzati (chiave per il matching testuale)."""
    return [
        m.get("mockup", "")
        for m in (risultato_check or {}).get("mockup_analizzati", [])
        if m.get("mockup")
    ]


def riassumi_non_conformita_per_contenuto(risultato_check, nomi_mockup_noti):
    """
    Costruisce un multiset di non conformita' basato sul CONTENUTO, non sul
    codice posizionale (SUP1-NN). Per ciascuna NC si cerca, all'interno del
    testo di 'descrizione', quali nomi mock-up noti sono citati letteralmente.
    La chiave di confronto e' (severita, frozenset dei mock-up citati).
    Questo rende il confronto insensibile al fatto che il modello numeri
    (SUP1-04 vs SUP1-05) le stesse NC in ordine diverso tra un run e l'altro.
    """
    contatore = {}
    for nc in (risultato_check or {}).get("non_conformita", []):
        descrizione = nc.get("descrizione", "")
        severita = nc.get("severita", "?")
        mockup_citati = frozenset(
            nome for nome in nomi_mockup_noti if nome and nome in descrizione
        )
        chiave = (severita, mockup_citati)
        contatore[chiave] = contatore.get(chiave, 0) + 1
    return contatore


def confronta_dizionari(run_a, run_b, etichetta):
    """Stampa un confronto leggibile tra due dizionari chiave->valore e ritorna il numero di differenze."""
    tutte_chiavi = sorted(set(run_a.keys()) | set(run_b.keys()), key=lambda k: str(k))
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


def confronta_nc_per_contenuto(run_a, run_b, etichetta):
    """
    Confronta due multiset di NC (chiave = severita + mock-up coinvolti).
    A differenza di confronta_dizionari, qui la chiave e' una tupla
    (severita, frozenset) quindi la stampa richiede un adattamento minimo.
    """
    tutte_chiavi = sorted(
        set(run_a.keys()) | set(run_b.keys()),
        key=lambda k: (k[0], sorted(k[1]))
    )
    differenze = 0
    print(f"\n  --- {etichetta} ---")
    if not tutte_chiavi:
        print("  (nessuna NC in nessuno dei due run)")
        return 0
    for chiave in tutte_chiavi:
        severita, mockup_set = chiave
        conteggio_a = run_a.get(chiave, 0)
        conteggio_b = run_b.get(chiave, 0)
        stato = "OK" if conteggio_a == conteggio_b else "DIVERSO"
        if stato == "DIVERSO":
            differenze += 1
        mockup_str = ", ".join(sorted(mockup_set)) if mockup_set else "(nessun mock-up riconosciuto nel testo)"
        print(f"  [{stato}] severita={severita}  mock-up={mockup_str}")
        print(f"      run 1: {conteggio_a} occorrenza/e   run 2: {conteggio_b} occorrenza/e")
    print(f"\n  Totale differenze su {etichetta}: {differenze}/{len(tutte_chiavi)}")
    return differenze


def main():
    print("Caricamento report congelati da disco (nessuna nuova estrazione)...")
    report1 = carica_report_congelato("report_agent1.json")
    report3 = carica_report_congelato("report_agent3.json")
    print("  -> report_agent1.json, report_agent3.json caricati.\n")

    client = anthropic.Anthropic()
    model = supervisore.MODEL

    print("=" * 60)
    print("  RUN 1 - check_wps_vs_mockup()")
    print("=" * 60)
    risultato_run1 = supervisore.check_wps_vs_mockup(report1, report3, client, model)

    print("\n" + "=" * 60)
    print("  RUN 2 - check_wps_vs_mockup() - STESSO INPUT del run 1")
    print("=" * 60)
    risultato_run2 = supervisore.check_wps_vs_mockup(report1, report3, client, model)

    # Salva entrambi i risultati grezzi per ispezione manuale
    with open(os.path.join(REPORT_DIR, "test_supervisor_check1_run1.json"), "w", encoding="utf-8") as f:
        json.dump(risultato_run1, f, ensure_ascii=False, indent=2)
    with open(os.path.join(REPORT_DIR, "test_supervisor_check1_run2.json"), "w", encoding="utf-8") as f:
        json.dump(risultato_run2, f, ensure_ascii=False, indent=2)
    print(f"\nRisultati grezzi salvati in:\n  {REPORT_DIR}\\test_supervisor_check1_run1.json\n  {REPORT_DIR}\\test_supervisor_check1_run2.json")

    n1 = len((risultato_run1 or {}).get("non_conformita", []))
    n2 = len((risultato_run2 or {}).get("non_conformita", []))
    print(f"\nNumero di non conformita': run1={n1}  run2={n2}  {'OK' if n1 == n2 else 'DIVERSO'}")

    esito1 = (risultato_run1 or {}).get("esito", "?")
    esito2 = (risultato_run2 or {}).get("esito", "?")
    print(f"Esito complessivo del check: run1={esito1}  run2={esito2}  {'OK' if esito1 == esito2 else 'DIVERSO'}")

    print("\n" + "-" * 60)
    print("CONFRONTO STRUTTURATO")
    print("-" * 60)

    riassunto_mockup_1 = riassumi_mockup_analizzati(risultato_run1)
    riassunto_mockup_2 = riassumi_mockup_analizzati(risultato_run2)
    diff_mockup = confronta_dizionari(
        riassunto_mockup_1, riassunto_mockup_2,
        f"mockup_analizzati (ordine campi: {', '.join(_CAMPI_BOOLEANI_MOCKUP)})"
    )

    # Nomi mock-up noti: unione tra i due run, per riconoscere le citazioni
    # testuali nelle descrizioni delle NC anche se un run ne cita uno in piu'/meno.
    nomi_mockup_noti = sorted(set(
        estrai_nomi_mockup_noti(risultato_run1) + estrai_nomi_mockup_noti(risultato_run2)
    ))

    riassunto_nc_1 = riassumi_non_conformita_per_contenuto(risultato_run1, nomi_mockup_noti)
    riassunto_nc_2 = riassumi_non_conformita_per_contenuto(risultato_run2, nomi_mockup_noti)
    diff_nc = confronta_nc_per_contenuto(
        riassunto_nc_1, riassunto_nc_2,
        "non_conformita (severita' + mock-up coinvolti, indipendente dal codice)"
    )

    diff_esito = 1 if esito1 != esito2 else 0

    print("\n" + "=" * 60)
    if diff_mockup == 0 and diff_nc == 0 and diff_esito == 0:
        print("  ESITO: nessuna differenza rilevata. check_wps_vs_mockup() e' stabile")
        print("  su questo input congelato con temperature=0.")
    else:
        if diff_esito > 0:
            print(f"  -> divergenza sull'esito complessivo: run1={esito1}  run2={esito2}")
        print("  ESITO: variabilita' residua rilevata con INPUT IDENTICO.")
        if diff_mockup > 0:
            print(f"  -> {diff_mockup} divergenza/e nei flag booleani di mockup_analizzati:")
            print("     il modello valuta diversamente la stessa coerenza WPS/mock-up")
            print("     a parita' di dati in ingresso.")
        if diff_nc > 0:
            print(f"  -> {diff_nc} divergenza/e nelle non conformita' finali (per contenuto,")
            print("     non per codice: quindi e' variabilita' reale, non un artefatto")
            print("     di rinumerazione).")
        print("  Prossimo passo: esaminare PROMPT_CHECK1 per individuare quale")
        print("  criterio lascia margine discrezionale al modello (stesso approccio")
        print("  gia' usato per rendere non derogabile la regola dello scarto")
        print("  macrografico in PROMPT_CHECK3).")
    print("=" * 60)


if __name__ == "__main__":
    main()
    