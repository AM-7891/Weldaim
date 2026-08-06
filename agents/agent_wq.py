# agent_wq.py
# WeldAIM — Agent 2: Qualifiche Saldatori (cartella 03) — con chunking WQ via utils.py
# Verifica: scadenze, norma applicabile, copertura giunti, RT per Al+BW
# Norme: ISO 9606-1, ISO 9606-2, ISO 14732 | Linea guida: QT.6495.024 §3
#
# NOTA (2026-07-25): rimosso il caricamento di welding map/disegni (cartelle
# 04_WELDING_MAP e 14_DISEGNI) che Agent 2 faceva in precedenza per il check
# "campo_validita". Motivo: (1) duplicava l'OCR gia' fatto da Agent 1 sugli
# stessi disegni (costo/token ridondante); (2) il confronto range WQ vs
# progetto e' un cross-check che spetta al Supervisor (check #4, Agent 1<->
# Agent2<->Agent3), non al singolo agente, per principio architetturale.
# Il tool check_campo_validita e' gia' progettato per "best effort" senza
# documento di riferimento (vedi sua descrizione) - nessuna modifica alla
# logica di check e' stata necessaria.
#
# NOTA (2026-07-29/30 — PROMPT CACHING):
# Aggiunto un solo campo cache_control a livello di richiesta (cache
# automatica) alla chiamata client.messages.create() dentro il loop
# agentico while. Motivo: MAX_ITER=30 - con piu' saldatori nello stesso
# welding book il loop puo' girare molte volte nella stessa run, ognuna
# rimandando l'intera cronologia crescente. La cache automatica sposta
# il breakpoint in avanti automaticamente ad ogni turno, cachando sia
# TOOLS (7 tool con schema dettagliati, statico, non cambia mai tra run)
# sia SYSTEM_PROMPT (statico, regole di dominio complete) sia il primo
# messaggio utente (statico entro la stessa run). Fix minimo di una riga,
# nessuna modifica alla struttura del prompt o alla logica di dominio.
# Diagnostica aggiunta con _stampa_uso_cache() per verificare hit reali.
#
# NOTA (2026-08-02): rimosso il blocco di debug temporaneo introdotto il
# 2026-07-26 per diagnosticare TCHUNK-01 (stampa a console del digest
# completo di ogni WQ). La diagnosi e' conclusa, il blocco non serve piu' e
# appesantiva l'output console durante i test end-to-end della UI.

import os
import json
import anthropic
from datetime import datetime, date
from dotenv import load_dotenv

from utils import estrai_testo_pdf_semplice, analizza_pdf_chunked, _stampa_uso_cache

load_dotenv()

# ─────────────────────────────────────────────
# CONFIGURAZIONE
# ─────────────────────────────────────────────
MODEL = "claude-sonnet-4-6"

# Struttura cartelle attesa (coerente con Agente 1):
#   test_docs/
#     03_WQ/            → PDF delle qualifiche saldatori (patentini)
TEST_DIR        = os.path.join(os.path.dirname(__file__), "..", "test_docs")
WQ_DIR          = os.path.join(TEST_DIR, "03_WQ")


# =============================================================================
# CHUNKING WQ — estrazione digest per singolo documento WQ (patentino saldatore)
# I patentini sono spesso scansionati e includono tabelle di rinnovo semestrale
# che crescono negli anni: possono diventare documenti lunghi. Stessa logica
# adattiva usata da Agente 1 per le WPQR (analizza_pdf_chunked gestisce da solo
# nativo/OCR, corto/lungo).
# =============================================================================

PROMPT_CHUNK_WQ = """Sei un assistente che estrae dati tecnici da una Qualifica Saldatore (WQ / patentino).
Stai leggendo SOLO una porzione del documento (chunk) — è normale che alcuni campi non compaiano in questo chunk specifico. Non inventare valori.

Estrai in JSON SOLO i campi che trovi esplicitamente in QUESTO chunk (ometti le chiavi assenti):

- nome_saldatore
- norma_dichiarata (es. "ISO 9606-1", "ISO 9606-2", "ISO 14732")
- tipo_saldatore (uno tra: "manuale", "meccanizzato", "automatico", "non_specificato")
- materiale_base (es. "acciaio al carbonio", "alluminio", "nichel")
- processo_saldatura (es. "135-MAG", "141-TIG")
- tipo_giunto (se indicato: "BW" o "FW")
- spessore_range_qualificato (testo originale, es. "3-20 mm")
- posizioni_qualificate (es. ["PA","PB"])
- data_rilascio (testo originale)
- data_scadenza (testo originale)
- firma_cs_presente (true/false se rilevabile)
- modalita_identificazione_cs (es. "firma", "timbro", "sigla", "non trovato")
- date_rinnovi_semestrali (lista di date/stringhe trovate per la continuità semestrale)
- note_rilevanti (altre info utili ai check: RT allegato, riferimenti a giunti/disegni, ecc.)

Rispondi SOLO con il JSON, nessun testo extra, nessun backtick.

=== CHUNK DEL DOCUMENTO ===
{testo_chunk}
"""

PROMPT_AGGREGAZIONE_WQ = """Hai ricevuto estrazioni parziali da chunk diversi dello stesso documento WQ (patentino saldatore).
Unisci tutto in UN SOLO JSON consolidato con questi campi (usa null se un'informazione non è mai comparsa in nessun chunk):

nome_saldatore, norma_dichiarata, tipo_saldatore, materiale_base, processo_saldatura,
tipo_giunto, spessore_range_qualificato, posizioni_qualificate, data_rilascio, data_scadenza,
firma_cs_presente, modalita_identificazione_cs, date_rinnovi_semestrali, note_rilevanti

Se un campo compare con valori diversi in chunk diversi, usa il valore più completo e segnala la discrepanza in note_rilevanti.

Rispondi SOLO con il JSON finale, nessun testo extra, nessun backtick.

=== ESTRAZIONI PARZIALI ===
{risultati_parziali}
"""


def estrai_dati_wq(percorso_pdf: str, client, model: str = MODEL) -> dict:
    """
    Estrae i dati essenziali da una WQ con chunking automatico.
    Gestisce WQ corte (analisi diretta) o lunghe (chunk + aggregazione),
    native o scansionate — logica adattiva già dentro utils.analizza_pdf_chunked.
    """
    print(f"  📄 Estrazione dati WQ: {os.path.basename(percorso_pdf)}")
    digest = analizza_pdf_chunked(
        percorso_pdf=percorso_pdf,
        client=client,
        model=model,
        prompt_per_chunk=PROMPT_CHUNK_WQ,
        prompt_aggregazione=PROMPT_AGGREGAZIONE_WQ,
        max_tokens_chunk=800,
        max_tokens_aggregazione=1200
    )
    digest["_nome_file"] = os.path.basename(percorso_pdf)

    return digest


def carica_wq_chunked(cartella: str, client, model: str = MODEL) -> list[dict]:
    """
    Carica tutte le WQ da una cartella ed estrae i dati essenziali tramite chunking.
    """
    digests = []
    if not os.path.exists(cartella):
        return digests
    for nome_file in os.listdir(cartella):
        if nome_file.lower().endswith(".pdf"):
            percorso = os.path.join(cartella, nome_file)
            digest = estrai_dati_wq(percorso, client, model)
            digests.append(digest)
            print(f"  ✅ Digest WQ pronto: {nome_file}")
    return digests


# =============================================================================
# DEFINIZIONE TOOL — Agent 2 (invariati rispetto alla versione precedente)
# =============================================================================

TOOLS = [

    {
        "name": "check_norma_applicabile",
        "description": (
            "Verifica che la norma dichiarata nel documento WQ sia coerente con il tipo di saldatore "
            "e il materiale. "
            "Regole: saldatore manuale + acciaio/Ni → ISO 9606-1. "
            "Saldatore manuale + alluminio → ISO 9606-2. "
            "Operatore meccanizzato o automatico (qualsiasi materiale) → ISO 14732. "
            "Se la norma dichiarata non corrisponde → NC STOP. "
            "Norma assente nel documento → NC STOP."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "nome_documento": {"type": "string", "description": "Nome o ID del documento WQ analizzato"},
                "norma_dichiarata": {"type": "string", "description": "Norma riportata nel documento (es. ISO 9606-1)"},
                "tipo_saldatore": {"type": "string", "enum": ["manuale", "meccanizzato", "automatico", "non_specificato"],
                                   "description": "Tipo di saldatore desunto dal documento"},
                "materiale_base": {"type": "string", "description": "Materiale base (es. acciaio al carbonio, alluminio, nichel)"},
                "norma_corretta_attesa": {"type": "string", "description": "Norma che dovrebbe essere applicata secondo le regole"},
                "esito": {"type": "string", "enum": ["OK", "NC_STOP", "APPUNTO"],
                          "description": "Esito del check"},
                "descrizione": {"type": "string", "description": "Spiegazione dettagliata dell'esito"}
            },
            "required": ["nome_documento", "norma_dichiarata", "tipo_saldatore",
                         "materiale_base", "norma_corretta_attesa", "esito", "descrizione"]
        }
    },

    {
        "name": "check_scadenza_qualifica",
        "description": (
            "Estrae la data di scadenza della qualifica saldatore e la confronta con la data odierna. "
            "Se scaduta → NC STOP. "
            "Se scade entro 30 giorni → ATTENZIONE. "
            "Se valida oltre 30 giorni → OK. "
            "Se la data di scadenza non è leggibile o assente → NC STOP."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "nome_documento": {"type": "string"},
                "nome_saldatore": {"type": "string", "description": "Nome del saldatore come riportato nel documento"},
                "data_scadenza_raw": {"type": "string", "description": "Data scadenza come letta nel documento (es. '2025-12-31' o '31/12/2025')"},
                "data_scadenza_iso": {"type": "string", "description": "Data scadenza normalizzata in formato YYYY-MM-DD"},
                "giorni_alla_scadenza": {"type": "integer", "description": "Giorni rimanenti alla scadenza (negativo se già scaduta)"},
                "esito": {"type": "string", "enum": ["OK", "ATTENZIONE", "NC_STOP"]},
                "descrizione": {"type": "string"}
            },
            "required": ["nome_documento", "nome_saldatore", "data_scadenza_raw",
                         "data_scadenza_iso", "giorni_alla_scadenza", "esito", "descrizione"]
        }
    },

    {
        "name": "check_continuita_semestrale",
        "description": (
            "Verifica la presenza della documentazione di continuità semestrale (firma e data di rinnovo) "
            "nel documento WQ. "
            "ATTENZIONE: l'assenza della firma semestrale nel documento digitale NON è NC automatica — "
            "genera solo APPUNTO informativo, perché il rinnovo è spesso firmato in originale fisico "
            "dal fornitore durante le ispezioni reali. "
            "Riporta quello che è presente o assente senza alzare la gravità."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "nome_documento": {"type": "string"},
                "nome_saldatore": {"type": "string"},
                "continuita_presente": {"type": "boolean",
                                        "description": "True se nel documento c'è almeno una firma/data di rinnovo semestrale"},
                "date_rinnovi_trovate": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Lista delle date di rinnovo semestrale trovate (formato stringa come da documento)"
                },
                "esito": {"type": "string", "enum": ["OK", "APPUNTO"],
                          "description": "OK se presente, APPUNTO se assente (mai NC per questo check)"},
                "descrizione": {"type": "string"}
            },
            "required": ["nome_documento", "nome_saldatore", "continuita_presente",
                         "date_rinnovi_trovate", "esito", "descrizione"]
        }
    },

    {
        "name": "check_firma_cs",
        "description": (
            "Verifica che il documento WQ riporti firma, data e identificazione del Coordinatore di Saldatura (CS). "
            "Il CS può essere identificato con timbro, sigla o campo dedicato — non solo firma autografa. "
            "L'assenza genera APPUNTO (non STOP), coerente con correzione #010."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "nome_documento": {"type": "string"},
                "firma_cs_presente": {"type": "boolean"},
                "data_cs_presente": {"type": "boolean"},
                "modalita_identificazione": {"type": "string",
                                              "description": "Come è identificato il CS (firma, timbro, sigla, campo dedicato, non trovato)"},
                "esito": {"type": "string", "enum": ["OK", "APPUNTO"]},
                "descrizione": {"type": "string"}
            },
            "required": ["nome_documento", "firma_cs_presente", "data_cs_presente",
                         "modalita_identificazione", "esito", "descrizione"]
        }
    },

    {
        "name": "check_campo_validita",
        "description": (
            "Verifica il campo di validità della qualifica (processo, materiale, spessore, posizione) "
            "e confronta con i giunti del manufatto se il documento di riferimento è disponibile "
            "(welding map, disegno). "
            "Se il documento di riferimento non è fornito → check best effort: "
            "riporta solo i parametri estratti dalla WQ con APPUNTO, senza dare NC per assenza documento. "
            "Se il documento è disponibile e c'è discrepanza → NC_STOP o ATTENZIONE secondo gravità."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "nome_documento": {"type": "string"},
                "nome_saldatore": {"type": "string"},
                "processo_qualificato": {"type": "string", "description": "Processo saldatura qualificato (es. 135-MAG, 141-TIG)"},
                "materiale_qualificato": {"type": "string", "description": "Gruppo materiale qualificato (es. acciaio al C, lega alluminio)"},
                "spessore_range_qualificato": {"type": "string", "description": "Range spessori qualificati (es. '3-20 mm')"},
                "posizioni_qualificate": {"type": "array", "items": {"type": "string"},
                                          "description": "Posizioni di saldatura qualificate (es. PA, PB, PC...)"},
                "documento_riferimento_disponibile": {"type": "boolean",
                                                       "description": "True se welding map o disegno è disponibile per il confronto"},
                "discrepanze_trovate": {"type": "array", "items": {"type": "string"},
                                        "description": "Lista delle discrepanze rispetto al documento di riferimento, se disponibile"},
                "esito": {"type": "string", "enum": ["OK", "ATTENZIONE", "NC_STOP", "APPUNTO"]},
                "descrizione": {"type": "string"}
            },
            "required": ["nome_documento", "nome_saldatore", "processo_qualificato",
                         "materiale_qualificato", "spessore_range_qualificato",
                         "posizioni_qualificate", "documento_riferimento_disponibile",
                         "discrepanze_trovate", "esito", "descrizione"]
        }
    },

    {
        "name": "check_rt_alluminio_bw",
        "description": (
            "Verifica la presenza di un report RT quando il materiale è alluminio e il giunto è BW "
            "(butt weld / testa a testa), come richiesto da DVS 1619-1 §5.5.1. "
            "Il report RT NON deve essere necessariamente dentro la WQ: può essere un documento separato "
            "nel set documentale. L'agente verifica se esiste un documento RT che riporti "
            "il giunto e il saldatore a cui si riferisce. "
            "Se il materiale non è alluminio o il giunto non è BW → tool non applicabile (skip). "
            "Se applicabile e RT non trovato → ATTENZIONE (non STOP, perché potrebbe essere in altra cartella)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "nome_documento": {"type": "string"},
                "nome_saldatore": {"type": "string"},
                "check_applicabile": {"type": "boolean",
                                      "description": "True solo se materiale=Al e giunto=BW"},
                "rt_trovato": {"type": "boolean",
                               "description": "True se un report RT è stato trovato nel set documentale"},
                "rt_riferisce_giunto": {"type": "boolean",
                                        "description": "True se il report RT identifica il giunto specifico"},
                "rt_riferisce_saldatore": {"type": "boolean",
                                           "description": "True se il report RT identifica il saldatore specifico"},
                "esito": {"type": "string", "enum": ["OK", "ATTENZIONE", "NON_APPLICABILE"]},
                "descrizione": {"type": "string"}
            },
            "required": ["nome_documento", "nome_saldatore", "check_applicabile",
                         "rt_trovato", "esito", "descrizione"]
        }
    },

    {
        "name": "genera_report_agent2",
        "description": (
            "Genera il report finale dell'Agent 2 con tutti gli esiti raccolti dai tool precedenti. "
            "Aggrega le NC per saldatore e per documento. "
            "Determina il semaforo globale: "
            "STOP se almeno una NC_STOP; ATTENZIONE se almeno un ATTENZIONE senza STOP; GO se tutto OK/APPUNTO."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "saldatori_analizzati": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "nome_saldatore": {"type": "string"},
                            "documento_wq": {"type": "string"},
                            "norma": {"type": "string"},
                            "scadenza": {"type": "string"},
                            "esito_scadenza": {"type": "string"},
                            "esito_norma": {"type": "string"},
                            "esito_continuita": {"type": "string"},
                            "esito_firma_cs": {"type": "string"},
                            "esito_campo_validita": {"type": "string"},
                            "esito_rt_al_bw": {"type": "string"},
                            "nc_list": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Lista NC trovate per questo saldatore"
                            }
                        }
                    }
                },
                "semaforo_globale": {"type": "string", "enum": ["GO", "ATTENZIONE", "STOP"]},
                "nc_totali": {"type": "integer"},
                "appunti_totali": {"type": "integer"},
                "riepilogo_ita": {"type": "string", "description": "Riepilogo in italiano"},
                "riepilogo_eng": {"type": "string", "description": "Summary in English"}
            },
            "required": ["saldatori_analizzati", "semaforo_globale",
                         "nc_totali", "appunti_totali", "riepilogo_ita", "riepilogo_eng"]
        }
    }
]

# =============================================================================
# SYSTEM PROMPT — Agent 2
# =============================================================================

DATA_OGGI = date.today().strftime("%Y-%m-%d")

SYSTEM_PROMPT = f"""Sei Agent 2 di WeldAIM, specializzato nella verifica delle Qualifiche Saldatori (WQ).
Analizzi i DIGEST già estratti (via chunking) dai documenti della cartella 03 del Welding Book,
secondo la linea guida QT.6495.024 §3 e le norme ISO 9606-1, ISO 9606-2, ISO 14732.

I digest sono JSON compatti: ogni oggetto rappresenta una WQ già letta e riassunta nei campi
essenziali (nome_saldatore, norma_dichiarata, tipo_saldatore, materiale_base, processo_saldatura,
tipo_giunto, spessore_range_qualificato, posizioni_qualificate, data_rilascio, data_scadenza,
firma_cs_presente, modalita_identificazione_cs, date_rinnovi_semestrali, note_rilevanti).
Lavora SOLO sui dati presenti nel digest — non inventare valori assenti.

La data di oggi è: {DATA_OGGI}

## REGOLE DI DOMINIO OBBLIGATORIE

### Norma applicabile (CHECK CRITICO)
- Saldatore manuale + acciaio o Ni → deve dichiarare ISO 9606-1
- Saldatore manuale + alluminio → deve dichiarare ISO 9606-2
- Operatore meccanizzato o automatico (qualsiasi materiale) → deve dichiarare ISO 14732
- Norma dichiarata errata rispetto a materiale/processo → NC STOP
- Norma assente nel documento → NC STOP

### Scadenza qualifica
- Qualifica scaduta (data < oggi) → NC STOP
- Qualifica scade entro 30 giorni → ATTENZIONE
- Data scadenza assente/illeggibile → NC STOP

### Continuità semestrale
- Assenza firma semestrale nel digest → APPUNTO (non NC)
- Motivazione: il rinnovo fisico spesso esiste solo in originale presso il fornitore
- NON alzare mai la gravità oltre APPUNTO per questo check

### Firma CS
- CS può essere identificato con firma, timbro, sigla, campo dedicato
- Assenza → APPUNTO (non STOP)

### Campo di validità (best effort)
- Estrai sempre dal digest: processo, materiale, spessore, posizioni qualificate
- Se documento di riferimento (welding map/disegno) NON è disponibile → APPUNTO informativo
- Se documento disponibile e c'è discrepanza → NC_STOP o ATTENZIONE

### RT per alluminio + BW
- Applicabile SOLO se materiale=Al e giunto=BW (DVS 1619-1 §5.5.1)
- Il report RT può essere documento separato — non deve stare dentro la WQ
- Verifica che identifichi giunto e saldatore
- Se non trovato → ATTENZIONE (non STOP)
- Se materiale non è Al o giunto non è BW → tool NON applicabile

## LIVELLI DI ESITO
- NC_STOP → blocca il Welding Book
- ATTENZIONE → richiede verifica prima dell'approvazione
- APPUNTO → osservazione informativa, non bloccante
- OK → conforme

## ISTRUZIONI OPERATIVE
1. Per ogni digest WQ ricevuto, esegui TUTTI i check applicabili usando i tool
2. Usa i tool in ordine: norma → scadenza → continuità → firma CS → campo validità → RT (se Al+BW)
3. Alla fine chiama UNA SOLA VOLTA genera_report_agent2 con l'aggregazione di TUTTI i saldatori analizzati
4. Sii preciso sui nomi dei saldatori — sono la chiave di tracciabilità
5. Se un digest riporta discrepanze tra chunk diverse in note_rilevanti, segnalale come APPUNTO
"""

# =============================================================================
# ESECUZIONE TOOL (dispatcher)
# =============================================================================

def esegui_tool(nome_tool: str, input_tool: dict) -> str:
    """
    Dispatcher: riceve il nome del tool e l'input da Claude,
    esegue la logica Python e restituisce il risultato come stringa JSON.
    check_scadenza_qualifica richiede calcolo date Python-side (più affidabile del modello).
    """

    if nome_tool == "check_scadenza_qualifica":
        try:
            data_iso = input_tool.get("data_scadenza_iso", "")
            data_scadenza = datetime.strptime(data_iso, "%Y-%m-%d").date()
            oggi = date.today()
            giorni = (data_scadenza - oggi).days

            input_tool["giorni_alla_scadenza"] = giorni

            if giorni < 0:
                input_tool["esito"] = "NC_STOP"
                input_tool["descrizione"] = (
                    f"QUALIFICA SCADUTA da {abs(giorni)} giorni "
                    f"(scadenza: {data_iso}, oggi: {oggi})"
                )
            elif giorni <= 30:
                input_tool["esito"] = "ATTENZIONE"
                input_tool["descrizione"] = (
                    f"Qualifica in scadenza tra {giorni} giorni "
                    f"(scadenza: {data_iso})"
                )
            else:
                input_tool["esito"] = "OK"
                input_tool["descrizione"] = (
                    f"Qualifica valida — scadenza tra {giorni} giorni ({data_iso})"
                )

        except (ValueError, TypeError):
            input_tool["esito"] = "NC_STOP"
            input_tool["descrizione"] = (
                f"Data scadenza non parsabile o assente: '{input_tool.get('data_scadenza_iso', '')}'"
            )

        return json.dumps(input_tool, ensure_ascii=False)

    return json.dumps({
        "tool": nome_tool,
        "status": "eseguito",
        "risultato": input_tool
    }, ensure_ascii=False)


# =============================================================================
# AGENTIC LOOP — Agent 2
# =============================================================================

def analizza_qualifiche(wq_digests: list[dict],
                         doc_riferimento: list[dict] | None = None,
                         client=None) -> dict:
    """
    Punto di ingresso principale di Agent 2 (batch — coerente con Agente 1).

    Args:
        wq_digests: lista di digest WQ già estratti con chunking (utils.analizza_pdf_chunked)
        doc_riferimento: lista opzionale di documenti semplici (welding map, disegno)
                         per il check campo validità — testo pieno, no chunking.
                         Dal 2026-07-25 questo parametro non viene più popolato
                         dal __main__ (vedi nota in testa al file) - il check
                         campo_validita lavora quindi sempre in modalità best
                         effort, che è già gestita correttamente qui sotto.
        client: istanza anthropic.Anthropic() già creata

    Returns:
        dict con il report finale consolidato dell'agente (tutti i saldatori in un unico report)
    """
    if client is None:
        client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    print("\n" + "="*60)
    print("AGENT 2 — Qualifiche Saldatori")
    print("="*60)

    contesto_wq = "\n\n---\n\n".join(
        [f"[WQ: {d.get('_nome_file', '?')}]\n{json.dumps(d, ensure_ascii=False, indent=2)}"
         for d in wq_digests]
    ) if wq_digests else "Nessuna WQ trovata nella cartella 03_WQ."

    if doc_riferimento:
        contesto_riferimento = "\n\n---\n\n".join(
            [f"[{d['tipo']}: {d['nome']}]\n{d['testo']}" for d in doc_riferimento]
        )
    else:
        contesto_riferimento = "Nessun documento di riferimento fornito. Esegui check campo validità in modalità best effort."

    messaggio_utente = f"""Analizza le seguenti qualifiche saldatori (digest già estratti) per il Welding Book.
Digest WQ ricevuti: {len(wq_digests)}

=== DIGEST WQ (cartella 03, già estratti con chunking) ===
{contesto_wq}

=== DOCUMENTI DI RIFERIMENTO PER CHECK CAMPO VALIDITÀ (welding map / disegno) ===
{contesto_riferimento}

Esegui tutti i check previsti per ogni saldatore presente nei digest.
Usa i tool nell'ordine indicato e concludi con UNA SOLA chiamata a genera_report_agent2
che aggreghi TUTTI i saldatori analizzati."""

    messaggi = [{"role": "user", "content": messaggio_utente}]
    report_finale = {}
    iterazione = 0
    MAX_ITER = 30  # più saldatori in batch = più chiamate tool, alziamo il margine

    while iterazione < MAX_ITER:
        iterazione += 1
        print(f"\n[LOOP] Iterazione {iterazione}")

        risposta = client.messages.create(
            model=MODEL,
            max_tokens=8096,
            system=SYSTEM_PROMPT,
            # PROMPT CACHING (2026-07-29/30): cache automatica a livello di
            # richiesta. MAX_ITER=30 - con piu' saldatori il loop puo' girare
            # molte volte nella stessa run, ognuna rimandando l'intera
            # cronologia crescente. Questo campo sposta il breakpoint di
            # cache in avanti automaticamente ad ogni turno, cachando sia
            # TOOLS (7 tool, statico) sia SYSTEM_PROMPT (statico) sia il
            # messaggio utente iniziale (statico entro la stessa run).
            # Fix minimo di una riga, nessuna modifica alla struttura del
            # prompt sopra.
            cache_control={"type": "ephemeral"},
            tools=TOOLS,
            messages=messaggi
        )

        _stampa_uso_cache(risposta, etichetta=f"Agent2 iter {iterazione}")

        print(f"[LOOP] Stop reason: {risposta.stop_reason}")

        messaggi.append({"role": "assistant", "content": risposta.content})

        if risposta.stop_reason == "end_turn":
            print("[LOOP] Completato — end_turn")
            break

        if risposta.stop_reason == "tool_use":
            risultati_tool = []

            for blocco in risposta.content:
                if blocco.type != "tool_use":
                    continue

                print(f"[TOOL] Chiamata: {blocco.name}")

                risultato = esegui_tool(blocco.name, blocco.input)

                if blocco.name == "genera_report_agent2":
                    try:
                        report_finale = json.loads(risultato).get("risultato", blocco.input)
                    except Exception:
                        report_finale = blocco.input

                risultati_tool.append({
                    "type": "tool_result",
                    "tool_use_id": blocco.id,
                    "content": risultato
                })

            messaggi.append({"role": "user", "content": risultati_tool})
        else:
            print(f"  ⚠️  Stop reason inatteso: {risposta.stop_reason}")
            break

    return report_finale


# =============================================================================
# STAMPA REPORT
# =============================================================================

def stampa_report(report: dict):
    """Stampa il report Agent 2 in modo leggibile nel terminale."""
    print("\n" + "="*60)
    print("REPORT AGENT 2 — QUALIFICHE SALDATORI")
    print("="*60)

    semaforo = report.get("semaforo_globale", "N/D")
    emoji = {"GO": "🟢", "ATTENZIONE": "🟡", "STOP": "🔴"}.get(semaforo, "⚪")

    print(f"\nSEMAFORO: {emoji} {semaforo}")
    print(f"NC totali: {report.get('nc_totali', 0)}")
    print(f"Appunti: {report.get('appunti_totali', 0)}")

    print("\n--- SALDATORI ANALIZZATI ---")
    for saldatore in report.get("saldatori_analizzati", []):
        print(f"\n👤 {saldatore.get('nome_saldatore', 'N/D')} | Doc: {saldatore.get('documento_wq', '')}")
        print(f"   Norma: {saldatore.get('norma', 'N/D')} | Scadenza: {saldatore.get('scadenza', 'N/D')}")
        print(f"   Esiti → Norma:{saldatore.get('esito_norma','?')} | "
              f"Scadenza:{saldatore.get('esito_scadenza','?')} | "
              f"Continuità:{saldatore.get('esito_continuita','?')} | "
              f"CS:{saldatore.get('esito_firma_cs','?')} | "
              f"ValiditàQ:{saldatore.get('esito_campo_validita','?')} | "
              f"RT Al+BW:{saldatore.get('esito_rt_al_bw','?')}")
        for nc in saldatore.get("nc_list", []):
            print(f"   ⚠️  {nc}")

    print("\n--- RIEPILOGO ITALIANO ---")
    print(report.get("riepilogo_ita", ""))
    print("\n--- SUMMARY ENGLISH ---")
    print(report.get("riepilogo_eng", ""))


# =============================================================================
# MAIN — test locale
# =============================================================================

if __name__ == "__main__":
    print("🔍 WeldAIM — Agente 2: Analisi Qualifiche Saldatori")
    print(f"   Cartella test: {TEST_DIR}\n")

    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    # Carica tutti i digest WQ (chunking automatico gestito da utils.py)
    wq_digests = carica_wq_chunked(WQ_DIR, client, MODEL)

    # Dal 2026-07-25: NON carichiamo più welding map/disegni qui.
    # Il check "campo_validita" lavora in modalità best effort (già gestita
    # correttamente in analizza_qualifiche) e il confronto vero e proprio
    # WQ<->WPS/progetto spetta al Supervisor (check #4).
    doc_riferimento = []

    if not wq_digests:
        print("❌ Nessun documento WQ trovato.")
        print("   Crea la cartella:")
        print("     test_docs/03_WQ/   → PDF delle qualifiche saldatori")
    else:
        report = analizza_qualifiche(wq_digests, doc_riferimento, client=client)
        stampa_report(report)

        output_json = r"C:\Users\angma\Desktop\weldaim\report_agents\report_agent2.json"
        with open(output_json, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"\n📄 Report JSON salvato in: {output_json}")