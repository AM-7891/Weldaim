"""
WELDAIM — AGENT 5: PFC + EN 15085 + Saldatrici + Welding Map
Verifica documenti: PFC (cartella 11), EN 15085 (cartella 12),
Attrezzature/Saldatrici (cartella 13), Welding Map (cartella 04)

Struttura attesa in test_docs/:
    04_WELDING_MAP/         <- welding map (PDF, Excel, disegno)
    11_PFC/                 <- Piano di Fabbricazione e Controllo
    12_CERT_EN15085/        <- certificato ente + certificato Joincert
    13_REPORT_SALDATRICI/   <- report taratura IEC 60974-14 + cert. strumento

Importa da utils.py — assicurarsi che utils.py sia nella stessa cartella.

Aggiornamento 2026-07-26:
- Aggiunta normalizzazione difensiva degli elementi "nc" restituiti dal modello:
  in check_pfc si è verificato un TypeError perché un elemento dell'array nc era
  una stringa invece del dizionario atteso {severita, codice, descrizione,
  riferimento} - stessa variabilità non deterministica già osservata altrove
  (es. codici di errore interni inventati dal modello in altri agenti). Invece
  di far crashare lo script, ogni elemento non conforme viene ora convertito in
  un dizionario NC valido con severità ATTENZIONE, così l'informazione originale
  non va persa e il report resta generabile. Applicato in check_pfc (dove è
  avvenuto il crash) e, per coerenza/prevenzione, anche nell'aggregazione finale
  in analizza_pfc_en15085 per gli altri 3 tool.

Aggiornamento 2026-08-02 — FIX CAUSA RADICE del bug sopra:
- Individuata la causa: per documenti "corti" (sotto soglia chunking), il codice
  usa PROMPT_CHUNK direttamente come prompt finale, saltando PROMPT_AGGREGAZIONE.
  PROMPT_CHUNK di check_pfc (e, per lo stesso motivo, di check_welding_map)
  mostrava solo "nc": [] nell'esempio di formato, SENZA lo schema del singolo
  elemento (severita/codice/descrizione/riferimento) — quello schema esisteva
  SOLO in PROMPT_AGGREGAZIONE. Il modello, senza esempio, inventava un formato
  proprio (stringhe invece di dizionari), da cui il fallback GEN-98 osservato
  in produzione durante la validazione di regressione.
- FIX: aggiunto lo schema completo dell'elemento nc anche in PROMPT_CHUNK di
  check_pfc e check_welding_map, cosi' il modello lo rispetta indipendentemente
  dal fatto che il documento passi per chunking+aggregazione o per analisi
  diretta. _normalizza_nc() resta invariata come rete di sicurezza difensiva,
  non piu' come meccanismo primario.
"""

import os
import json
import anthropic
from datetime import datetime

from utils import (
    estrai_testo_pdf_semplice,
    estrai_testo_excel,
    analizza_pdf_chunked,
    analizza_testo_chunked,
    trova_file_per_estensione,
    pulisci_json
)

# ---------------------------------------------------------------------------
# CONFIGURAZIONE
# ---------------------------------------------------------------------------

client = anthropic.Anthropic()
MODEL = "claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# UTILITY DIFENSIVA — normalizza un elemento "nc" non conforme allo schema
# ---------------------------------------------------------------------------

def _normalizza_nc(nc, contesto: str = "") -> dict:
    """
    Garantisce che un elemento nc sia sempre un dizionario con i campi attesi.
    Se il modello ha restituito qualcosa di diverso da un dizionario (es. una
    stringa), lo converte in un dizionario NC valido con severità ATTENZIONE,
    preservando il contenuto originale nella descrizione invece di perderlo o
    far crashare il programma.

    NOTA (2026-08-02): questa funzione resta come rete di sicurezza difensiva.
    La causa radice del problema che l'ha resa necessaria è stata corretta
    allineando gli schemi nc in PROMPT_CHUNK e PROMPT_AGGREGAZIONE — vedi nota
    in testa al file. Da qui in avanti questa funzione dovrebbe attivarsi
    raramente; se si attiva spesso, è un segnale che qualche altro prompt ha
    lo stesso disallineamento di schema da correggere.
    """
    if isinstance(nc, dict):
        return nc
    return {
        "severita": "ATTENZIONE",
        "codice": "GEN-98",
        "descrizione": f"Elemento NC malformato restituito dal modello (non un dizionario){' — ' + contesto if contesto else ''}: {str(nc)}",
        "riferimento": "Verificare manualmente — errore di formato interno"
    }


# ---------------------------------------------------------------------------
# CALCOLO DETERMINISTICO — scadenza certificato taratura calibratore primario
# ---------------------------------------------------------------------------

def _valuta_scadenza_calibratore(data_scadenza_str, identificativo_saldatrice: str = "") -> dict | None:
    """
    Calcolo deterministico (Python, non lasciato al modello — stesso principio
    già usato per _calcola_esito_da_nc in Agent 1) della severità da applicare
    quando il report di taratura di una saldatrice riporta una data di validità
    esplicita per lo strumento (calibratore) primario citato.

    NOTA (2026-08-02 — correzione dominio, Angelo IWE): introdotta dopo aver
    osservato non-determinismo residuo sullo stesso identico dato di input tra
    run diversi (stesso testo di report, a volte classificato come semplice
    APPUNTO di "assenza documentale", a volte come STOP per scadenza comprovata)
    quando la classificazione era lasciata al giudizio del modello dentro il
    prompt. Il modello ora estrae SOLO la data (compito meccanico a basso
    rischio di variabilità, vedi campo data_scadenza_calibratore_primario nello
    schema JSON) — il calcolo della severità avviene qui, fuori dal modello.

    Una data di scadenza esplicita e comprovata nel testo del report NON è lo
    stesso caso di un certificato semplicemente assente: mette in dubbio la
    validità dell'intera taratura della saldatrice, che è ciò che il report
    doveva garantire — per questo qui può risultare STOP, mentre la semplice
    assenza documentale (nessuna data trovata) resta un APPUNTO gestito dal
    modello secondo la regola 3 del prompt di check_saldatrici.
    """
    if not data_scadenza_str or str(data_scadenza_str).strip().lower() in (
        "null", "none", "", "non disponibile", "non presente", "non leggibile"
    ):
        return None

    data_scadenza = None
    for fmt in ("%d/%m/%Y", "%d.%m.%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            data_scadenza = datetime.strptime(str(data_scadenza_str).strip(), fmt)
            break
        except ValueError:
            continue

    if data_scadenza is None:
        # Data presente nel testo ma non riconoscibile nei formati attesi:
        # non forziamo un giudizio automatico, segnaliamo solo la necessità
        # di verifica manuale — non indoviniamo la data.
        return {
            "severita": "APPUNTO",
            "codice": "SAL-03",
            "descrizione": (
                f"Data di validità del certificato di taratura dello strumento "
                f"primario citata nel report ({data_scadenza_str}) ma non "
                f"riconoscibile automaticamente nel formato atteso. "
                f"Verificare manualmente."
            ),
            "riferimento": "IEC 60974-14 — QT.6495.024 §13"
        }

    if data_scadenza < datetime.now():
        giorni_scaduto = (datetime.now() - data_scadenza).days
        rif_saldatrice = f" della saldatrice {identificativo_saldatrice}" if identificativo_saldatrice else ""
        return {
            "severita": "STOP",
            "codice": "SAL-02",
            "descrizione": (
                f"Il certificato di taratura dello strumento (calibratore) primario "
                f"citato nel report di taratura{rif_saldatrice} risulta scaduto il "
                f"{data_scadenza_str} (da {giorni_scaduto} giorni rispetto a oggi). "
                f"Evidenza esplicita e comprovata di scadenza nel testo del report — "
                f"non è un caso di assenza documentale: la validità dell'intera "
                f"taratura della saldatrice non è garantita."
            ),
            "riferimento": "IEC 60974-14 — QT.6495.024 §13"
        }

    return None


# ---------------------------------------------------------------------------
# TOOL 1 — VERIFICA WELDING MAP (cartella 04)
# ---------------------------------------------------------------------------

def check_welding_map(cartella_04: str) -> dict:
    """
    Verifica formale della welding map.
    Formati accettabili: PDF tabellare, Excel, disegno tecnico con WPS annotate,
    PDF con figurini giunti.
    Struttura libera — Claude interpreta senza cercare colonne fisse.

    Severità:
    - Welding map assente → ATTENZIONE (può essere mostrata in ispezione fisica)
    - WPS di riferimento per giunto assente → ATTENZIONE
    - Disegno Geismar senza nota fornitore → ATTENZIONE (rischio falsificazione revisione)
    - Identificativo giunto assente → APPUNTO
    - Campi incompleti non bloccanti → APPUNTO
    """
    if not os.path.isdir(cartella_04):
        return {
            "tool": "check_welding_map",
            "eseguito": False,
            "nc": [{"severita": "ATTENZIONE", "codice": "WM-00",
                    "descrizione": "Cartella 04 Welding Map non trovata",
                    "riferimento": "QT.6495.024 §4 — EN 15085-3 Annex B"}]
        }

    pdf_files = trova_file_per_estensione(cartella_04, [".pdf"])
    excel_files = trova_file_per_estensione(cartella_04, [".xlsx", ".xls", ".xlsm"])
    tutti_file = pdf_files + excel_files

    if not tutti_file:
        return {
            "tool": "check_welding_map",
            "eseguito": True,
            "nc": [{"severita": "ATTENZIONE", "codice": "WM-01",
                    "descrizione": "Nessun documento welding map trovato nella cartella 04. "
                                   "Il documento può essere presentato fisicamente in ispezione.",
                    "riferimento": "QT.6495.024 §4 — EN 15085-3 Annex B"}]
        }

    # Estrae testo da tutti i file
    testi = {}
    for f in pdf_files:
        nome = os.path.basename(f)
        print(f"[Agent5] Lettura welding map PDF: {nome}")
        testi[nome] = estrai_testo_pdf_semplice(f)
    for f in excel_files:
        nome = os.path.basename(f)
        print(f"[Agent5] Lettura welding map Excel: {nome}")
        testi[nome] = estrai_testo_excel(f)

    # Testo aggregato — se lungo usa chunking
    testo_aggregato = "\n\n".join(
        [f"--- FILE: {nome} ---\n{testo}" for nome, testo in testi.items()]
    )
    lista_file = list(testi.keys())

    PROMPT_CHUNK = """Sei un esperto di documentazione saldatura secondo EN 15085.
Analizza questo estratto di welding map da un Welding Book.

ESTRATTO:
{testo_chunk}

Identifica ed elenca:
1. Identificativi giunti/saldature presenti (es. W1, a3, HY6, V5)
2. Riferimenti WPS per ogni giunto
3. Riferimenti disegno
4. Eventuali riferimenti WPQR e WQ
5. Spessori e diametri se presenti
6. Se il documento sembra essere un disegno Geismar originale
7. Se presente nota che dichiara annotazioni a cura del fornitore

Se rilevi qui una non conformità, ogni elemento dell'array "nc" DEVE essere un
OGGETTO con questi esatti campi (mai una stringa semplice):
{
  "severita": "ATTENZIONE" oppure "APPUNTO",
  "codice": "es. WM-XX",
  "descrizione": "descrizione chiara dell'osservazione",
  "riferimento": "norma o paragrafo, es. QT.6495.024 §4 — EN 15085-3 Annex B"
}
Se non c'è nulla da segnalare in questo chunk, lascia "nc": [] vuoto — non
inventare NC per confermare che qualcosa va bene.

Rispondi ESCLUSIVAMENTE in JSON valido senza backtick:
{
  "giunti_trovati": [
    {
      "identificativo": "es. W1 o HY6",
      "wps_riferimento": "numero WPS se presente",
      "wpqr_riferimento": "numero WPQR se presente",
      "wq_riferimento": "numero WQ se presente",
      "spessore": "se presente",
      "diametro": "se presente",
      "tipo_giunto": "FW|BW|HV|HY|non identificabile"
    }
  ],
  "disegno_geismar": true,
  "nota_fornitore_presente": false,
  "riferimento_disegno": "numero disegno se leggibile",
  "formato_rilevato": "tabella|disegno_annotato|figurini_giunti|excel|altro",
  "nc": [],
  "note": "osservazioni sul chunk"
}"""

    PROMPT_AGGREGAZIONE = """Sei un esperto di documentazione saldatura secondo EN 15085.
Aggrega i seguenti risultati parziali dell'analisi di una welding map
e produci un report finale consolidato.

RISULTATI PARZIALI:
{risultati_parziali}

REGOLE DI VERIFICA FINALE:
1. Welding map presente con almeno un giunto identificato → documento accettabile
2. Nessun giunto con riferimento WPS → NC ATTENZIONE
3. Se il documento è un disegno Geismar (disegno_geismar=true) E non c'è nota
   che dichiara annotazioni a cura del fornitore → NC ATTENZIONE
   (rischio: il fornitore ha modificato il disegno originale alterando la revisione)
4. Identificativo giunto assente su alcuni giunti → APPUNTO
5. Mancanza di spessori/diametri/WPQR/WQ → nessuna NC (campi facoltativi)

FILE ANALIZZATI: """ + json.dumps(lista_file, ensure_ascii=False) + """

Rispondi ESCLUSIVAMENTE in JSON valido senza backtick:
{
  "formato_welding_map": "tabella|disegno_annotato|figurini_giunti|excel|misto",
  "totale_giunti_identificati": 0,
  "giunti_con_wps": 0,
  "giunti_senza_wps": 0,
  "disegno_geismar_rilevato": false,
  "nota_fornitore_presente": false,
  "nc": [
    {
      "severita": "ATTENZIONE|APPUNTO",
      "codice": "WM-XX",
      "descrizione": "descrizione NC",
      "riferimento": "norma o paragrafo"
    }
  ],
  "sintesi": "breve riepilogo"
}"""

    # Usa chunking se il testo è lungo
    if len(testo_aggregato) > 12000:
        print(f"[Agent5] Welding map lunga — attivo chunking testo")
        risultato = analizza_testo_chunked(
            testo=testo_aggregato,
            client=client,
            model=MODEL,
            prompt_per_chunk=PROMPT_CHUNK,
            prompt_aggregazione=PROMPT_AGGREGAZIONE,
            max_tokens_chunk=1500,
            max_tokens_aggregazione=2000,
            nome_file="welding_map"
        )
    else:
        # Analisi diretta
        prompt = PROMPT_CHUNK.replace("{testo_chunk}", testo_aggregato)
        # Per analisi diretta uso il prompt aggregazione che ha le regole complete
        prompt_diretto = PROMPT_AGGREGAZIONE.replace(
            "{risultati_parziali}",
            json.dumps([{"chunk": 1, "risultato": {"testo": testo_aggregato}}],
                       ensure_ascii=False)
        )
        risposta = client.messages.create(
            model=MODEL,
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt_diretto}]
        )
        testo_r = pulisci_json(risposta.content[0].text)
        try:
            risultato = json.loads(testo_r)
        except json.JSONDecodeError:
            risultato = {
                "raw": testo_r,
                "nc": [{"severita": "ATTENZIONE", "codice": "WM-99",
                        "descrizione": "Risposta AI non parsabile — verificare manualmente",
                        "riferimento": "Errore interno"}]
            }

    # Normalizza eventuali elementi nc malformati prima di restituire
    risultato["nc"] = [_normalizza_nc(nc, "check_welding_map") for nc in risultato.get("nc", [])]
    risultato["tool"] = "check_welding_map"
    risultato["eseguito"] = True
    return risultato


# ---------------------------------------------------------------------------
# TOOL 2 — VERIFICA PFC (cartella 11)
# ---------------------------------------------------------------------------

def check_pfc(cartella_11: str) -> dict:
    """
    Verifica formale del Piano di Fabbricazione e Controllo.
    Struttura libera — ogni fornitore ha il suo formato.
    L'agente verifica: coerenza riferimenti (disegni, WPS citati),
    presenza di una struttura generica accettabile.
    NON valuta il processo produttivo — quello resta al fornitore.
    """
    if not os.path.isdir(cartella_11):
        return {
            "tool": "check_pfc",
            "eseguito": False,
            "nc": [{"severita": "ATTENZIONE", "codice": "PFC-00",
                    "descrizione": "Cartella 11 PFC non trovata",
                    "riferimento": "QT.6495.024 §11 — EN 15085-5"}]
        }

    pdf_files = trova_file_per_estensione(cartella_11, [".pdf"])
    excel_files = trova_file_per_estensione(cartella_11, [".xlsx", ".xls", ".xlsm"])
    tutti_file = pdf_files + excel_files

    if not tutti_file:
        return {
            "tool": "check_pfc",
            "eseguito": True,
            "nc": [{"severita": "ATTENZIONE", "codice": "PFC-01",
                    "descrizione": "Nessun documento PFC trovato nella cartella 11",
                    "riferimento": "QT.6495.024 §11 — EN 15085-5"}]
        }

    PROMPT_CHUNK = """Sei un esperto di documentazione di produzione per strutture saldate EN 15085.
Analizza questo estratto di Piano di Fabbricazione e Controllo (PFC).
Il PFC ha struttura libera — ogni fornitore lo organizza a modo suo.
NON valutare le scelte di processo produttivo — quelle appartengono al fornitore.

ESTRATTO PFC:
{testo_chunk}

Verifica SOLO:
1. Sono presenti riferimenti a disegni o numeri commessa?
2. Sono citati riferimenti a WPS applicabili?
3. Sono presenti fasi di controllo o ispezione identificabili?
4. La struttura è genericamente quella di un piano di produzione/controllo?
5. Ci sono date o revisioni documentali?

Se rilevi qui una non conformità, ogni elemento dell'array "nc" DEVE essere un
OGGETTO con questi esatti campi (mai una stringa semplice):
{
  "severita": "ATTENZIONE" oppure "APPUNTO",
  "codice": "es. PFC-XX",
  "descrizione": "descrizione chiara dell'osservazione",
  "riferimento": "norma o paragrafo, es. QT.6495.024 §11 — EN 15085-5"
}
Se non c'è nulla da segnalare in questo chunk, lascia "nc": [] vuoto — non
inventare NC per confermare che qualcosa va bene.

Rispondi ESCLUSIVAMENTE in JSON valido senza backtick:
{
  "riferimenti_disegno_presenti": true,
  "wps_citati": ["lista WPS se presenti"],
  "fasi_controllo_presenti": true,
  "struttura_riconoscibile": true,
  "date_revisioni_presenti": true,
  "nc": [],
  "note": "osservazioni sul chunk"
}"""

    PROMPT_AGGREGAZIONE = """Aggrega i risultati parziali dell'analisi del PFC e produci il report finale.

RISULTATI PARZIALI:
{risultati_parziali}

REGOLE DI VERIFICA:
1. Struttura non riconoscibile come PFC → NC ATTENZIONE
2. Nessun riferimento a disegni o commessa → NC ATTENZIONE
3. Nessuna fase di controllo identificabile → NC APPUNTO
4. WPS non citati → NC APPUNTO (non STOP — la correlazione WPS/giunto è nella welding map)
5. NON generare NC per le scelte di processo produttivo

Rispondi ESCLUSIVAMENTE in JSON valido senza backtick:
{
  "struttura_pfc_riconoscibile": true,
  "riferimenti_disegno_presenti": true,
  "wps_citati": [],
  "fasi_controllo_presenti": true,
  "nc": [
    {
      "severita": "ATTENZIONE|APPUNTO",
      "codice": "PFC-XX",
      "descrizione": "descrizione NC",
      "riferimento": "QT.6495.024 §11 — EN 15085-5"
    }
  ],
  "sintesi": "breve riepilogo"
}"""

    # Analizza con chunking adattivo
    testi = {}
    for f in pdf_files:
        nome = os.path.basename(f)
        print(f"[Agent5] Lettura PFC PDF: {nome}")
        risultato = analizza_pdf_chunked(
            percorso_pdf=f,
            client=client,
            model=MODEL,
            prompt_per_chunk=PROMPT_CHUNK,
            prompt_aggregazione=PROMPT_AGGREGAZIONE,
            max_tokens_chunk=1500,
            max_tokens_aggregazione=2000
        )
        testi[nome] = risultato

    for f in excel_files:
        nome = os.path.basename(f)
        print(f"[Agent5] Lettura PFC Excel: {nome}")
        testo = estrai_testo_excel(f)
        risultato = analizza_testo_chunked(
            testo=testo,
            client=client,
            model=MODEL,
            prompt_per_chunk=PROMPT_CHUNK,
            prompt_aggregazione=PROMPT_AGGREGAZIONE,
            max_tokens_chunk=1500,
            max_tokens_aggregazione=2000,
            nome_file=nome
        )
        testi[nome] = risultato

    # Aggrega NC da tutti i file PFC
    tutte_nc = []
    for nome, ris in testi.items():
        for nc in ris.get("nc", []):
            # Difesa: il modello a volte restituisce un elemento "nc" che non rispetta
            # lo schema atteso (stringa invece di dizionario) - stessa variabilità
            # non deterministica già osservata altrove (TCHUNK-01, AGG-01). Invece di
            # crashare con TypeError su nc["file"] = nome, normalizziamo l'elemento
            # prima di assegnare il campo file.
            nc_normalizzato = _normalizza_nc(nc, f"check_pfc/{nome}")
            nc_normalizzato["file"] = nome
            tutte_nc.append(nc_normalizzato)

    struttura_ok = any(
        r.get("struttura_pfc_riconoscibile", False) for r in testi.values()
    )

    risultato_finale = {
        "tool": "check_pfc",
        "eseguito": True,
        "file_analizzati": list(testi.keys()),
        "struttura_pfc_riconoscibile": struttura_ok,
        "nc": tutte_nc,
        "sintesi": f"Analizzati {len(testi)} file PFC."
    }
    return risultato_finale


# ---------------------------------------------------------------------------
# TOOL 3 — VERIFICA CERTIFICATO EN 15085 (cartella 12)
# ---------------------------------------------------------------------------

def check_en15085(cartella_12: str) -> dict:
    """
    Verifica certificazione EN 15085.
    Richiede DUE documenti obbligatori:
    1. Certificato emesso da ente di certificazione (Bureau Veritas, IIS, RINA, ecc.)
    2. Certificato Joincert corrispondente (caricato dal fornitore — non verificato online)

    Entrambi devono essere:
    - In corso di validità
    - Con stesso scope (classi CL coperte, processi)
    - Riferiti al medesimo indirizzo azienda presente in WPS/WPQR/WQ

    Cross-check range WPQR vs EN 15085 → DELEGATO AL SUPERVISOR
    """
    if not os.path.isdir(cartella_12):
        return {
            "tool": "check_en15085",
            "eseguito": False,
            "nc": [{"severita": "STOP", "codice": "EN-00",
                    "descrizione": "Cartella 12 certificato EN 15085 non trovata",
                    "riferimento": "QT.6495.024 §12 — EN 15085-2"}]
        }

    pdf_files = trova_file_per_estensione(cartella_12, [".pdf"])

    if not pdf_files:
        return {
            "tool": "check_en15085",
            "eseguito": True,
            "nc": [{"severita": "STOP", "codice": "EN-01",
                    "descrizione": "Nessun certificato EN 15085 trovato nella cartella 12. "
                                   "Obbligatori: certificato ente di certificazione + Joincert.",
                    "riferimento": "QT.6495.024 §12 — EN 15085-2"}]
        }

    # Estrae testo da tutti i PDF
    testi = {}
    for f in pdf_files:
        nome = os.path.basename(f)
        print(f"[Agent5] Lettura EN 15085: {nome}")
        testi[nome] = estrai_testo_pdf_semplice(f)

    contenuto = "\n\n".join(
        [f"--- FILE: {nome} ---\n{testo}" for nome, testo in testi.items()]
    )
    lista_file = list(testi.keys())

    prompt = f"""Sei un esperto di certificazione EN 15085 per strutture saldate.
Analizza i seguenti documenti dalla cartella 12 (Certificato EN 15085) di un Welding Book.

FILE PRESENTI: {json.dumps(lista_file, ensure_ascii=False)}

CONTENUTO:
{contenuto}

REGOLE DI VERIFICA:
1. Devono essere presenti DUE documenti distinti:
   (a) Certificato emesso da ente di certificazione accreditato
       (Bureau Veritas, IIS, RINA, TÜV, DNV o simili)
   (b) Certificato Joincert corrispondente (documento DB)
   Un solo documento non è sufficiente — entrambi obbligatori → mancanza = NC STOP

2. Entrambi i certificati devono essere in corso di validità (non scaduti).
   Scaduto = NC STOP.

3. Lo scope dei due certificati deve corrispondere:
   stesse classi CL coperte (CL1, CL2, ecc.) e stessi processi di saldatura.
   Discrepanza scope → NC STOP.

4. L'indirizzo aziendale riportato in entrambi i certificati deve corrispondere
   all'indirizzo presente nei documenti WPS/WPQR/WQ del welding book.
   Se non puoi verificare (WPS non in input) → segnala come ATTENZIONE da verificare.

5. Il cross-check tra range qualifica WPQR e scope EN 15085 è DELEGATO AL SUPERVISOR
   — non generare NC per questo punto.

NOTA FONDAMENTALE SUI DUE TIPI DI DOCUMENTO:
Il certificato EN 15085 si presenta in DUE formati distinti che devi riconoscere:

(a) CERTIFICATO ENTE DI CERTIFICAZIONE (Bureau Veritas, IIS CERT, RINA, TUV, DNV):
    - Layout grafico elaborato con logo dell'ente in evidenza
    - Bureau Veritas: sfondo rosso/bianco con logo BV e sigillo ACCREDIA
    - IIS CERT: logo IIS con riferimento IIW/EWF
    - RINA: logo RINA con timbro ACCREDIA
    - Contiene: numero certificato ente, scope, coordinatori, date validita'
    - Allegati con range dettagliato (Appendix 1/3, 2/3, 3/3 per BV)

(b) CERTIFICATO ECWRV (formato standard europeo):
    - Emesso da ECWRV (European Committee for Welding of Railway Vehicles)
    - Titolo caratteristico a lettere spaziate: "C E R T I F I C A T E" o "C E R T I F I C A T O"
    - Sottotitolo: "Saldatura di veicoli ferroviari e relativi componenti secondo EN 15085-2"
    - Watermark di sfondo: "ECWRV" con stelle europee
    - Sfondo bianco, layout sobrio
    - Numero registro es. BVIT/15085/CL1/038, IIS/15085/CL1/029, RINA/15085/CL1/014
    - Range di certificazione in tabella con processo/materiale/dimensioni
    - Firmato dal responsabile dell'ente ma nel formato ECWRV standardizzato

IMPORTANTE: il documento ECWRV puo' essere nello stesso PDF del certificato ente
oppure in un file separato. Se trovi un file che contiene ENTRAMBI i formati,
considera ENTRAMBI i requisiti soddisfatti. NON dare NC STOP se il documento
ECWRV e' presente nello stesso PDF del certificato ente.

Rispondi ESCLUSIVAMENTE in JSON valido senza backtick:
{{
  "certificato_ente_presente": true,
  "nome_ente_certificazione": "Bureau Veritas|IIS|RINA|TUV|altro",
  "certificato_joincert_presente": true,
  "data_scadenza_ente": "data se leggibile",
  "data_scadenza_joincert": "data se leggibile",
  "ente_valido": true,
  "joincert_valido": true,
  "scope_ente": "CL1, CL2 — processi 111, 135 ecc.",
  "scope_joincert": "CL1, CL2 — processi 111, 135 ecc.",
  "scope_corrispondente": true,
  "indirizzo_ente": "indirizzo se leggibile",
  "indirizzo_joincert": "indirizzo se leggibile",
  "indirizzi_corrispondenti": true,
  "nc": [
    {{
      "severita": "STOP|ATTENZIONE|APPUNTO",
      "codice": "EN-XX",
      "descrizione": "descrizione NC",
      "riferimento": "EN 15085-2 — QT.6495.024 §12"
    }}
  ],
  "nota_supervisore": "Cross-check range WPQR vs scope EN 15085 delegato al Supervisor",
  "sintesi": "breve riepilogo"
}}"""

    risposta = client.messages.create(
        model=MODEL,
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )
    testo_r = pulisci_json(risposta.content[0].text)
    try:
        risultato = json.loads(testo_r)
    except json.JSONDecodeError:
        risultato = {
            "raw": testo_r,
            "nc": [{"severita": "ATTENZIONE", "codice": "EN-99",
                    "descrizione": "Risposta AI non parsabile — verificare manualmente",
                    "riferimento": "Errore interno"}]
        }

    # Normalizza eventuali elementi nc malformati prima di restituire
    risultato["nc"] = [_normalizza_nc(nc, "check_en15085") for nc in risultato.get("nc", [])]
    risultato["tool"] = "check_en15085"
    risultato["eseguito"] = True
    return risultato


# ---------------------------------------------------------------------------
# TOOL 4 — VERIFICA SALDATRICI E ATTREZZATURE (cartella 13)
# ---------------------------------------------------------------------------

def check_saldatrici(cartella_13: str) -> dict:
    """
    Verifica report taratura saldatrici e attrezzature.

    Gerarchia documenti:
    - Lista attrezzature → presenza opzionale, nessuna NC se assente
    - Report taratura saldatrici (IEC 60974-14) → assente = NC ATTENZIONE
    - Certificato taratura dello strumento citato nel report → assente = NC APPUNTO
    - Certificato taratura dello strumento con evidenza ESPLICITA di scadenza
      comprovata nel testo del report → NC STOP (correzione dominio 2026-08-02,
      Angelo IWE: una scadenza comprovata mette in dubbio l'intera taratura
      della saldatrice, diverso da una semplice assenza documentale)

    IEC 60974-14: norma per la verifica periodica e la manutenzione
    delle apparecchiature per saldatura ad arco.

    NOTA ROADMAP (non implementato qui): il confronto corretto per la scadenza
    della taratura andrebbe fatto contro la data REALE di esecuzione della
    saldatura specifica (dal PFC, cartella 11), non contro la data odierna —
    vedi roadmap "Cross-check data taratura saldatrice vs data reale produzione".
    """
    if not os.path.isdir(cartella_13):
        return {
            "tool": "check_saldatrici",
            "eseguito": False,
            "nc": [{"severita": "ATTENZIONE", "codice": "SAL-00",
                    "descrizione": "Cartella 13 attrezzature/saldatrici non trovata",
                    "riferimento": "QT.6495.024 §13 — IEC 60974-14"}]
        }

    pdf_files = trova_file_per_estensione(cartella_13, [".pdf"])
    excel_files = trova_file_per_estensione(cartella_13, [".xlsx", ".xls", ".xlsm"])
    tutti_file = pdf_files + excel_files

    if not tutti_file:
        return {
            "tool": "check_saldatrici",
            "eseguito": True,
            "nc": [{"severita": "ATTENZIONE", "codice": "SAL-01",
                    "descrizione": "Nessun documento trovato nella cartella 13. "
                                   "Richiesti report taratura saldatrici IEC 60974-14.",
                    "riferimento": "QT.6495.024 §13 — IEC 60974-14"}]
        }

    testi = {}
    for f in pdf_files:
        nome = os.path.basename(f)
        print(f"[Agent5] Lettura saldatrici/attrezzature: {nome}")
        testi[nome] = estrai_testo_pdf_semplice(f)
    for f in excel_files:
        nome = os.path.basename(f)
        print(f"[Agent5] Lettura lista attrezzature Excel: {nome}")
        testi[nome] = estrai_testo_excel(f)

    contenuto = "\n\n".join(
        [f"--- FILE: {nome} ---\n{testo}" for nome, testo in testi.items()]
    )
    lista_file = list(testi.keys())

    prompt = f"""Sei un esperto di attrezzature per saldatura e manutenzione strumentale.
Analizza i seguenti documenti dalla cartella 13 (Attrezzature/Saldatrici) di un Welding Book.

FILE PRESENTI: {json.dumps(lista_file, ensure_ascii=False)}

CONTENUTO:
{contenuto}

REGOLE DI VERIFICA:
1. LISTA ATTREZZATURE: presenza opzionale. Se presente, nessuna NC.
   Se assente, nessuna NC — non è obbligatoria.

2. REPORT TARATURA SALDATRICI (IEC 60974-14):
   - Obbligatorio per ogni saldatrice ad arco o TIG utilizzata.
   - Il report deve citare la norma IEC 60974-14.
   - Se assente → NC ATTENZIONE per ogni saldatrice non coperta da taratura.
   - Verifica che il report indichi: identificativo macchina, data taratura,
     esito (conforme/non conforme), prossima scadenza taratura.

3. CERTIFICATO DI TARATURA DELLO STRUMENTO citato nel report:
   - Ogni report IEC 60974-14 cita lo strumento usato per la taratura.
   - Se non è presente il certificato di taratura di quello strumento → NC APPUNTO
     (semplice assenza documentale, nessuna evidenza di problema reale).
   - Se lo strumento non è identificabile nel documento → NC APPUNTO.

4. IMPORTANTE — NON giudicare tu la severità legata alla scadenza del
   certificato di taratura dello strumento primario. Il tuo unico compito su
   questo punto è ESTRARRE, in modo meccanico e letterale, la data di validità
   dello strumento primario se il report la riporta esplicitamente (es. dopo
   diciture come "calib. valid", "validità", "scadenza taratura strumento")
   nel campo dedicato "data_scadenza_calibratore_primario" dello schema sotto,
   nel formato originale in cui compare nel testo (non convertirla, non
   interpretarla, non giudicarne la validità). Se questa data non compare nel
   testo, lascia il campo null. Il calcolo della severità basato su questa
   data avviene automaticamente fuori dal tuo output — non generare NC nella
   lista "nc" per la scadenza dello strumento primario: genera NC solo per
   l'assenza documentale generica (regola 3) o per lo strumento non
   identificabile, non per la sua eventuale scadenza.

5. Non generare NC STOP tu stesso in questo tool per nessun motivo — la
   massima severità che PUOI assegnare è ATTENZIONE. L'unico STOP possibile
   su questo check (scadenza comprovata del calibratore primario) viene
   calcolato automaticamente dal codice a partire dal campo data che estrai,
   non da te.

Rispondi ESCLUSIVAMENTE in JSON valido senza backtick:
{{
  "lista_attrezzature_presente": false,
  "saldatrici_identificate": [
    {{
      "identificativo": "es. SLD-01 o numero serie",
      "report_iec_60974_14_presente": true,
      "data_taratura": "data se leggibile",
      "scadenza_taratura": "data se leggibile",
      "esito_taratura": "conforme|non conforme|non leggibile",
      "strumento_taratura_citato": "identificativo strumento",
      "cert_strumento_presente": true,
      "data_scadenza_calibratore_primario": "data ESATTA come scritta nel testo se presente (es. 22.05.2024), altrimenti null — solo estrazione letterale, nessun giudizio"
    }}
  ],
  "nc": [
    {{
      "severita": "ATTENZIONE|APPUNTO",
      "codice": "SAL-XX",
      "descrizione": "descrizione NC — NON includere qui la scadenza del calibratore primario, quella è gestita automaticamente dal codice",
      "riferimento": "IEC 60974-14 — QT.6495.024 §13"
    }}
  ],
  "sintesi": "breve riepilogo"
}}"""

    risposta = client.messages.create(
        model=MODEL,
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )
    testo_r = pulisci_json(risposta.content[0].text)
    try:
        risultato = json.loads(testo_r)
    except json.JSONDecodeError:
        risultato = {
            "raw": testo_r,
            "nc": [{"severita": "ATTENZIONE", "codice": "SAL-99",
                    "descrizione": "Risposta AI non parsabile — verificare manualmente",
                    "riferimento": "Errore interno"}]
        }

    # Calcolo deterministico scadenza calibratore primario (vedi
    # _valuta_scadenza_calibratore) — non lasciato al giudizio del modello,
    # eseguito qui su ogni saldatrice identificata a partire dal campo
    # data_scadenza_calibratore_primario estratto meccanicamente sopra.
    for sald in risultato.get("saldatrici_identificate", []):
        esito_scadenza = _valuta_scadenza_calibratore(
            sald.get("data_scadenza_calibratore_primario"),
            sald.get("identificativo", "")
        )
        if esito_scadenza:
            risultato.setdefault("nc", []).append(esito_scadenza)

    # Normalizza eventuali elementi nc malformati prima di restituire
    risultato["nc"] = [_normalizza_nc(nc, "check_saldatrici") for nc in risultato.get("nc", [])]
    risultato["tool"] = "check_saldatrici"
    risultato["eseguito"] = True
    return risultato


# ---------------------------------------------------------------------------
# FUNZIONE PRINCIPALE — LOOP AGENTICO
# ---------------------------------------------------------------------------

def analizza_pfc_en15085(percorso_welding_book: str) -> dict:
    """
    Esegue il loop agentico in sequenza:
    1. check_welding_map   (cartella 04)
    2. check_pfc           (cartella 11)
    3. check_en15085       (cartella 12)
    4. check_saldatrici    (cartella 13)
    Aggrega NC e calcola verdetto finale.
    """
    print("\n" + "="*60)
    print("WELDAIM — AGENT 5: PFC + EN15085 + Saldatrici + Welding Map")
    print("="*60)

    # Individua cartelle per prefisso
    cartella_04 = cartella_11 = cartella_12 = cartella_13 = None

    if os.path.isdir(percorso_welding_book):
        for nome in os.listdir(percorso_welding_book):
            percorso = os.path.join(percorso_welding_book, nome)
            if os.path.isdir(percorso):
                if nome.startswith("04"):
                    cartella_04 = percorso
                elif nome.startswith("11"):
                    cartella_11 = percorso
                elif nome.startswith("12"):
                    cartella_12 = percorso
                elif nome.startswith("13"):
                    cartella_13 = percorso

    print(f"[Agent5] Cartella 04 (Welding Map): {cartella_04 or 'NON TROVATA'}")
    print(f"[Agent5] Cartella 11 (PFC):          {cartella_11 or 'NON TROVATA'}")
    print(f"[Agent5] Cartella 12 (EN 15085):     {cartella_12 or 'NON TROVATA'}")
    print(f"[Agent5] Cartella 13 (Saldatrici):   {cartella_13 or 'NON TROVATA'}")

    tutte_nc = []
    risultati_tool = []

    # TOOL 1 — Welding Map
    print("\n[Agent5] → Avvio check_welding_map...")
    r1 = check_welding_map(cartella_04 or "")
    risultati_tool.append(r1)
    tutte_nc.extend(r1.get("nc", []))

    # TOOL 2 — PFC
    print("\n[Agent5] → Avvio check_pfc...")
    r2 = check_pfc(cartella_11 or "")
    risultati_tool.append(r2)
    tutte_nc.extend(r2.get("nc", []))

    # TOOL 3 — EN 15085
    print("\n[Agent5] → Avvio check_en15085...")
    r3 = check_en15085(cartella_12 or "")
    risultati_tool.append(r3)
    tutte_nc.extend(r3.get("nc", []))

    # TOOL 4 — Saldatrici
    print("\n[Agent5] → Avvio check_saldatrici...")
    r4 = check_saldatrici(cartella_13 or "")
    risultati_tool.append(r4)
    tutte_nc.extend(r4.get("nc", []))

    verdetto = _calcola_verdetto(tutte_nc)

    report = {
        "agente": "Agent5_PFC_EN15085",
        "verdetto": verdetto,
        "totale_nc": len(tutte_nc),
        "nc_stop": [nc for nc in tutte_nc if nc.get("severita") == "STOP"],
        "nc_attenzione": [nc for nc in tutte_nc if nc.get("severita") == "ATTENZIONE"],
        "nc_appunto": [nc for nc in tutte_nc if nc.get("severita") == "APPUNTO"],
        "tutte_nc": tutte_nc,
        "dettaglio_tool": risultati_tool,
        "nota_supervisore": (
            "Cross-check range WPQR vs scope certificato EN 15085 delegato al Supervisor. "
            "Verifica indirizzo aziendale EN 15085 vs WPS/WPQR/WQ delegata al Supervisor."
        )
    }

    return report


# ---------------------------------------------------------------------------
# CALCOLO VERDETTO
# ---------------------------------------------------------------------------

def _calcola_verdetto(nc_list: list) -> str:
    """STOP > ATTENZIONE > APPUNTO > GO"""
    severita = {nc.get("severita", "") for nc in nc_list}
    if "STOP" in severita:
        return "STOP"
    elif "ATTENZIONE" in severita:
        return "ATTENZIONE"
    elif "APPUNTO" in severita:
        return "APPUNTO"
    else:
        return "GO"


# ---------------------------------------------------------------------------
# STAMPA REPORT
# ---------------------------------------------------------------------------

def stampa_report(report: dict):
    """Stampa il report in formato leggibile nel terminale."""
    print("\n" + "="*60)
    print(f"REPORT AGENT 5 — {report['agente']}")
    print("="*60)
    print(f"VERDETTO: {report['verdetto']}")
    print(f"NC totali: {report['totale_nc']}  "
          f"(STOP: {len(report['nc_stop'])} | "
          f"ATTENZIONE: {len(report['nc_attenzione'])} | "
          f"APPUNTO: {len(report['nc_appunto'])})")

    if report["nc_stop"]:
        print("\n🔴 NC STOP:")
        for nc in report["nc_stop"]:
            print(f"  [{nc.get('codice','—')}] {nc.get('descrizione','')}")
            print(f"   Rif: {nc.get('riferimento','')}")

    if report["nc_attenzione"]:
        print("\n🟠 NC ATTENZIONE:")
        for nc in report["nc_attenzione"]:
            print(f"  [{nc.get('codice','—')}] {nc.get('descrizione','')}")
            print(f"   Rif: {nc.get('riferimento','')}")

    if report["nc_appunto"]:
        print("\n🟡 NC APPUNTO:")
        for nc in report["nc_appunto"]:
            print(f"  [{nc.get('codice','—')}] {nc.get('descrizione','')}")
            print(f"   Rif: {nc.get('riferimento','')}")

    if not report["tutte_nc"]:
        print("\n✅ Nessuna non conformità rilevata.")

    print(f"\nNOTA SUPERVISORE: {report['nota_supervisore']}")
    print("="*60)


# ---------------------------------------------------------------------------
# ENTRY POINT
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    PERCORSO_TEST = str(BASE_DIR / "test_docs")

    report = analizza_pfc_en15085(PERCORSO_TEST)
    stampa_report(report)

    # Salva report JSON per il supervisore
    output_json = str(BASE_DIR / "report_agents" / "report_agent5.json")
    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n[OUTPUT] Report JSON salvato in: {output_json}")