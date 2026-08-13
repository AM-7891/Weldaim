"""
WELDAIM - supervisor_agent.py
Agente Supervisore - esegue i cross-check tra i report dei 5 agenti specializzati.

Percorso di destinazione: C:/Users/angma/Desktop/weldaim/agents/supervisor_agent.py

Cross-check previsti (5 totali):
  #1 - WPS coerente con report mock-up          (Agent 1 <-> Agent 3)            [IMPLEMENTATO]
  #2 - WPQR vs range certificato EN 15085       (Agent 1 <-> Agent 5)            [IMPLEMENTATO]
  #3 - Coerenza materiale/spessore              (Agent 1 <-> Agent 3 <-> Agent 4) [IMPLEMENTATO]
       NOTA: su indicazione IWE si usa Agent 3 (mock-up) al posto di Agent 5
       (welding map/PFC), per evitare complicazioni non necessarie legate
       all'interazione con la PFC (spesso template vuoti - vedi anche
       cancellazione del check #6 VT<->PFC).
  #4 - Copertura WQ su tutti i WPS citati       (Agent 1 <-> Agent 2 <-> Agent 3) [IMPLEMENTATO]
  #5 - Materiale d'apporto: WPS-WPQR e          (Agent 1 <-> Agent 4)            [IMPLEMENTATO]
       certificati-WPQR (doppia sotto-verifica A/B, per nomenclatura
       normativa, non marca/diametro)

Logica di aggregazione: vince sempre la severita' piu' alta tra tutti i check
eseguiti (STOP > ATTENZIONE > APPUNTO > GO), con tag conflitto_documentale
quando pertinente.

NOTA (2026-07-30): aggiunto vincolo esplicito in PROMPT_CHECK4 sulla soglia
di severita' della CONDIZIONE B (copertura WQ). Osservato in test (Abbati/DB,
30/07/2026, codice SUP4-01): il modello aveva scavalcato la soglia ATTENZIONE
prevista esplicitamente dal prompt per la Condizione B, elevandola a STOP con
una motivazione propria ("il saldatore mancante ha eseguito un mock-up
ufficiale... questa circostanza supera il livello ATTENZIONE"), nonostante il
prompt dicesse testualmente "NC ATTENZIONE (non STOP...)". Stesso tipo di
deriva gia' intercettato altrove nel progetto (vedi VINCOLO SULL'ESITO
COMPLESSIVO in CHECK-2 di Agent 3). Aggiunta una riga vincolante che chiude
esplicitamente la possibilita' di deroga discrezionale sulla severita' di
questa condizione.

NOTA (2026-08-08): PROMPT_CHECK2 aggiornato in seguito all'espansione dello
schema digest WPQR in agent_wps_wpqr.py (campo unico range_spessore_qualificato
sostituito con campi separati per tipo di giunto: range_spessore_bw,
range_spessore_fw_t1, range_spessore_fw_t2, altezza_gola_fw). Causa: verificato
su WPQR reale (RINA/Abbati 01/16) che una singola WPQR qualifica comunemente
sia BW sia FW con range diversi - un campo unico causava perdita/ambiguita' del
dato nel confronto con lo scope del certificato EN 15085, non un problema di
qualita' OCR come inizialmente ipotizzato. Il check ora confronta il campo
BW-specifico o FW-specifico a seconda del tipo di giunto in esame, invece di
un confronto generico su un solo valore.

NOTA (2026-08-10): PROMPT_CHECK2 esteso con mappe di copertura gruppo
materiale esplicite (ISO 15614-1 Tabella 5 per acciai gruppi 1/2/3, ISO
15614-2 Tabella 4 per alluminio) e correzione dominio #023 generalizzata
(incoerenza interna gruppo dichiarato vs materiale provino citato nelle
note). Le mappe sono fornite come testo autorevole nel prompt (opzione A -
veloce); refactor a override Python deterministico (opzione B) rimandato
a sessione dedicata, vedi roadmap.

NOTA (2026-08-13): aggiunto temperature=TEMPERATURA_VERDETTO (importata da
utils.py, valore 0) all'unica chiamata client.messages.create() di questo
file, dentro chiama_claude_json() — funzione centralizzata usata da tutti
e 5 i cross-check (#1-#5). PROBLEMA: questa chiamata genera i verdetti
(STOP/ATTENZIONE/APPUNTO) di ogni cross-check ma girava a temperature di
default (1.0) — non deterministica tra run identici (locale vs Streamlit
Cloud); confermato su Streamlit Cloud con SUP3-02 comparso solo in cloud.
Nessuna modifica ai prompt PROMPT_CHECK1...5 ne' alla logica di
aggregazione in aggrega_supervisor.
"""

import os
import json
import anthropic

from utils import pulisci_json, BASE_DIR, TEMPERATURA_VERDETTO  # riusa la utility gia' validata negli altri agenti

# ---------------------------------------------------------------------------
# CONFIGURAZIONE GLOBALE
# ---------------------------------------------------------------------------

# Modello Claude da usare per il lancio demo - non cambiare senza test comparativo
MODEL = "claude-sonnet-4-6"

# Cartella dove i 5 agenti salvano i loro report JSON
REPORT_DIR = str(BASE_DIR / "report_agents")

# Mappa nome file -> chiave interna, cosi' l'orchestratore sa dove cercare ogni report
FILE_REPORT = {
    "agent1": "report_agent1.json",
    "agent2": "report_agent2.json",
    "agent3": "report_agent3.json",
    "agent4": "report_agent4.json",
    "agent5": "report_agent5.json",
}

# Ranking di severita' per calcolare l'esito complessivo (piu' alto = piu' grave)
SEVERITA_RANK = {"STOP": 3, "ATTENZIONE": 2, "APPUNTO": 1, "GO": 0}


# ---------------------------------------------------------------------------
# UTILITY - caricamento report esistenti
# ---------------------------------------------------------------------------

def carica_report(nome_chiave):
    """
    Carica un report JSON di un agente dalla cartella report_agents.
    Ritorna None (senza errore) se il file non esiste ancora - questo permette
    all'orchestratore di saltare i check per cui manca un input, senza crashare.
    """
    percorso = os.path.join(REPORT_DIR, FILE_REPORT[nome_chiave])
    if not os.path.isfile(percorso):
        print(f"  [SKIP] {FILE_REPORT[nome_chiave]} non trovato - report non ancora generato")
        return None
    with open(percorso, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# UTILITY - chiamata Claude con parsing JSON sicuro
# ---------------------------------------------------------------------------

def chiama_claude_json(client, model, prompt, max_tokens=3000, codice_check="SUPX"):
    """
    Esegue una chiamata a Claude e prova a parsare la risposta come JSON.
    Se il parsing fallisce, ritorna un oggetto con una NC di tipo ATTENZIONE
    invece di far crashare l'intero Supervisor - stesso principio di
    error-handling visibile usato in check_en15085 nell'Agent 5.
    """
    risposta = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        # TEMPERATURE=0 (2026-08-13): questa chiamata genera i verdetti di
        # TUTTI i cross-check del Supervisor (STOP/ATTENZIONE/APPUNTO), dato
        # che tutte le funzioni check_* passano da qui. Deve essere
        # deterministica tra run identici (locale vs Streamlit Cloud) - vedi
        # nota changelog in testa al file.
        temperature=TEMPERATURA_VERDETTO,
        messages=[{"role": "user", "content": prompt}]
    )
    testo_pulito = pulisci_json(risposta.content[0].text)
    try:
        return json.loads(testo_pulito)
    except json.JSONDecodeError:
        return {
            "check": codice_check,
            "non_conformita": [{
                "severita": "ATTENZIONE",
                "codice": f"{codice_check}-ERR",
                "descrizione": f"Risposta del cross-check {codice_check} non parsabile come JSON.",
                "riferimento": "Errore interno di parsing",
                "conflitto_documentale": False,
                "contesto_conflitto": ""
            }],
            "esito": "ATTENZIONE",
            "_raw": testo_pulito
        }


# ---------------------------------------------------------------------------
# CROSS-CHECK #1 - WPS coerente con report mock-up (Agent 1 <-> Agent 3)
# ---------------------------------------------------------------------------

PROMPT_CHECK1 = """Sei un IWE (International Welding Engineer) che esegue un controllo incrociato
di coerenza documentale tra un report di analisi WPS/WPQR e un report di
analisi mock-up.

CONTESTO NORMATIVO:
Ogni mock-up deve citare il WPS utilizzato per la sua realizzazione. Le
variabili essenziali dichiarate nel mock-up (processo di saldatura, gruppo
materiale, spessore, posizione di saldatura, tipo di giunto) devono rientrare
nel range qualificato dal WPS citato.

REGOLA CRITICA SUL MATERIALE - NON TRATTARE COME MISMATCH:
La tipologia di fornitura del semilavorato (es. lamiera vs tubolare) NON e'
una variabile vincolante ai fini di questo confronto. Se il mock-up dichiara
un materiale su lamiera e il WPS lo qualifica su tubolare (o viceversa),
questo NON costituisce una non conformita' e non va segnalato neppure come
APPUNTO, purche' grado/gruppo materiale (es. S355J2) e range di spessore
siano coerenti tra i due documenti. Confronta SOLO su: grado/gruppo
materiale, range di spessore, processo, posizione, tipo di giunto -
ignora completamente la forma del semilavorato.

REPORT AGENT 1 (analisi WPS/WPQR):
{report_agent1_json}

REPORT AGENT 3 (analisi mock-up):
{report_agent3_json}

ESEGUI QUESTO CONTROLLO per ciascun mock-up analizzato da Agent 3:

1. ESISTENZA: il WPS citato nel mock-up e' presente tra quelli analizzati da
   Agent 1? Se il WPS citato non risulta tra i documenti caricati, il
   welding book e' incompleto per quel mock-up.
2. GRADO/GRUPPO MATERIALE: il materiale dichiarato nel mock-up (grado e
   norma, es. S355J2+N secondo EN 10025-2) rientra nel gruppo materiale
   qualificato dal WPS (secondo CEN ISO/TR 15608)? Ignora la forma del
   semilavorato (lamiera/tubolare) come da regola sopra.
3. SPESSORE: lo spessore dichiarato nel mock-up rientra nel range di
   spessore qualificato dal WPS?
4. PROCESSO: il processo di saldatura (ISO 4063) dichiarato nel mock-up
   coincide con quello del WPS?
5. POSIZIONE: la posizione di saldatura (ISO 6947) dichiarata nel mock-up
   rientra nel range qualificato dal WPS?
6. TIPO DI GIUNTO: il tipo di giunto (BW, BW parziale penetrazione, FW,
   ecc.) dichiarato nel mock-up e' coerente con quello qualificato dal WPS?

REGOLE DI SEVERITA':
- WPS citato nel mock-up non trovato tra i documenti caricati -> NC STOP:
  welding book incompleto, impossibile verificare la copertura del giunto.
- Una o piu' variabili essenziali (grado materiale, spessore, processo,
  posizione, giunto) FUORI dal range qualificato dal WPS -> NC STOP.
- Una variabile essenziale non chiaramente dichiarata in uno dei due
  documenti (dato mancante, non un mismatch) -> NC APPUNTO, specificando
  quale dato manca.
- Mismatch sulla sola forma del semilavorato (lamiera/tubolare) -> NON
  segnalare, nessuna non conformita'.
- Tutte le variabili essenziali verificabili sono coerenti -> nessuna non
  conformita' per quel mock-up.

Per ogni non conformita' rilevata, se emerge un conflitto diretto tra
affermazioni dei due report, aggiungi "conflitto_documentale": true e
specifica in "contesto_conflitto" quali documenti e quali valori sono in
conflitto.

Rispondi SOLO in JSON con questa struttura, nessun testo fuori dal JSON:
{{
  "check": "wps_vs_mockup",
  "mockup_analizzati": [
    {{
      "mockup": "nome file mock-up",
      "wps_citato": "numero WPS",
      "wps_trovato": true/false,
      "materiale_coerente": true/false/null,
      "spessore_coerente": true/false/null,
      "processo_coerente": true/false/null,
      "posizione_coerente": true/false/null,
      "giunto_coerente": true/false/null,
      "note": "spiegazione sintetica"
    }}
  ],
  "non_conformita": [
    {{
      "severita": "STOP" | "ATTENZIONE" | "APPUNTO",
      "codice": "SUP1-NN",
      "descrizione": "...",
      "riferimento": "...",
      "conflitto_documentale": true/false,
      "contesto_conflitto": "..."
    }}
  ],
  "esito": "GO" | "ATTENZIONE" | "STOP"
}}
"""


def check_wps_vs_mockup(report_agent1, report_agent3, client, model):
    """
    Cross-check #1: verifica che il/i WPS citati nei mock-up esistano nel
    welding book e che le variabili essenziali (materiale, spessore,
    processo, posizione, giunto) rientrino nel range qualificato.
    La forma del semilavorato (lamiera/tubolare) NON e' un criterio di
    mismatch - vedi regola esplicita nel prompt.
    Ritorna None se manca uno dei due report necessari.
    """
    if report_agent1 is None or report_agent3 is None:
        print("  [SKIP] check_wps_vs_mockup: report Agent 1 o Agent 3 mancante")
        return None

    print("  [CROSS-CHECK #1] WPS vs mock-up...")

    prompt = PROMPT_CHECK1.format(
        report_agent1_json=json.dumps(report_agent1, ensure_ascii=False, indent=2),
        report_agent3_json=json.dumps(report_agent3, ensure_ascii=False, indent=2)
    )

    risultato = chiama_claude_json(client, model, prompt, max_tokens=3000, codice_check="SUP1")
    risultato["tool"] = "check_wps_vs_mockup"
    return risultato


# ---------------------------------------------------------------------------
# CROSS-CHECK #2 - WPQR (Agent 1) vs Certificato EN 15085 (Agent 5)
# ---------------------------------------------------------------------------

PROMPT_CHECK2 = """Sei un IWE (International Welding Engineer) che esegue un controllo incrociato
di coerenza documentale tra un report di analisi WPQR e un report di analisi
di un certificato EN 15085.

CONTESTO NORMATIVO:
Il certificato EN 15085 (rilasciato da ente accreditato, es. Bureau Veritas,
o tramite piattaforma Jointcert) definisce lo scope di certificazione
dell'azienda tramite tre variabili tecniche: processo di saldatura (nomenclatura
ISO 4063), gruppo materiale (CEN ISO/TR 15608), e range dimensionale
(spessore per giunti BW, gola per giunti FW).

I WPQR (Welding Procedure Qualification Record) qualificano processi
produttivi specifici entro determinati range. Un WPQR e' coperto dal
certificato SOLO se processo, gruppo materiale e range dimensionale che
qualifica rientrano nello scope dichiarato dal certificato.

NON considerare rilevanti eventuali differenze di nomenclatura/numerazione
tra i WPQR citati nel report Agent 1 e riferimenti a PQR/WPQR eventualmente
presenti nel report Agent 5 (es. certificati Jointcert compilati in modo
non standard) - questo confronto per nome NON e' oggetto del controllo.
L'unico confronto valido e' sui RANGE TECNICI (processo, materiale,
dimensione), non sui nomi dei documenti.

FONTE DEI DATI TECNICI WPQR (importante — leggi qui, non altrove nel
report Agent 1): i dati tecnici di ciascuna WPQR si trovano nel campo
"wpqr_digests" del report Agent 1 — una lista con un digest per ciascuna
WPQR analizzata. Questo campo contiene i dati estratti direttamente dai
documenti WPQR originali (via OCR/chunking) e va usato come fonte primaria
per i tre controlli sotto. Il campo "dettaglio_check" del report Agent 1
contiene invece solo gli ESITI dei check interni di Agent 1 (conformi/non
conformi), non i dati tecnici grezzi — non affidarti a quello per estrarre
processo/materiale/range.

IMPORTANTE SUL RANGE DIMENSIONALE (spessore/gola): ogni digest in
"wpqr_digests" può riportare FINO A 4 campi di spessore distinti e NON
intercambiabili tra loro:
- range_spessore_bw (spessore materiale base per giunti Butt Joint/BW)
- range_spessore_fw_t1 e range_spessore_fw_t2 (spessore materiale base
  per giunti Fillet/FW — due lati del giunto)
- altezza_gola_fw (gola del cordone d'angolo, campo "Throat thickness" —
  particolarmente rilevante per giunti FW single pass)
Una WPQR può avere valorizzati SOLO i campi BW, SOLO i campi FW, o
ENTRAMBI se qualifica entrambi i tipi di giunto (caso comune - vedi campo
tipo_giunto_qualificato del digest). Quando confronti il range dimensionale
col certificato EN 15085, usa il campo che corrisponde al tipo di giunto
rilevante per il confronto in corso — se il certificato specifica un range
per giunti BW, confrontalo con range_spessore_bw, non con i campi FW, e
viceversa. Non fondere i valori di campi diversi in un unico confronto.

Se un digest ha un campo null o assente, quel singolo parametro non e'
verificabile per quella WPQR (vedi regola APPUNTO sotto) — ma prima di
dichiarare un dato mancante, controlla attentamente in wpqr_digests: e' la
fonte dove il dato, se estratto, si trova.

MAPPE DI COPERTURA GRUPPO MATERIALE (non derogabili - applica ESATTAMENTE
questi valori, non dedurre autonomamente dalla normativa. Copertura =
"il gruppo materiale del provino qualifica anche i gruppi elencati").

Acciai al carbonio - arco metallico (ISO 15614-1 Tabella 5 + nota a,
ISO 15608 Tabella 1, limitato ai gruppi 1/2/3 - gli unici in uso su
questo progetto):
  1.1 -> copre: 1.1
  1.2 -> copre: 1.1, 1.2
  1.3 -> copre: 1.1, 1.2, 1.3
  1.4 -> copre: 1.4 (isolato, criterio corrosione atmosferica, non
         ordinato per snervamento)
  2.1 -> copre: 1.1, 1.2, 2.1
  2.2 -> copre: 1.1, 1.2, 2.1, 2.2
  3.1 -> copre: 1.1, 1.2, 2.1, 3.1
  3.2 -> copre: 1.1, 1.2, 2.1, 3.1, 3.2
  3.3 -> copre: 3.3 (isolato, precipitation-hardened, nessuna gerarchia
         esplicita con gli altri sottogruppi)
Se nel digest o nel certificato compare un gruppo materiale NON elencato
sopra (es. 4, 5, 6, 7, 8, 9, 10, 11), NON applicare queste mappe - segnala
il dato come APPUNTO (gruppo materiale fuori dalla copertura attualmente
codificata, richiede verifica manuale IWE) invece di dedurre una
copertura.

Alluminio - arco (ISO 15614-2 Tabella 4) - copertura per giunti simili,
specifica per coppia, non monotona:
  21   -> copre: 21
  22.1 -> copre: 22.1, 22.2
  22.2 -> copre: 22.2, 22.1
  22.3 -> copre: 22.3, 22.1, 22.2, 22.4
  22.4 -> copre: 22.4, 22.1, 22.2, 22.3
  23.1 -> copre: 23.1, 22.1, 22.2 (solo se filler Al-Mg), 22.3 (solo se
          filler Al-Mg), 22.4 (solo se filler Al-Mg)
  23.2 -> copre: 23.2, 23.1, 22.1, 22.2 (solo se filler Al-Mg), 22.3 (solo
          se filler Al-Mg), 22.4 (solo se filler Al-Mg)
  24.1 -> copre: 24.1
  24.2 -> copre: 24.2, 24.1, 23.1 (solo se filler Al-Si)
  25   -> copre: 25, 24.1, 24.2
  26   -> copre: 26, 24.1 (solo per getti), 24.2 (solo per getti),
          25 (solo per getti)
NOTA: se una copertura e' condizionata a un filler specifico (Al-Mg,
Al-Si) o a getti, verifica quel dato nel digest prima di concludere che la
copertura si applica - se il dato sul filler/getto non e' disponibile nei
report, segnala come APPUNTO (dato non verificabile), non come automatica
non-copertura.

REGOLA SPECIFICA - INCOERENZA INTERNA GRUPPO MATERIALE (correzione dominio
#023, 2026-08-09, generalizzata 2026-08-10): puo' capitare che il campo
gruppo materiale dichiarato nel digest WPQR sia INCOERENTE con il gruppo
materiale derivabile dal materiale del provino citato nelle note dello
STESSO documento (es. campo range = "1-1" ma materiale provino S355J2+N =
gruppo reale 1.2). Questo NON e' un'ambiguita' di notazione benigna da
risolvere con documenti esterni: e' un ERRORE DI COMPILAZIONE del
certificato, verificabile internamente confrontando due campi dello stesso
documento. Se rilevi questo tipo di incoerenza interna, NON generare un
APPUNTO generico che rimanda a "verificare col certificato originale" -
genera invece una NC ATTENZIONE che spiega esplicitamente l'incoerenza
interna (quale campo dichiara cosa, quale gruppo risulta effettivamente
dal materiale del provino citato nello stesso documento, e se quel gruppo
reale risulta comunque coperto dallo scope del certificato applicando le
mappe sopra). La copertura effettiva dello scope non elimina la necessita'
di segnalare l'errore di compilazione del documento.

REPORT AGENT 1 (analisi WPQR):
{report_agent1_json}

REPORT AGENT 5 (analisi certificato EN 15085):
{report_agent5_json}

ESEGUI QUESTO CONTROLLO per ciascun WPQR analizzato da Agent 1:

1. PROCESSO: il processo di saldatura qualificato dal WPQR rientra tra i
   processi elencati nello scope del certificato EN 15085?
2. GRUPPO MATERIALE: il gruppo materiale (secondo CEN ISO/TR 15608)
   qualificato dal WPQR rientra nei gruppi materiale dello scope del
   certificato? Applica le MAPPE DI COPERTURA GRUPPO MATERIALE sopra per
   determinare quali gruppi risultano coperti dal gruppo del provino.
3. RANGE DIMENSIONALE: il range di spessore (BW - usa range_spessore_bw)
   o di gola (FW - usa altezza_gola_fw, o in sua assenza range_spessore_fw_t1/
   range_spessore_fw_t2) qualificato dal WPQR rientra nel range dimensionale
   dichiarato dal certificato - SOLO SE il certificato riporta un range
   dimensionale esplicito per quel tipo di giunto. Se il certificato non
   specifica dimensioni, o se il WPQR non qualifica quel tipo di giunto,
   segnala come dato non verificabile, NON come non conformita'.

REGOLE DI SEVERITA':
- Se NESSUNO dei parametri effettivamente verificabili (processo, materiale,
  range dimensionale) trova corrispondenza nello scope del certificato -
  cioe' il WPQR risulta completamente scoperto, senza alcuna sovrapposizione
  tecnica col certificato -> NC STOP: il WPQR non risulta coperto da alcuna
  certificazione EN 15085 valida.
- Se ALMENO UNO dei parametri verificabili e' coerente ma uno o piu' altri
  risultano fuori scope (mismatch parziale) -> NC ATTENZIONE (mai APPUNTO:
  resta un gap di copertura certificativa sostanziale, anche se non totale).
- Incoerenza interna tra gruppo materiale dichiarato nel campo range e
  gruppo derivabile dal materiale del provino citato nelle note dello
  stesso documento (vedi regola sopra) -> NC ATTENZIONE, anche se il
  gruppo effettivo risulta comunque coperto dallo scope del certificato.
  Il testo della NC deve spiegare l'incoerenza interna, non rimandare
  genericamente a verifiche esterne.
- Se un dato necessario al confronto manca in uno dei due report (es.
  range dimensionale non estratto dal certificato, o WPQR che non qualifica
  quel tipo di giunto) -> APPUNTO, specificando quale dato manca. Un dato
  mancante NON conta come mismatch ai fini della regola STOP/ATTENZIONE
  sopra - valuta solo sui parametri che sono stati effettivamente
  confrontabili.
- Se tutti i parametri verificabili sono coerenti -> nessuna non conformita'
  per quel WPQR.

Per ogni non conformita' rilevata, se emerge un conflitto diretto tra
affermazioni dei due report (es. un valore dichiarato in un report
contraddice un valore nell'altro), aggiungi il campo "conflitto_documentale":
true e specifica in "contesto_conflitto" quali documenti e quali valori
sono in conflitto.

Rispondi SOLO in JSON con questa struttura, nessun testo fuori dal JSON:
{{
  "check": "wpqr_vs_en15085",
  "wpqr_analizzati": [
    {{
      "wpqr": "nome file WPQR",
      "processo_coerente": true/false/null,
      "materiale_coerente": true/false/null,
      "dimensione_coerente": true/false/null,
      "note": "spiegazione sintetica"
    }}
  ],
  "non_conformita": [
    {{
      "severita": "STOP" | "ATTENZIONE" | "APPUNTO",
      "codice": "SUP2-NN",
      "descrizione": "...",
      "riferimento": "...",
      "conflitto_documentale": true/false,
      "contesto_conflitto": "..."
    }}
  ],
  "esito": "GO" | "ATTENZIONE" | "STOP"
}}
"""


def check_wpqr_vs_en15085(report_agent1, report_agent5, client, model):
    """
    Cross-check #2: confronta i WPQR analizzati da Agent 1 con lo scope
    del certificato EN 15085 analizzato da Agent 5. Usa i campi digest
    BW/FW-specifici (aggiornato 2026-08-08) e applica le mappe di
    copertura gruppo materiale (ISO 15614-1 Tabella 5 acciai gruppi 1/2/3,
    ISO 15614-2 Tabella 4 alluminio) aggiunte 2026-08-10 - vedi nota in
    testa al file.
    Ritorna None se manca uno dei due report necessari.
    """
    if report_agent1 is None or report_agent5 is None:
        print("  [SKIP] check_wpqr_vs_en15085: report Agent 1 o Agent 5 mancante")
        return None

    print("  [CROSS-CHECK #2] WPQR vs certificato EN 15085...")

    prompt = PROMPT_CHECK2.format(
        report_agent1_json=json.dumps(report_agent1, ensure_ascii=False, indent=2),
        report_agent5_json=json.dumps(report_agent5, ensure_ascii=False, indent=2)
    )

    risultato = chiama_claude_json(client, model, prompt, max_tokens=6000, codice_check="SUP2")
    risultato["tool"] = "check_wpqr_vs_en15085"
    return risultato


# ---------------------------------------------------------------------------
# CROSS-CHECK #3 - Coerenza materiale/spessore (Agent 1 <-> Agent 3 <-> Agent 4)
# NOTA: rispetto all'intestazione originale del file (che elencava Agent 5),
# su indicazione IWE si usa Agent 3 (mock-up) al posto di Agent 5 (welding
# map/PFC) - evita complicazioni non necessarie nell'interazione con la PFC.
# ---------------------------------------------------------------------------

PROMPT_CHECK3 = """Sei un IWE (International Welding Engineer) che esegue un controllo incrociato
di coerenza su grado materiale e spessore tra tre fonti: WPS/WPQR, mock-up
e certificato materiale base.

CONTESTO NORMATIVO:
Il grado/gruppo materiale e lo spessore devono essere coerenti tra tutti e
tre i documenti: il WPS deve qualificare quel grado e quello spessore, il
mock-up deve dichiarare lo stesso grado e spessore in uso, e il certificato
materiale base (3.1) deve attestare un materiale dello stesso grado e
spessore compatibile.

REGOLA - NON TRATTARE COME MISMATCH:
La tipologia di fornitura del semilavorato (lamiera/tubolare) NON e'
vincolante. Confronta solo su grado/gruppo materiale e spessore.

REPORT AGENT 1 (analisi WPS/WPQR):
{report_agent1_json}

REPORT AGENT 3 (analisi mock-up):
{report_agent3_json}

REPORT AGENT 4 (analisi certificati materiale base):
{report_agent4_json}

ESEGUI QUESTO CONTROLLO:

1. GRADO MATERIALE: il grado/gruppo materiale dichiarato nel mock-up
   coincide con quello qualificato dal WPS e con quello attestato dal
   certificato materiale?
2. SPESSORE: lo spessore dichiarato nel mock-up rientra nel range
   qualificato dal WPS ed e' attestato da un certificato materiale
   corrispondente (stesso spessore o spessore compatibile)?

REGOLE DI SEVERITA':
- Grado materiale o spessore NON coerente tra i tre documenti (mismatch
  sostanziale, es. certificato per spessore diverso da quello dichiarato
  nel mock-up/WPS) -> NC STOP.
- Nessun certificato disponibile per il grado/spessore dichiarato ->
  NC STOP (coerente con severita' zero-tolerance gia' applicata da Agent 4
  per assenza di certificato).
- Certificato presente ma con lieve scostamento dimensionale plausibile
  (es. tolleranza di laminazione) -> NC ATTENZIONE.
- Dato mancante in uno dei tre report (non un mismatch, un'assenza) ->
  NC APPUNTO, specificando quale dato manca.
- Tutti coerenti -> nessuna non conformita'.

Per ogni non conformita' rilevata, se emerge un conflitto diretto tra
affermazioni dei report, aggiungi "conflitto_documentale": true e
specifica in "contesto_conflitto" quali documenti e quali valori sono in
conflitto.

Rispondi SOLO in JSON con questa struttura, nessun testo fuori dal JSON:
{{
  "check": "coerenza_materiale_spessore",
  "confronti": [
    {{
      "mockup": "nome file mock-up",
      "grado_materiale_coerente": true/false/null,
      "spessore_coerente": true/false/null,
      "certificato_disponibile": true/false,
      "note": "spiegazione sintetica"
    }}
  ],
  "non_conformita": [
    {{
      "severita": "STOP" | "ATTENZIONE" | "APPUNTO",
      "codice": "SUP3-NN",
      "descrizione": "...",
      "riferimento": "...",
      "conflitto_documentale": true/false,
      "contesto_conflitto": "..."
    }}
  ],
  "esito": "GO" | "ATTENZIONE" | "STOP"
}}
"""


def check_spessore_materiale(report_agent1, report_agent3, report_agent4, client, model):
    """
    Cross-check #3: verifica coerenza di grado materiale e spessore tra
    WPS/WPQR (Agent 1), mock-up (Agent 3) e certificato materiale base
    (Agent 4). Su indicazione IWE, usa Agent 3 al posto di Agent 5
    (originariamente previsto nell'intestazione del file) per evitare
    complicazioni non necessarie nell'interazione con la PFC.
    Ritorna None se manca uno dei tre report necessari.

    NOTA FIRMA: la firma di questa funzione e' cambiata rispetto al
    placeholder originale (report_agent4, report_agent5 -> report_agent3,
    report_agent4). Vedi la chiamata aggiornata in run_supervisor().
    """
    if report_agent1 is None or report_agent3 is None or report_agent4 is None:
        print("  [SKIP] check_spessore_materiale: uno o piu' report mancanti (1/3/4)")
        return None

    print("  [CROSS-CHECK #3] Coerenza materiale/spessore (WPS-mockup-certificato)...")

    prompt = PROMPT_CHECK3.format(
        report_agent1_json=json.dumps(report_agent1, ensure_ascii=False, indent=2),
        report_agent3_json=json.dumps(report_agent3, ensure_ascii=False, indent=2),
        report_agent4_json=json.dumps(report_agent4, ensure_ascii=False, indent=2)
    )

    risultato = chiama_claude_json(client, model, prompt, max_tokens=3000, codice_check="SUP3")
    risultato["tool"] = "check_spessore_materiale"
    return risultato


# ---------------------------------------------------------------------------
# CROSS-CHECK #4 - Copertura WQ su tutti i WPS citati (Agent 1 <-> Agent 2 <-> Agent 3)
# ---------------------------------------------------------------------------

PROMPT_CHECK4 = """Sei un IWE (International Welding Engineer) che esegue un controllo incrociato
sulla copertura delle qualifiche saldatore (WQ) rispetto ai mock-up e ai WPS
del welding book.

CONTESTO NORMATIVO E CONTRATTUALE:
Per requisito normativo/contrattuale, ogni mock-up deve essere realizzato e
documentato da ALMENO 2 saldatori distinti. Allo stesso modo, il welding
book deve includere le qualifiche (WQ/patentini) di ALMENO 2 saldatori
distinti che coprano i giunti dei mock-up.

REGOLA IMPORTANTE - NON RICHIEDERE CORRISPONDENZA 1:1:
Non e' necessario che il numero di saldatori presenti nei mock-up coincida
esattamente con il numero di saldatori con WQ caricati nel welding book.
Possono esistere saldatori con WQ che qualificano SOLO giunti standard (non
coperti da mock-up) - questo e' normale e non va segnalato come anomalia.
Il controllo su questo punto riguarda SOLO il conteggio minimo di saldatori
distinti coperti da mock-up e il conteggio minimo di WQ distinti presenti,
non la loro corrispondenza reciproca.

REPORT AGENT 1 (analisi WPS/WPQR):
{report_agent1_json}

REPORT AGENT 2 (analisi qualifiche saldatore WQ):
{report_agent2_json}

REPORT AGENT 3 (analisi mock-up):
{report_agent3_json}

ESEGUI QUESTI CONTROLLI:

1. NUMERO SALDATORI NEI MOCK-UP: quanti saldatori distinti risultano
   citati/coperti complessivamente nei mock-up analizzati da Agent 3?
2. NUMERO WQ PRESENTI: quanti saldatori distinti hanno una qualifica WQ
   analizzata da Agent 2?
3. COERENZA RANGE WQ vs WPS: per ciascun WQ presente, il processo (ISO
   4063), il tipo di giunto e il range di spessore qualificato dal
   patentino sono compatibili con almeno un WPS del welding book (Agent 1)?

REGOLA GENERALE VALIDA PER ENTRAMBE LE CONDIZIONI A E B: NON generare MAI
una voce nell'array "non_conformita" per una condizione che risulta
SODDISFATTA — nemmeno a scopo di tracciabilita' del ragionamento o per
documentare che il controllo e' stato eseguito. Se la Condizione A e'
soddisfatta (mock-up coprono almeno 2 saldatori distinti), non generare
alcuna non conformita' per essa: il fatto che sia stata verificata va
riportato solo nel campo "saldatori_mockup_distinti", mai come voce di
non conformita' con una severita' associata. Lo stesso vale per la
Condizione B. Genera una voce in "non_conformita" SOLO quando una
condizione risulta effettivamente NON soddisfatta.

REGOLE DI SEVERITA' (sono DUE CONDIZIONI INDIPENDENTI - valutale separatamente,
NON fondere i due esiti in un'unica severita'):
- CONDIZIONE A (mock-up): se i mock-up coprono MENO di 2 saldatori distinti
  -> NC STOP: requisito normativo/contrattuale di copertura minima non
  soddisfatto sui mock-up stessi.
- CONDIZIONE B (WQ): se sono presenti WQ per MENO di 2 saldatori distinti
  -> NC STOP: requisito normativo che impone la disponibilita' di qualifiche
  (WQ/patentini) per almeno 2 saldatori distinti nel welding book, con
  range di copertura compatibile con i giunti da eseguire. Non e' un gap
  di sola documentazione recuperabile: senza il WQ del secondo saldatore
  non e' possibile attestare che chi ha saldato fosse qualificato a farlo.

  VINCOLO NON DEROGABILE SULLA CONDIZIONE B (aggiornato 2026-08-02, dopo
  revisione normativa da parte di Angelo IWE — sostituisce integralmente
  il vincolo precedente del 2026-07-30 che fissava questa condizione a
  ATTENZIONE): la severita' di questa condizione e' SEMPRE E SOLO STOP
  quando i WQ presenti coprono meno di 2 saldatori distinti, senza
  eccezioni discrezionali al ribasso. Non esiste una soglia inferiore che
  il modello possa applicare autonomamente per questa condizione: qualsiasi
  considerazione attenuante sul caso specifico va nella "descrizione" della
  non conformita', MAI usata per abbassare il campo "severita'" sotto STOP.
  Prima di scrivere la severita' finale per la Condizione B quando essa
  non e' soddisfatta, rileggi questa regola: se stai per scrivere
  ATTENZIONE o APPUNTO per la Condizione B non soddisfatta, fermati e
  correggi in STOP.

- Se un WQ presente NON e' compatibile (per processo, giunto o spessore)
  con nessun WPS del welding book -> NC STOP: qualifica del saldatore non
  pertinente o insufficiente per i giunti da eseguire. (Questa e' una NC
  distinta dalla Condizione B - riguarda la compatibilita' tecnica di un
  WQ presente, non l'assenza di un WQ.)
- Se un dato necessario al confronto manca (es. WQ senza range di spessore
  leggibile) -> NC APPUNTO, specificando quale dato manca.
- NON segnalare come anomalia il fatto che il numero di saldatori nei
  mock-up e il numero di WQ presenti non coincidano esattamente - vedi
  regola sopra.

ESEMPIO CONCRETO PER EVITARE CONFUSIONE TRA LE DUE CONDIZIONI: se i mock-up
coprono 2 saldatori distinti (es. Saldatore A e Saldatore B, condizione A
SODDISFATTA — nessuna voce in non_conformita' per la condizione A) ma i WQ
presenti nel welding book coprono SOLO il Saldatore A (1 saldatore,
condizione B NON soddisfatta) -> genera UNA voce NC STOP per la Condizione
B, nessuna voce per la Condizione A. Il fatto che manchi il WQ del
Saldatore B non retroagisce sulla condizione A, che resta soddisfatta e
non genera alcuna voce.

Per ogni non conformita' rilevata, se emerge un conflitto diretto tra
affermazioni dei report, aggiungi "conflitto_documentale": true e
specifica in "contesto_conflitto" quali documenti e quali valori sono in
conflitto.

Rispondi SOLO in JSON con questa struttura, nessun testo fuori dal JSON:
{{
  "check": "copertura_wq",
  "saldatori_mockup_distinti": 0,
  "saldatori_wq_distinti": 0,
  "wq_analizzati": [
    {{
      "wq": "nome file WQ",
      "saldatore": "identificativo se disponibile",
      "processo": "...",
      "compatibile_con_wps": true/false/null,
      "wps_compatibili": ["..."],
      "note": "spiegazione sintetica"
    }}
  ],
  "non_conformita": [
    {{
      "severita": "STOP" | "ATTENZIONE" | "APPUNTO",
      "codice": "SUP4-NN",
      "descrizione": "...",
      "riferimento": "...",
      "conflitto_documentale": true/false,
      "contesto_conflitto": "..."
    }}
  ],
  "esito": "GO" | "ATTENZIONE" | "STOP"
}}
"""


def check_copertura_wq(report_agent1, report_agent2, report_agent3, client, model):
    """
    Cross-check #4: verifica copertura minima di 2 saldatori distinti nei
    mock-up (STOP se < 2) e di 2 saldatori distinti con WQ presenti
    (STOP se < 2 - soglia non derogabile, aggiornata 2026-08-02: prima era
    ATTENZIONE, vedi nota nel prompt e log correzioni_dominio.txt), oltre
    alla compatibilita' di ciascun WQ con i WPS del welding book. NON
    richiede corrispondenza 1:1 tra saldatori nei mock-up e saldatori con
    WQ. Non genera piu' voci di non conformita' per condizioni soddisfatte
    (fix bug SUP4-01, 2026-08-02: prima poteva generare una voce STOP anche
    quando il testo stesso dichiarava la condizione soddisfatta).
    Ritorna None se manca uno dei tre report necessari.
    """
    if report_agent1 is None or report_agent2 is None or report_agent3 is None:
        print("  [SKIP] check_copertura_wq: uno o piu' report mancanti (1/2/3)")
        return None

    print("  [CROSS-CHECK #4] Copertura WQ su mock-up e WPS...")

    prompt = PROMPT_CHECK4.format(
        report_agent1_json=json.dumps(report_agent1, ensure_ascii=False, indent=2),
        report_agent2_json=json.dumps(report_agent2, ensure_ascii=False, indent=2),
        report_agent3_json=json.dumps(report_agent3, ensure_ascii=False, indent=2)
    )

    risultato = chiama_claude_json(client, model, prompt, max_tokens=3000, codice_check="SUP4")
    risultato["tool"] = "check_copertura_wq"
    return risultato


# ---------------------------------------------------------------------------
# CROSS-CHECK #5 - Materiale d'apporto: WPS-WPQR e Certificati-WPQR
# (Agent 1 <-> Agent 4)
# ---------------------------------------------------------------------------

PROMPT_CHECK5 = """Sei un IWE (International Welding Engineer) che esegue un controllo incrociato
sulla coerenza del materiale d'apporto (filo/elettrodo) tra WPS, WPQR e
certificati del materiale d'apporto.

CONTESTO NORMATIVO:
Il materiale d'apporto si identifica tramite la NOMENCLATURA NORMATIVA
(es. EN 14341-A G 46 4 M21 4Si1), non tramite marca commerciale o
diametro del filo. Due materiali d'apporto sono considerati coerenti se
la nomenclatura secondo norma coincide, indipendentemente da produttore
o diametro.

Ogni materiale d'apporto deve avere ALMENO 2 certificati: il 3.1+2.2 del
fabbricante e il corrispettivo certificato Jointcert.

FONTE DEI DATI TECNICI WPQR (importante): il campo "materiale_apporto"
qualificato da ciascuna WPQR si trova nel campo "wpqr_digests" del report
Agent 1 (lista di digest, uno per WPQR) — e' la fonte da usare per il
Controllo A sotto, non altri campi del report Agent 1.

REPORT AGENT 1 (analisi WPS/WPQR):
{report_agent1_json}

REPORT AGENT 4 (analisi certificati materiale d'apporto):
{report_agent4_json}

ESEGUI QUESTI DUE CONTROLLI DISTINTI:

CONTROLLO A - WPS vs WPQR (materiale d'apporto):
Per ciascun WPS analizzato da Agent 1, confronta la nomenclatura del
materiale d'apporto dichiarata nel WPS con quella qualificata dalla WPQR
corrispondente. Ignora marca commerciale e diametro - confronta solo la
nomenclatura secondo norma.

CONTROLLO B - Certificati vs WPQR (materiale d'apporto):
Per ciascun materiale d'apporto certificato (3.1+2.2 e/o Jointcert)
analizzato da Agent 4, verifica se la sua nomenclatura normativa trova
corrispondenza in ALMENO UNA delle WPQR analizzate da Agent 1.

REGOLE DI SEVERITA':

Controllo A:
- Nomenclatura del materiale d'apporto nel WPS diversa da quella
  qualificata dalla WPQR (mismatch di norma/classificazione, non di
  marca/diametro) -> NC STOP.
- Dato non chiaramente leggibile in uno dei due documenti -> NC APPUNTO.

Controllo B:
- Nomenclatura del materiale d'apporto presente nei certificati (Agent 4)
  che NON trova corrispondenza in NESSUNA WPQR analizzata da Agent 1
  -> NC STOP: il materiale d'apporto certificato non risulta qualificato
  da alcuna WPQR del welding book.

  CHIARIMENTO SEMPLIFICATO (2026-07-30, da IWE - non complicare oltre
  questo punto): un certificato del materiale d'apporto che riporta sia
  le caratteristiche chimiche sia quelle meccaniche e' un documento
  COMPLETO ai fini di questo controllo, indipendentemente da come le
  singole sezioni sono etichettate nel documento (3.1/2.2 separate o
  combinate in un unico certificato) - NON generare una NC per "manca la
  sezione 2.2" o "manca la sezione 3.1" se il contenuto tecnico completo
  (chimico + meccanico) e' comunque presente in un solo documento. Non
  serve tracciare le due sezioni come requisiti formali indipendenti.

  L'UNICO elemento realmente rilevante da segnalare su questo documento
  E' CHI LO HA EMESSO: se il certificato e' emesso da un DISTRIBUTORE
  anziche' dal PRODUTTORE/fabbricante, questo genera UN SOLO alert
  -> NC ATTENZIONE (non STOP): e' un caso particolare che si discosta
  dalla prassi normale (emissione diretta dal produttore) e merita
  segnalazione al Responsible Welding Coordinator, ma il certificato in
  se' resta tecnicamente valido. Non generare NC aggiuntive sullo stesso
  documento per la stessa ragione (un solo alert, non uno per "manca 3.1"
  e uno per "manca 2.2").

  Anche in questo caso vale il VINCOLO NON DEROGABILE SUL JOINCERT
  SCADUTO (2026-07-30): se il report Agent 4 indica joincert_scaduto=true
  SENZA evidenza di acquisto ante-scadenza valida (vedi campo
  evidenza_acquisto_ante_scadenza e le NC di tipo SA-XX nel report
  Agent 4), la severita' e' SEMPRE E SOLO STOP, MAI ATTENZIONE,
  indipendentemente dal fatto che la nomenclatura del materiale sia
  comunque tracciabile in una o piu' WPQR. Prima di scrivere la severita'
  finale per un materiale con Joincert scaduto e senza evidenza, rileggi
  questa regola: se stai per scrivere ATTENZIONE, fermati e correggi in
  STOP.

- Nessun certificato del materiale d'apporto (ne' chimico ne' meccanico,
  in nessuna forma) presente nel fascicolo -> NC STOP.
- Certificato Jointcert assente del tutto ma non ancora verificabile come
  scaduto (es. non caricato, ma senza evidenza che sia effettivamente
  richiesto o scaduto) -> NC ATTENZIONE.
- Dato non chiaramente leggibile -> NC APPUNTO.

In entrambi i controlli, NON considerare marca commerciale o diametro del
filo come criterio di mismatch - solo la nomenclatura secondo norma.

REGOLA DI SINTETICITA' (CRITICA per non esaurire il budget di token
disponibile): NON generare una voce nell'array corrispondente (
controllo_a_wps_vs_wpqr o controllo_b_certificati_vs_wpqr) per confermare
che una nomenclatura e' coerente ("CHECK OK", "coincidente", "nessuna
incongruenza"). Includi una voce SOLO se rilevi un problema reale (NC
STOP/ATTENZIONE) o un dato non leggibile (APPUNTO). Se un WPS/WPQR o un
certificato risulta semplicemente coerente, non aggiungere nulla per
quell'elemento negli array di dettaglio.

Per ogni non conformita' rilevata, se emerge un conflitto diretto tra
affermazioni dei report, aggiungi "conflitto_documentale": true e
specifica in "contesto_conflitto" quali documenti e quali valori sono in
conflitto.

Rispondi SOLO in JSON con questa struttura, nessun testo fuori dal JSON:
{{
  "check": "materiale_apporto",
  "controllo_a_wps_vs_wpqr": [
    {{
      "wps": "numero WPS",
      "materiale_apporto_wps": "nomenclatura dichiarata nel WPS",
      "materiale_apporto_wpqr": "nomenclatura qualificata dalla WPQR",
      "coerente": true/false/null,
      "note": "spiegazione sintetica"
    }}
  ],
  "controllo_b_certificati_vs_wpqr": [
    {{
      "certificato": "nome file certificato",
      "materiale_apporto_certificato": "nomenclatura dal certificato",
      "trovato_in_wpqr": true/false,
      "wpqr_corrispondente": "numero WPQR se trovato, altrimenti null",
      "certificati_completi": true/false,
      "note": "spiegazione sintetica"
    }}
  ],
  "non_conformita": [
    {{
      "severita": "STOP" | "ATTENZIONE" | "APPUNTO",
      "codice": "SUP5-NN",
      "descrizione": "...",
      "riferimento": "...",
      "conflitto_documentale": true/false,
      "contesto_conflitto": "..."
    }}
  ],
  "esito": "GO" | "ATTENZIONE" | "STOP"
}}
"""


def check_materiale_apporto(report_agent1, report_agent4, client, model):
    """
    Cross-check #5: due sotto-verifiche sul materiale d'apporto, entrambe
    basate su nomenclatura normativa (non marca/diametro):
    A) coerenza tra WPS e WPQR (usa solo report_agent1, che analizza
       entrambi i documenti);
    B) presenza in almeno una WPQR della nomenclatura dichiarata nei
       certificati materiale d'apporto (Agent 4) - STOP se nessuna WPQR
       la contiene.
    Ritorna None se manca uno dei due report necessari.
    """
    if report_agent1 is None or report_agent4 is None:
        print("  [SKIP] check_materiale_apporto: report Agent 1 o Agent 4 mancante")
        return None

    print("  [CROSS-CHECK #5] Materiale d'apporto: WPS-WPQR e certificati-WPQR...")

    prompt = PROMPT_CHECK5.format(
        report_agent1_json=json.dumps(report_agent1, ensure_ascii=False, indent=2),
        report_agent4_json=json.dumps(report_agent4, ensure_ascii=False, indent=2)
    )

    risultato = chiama_claude_json(client, model, prompt, max_tokens=6000, codice_check="SUP5")
    risultato["tool"] = "check_materiale_apporto"
    return risultato


# ---------------------------------------------------------------------------
# AGGREGAZIONE FINALE
# ---------------------------------------------------------------------------

def aggrega_supervisor(risultati_check):
    """
    Aggrega i risultati di tutti i cross-check eseguiti (quelli non-None)
    in un unico report finale. Vince sempre la severita' piu' alta tra
    tutti i check (STOP > ATTENZIONE > APPUNTO > GO).
    Ogni NC riceve i campi stato_chiusura/giustificazione_fornitore/
    data_chiusura per la futura estensione portale fornitore.
    """
    tutte_nc = []
    esito_massimo = "GO"

    for risultato in risultati_check:
        if risultato is None:
            continue  # check saltato per mancanza di input

        for nc in risultato.get("non_conformita", []):
            # Aggiunge i campi per estensione futura (portale fornitore)
            nc.setdefault("stato_chiusura", None)
            nc.setdefault("giustificazione_fornitore", None)
            nc.setdefault("data_chiusura", None)
            nc["tool_origine"] = risultato.get("tool", risultato.get("check", "sconosciuto"))
            tutte_nc.append(nc)

            # Aggiorna l'esito massimo complessivo
            severita_nc = nc.get("severita", "APPUNTO")
            if SEVERITA_RANK.get(severita_nc, 0) > SEVERITA_RANK.get(esito_massimo, 0):
                esito_massimo = severita_nc

    return {
        "agente": "Supervisor",
        "num_check_eseguiti": sum(1 for r in risultati_check if r is not None),
        "num_check_totali": len(risultati_check),
        "num_nc_totali": len(tutte_nc),
        "non_conformita": tutte_nc,
        "esito_finale": esito_massimo
    }


# ---------------------------------------------------------------------------
# ORCHESTRATORE PRINCIPALE
# ---------------------------------------------------------------------------

def run_supervisor(client, model=MODEL):
    """
    Carica tutti i report disponibili, esegue i cross-check per cui ha
    i dati necessari (saltando quelli non ancora disponibili) e aggrega
    tutto in un report finale.
    """
    print("Caricamento report agenti disponibili...")
    report1 = carica_report("agent1")
    report2 = carica_report("agent2")
    report3 = carica_report("agent3")
    report4 = carica_report("agent4")
    report5 = carica_report("agent5")

    print("\nEsecuzione cross-check...")
    risultati = [
        check_wps_vs_mockup(report1, report3, client, model),                      # #1
        check_wpqr_vs_en15085(report1, report5, client, model),                    # #2
        check_spessore_materiale(report1, report3, report4, client, model),        # #3 (report3, non report5)
        check_copertura_wq(report1, report2, report3, client, model),              # #4
        check_materiale_apporto(report1, report4, client, model),                  # #5
    ]

    return aggrega_supervisor(risultati)


def stampa_report(report):
    """Stampa a console un riepilogo leggibile del report Supervisor."""
    print("\n" + "=" * 60)
    print("  REPORT SUPERVISOR")
    print("=" * 60)
    print(f"  Check eseguiti : {report['num_check_eseguiti']}/{report['num_check_totali']}")
    print(f"  NC totali      : {report['num_nc_totali']}")
    print(f"  ESITO FINALE   : {report['esito_finale']}")
    print("=" * 60)

    for nc in report["non_conformita"]:
        icona = {"STOP": "🔴", "ATTENZIONE": "🟡", "APPUNTO": "📝"}.get(nc.get("severita"), "•")
        print(f"\n  {icona} [{nc.get('severita')}] — {nc.get('codice')} ({nc.get('tool_origine')})")
        print(f"     {nc.get('descrizione')}")
        print(f"     Rif.: {nc.get('riferimento')}")
        if nc.get("conflitto_documentale"):
            print(f"     ⚠ Conflitto documentale: {nc.get('contesto_conflitto')}")

    print("\n" + "=" * 60)


# ---------------------------------------------------------------------------
# ESECUZIONE DIRETTA
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("🔍 WeldAIM — Supervisor Agent")
    print(f"   Cartella report: {REPORT_DIR}\n")

    client = anthropic.Anthropic()

    report_finale = run_supervisor(client)
    stampa_report(report_finale)

    output_json = os.path.join(REPORT_DIR, "report_supervisor.json")
    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(report_finale, f, ensure_ascii=False, indent=2)
    print(f"\n  📄 Report JSON salvato: {output_json}")