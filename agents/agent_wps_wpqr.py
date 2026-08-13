# agent_wps_wpqr.py
# Agente 1 WeldAIM — Verifica WPS e WPQR (con chunking WPQR via utils.py)
#
# Riferimenti normativi (enti internazionali):
#   ISO 15609-1, ISO 15614-1, ISO 15614-2, ISO 15613,
#   EN 1011-1, EN 1011-2 Annex C, EN 1011-4, ISO 13916,
#   EN 15085-3 Annex B, serie ISO 9692, ISO/TR 15608
#
# Riferimenti contrattuali/capitolati (linee guida committente):
#   QT.6495.024 — Requisiti Welding Book (Geismar Italia)
#   QT.6495.022 — Requisiti carpenterie (Geismar Italia)
#   QT.6495.023 — Requisiti report mock-up (Geismar Italia)
#
# NOTA (2026-07-25): rimosso il caricamento della cartella 14_DISEGNI
# (disegni di produzione scansionati). Motivo: l'OCR su questi disegni
# risultava sistematicamente inaffidabile per la lettura dei simboli di
# giunto (ISO 2553) - generava solo APPUNTI a basso valore informativo
# ("simboli non leggibili dalla trascrizione OCR"), a fronte di un costo
# OCR/token non trascurabile. La welding map (04_WELDING_MAP) e la tavola
# giunti (05_CLASS_SALD) restano invece caricate: sono documenti nativi
# (non scansionati) e nei test hanno fornito segnale utile e affidabile
# per il check_giunto_iso2553, che gia' gestisce la priorita' delle fonti
# in "best effort" (welding map > disegno > tavola giunti > nessuno) -
# nessuna modifica alla logica del tool e' stata necessaria: con i disegni
# fuori, il tool usera' automaticamente welding map/tavola giunti.
# La lettura strutturata dei disegni (materiali, spessori, giunti, con
# output in un Excel di welding map precisa) e' spostata in roadmap come
# modulo dedicato "Drawing Intelligence" (probabile approccio vision,
# non OCR testuale).
#
# NOTA (2026-07-29/30 — PROMPT CACHING):
# Aggiunto un solo campo cache_control a livello di richiesta (cache
# automatica, non breakpoint espliciti come su Agent 3) alla chiamata
# client.messages.create() dentro il loop agentico while True. Motivo:
# questo loop chiama l'API piu' volte nella stessa run (una per ogni
# turno di tool_use, tipicamente 2-4 volte per via dei 5 tool concatenati
# in TOOLS), rimandando ogni volta l'intera cronologia crescente. La
# cache automatica sposta il breakpoint in avanti automaticamente ad ogni
# turno, cachando sia l'array TOOLS (statico, ~2000+ token, non cambia
# mai tra run diverse) sia il primo messaggio utente (WPS+WPQR, statico
# entro la stessa run). Fix minimo di una riga, nessuna modifica alla
# struttura del prompt o alla logica di dominio dei 5 check.
# Diagnostica aggiunta con _stampa_uso_cache() per verificare hit reali,
# non assumerli dal codice.
#
# NOTA (2026-08-08 — SCHEMA DIGEST WPQR ESPANSO):
# Il campo unico "range_spessore_qualificato" e' stato sostituito con
# campi separati per tipo di giunto e tipo di spessore, dopo aver
# verificato (test diretto OCR su WPQR reale RINA/Abbati 01/16, IWE
# Angelo) che una singola WPQR qualifica comunemente SIA BW SIA FW con
# range DIVERSI, e che nel documento sorgente esistono fino a 3 righe di
# spessore normativamente distinte (Parent material thickness BW/FW,
# Throat thickness, Weld deposit thickness) - comprimerle in un solo
# campo stringa causava perdita/ambiguita' del dato, non un problema di
# qualita' OCR o di taglio-chunk come inizialmente ipotizzato. Nuovi
# campi: range_spessore_bw, range_spessore_fw_t1, range_spessore_fw_t2,
# altezza_gola_fw, spessore_deposito_saldato. Aggiunti anche i campi
# tipo_processo, passate, tipo_giunto_qualificato, diametro_esterno_
# qualificato, tipo_gas_protezione, tipo_corrente, apporto_termico
# (richiesti da IWE per copertura completa dei dati WPQR rilevanti).
# Coerente modifica applicata a PROMPT_CHECK2 in supervisor_agent.py
# (stessa sessione) - NON ancora applicata al prompt interno di
# check_corrispondenza_1a1 in questo file, che resta su base testuale
# generica per ora (task separato, fuori scope di questa modifica).
#
# NOTA (2026-08-13 — TEMPERATURE=0 SU CHIAMATA VERDETTO-GENERANTE):
# Aggiunto temperature=TEMPERATURA_VERDETTO (importata da utils.py, valore
# 0) alla chiamata client.messages.create() dentro il loop agentico
# (while True, variabile turno). PROBLEMA: questa chiamata genera i
# verdetti (GO/ATTENZIONE/STOP) tramite i tool_use, ma girava a
# temperature di default (1.0) — non deterministica. Confermato su
# Streamlit Cloud: stesso set documenti, run locale vs cloud, Agente 1
# spostato da GO ad ATTENZIONE. TEMPERATURA_ESTRAZIONE (usata dentro
# utils.py per l'estrazione dati WPQR via analizza_pdf_chunked) non era
# il problema — quella era gia' corretta. Il problema era specificamente
# questa chiamata, che genera il verdetto finale e non passava alcun
# parametro temperature.

import os
import json
import anthropic
from datetime import datetime
from dotenv import load_dotenv

from utils import estrai_testo_pdf_semplice, analizza_pdf_chunked, _stampa_uso_cache, BASE_DIR, TEMPERATURA_VERDETTO

load_dotenv()

# ─────────────────────────────────────────────
# CONFIGURAZIONE
# ─────────────────────────────────────────────
MODEL = "claude-sonnet-4-6"

# Struttura cartelle attesa:
#   test_docs/
#     01_WPS/        → PDF dei WPS di produzione
#     02_WPQR/       → PDF dei WPQR
#     04_WELDING_MAP/ → PDF welding map (opzionale)
#     05_CLASS_SALD/ → PDF tavola classificazione saldature / tavola giunti (opzionale)
TEST_DIR      = os.path.join(os.path.dirname(__file__), "..", "test_docs")
WPS_DIR       = os.path.join(TEST_DIR, "01_WPS")
WPQR_DIR      = os.path.join(TEST_DIR, "02_WPQR")
WMAP_DIR      = os.path.join(TEST_DIR, "04_WELDING_MAP")
CLASS_DIR     = os.path.join(TEST_DIR, "05_CLASS_SALD")


# ─────────────────────────────────────────────
# CARICAMENTO DOCUMENTI SEMPLICI (WPS, welding map, tavola giunti)
# Nessun chunking qui: sono documenti normalmente brevi (1-3 pagine).
# ─────────────────────────────────────────────
def carica_documenti(cartella: str, tipo: str) -> list[dict]:
    """
    Carica tutti i PDF da una cartella.
    Restituisce lista di dict: {nome, tipo, testo}
    tipo = etichetta usata nei log (es. 'WPS', 'WELDING_MAP', 'TAVOLA_GIUNTI')
    """
    documenti = []
    if not os.path.exists(cartella):
        # Cartella opzionale: non è un errore se non esiste
        return documenti

    for nome_file in os.listdir(cartella):
        if nome_file.lower().endswith(".pdf"):
            percorso = os.path.join(cartella, nome_file)
            testo = estrai_testo_pdf_semplice(percorso)
            documenti.append({
                "nome": nome_file,
                "tipo": tipo,
                "testo": testo
            })
            print(f"  ✅ Caricato {tipo}: {nome_file} ({len(testo)} caratteri)")

    return documenti


# ─────────────────────────────────────────────
# CHUNKING WPQR — estrazione dati con gestione automatica documenti lunghi
# ─────────────────────────────────────────────
PROMPT_CHUNK_WPQR = """Sei un assistente che estrae dati tecnici da un WPQR (Welding Procedure Qualification Record).
Stai leggendo SOLO una porzione del documento (chunk) — è normale che alcuni campi non compaiano in questo chunk specifico. Non inventare valori.

Estrai in JSON SOLO i campi che trovi esplicitamente in QUESTO chunk (ometti le chiavi assenti):

- numero_wpqr
- processo_saldatura (es. "135", "141")
- tipo_processo (es. "Manual", "Partly mechanized", "Fully mechanized", "Automatic")
- passate (es. "Single", "Multiple" — vedi campo "Single/Multiple pass")
- gruppo_materiale_base (nomenclatura normativa, es. ISO/TR 15608 gruppo "1-1")
- tipo_giunto_qualificato (es. "BW", "FW", "BW e FW" — leggi il campo "Joint type")

IMPORTANTE SUGLI SPESSORI — nella WPQR possono comparire fino a 3 righe di spessore DISTINTE e NON intercambiabili. Estraile separatamente, riportando il testo originale così come scritto nel documento:
- range_spessore_bw (spessore materiale base per giunti Butt Joint / BW, es. "3 to 24")
- range_spessore_fw_t1 (spessore materiale base t1 per giunti Fillet/FW, es. "6 to 24")
- range_spessore_fw_t2 (spessore materiale base t2 per giunti Fillet/FW, es. "6 to 24")
- altezza_gola_fw (campo "Throat thickness" — es. "No restriction" o un range in mm; rilevante specialmente per FW single pass)
- spessore_deposito_saldato (campo "Weld deposit thickness", es. "3 to 24" — è un dato diverso dallo spessore del materiale base, non confonderli)

Se la WPQR qualifica SOLO BW, i campi FW (range_spessore_fw_t1, range_spessore_fw_t2, altezza_gola_fw) restano assenti — e viceversa. Non inventare un valore per un tipo di giunto non qualificato dal documento.

Altri campi:
- diametro_esterno_qualificato (campo "Outside diameter", es. "Over 150" — testo originale)
- materiale_apporto (nomenclatura normativa e classificazione completa, es. "EN ISO 14341-A: G 46 4 M21 4Si1")
- tipo_gas_protezione (campo "Shielding gas", es. "M14 with max. CO2% = 3,3")
- tipo_corrente (campo "Type of welding current", es. "DCEP")
- apporto_termico (campo "Heat input", testo originale con unità, es. "6,9 to 17,8 kJ/cm")
- posizioni_qualificate (es. ["PA","PB"])
- temperatura_preriscaldo (campo "Preheat min.", testo originale)
- temperatura_interpass_max (campo "Interpass temp. Max.", testo originale)
- data_documento
- note_rilevanti (altre info tecniche utili ai check di corrispondenza WPS/WPQR)

Rispondi SOLO con il JSON, nessun testo extra, nessun backtick.

=== CHUNK DEL DOCUMENTO ===
{testo_chunk}
"""

PROMPT_AGGREGAZIONE_WPQR = """Hai ricevuto estrazioni parziali da chunk diversi dello stesso documento WPQR.
Unisci tutto in UN SOLO JSON consolidato con questi campi (usa null se un'informazione non è mai comparsa in nessun chunk):

numero_wpqr, processo_saldatura, tipo_processo, passate, gruppo_materiale_base,
tipo_giunto_qualificato, range_spessore_bw, range_spessore_fw_t1, range_spessore_fw_t2,
altezza_gola_fw, spessore_deposito_saldato, diametro_esterno_qualificato,
materiale_apporto, tipo_gas_protezione, tipo_corrente, apporto_termico,
posizioni_qualificate, temperatura_preriscaldo, temperatura_interpass_max,
data_documento, note_rilevanti

IMPORTANTE: i campi range_spessore_bw, range_spessore_fw_t1, range_spessore_fw_t2 e
altezza_gola_fw sono normativamente DISTINTI e NON vanno fusi tra loro né con
spessore_deposito_saldato. Se un chunk riporta un valore per range_spessore_bw e un
altro per range_spessore_fw_t1, mantienili come campi separati nel JSON finale — non
scegliere "il più completo" tra loro, sono dati diversi entrambi da conservare.

Se un campo compare con valori diversi in chunk diversi PER LO STESSO parametro
(es. due chunk diversi riportano range_spessore_bw diverso), usa il valore più completo
e segnala la discrepanza in note_rilevanti.

Rispondi SOLO con il JSON finale, nessun testo extra, nessun backtick.

=== ESTRAZIONI PARZIALI ===
{risultati_parziali}
"""


def estrai_dati_wpqr(percorso_pdf: str, client, model: str = MODEL) -> dict:
    """
    Estrae i dati essenziali da una WPQR con chunking automatico.
    Gestisce sia WPQR corte (analisi diretta, 1 sola chiamata) sia lunghe
    (chunk + aggregazione), native o scansionate — la logica adattiva
    è già dentro utils.analizza_pdf_chunked.
    """
    print(f"  📄 Estrazione dati WPQR: {os.path.basename(percorso_pdf)}")
    digest = analizza_pdf_chunked(
        percorso_pdf=percorso_pdf,
        client=client,
        model=model,
        prompt_per_chunk=PROMPT_CHUNK_WPQR,
        prompt_aggregazione=PROMPT_AGGREGAZIONE_WPQR,
        max_tokens_chunk=800,
        max_tokens_aggregazione=1200
    )
    digest["_nome_file"] = os.path.basename(percorso_pdf)
    return digest


def carica_wpqr_chunked(cartella: str, client, model: str = MODEL) -> list[dict]:
    """
    Carica tutte le WPQR da una cartella ed estrae i dati essenziali
    tramite chunking (gestisce WPQR di qualsiasi lunghezza).
    """
    digests = []
    if not os.path.exists(cartella):
        return digests
    for nome_file in os.listdir(cartella):
        if nome_file.lower().endswith(".pdf"):
            percorso = os.path.join(cartella, nome_file)
            digest = estrai_dati_wpqr(percorso, client, model)
            digests.append(digest)
            print(f"  ✅ Digest WPQR pronto: {nome_file}")
    return digests


# ─────────────────────────────────────────────
# DEFINIZIONE DEI TOOL (schemi JSON per Claude)
# ─────────────────────────────────────────────
NC_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "documento": {
            "type": "string",
            "description": (
                "Nome del file con la non conformità o l'osservazione. "
                "IMPORTANTE: non creare una voce in questo array per confermare "
                "che un parametro e' conforme (niente 'CHECK OK', 'coincidente', "
                "'nessuna incongruenza'). Ogni voce deve rappresentare una NC "
                "reale (STOP/ATTENZIONE) o un'osservazione con incertezza "
                "genuina che richiede verifica umana (APPUNTO). Se un parametro "
                "e' semplicemente conforme, non generare nulla per quel parametro."
            )
        },
        "descrizione": {
            "type": "string",
            "description": "Descrizione chiara del problema o dell'osservazione"
        },
        "severita": {
            "type": "string",
            "enum": ["STOP", "ATTENZIONE", "APPUNTO"],
            "description": (
                "STOP = NC bloccante, documento non accettabile. "
                "ATTENZIONE = anomalia da segnalare, richiede verifica. "
                "APPUNTO = osservazione informativa, non è una NC."
            )
        },
        "riferimento_normativo": {
            "type": "string",
            "description": (
                "Norma internazionale e paragrafo di riferimento (es. ISO 15614-1 §8.4.3). "
                "Se il riferimento è un capitolato o linea guida committente (es. QT.6495.024), "
                "indicarlo esplicitamente come 'Capitolato: QT.6495.024 §x.x' — "
                "NON come norma internazionale."
            )
        }
    },
    "required": ["documento", "descrizione", "severita", "riferimento_normativo"]
}

TOOLS = [
    {
        "name": "check_corrispondenza_1a1",
        "description": (
            "Verifica la corrispondenza tra WPS di produzione e WPQR di riferimento. "
            "NON confrontare i numeri di documento — verifica che i PARAMETRI della WPS "
            "rientrino nei range qualificati dalla WPQR collegata, secondo ISO 15614-1 "
            "(acciai) o ISO 15614-2 (alluminio) o altra norma applicabile identificata "
            "dal documento stesso. "
            "\n\nNOTA IMPORTANTE sulla norma di qualifica: ISO 15613 è una norma "
            "LEGITTIMA per giunti non standard — si rifà alla serie ISO 15614 ed è "
            "accettabile. NON segnalarla come NC né come ATTENZIONE. Se presente, "
            "registrala al massimo come APPUNTO informativo 'qualifica per giunto "
            "non standard — ISO 15613'. "
            "\n\nCheck da eseguire:"
            "\n1. PROCESSO: il processo indicato nella WPS (es. 135, 141) deve rientrare "
            "   nei processi qualificati dalla WPQR."
            "\n2. MATERIALE BASE: il gruppo materiale della WPS deve corrispondere al gruppo "
            "   qualificato nella WPQR — valuta per nomenclatura normativa (es. gruppo "
            "   ISO/TR 15608), NON per brand o denominazione commerciale."
            "\n3. SPESSORE MATERIALE BASE: verifica in due passi:"
            "\n   PASSO A — leggi il range qualificato DICHIARATO nella WPQR "
            "   (es. 'qualified range: 3 to 20 mm', 'FROM 3 TO 20'). "
            "   Se il range è esplicitamente riportato, usalo direttamente — "
            "   NON ricalcolarlo con la formula."
            "\n   PASSO B — solo se il range NON è dichiarato nella WPQR, "
            "   applicare la formula da ISO 15614-1 §8.4.3. ATTENZIONE: "
            "   la tabella degli spessori per giunti a cordone d'angolo (FW) "
            "   è DIVERSA da quella per giunti testa a testa (BW). "
            "   Identifica prima il tipo di giunto qualificato prima di applicare "
            "   la formula corretta."
            "\n4. MATERIALE D'APPORTO: classificazione del consumabile nella WPS deve "
            "   corrispondere a quella della WPQR per nomenclatura di norma "
            "   (es. EN ISO 14341-A) e per caratteristiche chimiche/meccaniche del "
            "   filo, indipendentemente dal produttore/brand."
            "\n   NON VERIFICARE IL DIAMETRO DEL FILO/CONSUMABILE (regola fissata da "
            "   IWE, non un'area di giudizio discrezionale): secondo ISO 15614-1 il "
            "   diametro del filo NON è una variabile essenziale per il processo 135 "
            "   (né per altri processi ad arco simili). Una differenza di diametro tra "
            "   WPS e WPQR (es. Ø1.0 mm vs Ø1.2 mm) NON genera NC né APPUNTO — ignora "
            "   completamente il diametro in questo check. Ciò che conta è SOLO la "
            "   classificazione normativa del filo (norma + caratteristiche chimiche/"
            "   meccaniche), non le sue dimensioni fisiche."
            "\n5. POSIZIONE DI SALDATURA: la posizione nella WPS (es. PA, PB, PF) deve "
            "   rientrare nelle posizioni qualificate dalla WPQR."
            "\n\nDopo questi 5 check, chiama OBBLIGATORIAMENTE i due sub-tool:"
            "\n- check_giunto_iso2553"
            "\n- check_preriscaldo_interpass"
            "\n\nREGOLA DI SINTETICITA' (CRITICA per non esaurire il budget di "
            "token disponibile): NON generare una voce in non_conformita per "
            "ogni parametro che risulta conforme. Se tutti i 5 parametri sono "
            "conformi, restituisci non_conformita come array VUOTO e basta. "
            "Genera una voce SOLO per un parametro che presenta una NC reale "
            "(STOP/ATTENZIONE) o un'ambiguita' che richiede verifica umana "
            "(APPUNTO) - mai per confermare che qualcosa va bene."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "non_conformita": {
                    "type": "array",
                    "description": "Lista delle NC trovate sui 5 parametri principali. Vuota se tutto ok.",
                    "items": NC_ITEM_SCHEMA
                },
                "esito": {
                    "type": "string",
                    "enum": ["GO", "ATTENZIONE", "STOP"],
                    "description": "Esito dei 5 check principali (esclusi giunto e preriscaldo)"
                }
            },
            "required": ["non_conformita", "esito"]
        }
    },
    {
        "name": "check_giunto_iso2553",
        "description": (
            "Sub-tool di check_corrispondenza_1a1. "
            "Verifica la coerenza della rappresentazione del giunto nella WPS rispetto "
            "al documento di riferimento disponibile. "
            "\n\nRiferimenti normativi:"
            "\n- ISO 2553: simboli di saldatura sui disegni tecnici"
            "\n- EN 15085-3 Annex B: preparazione dei giunti per strutture ferroviarie"
            "\n- Serie ISO 9692: preparazione dei giunti per saldatura ad arco"
            "\n\nLogica di verifica (usa il documento disponibile, in ordine di priorità):"
            "\n1. Se è disponibile la WELDING MAP: confronta il giunto della WPS con "
            "   i giunti indicati nella welding map."
            "\n2. Se non c'è welding map ma c'è la TAVOLA GIUNTI o classificazione "
            "   saldature: confronta con quella."
            "\n3. Se nessun documento di riferimento è disponibile: segnala ATTENZIONE "
            "   (non STOP) — il confronto non è possibile, ma non è NC automatica."
            "\n\nNON dare NC STOP automatica per assenza welding map. "
            "Ragiona sempre sul documento disponibile. "
            "\n\nAspetti da verificare nel confronto:"
            "\n- Tipo di giunto (BW testa a testa, FW a cordone d'angolo, ecc.)"
            "\n- Angolo di preparazione"
            "\n- Apertura di radice"
            "\n- Presenza/assenza di backing"
            "\n- Numero di passate dichiarato coerente con il giunto"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "documento_riferimento_usato": {
                    "type": "string",
                    "description": (
                        "Quale documento è stato usato come riferimento per il confronto: "
                        "'welding_map', 'tavola_giunti', 'nessuno'"
                    )
                },
                "non_conformita": {
                    "type": "array",
                    "description": "Lista delle NC o APPUNTI sul giunto. Vuota se tutto ok.",
                    "items": NC_ITEM_SCHEMA
                },
                "esito": {
                    "type": "string",
                    "enum": ["GO", "ATTENZIONE", "STOP"]
                }
            },
            "required": ["documento_riferimento_usato", "non_conformita", "esito"]
        }
    },
    {
        "name": "check_preriscaldo_interpass",
        "description": (
            "Sub-tool di check_corrispondenza_1a1. "
            "Verifica la coerenza delle temperature di preriscaldo e di interpass "
            "dichiarate nella WPS rispetto a quanto qualificato nella WPQR. "
            "\n\nRiferimenti normativi (materiale-agnostici — applica la norma corretta):"
            "\n- ISO 15614-1 §8.4.8: range di preriscaldo qualificato (acciai)"
            "\n- ISO 15614-1 §8.4.9: temperatura massima di interpass qualificata (acciai)"
            "\n- ISO 15614-2: stessi concetti per alluminio e leghe di alluminio"
            "\n- EN 1011-1: regole generali saldatura ad arco"
            "\n- EN 1011-2 Annex C: valutazione rischio criccatura da idrogeno (acciai C/basso-legati)"
            "\n- EN 1011-4: preriscaldo per alluminio"
            "\n- ISO 13916: metodo di misura della temperatura di preriscaldo e interpass"
            "\n\nLogica materiale-agnostica:"
            "\n- Identifica prima il materiale base dalla WPS/WPQR"
            "\n- Seleziona la norma applicabile in funzione del materiale"
            "\n- Applica la stessa logica di verifica con la norma corretta"
            "\n\nCheck da eseguire:"
            "\n1. PRERISCALDO — RANGE WPQR: la temperatura di preriscaldo nella WPS "
            "   deve rientrare nel range qualificato dalla WPQR (ISO 15614-1 §8.4.8 "
            "   o equivalente per il materiale). NC ATTENZIONE se fuori range."
            "\n2. PRERISCALDO — OBBLIGO DICHIARAZIONE: se il materiale e/o lo spessore "
            "   richiedono preriscaldo (es. acciai al C con CE elevato per EN 1011-2 "
            "   Annex C), verificare che la WPS lo dichiari esplicitamente. "
            "   NC ATTENZIONE se mancante."
            "\n3. CARBON EQUIVALENT (CE): leggere prima il CE dichiarato nella WPS dal "
            "   coordinatore. Se non presente, tentare di calcolarlo dal certificato "
            "   materiale disponibile. Se non calcolabile, segnalare come APPUNTO."
            "\n4. INTERPASS — TEMPERATURA MASSIMA: la temperatura di interpass nella WPS "
            "   non deve superare il massimo qualificato nella WPQR (ISO 15614-1 §8.4.9 "
            "   o equivalente)."
            "\n\n   REGOLA DETERMINISTICA SU INTERPASS N.A./ASSENTE (fissata da IWE, "
            "   NON un'area di giudizio discrezionale - applicarla sempre, senza "
            "   oscillare tra severita' diverse per lo stesso scenario):"
            "\n   - GIUNTO SINGLE PASS (una sola passata di saldatura, qualsiasi tipo "
            "     di giunto: BW, FW, parziale o piena penetrazione): l'interpass e' "
            "     per definizione non applicabile (non esiste una 'temperatura tra "
            "     una passata e l'altra' se c'e' una sola passata). WPS con interpass "
            "     dichiarato 'N.A.', vuoto, o assente in questo caso e' CORRETTO. "
            "     NON generare nessuna NC ne' APPUNTO per questo motivo - non e' una "
            "     carenza, e' l'esito atteso."
            "\n   - GIUNTO MULTIPASS (2 o piu' passate): il valore di interpass DEVE "
            "     essere dichiarato nella WPS. Se manca, dichiarato 'N.A.' o vuoto in "
            "     un giunto multipass -> NC ATTENZIONE (non APPUNTO, non STOP)."
            "\n   - Determina il numero di passate dalla WPS stessa (spesso dichiarato "
            "     esplicitamente, es. 'Multi-Layer', 'Single-Layer', numero di layer/pass "
            "     o cordoni). Se il numero di passate non e' determinabile dal testo "
            "     disponibile, segnala APPUNTO indicando che il dato mancante e' il "
            "     numero di passate stesso, non l'interpass."
            "\n5. METODO DI MISURA: se il preriscaldo è previsto, la WPS deve specificare "
            "   il metodo di misura della temperatura in accordo a ISO 13916 "
            "   (es. termometro a contatto, termocolori). NC APPUNTO se mancante."
            "\n\nIMPORTANTE: questo agente verifica SOLO la coerenza documentale. "
            "NON esprime giudizi sulla correttezza produttiva delle temperature scelte."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "materiale_identificato": {
                    "type": "string",
                    "description": "Materiale base identificato dai documenti (es. 'S355 acciaio basso-legato', 'EN AW-6082 alluminio')"
                },
                "norma_preriscaldo_applicata": {
                    "type": "string",
                    "description": "Norma usata per la valutazione del preriscaldo (es. 'EN 1011-2 Annex C', 'EN 1011-4')"
                },
                "ce_valore": {
                    "type": "string",
                    "description": "Valore CE trovato o calcolato. 'non_disponibile' se non reperibile."
                },
                "ce_fonte": {
                    "type": "string",
                    "enum": ["dichiarato_wps", "calcolato_certificato", "non_disponibile"],
                    "description": "Da dove proviene il valore CE"
                },
                "non_conformita": {
                    "type": "array",
                    "description": "Lista delle NC, ATTENZIONI e APPUNTI. Vuota se tutto ok.",
                    "items": NC_ITEM_SCHEMA
                },
                "esito": {
                    "type": "string",
                    "enum": ["GO", "ATTENZIONE", "STOP"]
                }
            },
            "required": [
                "materiale_identificato",
                "norma_preriscaldo_applicata",
                "ce_valore",
                "ce_fonte",
                "non_conformita",
                "esito"
            ]
        }
    },
    {
        "name": "check_firma_cs",
        "description": (
            "Verifica che ogni WPS e WPQR riporti un riferimento al CS "
            "(Coordinatore di Saldatura): firma, timbro, sigla, nome, o qualsiasi "
            "campo che identifichi il responsabile tecnico del documento. "
            "Riferimento normativo: ISO 15609-1. "
            "Riferimento capitolato committente: QT.6495.024 §1.1.1 "
            "(linea guida Geismar Italia — non è una norma internazionale). "
            "\n\nIMPORTANTE: la firma o approvazione CS può presentarsi in forme "
            "diverse (timbro, sigla, campo non standard, riferimento IWE/EWE). "
            "NON generare NC STOP né ATTENZIONE per questo check. "
            "Se il riferimento CS non è chiaramente identificabile, genera solo "
            "un APPUNTO — non è una NC bloccante perché l'identificazione del CS "
            "può richiedere verifica diretta del documento originale."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "non_conformita": {
                    "type": "array",
                    "description": "Lista delle NC trovate. Vuota se tutto ok.",
                    "items": NC_ITEM_SCHEMA
                },
                "esito": {
                    "type": "string",
                    "enum": ["GO", "ATTENZIONE", "STOP"]
                }
            },
            "required": ["non_conformita", "esito"]
        }
    },
    {
        "name": "check_date_cronologia",
        "description": (
            "Verifica che la data del WPS sia uguale o successiva alla data della WPQR "
            "collegata. Regola: non si può emettere una WPS di produzione prima di aver "
            "completato la qualifica di procedura (WPQR). "
            "Se data WPS < data WPQR → NC STOP."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "non_conformita": {
                    "type": "array",
                    "description": "Lista delle NC trovate. Vuota se tutto ok.",
                    "items": NC_ITEM_SCHEMA
                },
                "esito": {
                    "type": "string",
                    "enum": ["GO", "ATTENZIONE", "STOP"]
                }
            },
            "required": ["non_conformita", "esito"]
        }
    }
]


# ─────────────────────────────────────────────
# LOGICA DI ESECUZIONE DEI TOOL
# ─────────────────────────────────────────────

def _calcola_esito_da_nc(non_conformita: list) -> str:
    """
    Calcolo deterministico (Python, non lasciato al modello — stesso principio
    già usato per joincert_scaduto in Agent 4 e giorni_alla_scadenza in Agent 2)
    dell'esito di un tool dalla severita' massima presente nella sua lista
    non_conformita, invece di fidarsi del campo 'esito' scritto dal modello
    come valore indipendente.

    NOTA (2026-07-30): risolve un'incoerenza osservata in produzione — un
    tool poteva restituire esito='ATTENZIONE' anche quando tutti gli elementi
    elencati in non_conformita erano di severita' APPUNTO, perche' i due campi
    non erano vincolati tra loro nello schema. STOP > ATTENZIONE > GO. Gli
    APPUNTI non alzano mai l'esito sopra GO.
    """
    severita_presenti = {nc.get("severita", "") for nc in non_conformita}
    if "STOP" in severita_presenti:
        return "STOP"
    elif "ATTENZIONE" in severita_presenti:
        return "ATTENZIONE"
    else:
        return "GO"


def esegui_tool(nome_tool: str, input_tool: dict) -> dict:
    """
    Claude decide QUALE tool chiamare e con QUALI parametri.
    Claude ha già analizzato i documenti e prodotto la struttura NC.
    L'esito viene ricalcolato qui deterministicamente dalla severita' massima
    in non_conformita (vedi _calcola_esito_da_nc) invece di usare il campo
    'esito' scritto dal modello — elimina la fonte di incoerenza descritta lì.
    """
    if "non_conformita" in input_tool:
        input_tool["esito"] = _calcola_esito_da_nc(input_tool.get("non_conformita", []))
    return {
        "tool": nome_tool,
        "risultato": input_tool
    }


# ─────────────────────────────────────────────
# AGENTE PRINCIPALE
# ─────────────────────────────────────────────
def run_agent_wps_wpqr(
    wps_docs: list[dict],
    wpqr_digests: list[dict],
    doc_riferimento_giunti: list[dict] | None = None,
    client=None
) -> dict:
    """
    Esegue l'Agente 1 WPS/WPQR.

    Parametri:
      wps_docs        — lista documenti WPS (obbligatorio, testo pieno)
      wpqr_digests    — lista digest WPQR già estratti con chunking (obbligatorio)
      doc_riferimento_giunti — lista documenti opzionali per il check giunto:
                               welding map, tavola giunti (i disegni scansionati
                               non vengono più caricati - vedi nota in testa al file)
      client — istanza anthropic.Anthropic() già creata (per non ricrearla ogni volta)
    """
    if client is None:
        client = anthropic.Anthropic()

    # Costruisce il contesto testuale per Claude
    contesto_wps = "\n\n---\n\n".join(
        [f"[WPS: {d['nome']}]\n{d['testo']}" for d in wps_docs]
    ) if wps_docs else "Nessun WPS trovato nella cartella 01_WPS."

    contesto_wpqr = "\n\n---\n\n".join(
        [f"[WPQR: {d.get('_nome_file', '?')}]\n{json.dumps(d, ensure_ascii=False, indent=2)}"
         for d in wpqr_digests]
    ) if wpqr_digests else "Nessun WPQR trovato nella cartella 02_WPQR."

    # Documenti opzionali per il check giunto
    if doc_riferimento_giunti:
        contesto_giunti = "\n\n---\n\n".join(
            [f"[{d['tipo']}: {d['nome']}]\n{d['testo']}" for d in doc_riferimento_giunti]
        )
    else:
        contesto_giunti = "Nessun documento di riferimento giunti fornito (welding map, tavola giunti)."

    # Prompt principale all'agente
    messaggio_utente = f"""Sei un Coordinatore di Saldatura (IWE) che analizza un Welding Book.

Analizza i documenti WPS e i digest WPQR forniti ed esegui i seguenti check nell'ordine indicato:

1. check_corrispondenza_1a1
   Verifica che i parametri di ogni WPS rientrino nei range qualificati dal digest WPQR collegato.
   NON confrontare i numeri di documento — verifica i parametri tecnici:
   processo, materiale base (per gruppo normativo), spessore (range ISO 15614-1 §8.4.3
   o norma applicabile), materiale d'apporto (per nomenclatura normativa), posizione.
   Dopo questo tool, chiama OBBLIGATORIAMENTE i due sub-tool:

2. check_giunto_iso2553
   Confronta la rappresentazione del giunto nella WPS con il documento di riferimento
   disponibile tra quelli forniti. Usa il documento più specifico disponibile.
   Se nessun documento è disponibile, segnala ATTENZIONE (non STOP).

3. check_preriscaldo_interpass
   Verifica preriscaldo e interpass rispetto al digest WPQR e alla norma applicabile
   al materiale identificato. Logica materiale-agnostica.
   REGOLA FISSA su interpass N.A./assente: se il giunto è single pass (una sola
   passata), l'interpass è per definizione non applicabile - N.A. è CORRETTO,
   non generare nessuna NC. Se il giunto è multipass (2+ passate) e l'interpass
   non è dichiarato, è NC ATTENZIONE. Determina il numero di passate dal testo
   della WPS stessa.

4. check_firma_cs
   Verifica firma e data del CS su ogni documento.

5. check_date_cronologia
   Verifica che data WPS >= data WPQR collegata (campo data_documento nel digest).

Per ogni NC usa:
  STOP       = documento non accettabile, blocca il Welding Book
  ATTENZIONE = anomalia da verificare
  APPUNTO    = osservazione informativa (es. continuità semestrale non verificabile da remoto)

REGOLA DI SINTETICITA' VALIDA PER TUTTI I CHECK SOPRA (critica per non
esaurire il budget di token disponibile): non generare mai una voce di NC
o APPUNTO per confermare che un parametro e' conforme ("CHECK OK",
"coincidente", "nessuna incongruenza rilevata"). Genera una voce SOLO
quando c'e' una NC reale o un'ambiguita' che richiede verifica umana. Se
un intero check risulta interamente conforme, restituisci il suo array
non_conformita vuoto senza commenti aggiuntivi.

=== DOCUMENTI WPS (cartella 01) ===
{contesto_wps}

=== DIGEST WPQR (cartella 02, già estratti con chunking) ===
{contesto_wpqr}

=== DOCUMENTI DI RIFERIMENTO GIUNTI (welding map / tavola giunti) ===
{contesto_giunti}

Esegui tutti i check ora, nell'ordine indicato."""

    messages = [{"role": "user", "content": messaggio_utente}]

    # Raccoglie tutti i risultati dei tool
    risultati_check = []

    print("\n🤖 Agente 1 in esecuzione...")

    # Loop agentico: continua finché Claude non smette di chiamare tool
    turno = 0
    while True:
        turno += 1
        response = client.messages.create(
            model=MODEL,
            max_tokens=8192,  # margine di sicurezza - il fix primario e' la
                               # regola di sinteticita' aggiunta ai prompt sopra
            # TEMPERATURE=0 (2026-08-13): questa chiamata genera i verdetti
            # (GO/ATTENZIONE/STOP) tramite tool_use. Deve essere deterministica
            # tra run identici (locale vs Streamlit Cloud) - vedi nota changelog
            # in testa al file per il problema che risolve.
            temperature=TEMPERATURA_VERDETTO,
            # PROMPT CACHING (2026-07-29/30): cache automatica a livello di
            # richiesta. Il loop chiama l'API piu' volte per run (una per
            # turno di tool_use): questo campo sposta il breakpoint di cache
            # in avanti automaticamente ad ogni turno, cachando sia TOOLS
            # (statico, non cambia mai tra run) sia il messaggio utente
            # iniziale (statico entro la stessa run). Fix minimo di una riga,
            # nessuna modifica alla struttura del prompt sopra.
            cache_control={"type": "ephemeral"},
            tools=TOOLS,
            messages=messages
        )

        _stampa_uso_cache(response, etichetta=f"Agent1 turno {turno}")

        # Aggiunge la risposta di Claude alla cronologia
        messages.append({"role": "assistant", "content": response.content})

        # Controlla stop_reason
        if response.stop_reason == "end_turn":
            # Claude ha finito — nessun tool da eseguire
            break

        if response.stop_reason == "tool_use":
            # Claude ha chiamato uno o più tool
            tool_results = []

            for blocco in response.content:
                if blocco.type == "tool_use":
                    print(f"  🔧 Esecuzione tool: {blocco.name}")
                    risultato = esegui_tool(blocco.name, blocco.input)
                    risultati_check.append(risultato)

                    # Prepara il risultato da restituire a Claude
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": blocco.id,
                        "content": json.dumps(risultato, ensure_ascii=False)
                    })

            # Restituisce i risultati a Claude per il turno successivo
            messages.append({"role": "user", "content": tool_results})

        else:
            # Stop reason inatteso — esci comunque
            print(f"  ⚠️  Stop reason inatteso: {response.stop_reason}")
            break

    # ─────────────────────────────────────────
    # COSTRUISCE IL REPORT FINALE
    # ─────────────────────────────────────────
    tutte_le_nc = []
    esito_finale = "GO"  # parte da GO, peggiora se trova NC bloccanti

    for check in risultati_check:
        res = check["risultato"]
        nc_list = res.get("non_conformita", [])
        esito_check = res.get("esito", "GO")

        tutte_le_nc.extend(nc_list)

        # Logica semaforo: STOP > ATTENZIONE > GO
        # Gli APPUNTI non influenzano il semaforo
        if esito_check == "STOP":
            esito_finale = "STOP"
        elif esito_check == "ATTENZIONE" and esito_finale != "STOP":
            esito_finale = "ATTENZIONE"

    # Separa NC vere dagli appunti per il report
    nc_vere    = [nc for nc in tutte_le_nc if nc.get("severita") in ("STOP", "ATTENZIONE")]
    appunti    = [nc for nc in tutte_le_nc if nc.get("severita") == "APPUNTO"]

    report = {
        "agente": "Agente 1 — WPS/WPQR",
        "data_analisi": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "num_wps_analizzati": len(wps_docs),
        "num_wpqr_analizzati": len(wpqr_digests),
        "esito_finale": esito_finale,
        "num_nc_totali": len(nc_vere),
        "num_appunti": len(appunti),
        "non_conformita": nc_vere,
        "appunti": appunti,
        "dettaglio_check": risultati_check,
        # AGGIUNTO 2026-08-02: i digest WPQR grezzi (numero_wpqr, processo,
        # gruppo_materiale_base, spessori, materiale_apporto, posizioni
        # qualificate, ecc.) venivano usati SOLO per costruire il prompt di
        # analisi di questo agente e poi scartati — non arrivavano mai al
        # file JSON salvato su disco. Il Supervisor (cross-check #2, WPQR
        # vs scope EN 15085) legge questo JSON e non aveva quindi accesso
        # ai dati tecnici necessari al confronto, anche quando l'estrazione
        # OCR/testo era corretta (bug rilevato: SUP2-01/02/03, sessione IWE
        # 2026-08-02 — confermato che i dati erano correttamente leggibili
        # nei PDF originali, il problema era che non venivano propagati).
        "wpqr_digests": wpqr_digests
    }

    return report


# ─────────────────────────────────────────────
# STAMPA REPORT A CONSOLE
# ─────────────────────────────────────────────
def stampa_report(report: dict):
    """Stampa il report in modo leggibile a console."""
    semaforo = {
        "GO":         "🟢 GO",
        "ATTENZIONE": "🟡 ATTENZIONE",
        "STOP":       "🔴 STOP"
    }

    print("\n" + "="*60)
    print(f"  {report['agente']}")
    print(f"  Data analisi: {report['data_analisi']}")
    print("="*60)
    print(f"  WPS analizzati : {report['num_wps_analizzati']}")
    print(f"  WPQR analizzati: {report['num_wpqr_analizzati']}")
    print(f"  NC trovate     : {report['num_nc_totali']}")
    print(f"  Appunti        : {report['num_appunti']}")
    print(f"\n  ESITO: {semaforo.get(report['esito_finale'], report['esito_finale'])}")
    print("="*60)

    # Stampa NC (STOP e ATTENZIONE)
    if report["non_conformita"]:
        print("\n  NON CONFORMITÀ RILEVATE:")
        for i, nc in enumerate(report["non_conformita"], 1):
            icona = "🔴" if nc["severita"] == "STOP" else "🟡"
            print(f"\n  {i}. {icona} [{nc['severita']}] — {nc['documento']}")
            print(f"     {nc['descrizione']}")
            print(f"     Rif.: {nc.get('riferimento_normativo', 'n.d.')}")
    else:
        print("\n  ✅ Nessuna non conformità rilevata.")

    # Stampa Appunti (informativi, non bloccanti)
    if report["appunti"]:
        print("\n  📝 APPUNTI (osservazioni non bloccanti):")
        for i, ap in enumerate(report["appunti"], 1):
            print(f"\n  {i}. 📝 [APPUNTO] — {ap['documento']}")
            print(f"     {ap['descrizione']}")
            print(f"     Rif.: {ap.get('riferimento_normativo', 'n.d.')}")

    print("\n" + "="*60)


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────
if __name__ == "__main__":
    print("🔍 WeldAIM — Agente 1: Analisi WPS/WPQR")
    print(f"   Cartella test: {TEST_DIR}\n")

    # Un solo client Anthropic condiviso da tutto lo script
    client = anthropic.Anthropic()

    # Carica documenti obbligatori
    wps_docs      = carica_documenti(WPS_DIR, "WPS")
    wpqr_digests  = carica_wpqr_chunked(WPQR_DIR, client, MODEL)

    # Carica documenti opzionali per il check giunto
    # (welding map, tavola giunti — se esistono).
    # Dal 2026-07-25: NON carichiamo più i disegni scansionati (14_DISEGNI) -
    # vedi nota in testa al file.
    doc_riferimento_giunti = []
    doc_riferimento_giunti += carica_documenti(WMAP_DIR, "WELDING_MAP")
    doc_riferimento_giunti += carica_documenti(CLASS_DIR, "TAVOLA_GIUNTI")

    if not wps_docs and not wpqr_digests:
        print("❌ Nessun documento trovato.")
        print("   Crea le cartelle:")
        print("     test_docs/01_WPS/   → PDF dei WPS")
        print("     test_docs/02_WPQR/  → PDF dei WPQR")
        print("   Opzionale per check giunto:")
        print("     test_docs/04_WELDING_MAP/")
        print("     test_docs/05_CLASS_SALD/")
    else:
        # Esegui l'agente
        report = run_agent_wps_wpqr(wps_docs, wpqr_digests, doc_riferimento_giunti, client=client)

        # Stampa a console
        stampa_report(report)

        # Salva il report JSON
        output_json = str(BASE_DIR / "report_agents" / "report_agent1.json")
        with open(output_json, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        print(f"\n  📄 Report JSON salvato: {output_json}")