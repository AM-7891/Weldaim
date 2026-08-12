# =============================================================================
# app.py — WeldAIM Streamlit UI (Consolidamento UI: bottone unico + semaforo)
#
# Sostituisce la versione a 6 bottoni separati con un unico flusso:
# "Analizza Welding Book completo" esegue in sequenza Agente 1→5 + Supervisore
# e mostra un riepilogo semaforo GO/ATTENZIONE/STOP, con il dettaglio grezzo
# disponibile ma non srotolato di default.
#
# Cambiamenti rispetto alla versione a bottoni separati:
# - check_visual_test() di Agent 3 ora ritorna una TUPLA
#   (risultati_vt, documenti_supporto) invece di una lista sola — aggiornato
#   qui di conseguenza (vedi fix classificazione VT vs documento di supporto,
#   sessione 2026-08-02).
# - Agente 3 riusa i WPS/WPQR gia' caricati ed elaborati dall'Agente 1 nello
#   stesso run, invece di ricaricarli/rielaborarli da capo — evita di
#   rieseguire il chunking delle WPQR una seconda volta nella stessa run
#   (risparmio token/costi/tempo).
# - Ogni agente e' avvolto in un try/except indipendente: se un agente fallisce,
#   gli altri proseguono comunque e l'errore viene mostrato in un riquadro di
#   avviso, invece di far crashare l'intera analisi.
# - I risultati restano visibili tra un rerun e l'altro di Streamlit tramite
#   st.session_state, cosi' non spariscono se l'utente interagisce con un
#   expander dopo l'esecuzione.
# =============================================================================

import streamlit as st
import json
import os
import re
import anthropic

import agent_wps_wpqr as agente1
import agent_wq as agente2
import agent_mockup_vt as agente3
import agent_materiali as agente4
import agent_pfc_en15085 as agente5
import supervisor_agent as supervisore
from utils import BASE_DIR

REPORT_DIR = str(BASE_DIR / "report_agents")
TEST_DIR = str(BASE_DIR / "test_docs")
CARTELLA_MOCKUP = os.path.join(TEST_DIR, "07_MOCKUP")
CARTELLA_VT = os.path.join(TEST_DIR, "09_VT")
DATA_PRODUZIONE = None  # best-effort, come da __main__ originale di Agente 3

st.set_page_config(page_title="WeldAIM", layout="wide")
st.title("🔍 WeldAIM — Analisi Welding Book")
st.caption("Esegue in sequenza i 5 agenti specializzati e il Supervisore, poi mostra il verdetto complessivo.")


# -----------------------------------------------------------------------
# UTILITY — estrazione esito e visualizzazione semaforo
# -----------------------------------------------------------------------

SEVERITA_RANK = {"STOP": 3, "ATTENZIONE": 2, "APPUNTO": 1, "GO": 0, "N/D": -1}
SEMAFORO_ICONE = {"STOP": "🔴", "ATTENZIONE": "🟡", "APPUNTO": "🟠", "GO": "🟢", "N/D": "⚪"}
SEMAFORO_COLORI = {
    "STOP": "#ffe0e0", "ATTENZIONE": "#fff6d8", "APPUNTO": "#fff0e0",
    "GO": "#e2f7e6", "N/D": "#eeeeee",
}


def estrai_esito(report: dict, chiavi_candidate: list) -> str:
    """
    Cerca il primo campo tra quelli candidati che contiene l'esito
    complessivo di un report agente (es. 'esito_finale', 'verdetto').
    Ritorna 'N/D' se il report e' assente o nessuna chiave candidata e'
    presente/valida — non fa crashare la UI se lo schema di un agente
    e' diverso da quanto previsto.
    """
    if not isinstance(report, dict):
        return "N/D"
    for chiave in chiavi_candidate:
        valore = report.get(chiave)
        if isinstance(valore, str) and valore.strip().upper() in SEVERITA_RANK:
            return valore.strip().upper()
    return "N/D"


_PATTERN_GIUDIZIO = re.compile(r"Giudizio complessivo:\s*(GO|ATTENZIONE|STOP)", re.IGNORECASE)


def estrai_verdetto_agent3(report3: dict) -> str:
    """
    Il report di Agent 3 non ha un campo esito strutturato a livello
    globale: ogni voce di risultati_mockup/risultati_vt contiene il
    verdetto dentro il testo libero dell'analisi, come riga
    'Giudizio complessivo: GO|ATTENZIONE|STOP'. I documenti di supporto
    (qualifiche operatore) non hanno giudizio proprio e vengono ignorati
    qui di proposito (vedi fix classificazione VT, 2026-08-02).
    Ritorna il verdetto piu' severo tra tutti quelli trovati.
    """
    if not isinstance(report3, dict):
        return "N/D"
    peggiore = "N/D"
    for lista_chiave in ("risultati_mockup", "risultati_vt"):
        for voce in report3.get(lista_chiave, []) or []:
            testo = voce.get("analisi", "") if isinstance(voce, dict) else ""
            match = _PATTERN_GIUDIZIO.search(testo)
            if match:
                esito = match.group(1).upper()
                if peggiore == "N/D" or SEVERITA_RANK.get(esito, -1) > SEVERITA_RANK.get(peggiore, -1):
                    peggiore = esito
    return peggiore


def mostra_semaforo(etichetta: str, esito: str, dettaglio: str = ""):
    """Renderizza una card semaforo colorata per un singolo agente o per il verdetto complessivo."""
    icona = SEMAFORO_ICONE.get(esito, "⚪")
    colore_bg = SEMAFORO_COLORI.get(esito, "#eeeeee")
    dettaglio_html = f" — {dettaglio}" if dettaglio else ""
    # NOTA (2026-08-02): colori testo impostati esplicitamente in scuro
    # (#1a1a1a / #444444) — senza questa specifica il testo eredita il
    # bianco del tema scuro di Streamlit e diventa illeggibile sugli
    # sfondi pastello chiari delle card (bug rilevato in screenshot di
    # validazione UI).
    st.markdown(
        f"""<div style="background-color:{colore_bg}; border-radius:10px; padding:16px; text-align:center; border:1px solid rgba(0,0,0,0.08);">
        <div style="font-size:30px; line-height:1;">{icona}</div>
        <div style="font-weight:600; margin-top:4px; color:#1a1a1a;">{etichetta}</div>
        <div style="font-size:13px; color:#444444; margin-top:2px;">{esito}{dettaglio_html}</div>
        </div>""",
        unsafe_allow_html=True,
    )


# -----------------------------------------------------------------------
# ESECUZIONE — bottone unico
# -----------------------------------------------------------------------

avvia = st.button("🚀 Analizza Welding Book completo", type="primary")

if avvia:

    progress = st.progress(0, text="Avvio analisi...")
    client = anthropic.Anthropic()

    report1 = report2 = report3 = report4 = report5 = report_sup = None
    wps_docs, wpqr_digests = [], []  # inizializzati fuori dal try, riusati da Agente 3
    errori = []

    # ---------------------------------------------------------------
    # AGENTE 1 — WPS/WPQR
    # ---------------------------------------------------------------
    progress.progress(5, text="Agente 1 — caricamento e analisi WPS/WPQR...")
    try:
        wps_docs = agente1.carica_documenti(agente1.WPS_DIR, "WPS")
        wpqr_digests = agente1.carica_wpqr_chunked(agente1.WPQR_DIR, client, agente1.MODEL)
        doc_riferimento_giunti = []
        doc_riferimento_giunti += agente1.carica_documenti(agente1.WMAP_DIR, "WELDING_MAP")
        doc_riferimento_giunti += agente1.carica_documenti(agente1.CLASS_DIR, "TAVOLA_GIUNTI")

        if not wps_docs and not wpqr_digests:
            errori.append("Agente 1: nessun documento trovato in 01_WPS o 02_WPQR.")
        else:
            report1 = agente1.run_agent_wps_wpqr(wps_docs, wpqr_digests, doc_riferimento_giunti, client=client)
            with open(os.path.join(REPORT_DIR, "report_agent1.json"), "w", encoding="utf-8") as f:
                json.dump(report1, f, ensure_ascii=False, indent=2)
    except Exception as e:
        errori.append(f"Agente 1: errore — {e}")

    # ---------------------------------------------------------------
    # AGENTE 2 — Qualifiche Saldatori (WQ)
    # ---------------------------------------------------------------
    progress.progress(25, text="Agente 2 — analisi qualifiche saldatori...")
    try:
        wq_digests = agente2.carica_wq_chunked(agente2.WQ_DIR, client, agente2.MODEL)
        if not wq_digests:
            errori.append("Agente 2: nessun documento WQ trovato in 03_WQ.")
        else:
            report2 = agente2.analizza_qualifiche(wq_digests, [], client=client)
            with open(os.path.join(REPORT_DIR, "report_agent2.json"), "w", encoding="utf-8") as f:
                json.dump(report2, f, ensure_ascii=False, indent=2)
    except Exception as e:
        errori.append(f"Agente 2: errore — {e}")

    # ---------------------------------------------------------------
    # AGENTE 3 — Mock-up + Visual Test
    # Riusa wps_docs/wpqr_digests gia' caricati dall'Agente 1 sopra, invece
    # di ricaricarli e rielaborarli (evita un secondo giro di chunking
    # sulle stesse WPQR nella stessa run).
    # ---------------------------------------------------------------
    progress.progress(40, text="Agente 3 — analisi mock-up e visual test...")
    try:
        if not wps_docs and not wpqr_digests:
            # Fallback: l'Agente 1 non ha caricato nulla (es. cartelle vuote
            # o errore sopra) — Agente 3 prova comunque a caricare da solo.
            wps_docs = agente1.carica_documenti(agente1.WPS_DIR, "WPS")
            wpqr_digests = agente1.carica_wpqr_chunked(agente1.WPQR_DIR, client, agente1.MODEL)

        wps_testo_combinato = "\n\n---\n\n".join(
            [f"[WPS: {d['nome']}]\n{d['testo']}" for d in wps_docs]
        ) if wps_docs else ""
        wpqr_testo_combinato = "\n\n---\n\n".join(
            [f"[WPQR: {d.get('_nome_file', '?')}]\n{json.dumps(d, ensure_ascii=False, indent=2)}"
             for d in wpqr_digests]
        ) if wpqr_digests else ""

        pdf_mockup = agente3.trova_file_per_estensione(CARTELLA_MOCKUP, [".pdf"])
        risultati_mockup = []
        if pdf_mockup:
            risultati_mockup = agente3.check_mockup(pdf_mockup, wps_testo_combinato, wpqr_testo_combinato)
        else:
            errori.append("Agente 3: nessun PDF trovato nella cartella 07_MOCKUP.")

        pdf_vt = agente3.trova_file_per_estensione(CARTELLA_VT, [".pdf"])
        risultati_vt, documenti_supporto = [], []
        if pdf_vt:
            # check_visual_test ritorna una TUPLA da questa sessione in poi
            # (fix classificazione VT vs documento di supporto, 2026-08-02)
            risultati_vt, documenti_supporto = agente3.check_visual_test(pdf_vt, DATA_PRODUZIONE)
        else:
            errori.append("Agente 3: nessun PDF trovato nella cartella 09_VT — check VT saltato.")

        report3 = {
            "agente": "Agent3_Mockup_VT",
            "risultati_mockup": risultati_mockup,
            "risultati_vt": risultati_vt,
            "documenti_supporto": documenti_supporto,
        }
        with open(os.path.join(REPORT_DIR, "report_agent3.json"), "w", encoding="utf-8") as f:
            json.dump(report3, f, ensure_ascii=False, indent=2)
    except Exception as e:
        errori.append(f"Agente 3: errore — {e}")

    # ---------------------------------------------------------------
    # AGENTE 4 — Certificati Materiali
    # ---------------------------------------------------------------
    progress.progress(60, text="Agente 4 — analisi certificati materiali...")
    try:
        report4 = agente4.analizza_materiali(TEST_DIR)
        with open(os.path.join(REPORT_DIR, "report_agent4.json"), "w", encoding="utf-8") as f:
            json.dump(report4, f, ensure_ascii=False, indent=2)
    except Exception as e:
        errori.append(f"Agente 4: errore — {e}")

    # ---------------------------------------------------------------
    # AGENTE 5 — PFC / EN15085 / Attrezzature
    # ---------------------------------------------------------------
    progress.progress(75, text="Agente 5 — analisi PFC/EN15085/attrezzature...")
    try:
        report5 = agente5.analizza_pfc_en15085(TEST_DIR)
        with open(os.path.join(REPORT_DIR, "report_agent5.json"), "w", encoding="utf-8") as f:
            json.dump(report5, f, ensure_ascii=False, indent=2)
    except Exception as e:
        errori.append(f"Agente 5: errore — {e}")

    # ---------------------------------------------------------------
    # SUPERVISORE — cross-check finali su tutti i report salvati su disco
    # ---------------------------------------------------------------
    progress.progress(90, text="Supervisore — cross-check finali...")
    try:
        report_sup = supervisore.run_supervisor(client)
        with open(os.path.join(REPORT_DIR, "report_supervisor.json"), "w", encoding="utf-8") as f:
            json.dump(report_sup, f, ensure_ascii=False, indent=2)
    except Exception as e:
        errori.append(f"Supervisore: errore — {e}")

    progress.progress(100, text="Analisi completata.")

    # Salvato in session_state cosi' i risultati restano visibili anche
    # dopo un rerun dovuto all'interazione con un expander o un bottone
    # di download, senza dover rilanciare l'intera analisi.
    st.session_state["ultimo_run"] = {
        "report1": report1, "report2": report2, "report3": report3,
        "report4": report4, "report5": report5, "report_sup": report_sup,
        "errori": errori,
    }


# -----------------------------------------------------------------------
# VISUALIZZAZIONE RISULTATI
# -----------------------------------------------------------------------

if "ultimo_run" in st.session_state:

    dati = st.session_state["ultimo_run"]
    report1, report2, report3 = dati["report1"], dati["report2"], dati["report3"]
    report4, report5, report_sup = dati["report4"], dati["report5"], dati["report_sup"]
    errori = dati["errori"]

    if errori:
        with st.expander(f"⚠️ {len(errori)} avviso/i durante l'esecuzione", expanded=False):
            for e in errori:
                st.warning(e)

    st.divider()

    # -------- Verdetto complessivo (Supervisore) --------
    esito_sup = estrai_esito(report_sup, ["esito_finale"])
    st.subheader("Verdetto complessivo")
    mostra_semaforo(
        "Supervisore",
        esito_sup,
        f"{report_sup.get('num_nc_totali', '?')} non conformità rilevate" if report_sup else "non eseguito",
    )

    st.divider()

    # -------- Semaforo per agente --------
    st.subheader("Dettaglio per agente")
    col1, col2, col3, col4, col5 = st.columns(5)

    with col1:
        mostra_semaforo("Agente 1\nWPS/WPQR", estrai_esito(report1, ["esito_finale"]))
    with col2:
        # Schema di report2 (Agente 2) non confermato al 100% su questo file:
        # provo piu' chiavi candidate, ricadendo su N/D senza crashare se
        # nessuna corrisponde — verificare e aggiustare se necessario.
        mostra_semaforo("Agente 2\nWQ", estrai_esito(report2, ["esito_finale", "semaforo_globale", "verdetto"]))
    with col3:
        mostra_semaforo("Agente 3\nMock-up/VT", estrai_verdetto_agent3(report3))
    with col4:
        mostra_semaforo("Agente 4\nMateriali", estrai_esito(report4, ["verdetto", "esito_finale"]))
    with col5:
        mostra_semaforo("Agente 5\nPFC/EN15085", estrai_esito(report5, ["verdetto", "esito_finale"]))

    st.divider()

    # -------- Non conformità del Supervisore, raggruppate per severita' --------
    if report_sup and report_sup.get("non_conformita"):
        st.subheader("Non conformità rilevate dal Supervisore")
        for livello, icona in [("STOP", "🔴"), ("ATTENZIONE", "🟡"), ("APPUNTO", "📝")]:
            voci = [nc for nc in report_sup["non_conformita"] if nc.get("severita") == livello]
            if voci:
                with st.expander(f"{icona} {livello} ({len(voci)})", expanded=(livello == "STOP")):
                    for nc in voci:
                        st.markdown(f"**{nc.get('codice', '—')}** — {nc.get('descrizione', '')}")
                        st.caption(f"Rif.: {nc.get('riferimento', '')}  |  Fonte: {nc.get('tool_origine', '')}")
                        if nc.get("conflitto_documentale"):
                            st.info(f"⚠ Conflitto documentale: {nc.get('contesto_conflitto', '')}")
                        st.markdown("---")

    st.divider()

    # -------- Dettaglio grezzo per agente (espandibile, non srotolato) --------
    st.subheader("Dettaglio completo per agente")
    with st.expander("Agente 1 — JSON completo"):
        st.json(report1 or {})
    with st.expander("Agente 2 — JSON completo"):
        st.json(report2 or {})
    with st.expander("Agente 3 — JSON completo"):
        st.json(report3 or {})
    with st.expander("Agente 4 — JSON completo"):
        st.json(report4 or {})
    with st.expander("Agente 5 — JSON completo"):
        st.json(report5 or {})
    with st.expander("Supervisore — JSON completo"):
        st.json(report_sup or {})

    # -------- Download report completo --------
    st.divider()
    report_completo = {
        "agente1": report1, "agente2": report2, "agente3": report3,
        "agente4": report4, "agente5": report5, "supervisore": report_sup,
    }
    st.download_button(
        "⬇️ Scarica report completo (JSON)",
        data=json.dumps(report_completo, ensure_ascii=False, indent=2),
        file_name="weldaim_report_completo.json",
        mime="application/json",
    )