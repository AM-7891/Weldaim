"""
WELDAIM — AGENT 4: Materiali e Certificati
Verifica certificati materiale base (cartella 08) e materiale d'apporto + gas (cartella 06)

Versione con retrofit utils.py: ogni certificato (PDF o Excel) viene prima trasformato
in un DIGEST strutturato tramite chunking adattivo (nativo/OCR, corto/lungo — gestito
da utils.analizza_pdf_chunked / analizza_testo_chunked), poi i digest vengono passati
alla valutazione finale invece del testo grezzo concatenato.
Stesso pattern già validato su Agent 1 (WPS/WPQR) e Agent 2 (WQ).

Struttura attesa:
    test_docs/
        06_CERT_MATERIALE_APPORTO_GAS/
            [PDF certificati materiale d'apporto: 3.1 + 2.2 + Joincert]
            [PDF certificati gas di protezione: EN ISO 14175 + composizione]
        08_CERT_MATERIALE_BASE_TIPO 3.1/
            [PDF certificati 3.1]
            [Excel lista certificati commessa]
            [PDF elenco/riepilogo tracciabilità — opzionale, in alternativa o in aggiunta all'Excel]

Aggiornamento 2026-07-19:
- Il riepilogo di tracciabilità può arrivare come Excel E/O come PDF (spesso una stampa
  dell'Excel). Un PDF il cui nome richiama un elenco/riepilogo/lista/distinta viene
  distinto in modo deterministico (solo nome file, nessuna chiamata al modello) dai PDF
  di certificato singolo, e processato con lo stesso schema di estrazione dell'Excel.
- MB-EXCEL-01 (certificati dichiarati senza PDF corrispondente) è ora STOP, non ATTENZIONE:
  zero tolleranza su qualsiasi certificato mancante rispetto al riepilogo dichiarato (#024).

Aggiornamento 2026-07-26:
- RIMOSSA la ricerca di sottocartelle "Materiale d'apporto" e "gas" dentro la cartella 06
  in check_materiale_apporto, check_gas_protezione e nel fallback di check_scadenza_apporto.
  Da quando la struttura di test_docs è stata appiattita (os.listdir() non ricorsivo in
  utils.py), quelle sottocartelle non esistono più: il check falliva sempre con NC STOP/
  ATTENZIONE anche a certificati fisicamente presenti (falso positivo strutturale, non un
  problema documentale reale). Ora si legge direttamente tutto il contenuto di cartella_06
  e si lascia che la classificazione per contenuto (campo tipo_documento, già previsto nel
  prompt di check_materiale_apporto) distingua i documenti del filo da quelli del gas.
  Trade-off accettato: alcuni PDF possono essere "digest-ati" due volte (una per ciascun
  tool) se la cartella contiene entrambi i tipi di certificato — costo trascurabile alla
  scala attuale, da rivedere se la cartella crescerà molto (possibile ottimizzazione
  futura: estrazione unica condivisa tra i due tool).
"""

import os
import json
import anthropic
from datetime import date, datetime
from dotenv import load_dotenv

from utils import (
    estrai_testo_excel,
    analizza_pdf_chunked,
    analizza_testo_chunked,
    trova_file_per_estensione,
    pulisci_json
)

load_dotenv()

# ---------------------------------------------------------------------------
# CONFIGURAZIONE
# ---------------------------------------------------------------------------

MODEL = "claude-sonnet-4-6"
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


# ---------------------------------------------------------------------------
# PROMPT DIGEST — MATERIALE BASE (cartella 08, singolo certificato PDF)
# ---------------------------------------------------------------------------

PROMPT_CHUNK_MATBASE = """Sei un assistente che estrae dati tecnici da un certificato di materiale base (EN 10204).
Stai leggendo SOLO una porzione del documento (chunk) — è normale che alcuni campi non compaiano in questo chunk specifico. Non inventare valori.

Estrai in JSON SOLO i campi che trovi esplicitamente in QUESTO chunk (ometti le chiavi assenti):

- tipo_materiale (uno tra: "tubolare", "lamiera", "alluminio", "altro")
- fornitore (nome produttore/fornitore se leggibile)
- tipo_certificato (uno tra: "3.1", "2.2", "3.2", "altro")
- norma_prodotto (es. "EN 10210", "EN 10219", altra norma di prodotto)
- dop_presente (true/false se rilevabile — Dichiarazione di Prestazione)
- cpr_presente (true/false se rilevabile — marcatura CPR)
- colata_o_lotto (numero colata/lotto se presente, utile per tracciabilità)
- note_rilevanti (altre info utili: anomalie, doppie certificazioni, ecc.)

Rispondi SOLO con il JSON, nessun testo extra, nessun backtick.

=== CHUNK DEL DOCUMENTO ===
{testo_chunk}
"""

PROMPT_AGGREGAZIONE_MATBASE = """Hai ricevuto estrazioni parziali da chunk diversi dello stesso certificato materiale base.
Unisci tutto in UN SOLO JSON consolidato con questi campi (usa null se un'informazione non è mai comparsa in nessun chunk):

tipo_materiale, fornitore, tipo_certificato, norma_prodotto, dop_presente, cpr_presente,
colata_o_lotto, note_rilevanti

Se un campo compare con valori diversi in chunk diversi, usa il valore più completo e segnala la discrepanza in note_rilevanti.

Rispondi SOLO con il JSON finale, nessun testo extra, nessun backtick.

=== ESTRAZIONI PARZIALI ===
{risultati_parziali}
"""


# ---------------------------------------------------------------------------
# PROMPT DIGEST — LISTA CERTIFICATI (cartella 08) — usato sia per Excel che per
# PDF-riepilogo: stesso schema di estrazione, cambia solo la sorgente del testo.
# ---------------------------------------------------------------------------

PROMPT_CHUNK_EXCEL_LISTA = """Sei un assistente che estrae dati da una lista di certificati materiale per una commessa
(può provenire da un file Excel o da un PDF stampato da Excel — trattali allo stesso modo).
Il file non ha struttura standard — interpretalo liberamente.
Stai leggendo SOLO una porzione del contenuto (chunk). Non inventare valori.

Estrai in JSON:
- certificati_dichiarati (lista di stringhe — identificativi/numeri certificato o nomi file dichiarati in questa porzione)
- note_rilevanti

Rispondi SOLO con il JSON, nessun testo extra, nessun backtick.

=== CHUNK DEL CONTENUTO ===
{testo_chunk}
"""

PROMPT_AGGREGAZIONE_EXCEL_LISTA = """Hai ricevuto estrazioni parziali da chunk diversi della stessa lista certificati.
Unisci tutto in UN SOLO JSON consolidato:

- certificati_dichiarati (unione di tutte le liste trovate, senza duplicati)
- note_rilevanti

Rispondi SOLO con il JSON finale, nessun testo extra, nessun backtick.

=== ESTRAZIONI PARZIALI ===
{risultati_parziali}
"""


# ---------------------------------------------------------------------------
# PROMPT DIGEST — MATERIALE D'APPORTO (cartella 06)
# ---------------------------------------------------------------------------

PROMPT_CHUNK_APPORTO = """Sei un assistente che estrae dati tecnici da un documento relativo al materiale d'apporto per saldatura (certificato 3.1/2.2, Joincert, DDT).
Stai leggendo SOLO una porzione del documento (chunk). Non inventare valori.

Estrai in JSON SOLO i campi che trovi esplicitamente in QUESTO chunk (ometti le chiavi assenti):

- tipo_documento (uno tra: "certificato_3.1", "certificato_2.2", "certificato_combinato_3.1_2.2", "joincert", "ddt", "gas_protezione", "altro")
- materiale_apporto (descrizione/nomenclatura filo se leggibile)
- nomenclatura_filo (designazione normativa del filo, es. "G 42 4 M21 4Si1" secondo ISO 14341, se presente)
- posizioni_qualificate (lista posizioni di saldatura qualificate nel Joincert, es. ["PA","PB"], se presente)
- fornitore
- joincert_scaduto (true/false se rilevabile da una data di scadenza)
- data_scadenza_joincert (testo originale)
- evidenza_acquisto_ante_scadenza (true/false — presenza di DDT, bolla, PO/ordine cliente, o riferimento PFC con data)
- tipo_evidenza_acquisto (uno tra: "DDT", "bolla", "PO_ordine_cliente", "riferimento_PFC", "nessuna")
- data_evidenza_acquisto (testo originale se presente — la data del documento di evidenza, non confonderla con la data di consegna/DDT se sono documenti diversi)
- note_rilevanti

Rispondi SOLO con il JSON, nessun testo extra, nessun backtick.

=== CHUNK DEL DOCUMENTO ===
{testo_chunk}
"""

PROMPT_AGGREGAZIONE_APPORTO = """Hai ricevuto estrazioni parziali da chunk diversi dello stesso documento materiale d'apporto.
Unisci tutto in UN SOLO JSON consolidato con questi campi (usa null se un'informazione non è mai comparsa in nessun chunk):

tipo_documento, materiale_apporto, nomenclatura_filo, posizioni_qualificate, fornitore,
joincert_scaduto, data_scadenza_joincert, evidenza_acquisto_ante_scadenza, tipo_evidenza_acquisto,
data_evidenza_acquisto, note_rilevanti

Se un campo compare con valori diversi in chunk diversi, usa il valore più completo e segnala la discrepanza in note_rilevanti.

Rispondi SOLO con il JSON finale, nessun testo extra, nessun backtick.

=== ESTRAZIONI PARZIALI ===
{risultati_parziali}
"""


# ---------------------------------------------------------------------------
# PROMPT DIGEST — GAS DI PROTEZIONE (cartella 06)
# ---------------------------------------------------------------------------

PROMPT_CHUNK_GAS = """Sei un assistente che estrae dati tecnici da un certificato di gas di protezione per saldatura.
Stai leggendo SOLO una porzione del documento (chunk). Non inventare valori.

Estrai in JSON SOLO i campi che trovi esplicitamente in QUESTO chunk (ometti le chiavi assenti):

- en_iso_14175_citata (true/false)
- composizione_indicata (true/false)
- dettaglio_composizione (es. "Ar 82% CO2 18%")
- fornitore
- note_rilevanti

Rispondi SOLO con il JSON, nessun testo extra, nessun backtick.

=== CHUNK DEL DOCUMENTO ===
{testo_chunk}
"""

PROMPT_AGGREGAZIONE_GAS = """Hai ricevuto estrazioni parziali da chunk diversi dello stesso certificato gas.
Unisci tutto in UN SOLO JSON consolidato con questi campi (usa null se un'informazione non è mai comparsa in nessun chunk):

en_iso_14175_citata, composizione_indicata, dettaglio_composizione, fornitore, note_rilevanti

Rispondi SOLO con il JSON finale, nessun testo extra, nessun backtick.

=== ESTRAZIONI PARZIALI ===
{risultati_parziali}
"""


# ---------------------------------------------------------------------------
# ESTRAZIONE DIGEST — wrapper su utils.py (chunking automatico)
# ---------------------------------------------------------------------------

def estrai_digest_pdf(percorso_pdf: str, prompt_chunk: str, prompt_agg: str,
                       max_tokens_chunk: int = 800, max_tokens_agg: int = 1200) -> dict:
    """
    Estrae un digest strutturato da un singolo PDF con chunking automatico
    (nativo/OCR, corto/lungo — gestito interamente da utils.analizza_pdf_chunked).
    """
    print(f"  📄 Estrazione digest: {os.path.basename(percorso_pdf)}")
    digest = analizza_pdf_chunked(
        percorso_pdf=percorso_pdf,
        client=client,
        model=MODEL,
        prompt_per_chunk=prompt_chunk,
        prompt_aggregazione=prompt_agg,
        max_tokens_chunk=max_tokens_chunk,
        max_tokens_aggregazione=max_tokens_agg
    )
    digest["_nome_file"] = os.path.basename(percorso_pdf)
    return digest


def estrai_digest_excel(percorso_excel: str) -> dict:
    """
    Estrae un digest strutturato dalla lista Excel certificati con chunking automatico su testo.
    Su liste corte (caso tipico) analizza_testo_chunked fa una sola chiamata diretta, zero overhead.
    """
    print(f"  📊 Estrazione digest Excel: {os.path.basename(percorso_excel)}")
    testo = estrai_testo_excel(percorso_excel)
    # Forziamo chunk più piccoli (3000 car. invece del default 12000): una lista certificati
    # può avere decine di righe e produrre un array JSON lungo — meglio più chunk piccoli e
    # affidabili che uno grande a rischio troncamento (visto in test: 59 certificati su un
    # solo chunk da 2500 token andava comunque in errore).
    digest = analizza_testo_chunked(
        testo=testo,
        client=client,
        model=MODEL,
        prompt_per_chunk=PROMPT_CHUNK_EXCEL_LISTA,
        prompt_aggregazione=PROMPT_AGGREGAZIONE_EXCEL_LISTA,
        caratteri_per_chunk=3000,
        max_tokens_chunk=1500,
        max_tokens_aggregazione=3000,
        nome_file=os.path.basename(percorso_excel)
    )
    digest["_nome_file"] = os.path.basename(percorso_excel)
    return digest


# ---------------------------------------------------------------------------
# RICONOSCIMENTO PDF-RIEPILOGO (deterministico, solo nome file) — 2026-07-19
# ---------------------------------------------------------------------------

PAROLE_CHIAVE_RIEPILOGO = ["elenco", "lista", "riepilogo", "distinta", "tracciabilit"]


def _e_pdf_riepilogo(nome_file: str) -> bool:
    """
    Determina se un PDF è un elenco/riepilogo di tracciabilità (da processare come lista
    di certificati dichiarati) oppure un certificato di materiale singolo, in base al nome
    file. Controllo deterministico su parole chiave, nessuna chiamata al modello.
    """
    nome_norm = nome_file.lower()
    return any(parola in nome_norm for parola in PAROLE_CHIAVE_RIEPILOGO)


def estrai_digest_pdf_riepilogo(percorso_pdf: str) -> dict:
    """
    Estrae un digest strutturato da un PDF-riepilogo/elenco di tracciabilità (identificato
    dal nome file), usando lo stesso schema di estrazione della lista Excel — stesso
    principio, cambia solo la sorgente (PDF invece di foglio di calcolo). Spesso questi PDF
    derivano da una stampa Excel, quindi tipicamente hanno testo nativo (chunking gestito
    automaticamente da analizza_pdf_chunked, nativo o OCR a seconda del caso).
    """
    print(f"  📄 Estrazione digest PDF-riepilogo: {os.path.basename(percorso_pdf)}")
    digest = analizza_pdf_chunked(
        percorso_pdf=percorso_pdf,
        client=client,
        model=MODEL,
        prompt_per_chunk=PROMPT_CHUNK_EXCEL_LISTA,
        prompt_aggregazione=PROMPT_AGGREGAZIONE_EXCEL_LISTA,
        max_tokens_chunk=1500,
        max_tokens_aggregazione=3000
    )
    digest["_nome_file"] = os.path.basename(percorso_pdf)
    return digest


# ---------------------------------------------------------------------------
# CALCOLO DETERMINISTICO — confronto lista dichiarata (Excel e/o PDF) vs PDF presenti
# ---------------------------------------------------------------------------

def _normalizza_id(testo: str) -> str:
    """Normalizza un identificativo per il confronto (minuscolo, senza estensione file)."""
    if not testo:
        return ""
    t = str(testo).strip().lower()
    for ext in (".pdf", ".xlsx", ".xls", ".xlsm"):
        if t.endswith(ext):
            t = t[: -len(ext)]
    return t


def _calcola_confronto_excel(digest_liste: list, pdf_presenti: list) -> dict:
    """
    Calcolo deterministico (Python, non lasciato al modello) del confronto tra i certificati
    dichiarati nella/e lista/e di tracciabilità (Excel e/o PDF-riepilogo) e i PDF di
    certificato realmente presenti nella cartella 08.
    Con decine di voci, chiedere al modello di riscrivere per intero entrambe le liste nel
    JSON di output rischia il troncamento (visto in test: risposta tagliata a metà con 60+
    voci duplicate tra dichiarati/mancanti) — è un confronto di stringhe, non un giudizio,
    quindi va calcolato qui e passato come dato di fatto.
    Match per sottostringa normalizzata: l'identificativo dichiarato è tipicamente il nome
    file senza estensione, con piccole variazioni di spaziatura/maiuscole.
    """
    certificati_dichiarati = []
    for d in digest_liste:
        certificati_dichiarati.extend(d.get("certificati_dichiarati") or [])

    pdf_norm = [_normalizza_id(p) for p in pdf_presenti]

    mancanti = []
    for cert in certificati_dichiarati:
        cert_norm = _normalizza_id(cert)
        if not cert_norm:
            continue
        trovato = any(cert_norm in p or p in cert_norm for p in pdf_norm)
        if not trovato:
            mancanti.append(cert)

    return {
        "excel_presente": bool(digest_liste),
        "certificati_dichiarati": certificati_dichiarati,
        "certificati_mancanti": mancanti
    }


# ---------------------------------------------------------------------------
# TOOL 1 — VERIFICA MATERIALE BASE (cartella 08)
# ---------------------------------------------------------------------------

def check_materiale_base(cartella_08: str) -> dict:
    """
    Verifica certificati EN 10204 tipo 3.1 per materiale base, lavorando su digest
    già estratti (chunking automatico) invece che su testo grezzo concatenato.
    - Tubolari: 3.1 + DoP + CPR + coerenza EN 10210 (caldo) vs EN 10219 (freddo)
    - Lamiere:  3.1 + DoP
    - Alluminio: 3.1
    - Lista tracciabilità (Excel e/o PDF-riepilogo): confronto certificati dichiarati
      vs PDF di certificato presenti. Zero tolleranza: qualsiasi certificato dichiarato
      senza PDF corrispondente -> STOP (#024, 2026-07-19).
    """
    if not os.path.isdir(cartella_08):
        return {
            "tool": "check_materiale_base",
            "eseguito": False,
            "nc": [{"severita": "STOP", "codice": "MB-00",
                    "descrizione": "Cartella 08 materiale base non trovata",
                    "riferimento": "QT.6495.024 §8 — EN 10204"}]
        }

    pdf_files_tutti = trova_file_per_estensione(cartella_08, [".pdf"])
    excel_files = trova_file_per_estensione(cartella_08, [".xlsx", ".xls", ".xlsm"])

    # Separazione deterministica (solo nome file, nessuna chiamata al modello): un PDF il
    # cui nome richiama un elenco/riepilogo di tracciabilità va processato come LISTA di
    # certificati dichiarati, non come singolo certificato di materiale.
    pdf_riepilogo = [f for f in pdf_files_tutti if _e_pdf_riepilogo(os.path.basename(f))]
    pdf_files = [f for f in pdf_files_tutti if f not in pdf_riepilogo]

    if not pdf_files and not excel_files and not pdf_riepilogo:
        return {
            "tool": "check_materiale_base",
            "eseguito": True,
            "nc": [{"severita": "STOP", "codice": "MB-01",
                    "descrizione": "Nessun documento trovato nella cartella 08",
                    "riferimento": "QT.6495.024 §8 — EN 10204 tipo 3.1 obbligatorio"}]
        }

    digest_certificati = [
        estrai_digest_pdf(f, PROMPT_CHUNK_MATBASE, PROMPT_AGGREGAZIONE_MATBASE)
        for f in pdf_files
    ]
    digest_excel = [estrai_digest_excel(f) for f in excel_files]
    digest_pdf_riepilogo = [estrai_digest_pdf_riepilogo(f) for f in pdf_riepilogo]

    # Le liste dichiarate possono arrivare da Excel E/O da PDF-riepilogo: si sommano,
    # il confronto finale non distingue la fonte.
    digest_liste_tracciabilita = digest_excel + digest_pdf_riepilogo

    lista_file_presenti = [d.get("_nome_file") for d in digest_certificati]
    confronto_excel_calc = _calcola_confronto_excel(digest_liste_tracciabilita, lista_file_presenti)

    if confronto_excel_calc["excel_presente"]:
        n_dichiarati = len(confronto_excel_calc["certificati_dichiarati"])
        n_mancanti = len(confronto_excel_calc["certificati_mancanti"])
        riepilogo_excel = (
            f"CONFRONTO LISTA TRACCIABILITA' vs PDF PRESENTI (già calcolato in Python — non ricalcolarlo tu, "
            f"non riscrivere le liste nella risposta): {n_dichiarati} certificati dichiarati in totale, "
            f"{n_mancanti} senza PDF corrispondente nella cartella 08."
        )
    else:
        riepilogo_excel = "Nessuna lista di tracciabilità (Excel o PDF-riepilogo) presente in cartella 08."

    prompt = f"""Sei un esperto di certificazione materiali per strutture saldate secondo EN 15085.
Analizza i seguenti DIGEST già estratti (via chunking) dai certificati della cartella 08 (Certificati Materiale Base) di un Welding Book.

FILE PDF PRESENTI: {json.dumps(lista_file_presenti, ensure_ascii=False)}

DIGEST CERTIFICATI:
{json.dumps(digest_certificati, ensure_ascii=False, indent=2)}

{riepilogo_excel}

REGOLE DI VERIFICA:
1. Per ogni digest, identifica il tipo di materiale (tubolare, lamiera, alluminio, altro).
2. TUBOLARI: obbligatori EN 10204 tipo 3.1 + DoP (Dichiarazione di Prestazione) + CPR.
   Verifica coerenza tra EN 10210 (formato caldo) e EN 10219 (formato freddo) — non intercambiabili per lo stesso prodotto.
3. LAMIERE: obbligatori EN 10204 tipo 3.1 + DoP.
4. ALLUMINIO: obbligatorio EN 10204 tipo 3.1.
5. Verifica che il tipo di certificato sia effettivamente 3.1 (non 2.2, non 3.2).
6. Se più certificati fanno riferimento allo stesso materiale ma con fornitori diversi,
   analizzali individualmente — è normale avere più fornitori per lo stesso tipo di materiale.
7. Non generare tu una NC sui certificati mancanti della lista di tracciabilità — è già gestito
   dal calcolo sopra, se ne occupa il codice, non tu.

LIVELLI DI SEVERITA':
- STOP: certificato 3.1 assente o tipo certificato errato
- ATTENZIONE: DoP assente per tubolari/lamiere, CPR assente per tubolari
- Se mancano sia il cert. 3.1 che il 2.2 (anche in documento combinato) -> ATTENZIONE.
  Se presente documento combinato 3.1+2.2 -> requisito soddisfatto, nessuna NC.
  NOTA: il certificato 2.2 riporta per definizione valori nominali di specifica, non valori misurati.

IMPORTANTE SUL FORMATO: nelle descrizioni delle NC usa conteggi/sintesi, non elencare decine di nomi
file per esteso — la risposta deve restare compatta.

Rispondi ESCLUSIVAMENTE in JSON valido, senza backtick, senza testo prima o dopo:
{{
  "materiali_identificati": [
    {{
      "file": "nome_file.pdf",
      "tipo_materiale": "tubolare|lamiera|alluminio|altro",
      "fornitore": "nome fornitore se leggibile",
      "tipo_certificato": "3.1|2.2|altro",
      "norma_prodotto": "EN 10210|EN 10219|altra",
      "dop_presente": true,
      "cpr_presente": true,
      "note": "osservazioni specifiche, sintetiche"
    }}
  ],
  "nc": [
    {{
      "severita": "STOP|ATTENZIONE|APPUNTO",
      "codice": "MB-01",
      "descrizione": "descrizione NC, sintetica",
      "riferimento": "norma o paragrafo"
    }}
  ],
  "sintesi": "testo breve riassuntivo"
}}"""

    risposta = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}]
    )

    testo = pulisci_json(risposta.content[0].text)

    try:
        risultato = json.loads(testo)
    except json.JSONDecodeError:
        risultato = {
            "raw": testo,
            "nc": [{"severita": "ATTENZIONE", "codice": "MB-99",
                    "descrizione": "Risposta AI non parsabile — verificare manualmente",
                    "riferimento": "Errore interno agente"}]
        }

    # Confronto tracciabilità iniettato dal calcolo deterministico, non da quanto (eventualmente) scritto dal modello
    risultato["confronto_lista_excel"] = confronto_excel_calc

    # NC deterministica per certificati mancanti (niente liste lunghe dentro al testo libero del modello).
    # Zero tolleranza (#024, 2026-07-19): qualsiasi certificato dichiarato senza PDF corrispondente -> STOP.
    if confronto_excel_calc["certificati_mancanti"]:
        n_mancanti = len(confronto_excel_calc["certificati_mancanti"])
        anteprima = ", ".join(confronto_excel_calc["certificati_mancanti"][:5])
        extra = f" (+{n_mancanti - 5} altri — vedi campo confronto_lista_excel.certificati_mancanti)" if n_mancanti > 5 else ""
        risultato.setdefault("nc", []).append({
            "severita": "STOP",
            "codice": "MB-EXCEL-01",
            "descrizione": f"{n_mancanti} certificati dichiarati nella lista di tracciabilità non hanno un PDF corrispondente in cartella 08: {anteprima}{extra}.",
            "riferimento": "QT.6495.024 §8 — EN 10204 tipo 3.1"
        })

    risultato["tool"] = "check_materiale_base"
    risultato["eseguito"] = True
    risultato["_digest"] = digest_certificati
    risultato["_digest_excel"] = digest_liste_tracciabilita
    return risultato


# ---------------------------------------------------------------------------
# CALCOLO DETERMINISTICO — scadenza Joincert (Python, non lasciato al modello)
# ---------------------------------------------------------------------------

def _parse_data_certificato(testo_data):
    """
    Interpreta una data testuale nei formati comuni trovati nei certificati.
    Ritorna un oggetto date, o None se non parsabile.
    """
    if not testo_data:
        return None
    testo_data = str(testo_data).strip()
    formati = ["%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d",
               "%Y.%m.%d", "%Y/%m/%d", "%d.%m.%y", "%d/%m/%y"]
    for fmt in formati:
        try:
            return datetime.strptime(testo_data, fmt).date()
        except ValueError:
            continue
    return None


def _calcola_scadenza_joincert(digest_apporto: list):
    """
    Calcolo deterministico (Python) dello stato di scadenza del Joincert — non lasciato
    all'interpretazione del modello, stesso principio già usato in Agente 2 per le WQ.
    Cerca tra i digest un documento con data_scadenza_joincert valorizzata.

    Ritorna: (joincert_scaduto: bool|None, data_scadenza: date|None, giorni: int|None)
    None = data assente o non interpretabile nei digest -> richiede verifica manuale.
    """
    for d in digest_apporto:
        data_parsata = _parse_data_certificato(d.get("data_scadenza_joincert"))
        if data_parsata:
            giorni = (data_parsata - date.today()).days
            return giorni < 0, data_parsata, giorni
    return None, None, None


# ---------------------------------------------------------------------------
# TOOL 2 — VERIFICA MATERIALE D'APPORTO (cartella 06 - lettura piatta)
# ---------------------------------------------------------------------------

def check_materiale_apporto(cartella_06: str) -> dict:
    """
    Verifica certificati materiale d'apporto (filo).
    Obbligatori: cert. 3.1 + cert. 2.2 produttore + documento Joincert valido.
    Se Joincert scaduto: accetta DDT/bolla ante-scadenza o riferimento PFC.

    Legge direttamente tutto il contenuto di cartella_06 (nessuna sottocartella
    "Materiale d'apporto" - vedi nota 2026-07-26 in testa al file). La cartella
    può contenere anche certificati gas: la classificazione per contenuto
    (tipo_documento) fatta dal modello nel prompt distingue i due tipi.
    """
    pdf_files = trova_file_per_estensione(cartella_06, [".pdf"])

    if not pdf_files:
        return {
            "tool": "check_materiale_apporto",
            "eseguito": True,
            "joincert_scaduto": False,
            "nc": [{"severita": "STOP", "codice": "MA-01",
                    "descrizione": "Nessun documento PDF trovato nella cartella 06_CERT_MATERIALE_APPORTO_GAS",
                    "riferimento": "QT.6495.024 §6 — certificati obbligatori"}]
        }

    digest_apporto = [
        estrai_digest_pdf(f, PROMPT_CHUNK_APPORTO, PROMPT_AGGREGAZIONE_APPORTO)
        for f in pdf_files
    ]

    joincert_scaduto_calc, data_scadenza_calc, giorni_calc = _calcola_scadenza_joincert(digest_apporto)
    oggi_str = date.today().strftime("%Y-%m-%d")

    if joincert_scaduto_calc is None:
        contesto_calcolo = (
            f"CALCOLO SCADENZA JOINCERT: nessuna data di scadenza interpretabile trovata nei digest "
            f"(data odierna di riferimento: {oggi_str}). Non dedurre tu una scadenza — segnala la cosa "
            f"come impossibilità di verifica automatica."
        )
    elif joincert_scaduto_calc:
        contesto_calcolo = (
            f"CALCOLO DETERMINISTICO SCADENZA JOINCERT (già calcolato in Python, usa SEMPRE questo valore "
            f"— non dedurlo tu dalle date nei digest): joincert_scaduto = true, "
            f"scaduto il {data_scadenza_calc.isoformat()}, da {abs(giorni_calc)} giorni rispetto a oggi ({oggi_str})."
        )
    else:
        contesto_calcolo = (
            f"CALCOLO DETERMINISTICO SCADENZA JOINCERT (già calcolato in Python, usa SEMPRE questo valore "
            f"— non dedurlo tu dalle date nei digest): joincert_scaduto = false, "
            f"valido fino al {data_scadenza_calc.isoformat()}, ancora {giorni_calc} giorni rispetto a oggi ({oggi_str})."
        )

    prompt = f"""Sei un esperto di certificazione materiali d'apporto per saldatura secondo EN 15085 e standard Deutsche Bahn.
Analizza i seguenti DIGEST già estratti (via chunking) dai documenti della cartella 06
(CERT_MATERIALE_APPORTO_GAS). ATTENZIONE: questa cartella può contenere anche certificati
del gas di protezione mescolati agli stessi file — classifica ogni documento per il suo
contenuto reale (campo tipo_documento), non assumere che tutti i file riguardino il filo.

{contesto_calcolo}

DIGEST DOCUMENTI:
{json.dumps(digest_apporto, ensure_ascii=False, indent=2)}

REGOLE DI VERIFICA:
1. Devono essere presenti: (a) certificato che attesti EN 10204 tipo 3.1 E tipo 2.2 — possono essere
   due documenti separati OPPURE un unico documento combinato che contiene entrambe le sezioni 3.1 e 2.2.
   Un documento combinato 3.1+2.2 è pienamente accettabile e non va segnalato come NC.
   (b) documento Joincert (omologazione DB del materiale d'apporto).
2. Il Joincert deve essere valido (non scaduto) — usa il valore joincert_scaduto indicato nel CALCOLO
   DETERMINISTICO sopra, non ricalcolarlo confrontando tu le date. Se scaduto, è accettabile SOLO se presente
   evidenza di acquisto ante-scadenza: DDT, bolla di consegna, PO/ordine cliente, o riferimento al PFC —
   un PO/ordine cliente è considerato impegno contrattuale valido, non serve necessariamente prova di
   movimentazione merce (DDT/bolla).
   IMPORTANTE: confronta la data di OGNI singolo documento di evidenza individualmente con la data di
   scadenza del Joincert — non generalizzare "tutte le evidenze sono successive" se anche solo un
   documento (es. il PO) ha data antecedente alla scadenza.
3. Joincert scaduto senza NESSUNA evidenza ante-scadenza (DDT, bolla, PO o PFC con data precedente) -> STOP.
4. Joincert scaduto con ALMENO UNA evidenza ante-scadenza (anche solo il PO, se datato prima della scadenza) -> ATTENZIONE.
5. Manca cert. 3.1 -> STOP.
6. Manca cert. 2.2 produttore -> ATTENZIONE.
7. Manca Joincert del tutto -> STOP.
8. Un documento identificato come certificato gas di protezione (tipo_documento diverso da
   3.1/2.2/joincert/ddt riguardanti il filo) NON genera NC in questo check — è compito di
   check_gas_protezione, ignoralo qui.

Rispondi ESCLUSIVAMENTE in JSON valido, senza backtick, senza testo prima o dopo:
{{
  "documenti_identificati": [
    {{
      "file": "nome_file.pdf",
      "tipo_documento": "certificato_3.1|certificato_2.2|joincert|ddt|gas_protezione|altro",
      "materiale_apporto": "descrizione se leggibile",
      "joincert_scaduto": false,
      "data_scadenza_joincert": "data se presente",
      "evidenza_acquisto_ante_scadenza": false,
      "note": "osservazioni"
    }}
  ],
  "joincert_scaduto": false,
  "nc": [
    {{
      "severita": "STOP|ATTENZIONE|APPUNTO",
      "codice": "MA-01",
      "descrizione": "descrizione NC",
      "riferimento": "norma o paragrafo"
    }}
  ],
  "sintesi": "testo breve riassuntivo"
}}"""

    risposta = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}]
    )

    testo = pulisci_json(risposta.content[0].text)

    try:
        risultato = json.loads(testo)
    except json.JSONDecodeError:
        risultato = {
            "raw": testo,
            "joincert_scaduto": False,
            "nc": [{"severita": "ATTENZIONE", "codice": "MA-99",
                    "descrizione": "Risposta AI non parsabile — verificare manualmente",
                    "riferimento": "Errore interno"}]
        }

    # Override deterministico: il valore calcolato in Python vince sempre su quello (eventualmente
    # diverso) restituito dal modello nel JSON — stesso principio del check scadenza WQ in Agente 2.
    if joincert_scaduto_calc is not None:
        risultato["joincert_scaduto"] = joincert_scaduto_calc
    else:
        # Data non interpretabile automaticamente: fail-safe conservativo, tratta come scaduto
        # per forzare la verifica (attiva check_scadenza_apporto) invece di passare silenziosamente.
        risultato["joincert_scaduto"] = True
        risultato.setdefault("nc", []).append({
            "severita": "ATTENZIONE",
            "codice": "MA-05",
            "descrizione": "Impossibile determinare automaticamente la data di scadenza del Joincert dai documenti — verificare manualmente.",
            "riferimento": "QT.6495.024 §6"
        })

    risultato["tool"] = "check_materiale_apporto"
    risultato["eseguito"] = True
    risultato["_digest"] = digest_apporto
    return risultato


# ---------------------------------------------------------------------------
# TOOL 3 — VERIFICA GAS DI PROTEZIONE (cartella 06 - lettura piatta)
# ---------------------------------------------------------------------------

def check_gas_protezione(cartella_06: str) -> dict:
    """
    Verifica certificato gas di protezione.
    - File presente: verifica EN ISO 14175 + composizione. Se mancano -> APPUNTO.
    - File assente: ATTENZIONE (non bloccante).

    Legge direttamente tutto il contenuto di cartella_06 (nessuna sottocartella
    "gas" - vedi nota 2026-07-26 in testa al file). La cartella può contenere
    anche certificati del materiale d'apporto: il prompt istruisce il modello
    a ignorare i documenti che non sono certificati gas.
    """
    pdf_files = trova_file_per_estensione(cartella_06, [".pdf"])

    if not pdf_files:
        return {
            "tool": "check_gas_protezione",
            "eseguito": True,
            "gas_presente": False,
            "nc": [{"severita": "ATTENZIONE", "codice": "GAS-01",
                    "descrizione": "Nessun documento PDF trovato nella cartella 06_CERT_MATERIALE_APPORTO_GAS — certificato gas di protezione non fornito",
                    "riferimento": "QT.6495.024 §6 — buona prassi per processi con gas di protezione"}],
            "sintesi": "Nessun certificato gas di protezione presente."
        }

    digest_gas = [
        estrai_digest_pdf(f, PROMPT_CHUNK_GAS, PROMPT_AGGREGAZIONE_GAS,
                          max_tokens_chunk=500, max_tokens_agg=700)
        for f in pdf_files
    ]

    prompt = f"""Sei un esperto di materiali per saldatura.
Analizza i seguenti DIGEST già estratti (via chunking) da documenti della cartella
06_CERT_MATERIALE_APPORTO_GAS. ATTENZIONE: questa cartella può contenere anche
certificati del materiale d'apporto (filo) mescolati agli stessi file — questo check
riguarda SOLO i documenti che sono effettivamente certificati del gas di protezione;
ignora completamente ogni documento che non riguardi il gas (es. certificati 3.1/2.2
o Joincert del filo), non generare NC per la loro presenza o assenza qui.

DIGEST:
{json.dumps(digest_gas, ensure_ascii=False, indent=2)}

VERIFICA (aggregando solo sui digest che sono effettivamente certificati gas):
1. Almeno un digest di un certificato gas fa riferimento alla norma EN ISO 14175?
2. È indicata la composizione del gas (es. percentuali di Ar, CO2, O2, He)?
3. Se NESSUNO dei documenti forniti è un certificato gas (tutti risultano di altro tipo,
   es. materiale d'apporto), tratta la situazione come gas_presente: false e genera
   la stessa ATTENZIONE prevista per l'assenza totale.

REGOLE SEVERITA':
- Entrambi presenti (EN ISO 14175 + composizione) -> nessuna NC
- Manca composizione O riferimento EN ISO 14175 -> APPUNTO
- Nessun certificato gas identificabile tra i documenti forniti -> ATTENZIONE
- Non emettere mai STOP per il gas — è sempre non bloccante

Rispondi ESCLUSIVAMENTE in JSON valido, senza backtick, senza testo prima o dopo:
{{
  "gas_presente": true,
  "en_iso_14175_citata": true,
  "composizione_indicata": true,
  "dettaglio_composizione": "es. Ar 82% CO2 18%",
  "nc": [],
  "sintesi": "breve riepilogo"
}}"""

    risposta = client.messages.create(
        model=MODEL,
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}]
    )

    testo = pulisci_json(risposta.content[0].text)

    try:
        risultato = json.loads(testo)
    except json.JSONDecodeError:
        risultato = {
            "gas_presente": True,
            "raw": testo,
            "nc": [{"severita": "APPUNTO", "codice": "GAS-99",
                    "descrizione": "Risposta AI non parsabile — verificare manualmente",
                    "riferimento": "Errore interno"}]
        }

    risultato["tool"] = "check_gas_protezione"
    risultato["eseguito"] = True
    risultato["_digest"] = digest_gas
    return risultato


# ---------------------------------------------------------------------------
# TOOL 4 — VERIFICA SCADENZA APPORTO (solo se Joincert scaduto)
# ---------------------------------------------------------------------------

def check_scadenza_apporto(cartella_06: str, digest_apporto: list = None) -> dict:
    """
    Attivato solo se check_materiale_apporto ha rilevato Joincert scaduto.
    Riusa i digest già estratti da check_materiale_apporto (nessuna nuova lettura/OCR dei PDF —
    risparmio token e tempo). Se chiamato in standalone senza digest, li estrae al volo come fallback,
    leggendo direttamente cartella_06 (nessuna sottocartella "apporto" - vedi nota 2026-07-26).
    Cerca evidenza di acquisto ante-scadenza: DDT, bolla, riferimento PFC.
    """
    if not digest_apporto:
        pdf_files = trova_file_per_estensione(cartella_06, [".pdf"])
        if not pdf_files:
            return {
                "tool": "check_scadenza_apporto",
                "eseguito": False,
                "nc": [{"severita": "STOP", "codice": "SA-00",
                        "descrizione": "Joincert scaduto — nessun documento trovato in cartella 06 per verificare evidenza acquisto ante-scadenza",
                        "riferimento": "QT.6495.024 §6"}]
            }
        digest_apporto = [
            estrai_digest_pdf(f, PROMPT_CHUNK_APPORTO, PROMPT_AGGREGAZIONE_APPORTO)
            for f in pdf_files
        ]

    oggi_str = date.today().strftime("%Y-%m-%d")

    prompt = f"""Il Joincert del materiale d'apporto risulta scaduto (confermato da calcolo deterministico,
data odierna di riferimento: {oggi_str}).
Analizza i seguenti DIGEST già estratti e cerca evidenza di acquisto PRIMA della scadenza (non prima di oggi).
Evidenze accettabili: DDT, bolla di consegna, PO/ordine cliente, o riferimento al PFC — un PO/ordine
cliente è impegno contrattuale valido, non serve necessariamente prova di movimentazione merce.

IMPORTANTE: nei digest possono comparire PIÙ documenti/date diverse (es. un DDT di consegna E un PO
distinti). Confronta la data di OGNI documento individualmente con la data di scadenza del Joincert.
Se anche un solo documento (es. il PO) ha data antecedente alla scadenza, l'evidenza è valida — non
affermare che "tutte le date sono successive" senza aver verificato ciascuna singolarmente.

DIGEST:
{json.dumps(digest_apporto, ensure_ascii=False, indent=2)}

Rispondi ESCLUSIVAMENTE in JSON valido, senza backtick, senza testo prima o dopo:
{{
  "evidenza_trovata": false,
  "tipo_evidenza": "DDT|bolla|PO_ordine_cliente|riferimento_PFC|nessuna",
  "data_evidenza": "data se leggibile",
  "nc": [
    {{
      "severita": "STOP",
      "codice": "SA-01",
      "descrizione": "descrizione",
      "riferimento": "QT.6495.024 §6"
    }}
  ],
  "sintesi": "breve riepilogo"
}}"""

    risposta = client.messages.create(
        model=MODEL,
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}]
    )

    testo = pulisci_json(risposta.content[0].text)

    try:
        risultato = json.loads(testo)
    except json.JSONDecodeError:
        risultato = {
            "evidenza_trovata": False,
            "nc": [{"severita": "STOP", "codice": "SA-99",
                    "descrizione": "Impossibile determinare evidenza acquisto — Joincert scaduto",
                    "riferimento": "QT.6495.024 §6"}]
        }

    risultato["tool"] = "check_scadenza_apporto"
    risultato["eseguito"] = True
    return risultato


# ---------------------------------------------------------------------------
# FUNZIONE PRINCIPALE — LOOP AGENTICO
# ---------------------------------------------------------------------------

def analizza_materiali(percorso_welding_book: str) -> dict:
    """
    Esegue il loop agentico in sequenza:
    1. check_materiale_base     (cartella 08)
    2. check_materiale_apporto  (cartella 06)
    3. check_gas_protezione     (cartella 06)
    4. check_scadenza_apporto   (solo se Joincert scaduto — riusa i digest del punto 2)
    Aggrega NC e calcola verdetto finale.
    """
    print("\n" + "="*60)
    print("WELDAIM — AGENT 4: Materiali e Certificati")
    print("="*60)

    cartella_06 = None
    cartella_08 = None

    if os.path.isdir(percorso_welding_book):
        for nome in os.listdir(percorso_welding_book):
            percorso = os.path.join(percorso_welding_book, nome)
            if os.path.isdir(percorso):
                if nome.startswith("06"):
                    cartella_06 = percorso
                elif nome.startswith("08"):
                    cartella_08 = percorso

    print(f"[Agent4] Cartella 06: {cartella_06 or 'NON TROVATA'}")
    print(f"[Agent4] Cartella 08: {cartella_08 or 'NON TROVATA'}")

    tutte_nc = []
    risultati_tool = []

    # TOOL 1 — materiale base
    print("\n[Agent4] → Avvio check_materiale_base...")
    r1 = check_materiale_base(cartella_08 or "")
    risultati_tool.append(r1)
    tutte_nc.extend(r1.get("nc", []))

    # TOOL 2 — materiale d'apporto
    print("\n[Agent4] → Avvio check_materiale_apporto...")
    r2 = check_materiale_apporto(cartella_06 or "")
    risultati_tool.append(r2)
    tutte_nc.extend(r2.get("nc", []))

    # TOOL 3 — gas di protezione
    print("\n[Agent4] → Avvio check_gas_protezione...")
    r3 = check_gas_protezione(cartella_06 or "")
    risultati_tool.append(r3)
    tutte_nc.extend(r3.get("nc", []))

    # TOOL 4 — scadenza apporto (condizionale, riusa digest di r2)
    joincert_scaduto = r2.get("joincert_scaduto", False)
    if joincert_scaduto:
        print("\n[Agent4] → Joincert scaduto rilevato. Avvio check_scadenza_apporto...")
        r4 = check_scadenza_apporto(cartella_06 or "", digest_apporto=r2.get("_digest"))
        risultati_tool.append(r4)
        tutte_nc.extend(r4.get("nc", []))
    else:
        print("\n[Agent4] → Joincert valido. Tool check_scadenza_apporto non necessario.")

    verdetto = _calcola_verdetto(tutte_nc)

    report = {
        "agente": "Agent4_Materiali",
        "verdetto": verdetto,
        "totale_nc": len(tutte_nc),
        "nc_stop": [nc for nc in tutte_nc if nc.get("severita") == "STOP"],
        "nc_attenzione": [nc for nc in tutte_nc if nc.get("severita") == "ATTENZIONE"],
        "nc_appunto": [nc for nc in tutte_nc if nc.get("severita") == "APPUNTO"],
        "tutte_nc": tutte_nc,
        "dettaglio_tool": risultati_tool,
        "nota_supervisore": (
            "Verificare cross-check posizioni Joincert vs WPS — delegato al Supervisor."
            if not joincert_scaduto else
            "Joincert scaduto — verificare evidenza acquisto ante-scadenza prima dell'approvazione."
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
    print(f"REPORT AGENT 4 — {report['agente']}")
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
    PERCORSO_TEST = r"C:\Users\angma\Desktop\weldaim\test_docs"

    report = analizza_materiali(PERCORSO_TEST)
    stampa_report(report)

    # Salva il report JSON per il supervisore
    output_json = r"C:\Users\angma\Desktop\weldaim\report_agents\report_agent4.json"
    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n[OUTPUT] Report JSON salvato in: {output_json}")
