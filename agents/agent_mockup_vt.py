# =============================================================================
# agent_mockup_vt.py
# Agent 3 — Mock-up + Visual Test
# Verifica documentale report mock-up (e7, e8) e report VT (e9, e10)
# Mock-up: verifica catena Mock-up → WPS → WPQR (pre-produzione)
# VT: verifica conformità report controllo visivo (produzione)
# I due check sono indipendenti — il supervisore li chiama separatamente
#
# Retrofit (sessione precedente):
# - estrazione PDF (report mock-up/VT) via utils.py, niente più pipeline OCR locale
# - WPS/WPQR NON più duplicati: riusa carica_documenti/carica_wpqr_chunked di
#   Agente 1 (chunking incluso sulle WPQR)
#
# Retrofit (sessione caching):
# - Il contenuto del prompt e' stato riordinato per abilitare il caching:
#   PRIMA le parti statiche (istruzioni fisse, poi WPS/WPQR), ALLA FINE
#   il testo del report specifico (l'unica cosa che cambia davvero ad ogni
#   chiamata all'interno dello stesso ciclo di analisi).
# - check_mockup: 2 breakpoint di cache — Blocco A (istruzioni fisse mock-up,
#   identiche in OGNI chiamata, ovunque) + Blocco B (WPS+WPQR del welding
#   book, identici per TUTTI i report mock-up della stessa run) + Blocco C
#   dinamico (testo del singolo report, mai cacheato).
# - check_visual_test: 1 breakpoint di cache — Blocco A (istruzioni fisse VT)
#   + Blocco B dinamico (data produzione + testo del singolo report VT).
#   Non c'e' WPS/WPQR nel check VT, quindi non serve un secondo breakpoint.
# - Diagnostica cache stampata dopo ogni chiamata con _stampa_uso_cache()
#   per verificare che il risparmio sia reale, non assunto.
#
# Retrofit (questa sessione — CLASSIFICAZIONE DOCUMENTO VT vs SUPPORTO):
# - PROBLEMA RISOLTO: file caricati nella cartella VT che in realta' sono
#   certificati di qualifica personale dell'operatore (non report VT veri)
#   ricevevano comunque un "Giudizio complessivo" GO/ATTENZIONE/STOP come se
#   fossero un report di ispezione fallito. Questo genera falsi STOP fuorvianti.
# - FIX: il prompt ora chiede al modello una classificazione preliminare
#   obbligatoria (REPORT_VT vs DOCUMENTO_SUPPORTO) PRIMA di eseguire i check.
#   Se e' un documento di supporto, il modello NON esegue CHECK-1...7 e NON
#   produce un giudizio complessivo — estrae solo i dati della qualifica.
# - check_visual_test() ora ritorna DUE liste (prima ne ritornava una):
#   risultati_vt (report VT veri, con giudizio) e documenti_supporto
#   (qualifiche/certificati, senza giudizio, solo dati estratti).
# - ATTENZIONE CHIAMANTI: questo cambia la firma di ritorno della funzione.
#   Se supervisor_agent.py chiama check_visual_test() direttamente, va
#   aggiornato per gestire la tupla (risultati_vt, documenti_supporto)
#   invece di una lista singola. Verificare prima di considerare chiuso.
#
# ATTENZIONE VALIDAZIONE: il contenuto dei controlli mock-up (CHECK-1...11) e
# dei controlli VT (CHECK-1...7) e' rimasto IDENTICO parola per parola rispetto
# alla versione precedente. E' stata aggiunta SOLO la classificazione preliminare
# in testa al prompt VT. Dopo il deploy, ri-testare sul dataset coerente e
# confrontare check-by-check con l'ultimo output buono prima di considerare
# questa versione definitiva.
# WeldAIM — Sprint E (caching) + fix classificazione VT
# =============================================================================

import os
import json
from dotenv import load_dotenv
import anthropic

from utils import (
    estrai_testo_pdf_semplice,
    trova_file_per_estensione,
    _prepara_content_multi_cache,
    _stampa_uso_cache,
    BASE_DIR,
)

# Riuso del caricamento WPS/WPQR di Agente 1 — non duplichiamo la pipeline
# (import di modulo: agent_wps_wpqr.py deve stare nella stessa cartella agents\)
import agent_wps_wpqr as agente1

# Carica variabili d'ambiente dal file .env
load_dotenv()

# Client Anthropic con API key esplicita
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

MODEL = "claude-sonnet-4-6"


# =============================================================================
# BLOCCO A — ISTRUZIONI FISSE MOCK-UP (statiche, identiche in ogni chiamata)
# Questo e' il blocco che viene marcato cache_control: se il testo supera la
# soglia minima (1024 token Sonnet), dalla seconda chiamata in poi Anthropic
# lo legge dalla cache invece di rielaborarlo — risparmio sul costo di input.
#
# NESSUNA MODIFICA in questa sessione — contenuto identico alla versione
# precedente, invariato parola per parola.
# =============================================================================

ISTRUZIONI_FISSE_MOCKUP = """Sei un esperto di coordinamento della saldatura (IWE — International Welding Engineer).
Devi verificare la conformità documentale di un report di mock-up per la commessa Deutsche Bahn,
secondo i requisiti di QT.6495.023 (linea guida Geismar Italia — documento contrattuale, non norma)
e le norme ISO/EN applicabili.

NON stai valutando la qualità produttiva della saldatura.
Stai verificando SOLO la conformità documentale del report.

Esegui i seguenti controlli e per ognuno indica:
- ESITO: OK / NC-STOP / NC-ATTENZIONE / APPUNTO / NON VERIFICABILE
- MOTIVAZIONE: spiegazione tecnica concisa
- RIFERIMENTO NORMATIVO: norma ISO/EN o documento contrattuale Geismar (citali separatamente)

FORMATO DI OUTPUT — REGOLE VINCOLANTI (leggi prima di scrivere):
- NIENTE markdown: no titoli con #, no grassetto **, no tabelle, no emoji o checkmark (✅❌⚠️ℹ️).
  Solo testo semplice riga per riga, esattamente nel formato indicato più sotto.
- MOTIVAZIONE = massimo 3-4 frasi. Non elencare ogni singolo campo verificato con esito OK —
  cita solo ciò che è assente, incoerente o rilevante per l'esito scelto. Un check con esito OK
  può avere una motivazione di una sola frase ("Tutti i parametri essenziali coerenti con WPS e WPQR").
- Non riportare confronti punto-per-punto in forma tabellare: riassumi in prosa se necessario
  ("processo, materiale, spessore e posizione coerenti tra mock-up e WPQR 8219").
  Se c'è una sola discrepanza rilevante, descrivi solo quella.
- Segui l'ordine e le etichette esatte del formato sotto, senza aggiungere sezioni extra.

CONTROLLI DA ESEGUIRE:

[CHECK-1] FASE PRELIMINARE — Tracciabilità e dati identificativi
Verifica che siano presenti:
- Rintracciabilità della macrografia
- Tipologia del giunto (es. FWa5, BWs3, 4HY) con simbolo ISO 2553
- Materiale base: denominazione, norma di riferimento, dimensioni
- WPS di riferimento citata nel report
- Nome del saldatore che ha eseguito il giunto
Nota: la designazione testuale del giunto (es. ½HV10, HY, BW) è sufficiente
per identificare la tipologia. Il simbolo grafico ISO 2553 è apprezzabile
ma NON obbligatorio — assenza → APPUNTO, mai NC-ATTENZIONE.
Assenza di uno o più elementi sostanziali → NC-ATTENZIONE.
   Nota: campi non obbligatori per contratto (es. campo CLIENTE) se vuoti
   → APPUNTO, non NC-ATTENZIONE. Non è mai stato richiesto esplicitamente
   che il campo cliente fosse compilato nel report mock-up.

[CHECK-2] VERIFICA CATENA MOCK-UP → WPS → WPQR
Verifica la catena completa in tre passi:

a) La WPS citata nel report esiste nel welding book
   (confronta con il testo WPS fornito)

b) Le variabili essenziali del giunto realizzato nel mock-up
   (processo di saldatura, gruppo di materiale base, spessore, posizione)
   rientrano nel campo qualificato di quella WPS.
   Riferimento: ISO 15614-1 §8.4.3 per acciai, ISO 15614-2 per alluminio.
   Leggi prima il range dichiarato nella WPS/WPQR; applica la formula
   0.5t/2t solo se il range non è esplicitamente dichiarato.
   Per la posizione di saldatura: se non è esplicitamente dichiarata
   nel report mock-up → APPUNTO, non NC-ATTENZIONE. La posizione
   qualificata fa fede da quanto dichiarato nella WPS collegata.
   Segnala comunque l'assenza come osservazione per incentivare
   la compilazione completa del report.
   Distingui BW e FW — le tabelle di qualifica sono diverse.

   REGOLA CRITICA — classificazione T-Joint secondo ISO 15614-1:
   - T-Joint SENZA preparazione del cianfrino → FW (fillet weld)
   - T-Joint CON cianfrino a PIENA penetrazione → HV → classificato come BW
   - T-Joint CON cianfrino a PENETRAZIONE PARZIALE → HY → classificato come BW
   NON classificare HV o HY come FW. La distinzione BW/FW dipende dalla
   presenza o assenza della preparazione del cianfrino, non dall'angolo.

   GESTIONE ISO 15613:
   Se la WPQR è qualificata secondo ISO 15613 (pre-production welding test):
   - ISO 15613 è una scelta tecnica legittima del CS, non genera NC o ATTENZIONE
   - ISO 15613 fa riferimento esplicito a ISO 15614 per i test — verifica i
     parametri dichiarati nella WPQR stessa, non applicare meccanicamente
     le tabelle ISO 15614-1 §8.4.3
   - Se un test previsto da ISO 15614 è assente nella WPQR ISO 15613 perché
     fisicamente impossibile sul giunto specifico (es. RT su giunto HY a
     parziale penetrazione), l'assenza è tecnicamente giustificata → APPUNTO,
     non NC. L'agente deve riconoscere e dichiarare la giustificazione tecnica.

c) La WPS è supportata da una WPQR nel welding book, e quella WPQR
   qualifica effettivamente i parametri del giunto del mock-up.

Se WPS non identificabile → NC-ATTENZIONE.
   Nota sulla cronologia WPS: se la data della WPS è successiva alla data
   del report mock-up, segnalarlo come APPUNTO — è un'osservazione rilevante
   ma non bloccante. Non classificare come NC-ATTENZIONE o NC-STOP.
   VINCOLO SULL'ESITO COMPLESSIVO DEL CHECK-2: questa anomalia cronologica è un
   APPUNTO anche nell'Esito finale del check, non solo nella singola voce — non
   deve MAI da sola far salire l'Esito del CHECK-2 a NC-ATTENZIONE. Se, dopo aver
   escluso questa anomalia, non ci sono altre non conformità nei passi a/b/c,
   l'Esito del CHECK-2 è OK o APPUNTO, mai NC-ATTENZIONE. Prima di scrivere
   l'Esito finale del check, rileggi se la motivazione a giustificarlo è solo
   la data della WPS: se sì, correggi l'Esito.
   Nota critica sulla catena WPS→WPQR: una WPQR può supportare più WPS
   diverse. È normale e corretto che la WPQR faccia riferimento ad una WPS
   diversa da quella citata nel mock-up. NON generare NC per questo motivo.
   Verifica SOLO che i parametri qualificati dalla WPQR coprano le variabili
   essenziali del giunto del mock-up. Non esiste requisito di collegamento
   biunivoco esplicito tra WPS e WPQR.
Se WPQR non fornita o non identificabile → NON VERIFICABILE con APPUNTO.

[CHECK-3] RIFERIMENTI DEL REPORT
Verifica la presenza di:
- Numero disegno + indice revisione + data
- Riferimento normativo di accettabilità:
  ISO 5817 liv.B per acciai, ISO 10042 liv.B per alluminio
- Altri riferimenti applicabili: ISO 6520-1, EN 15085-4, DVS 1621, ISO 17639

Assenza del riferimento normativo di accettabilità (ISO 5817 / ISO 10042) → NC-ATTENZIONE.

Le seguenti situazioni restano SEMPRE APPUNTO, anche se presenti insieme, e NON possono
mai far scattare NC-ATTENZIONE o sommarsi tra loro per aumentare la severità:
- Data del disegno mancante o non riportata (numero disegno e revisione presenti)
- Citazione incoerente o non ripetuta su tutte le pagine di riferimenti secondari
  (es. ISO 6520-1, ISO 17639, DVS 1621) quando il riferimento di accettabilità
  principale (ISO 5817/ISO 10042) è comunque presente
- Riferimenti normativi sovrabbondanti o non strettamente pertinenti al tipo di esame

Non cumulare più osservazioni minori per giustificare un'escalation a NC-ATTENZIONE:
ogni elemento va valutato singolarmente rispetto alla soglia sopra definita.

[CHECK-4] TIPOLOGIA E FORMATO DEL REPORT
Verifica:
- Numero report + indice di revisione
- Riferimento a ISO 17639 e/o DVS 1621
- Doppia lingua ITA-ENG (o almeno indicazione bilingue)
- Numero pagina e totale pagine su ogni pagina
- Data del report (emissione o ultima revisione)

[CHECK-5] COERENZA MATERIALE BASE
Verifica che i valori del materiale base dichiarati nella sezione apposita
corrispondano a quelli misurabili/visibili nelle macrografie
(coerenza interna al documento).
Se le macrografie non sono leggibili come testo → NON VERIFICABILE con APPUNTO.

[CHECK-6] SPESSORE NELLE MACROGRAFIE
Verifica che lo spessore misurato nelle macrografie corrisponda al nominale dichiarato.
Se non corrisponde, il report DEVE contenere una dichiarazione esplicita che indica
che quello spessore specifico è stato tagliato (problema di inglobamento in laboratorio).
Assenza di tale nota esplicativa quando gli spessori non coincidono → NC-STOP.
Spessori corrispondenti → OK.

[CHECK-7] MISURE GEOMETRICHE OBBLIGATORIE
Per giunti FW (cordone d'angolo):
  verifica che sia riportata l'altezza di gola (a) e che sia conforme al nominale.
Per giunti BW (piena penetrazione) o a penetrazione parziale:
  verifica che sia riportata la penetrazione e che sia conforme al nominale.
Controllo sia di PRESENZA che di ACCETTABILITÀ rispetto al disegno/WPS.
Assenza misura → NC-ATTENZIONE.
Misura presente ma non conforme al nominale → NC-STOP.

[CHECK-8] CONTENUTO FOTOGRAFICO (verifica testuale — senza valutazione visiva approfondita)
Verifica che il report dichiari o evidenzi, a livello di report (non necessariamente
ripetuto su ogni singola pagina o foto):
- Almeno 2 foto della stessa macrografia
  (una senza indicazioni, una con misurazioni)
- Ingrandimento dichiarato tra 2.5x e 10x
  (eccezione accettata: prima foto anche sotto 2.5x se materiale troppo spesso
   per mostrare lo spessore intero — la seconda deve essere nella fascia 2.5x–10x)
- Posizioni S/M/E indicate se richieste 3 macrografie (saldature difficili
  o acciai con CEV >= 0.45)

Dati tecnici comuni al provino (ingrandimento, finitura superficiale, reagente di
attacco, incertezza di misura) valgono per l'intero report se dichiarati anche una
sola volta in una qualsiasi pagina: NON è richiesto che vengano ripetuti su ogni
pagina o accanto a ogni singola foto. La mancata ripetizione di questi dati su una
pagina dove sono già stati dichiarati altrove nel report NON costituisce NC.

[CHECK-9] CLASSIFICAZIONE E VALUTAZIONE INDICAZIONI
Le "indicazioni" sono le imperfezioni/discontinuità eventualmente rilevate nella saldatura durante
l'esame macrografico (es. porosità, inclusioni, mancanza di fusione) — vanno documentate anche
quelle giudicate accettabili, non solo quelle che causano scarto.

Verifica che, SE sono presenti indicazioni:
- Siano misurate (anche quelle accettabili)
- Siano classificate secondo ISO 6520-1
- Siano esplicitamente valutate secondo ISO 5817 liv.B (acciai) o ISO 10042 liv.B (alluminio)

REGOLA SULL'ASSENZA DI INDICAZIONI — quando "nessuna indicazione" è già soddisfatto:
Un giudizio globale esplicito tipo "ESAME VISIVO: ACCETTABILE" / "ESAME DIMENSIONALE: ACCETTABILE"
è di per sé una dichiarazione sufficiente di assenza di indicazioni rilevanti, anche senza un elenco
dedicato o la frase letterale "nessuna indicazione rilevata" — è prassi standard nei report
macrografici che un giudizio di accettabilità implichi l'assenza di indicazioni da classificare.
In questo caso → OK, non richiedere classificazione ISO 6520-1 (non c'è nulla da classificare).
Genera NC-ATTENZIONE SOLO se:
  a) il report ha una sezione/campo dedicato alle indicazioni (es. tabella "indicazioni riscontrate")
     presente ma lasciata vuota in modo ambiguo o incoerente col giudizio finale, oppure
  b) il report menziona esplicitamente la presenza di un'indicazione ma non la classifica/misura/valuta.
Nella motivazione dichiara sempre esplicitamente quale caso si applica (giudizio globale accettato
come sufficiente, oppure indicazione non classificata) — non lasciarlo intuire al lettore.

[CHECK-10] PROVE DI DUREZZA (se applicabile)
Verifica se il report menziona prove di durezza.
Le prove sono obbligatorie se:
  - Acciaio con CEV >= 0.45 → per qualsiasi spessore
  - BW con spessore >= 25mm → HV10 secondo ISO 9015-1
  - FW o HV/HY con spessore >= 15mm → HV10 secondo ISO 9015-1
Se non applicabile al caso specifico → N/A.
Se applicabile ma assente → NC-ATTENZIONE.

[CHECK-11] FIRME
Verifica la presenza di:
- Firma del tecnico che ha eseguito le prove
- Firma del CS (Coordinatore di Saldatura) per accettazione
Il CS può essere identificato con firma, timbro, sigla o campo dedicato.
Assenza firma CS → NC-ATTENZIONE.

---
Produci l'output nel seguente formato strutturato:

REPORT ANALIZZATO: [nome file]

CHECK-1 | FASE PRELIMINARE
Esito: [OK / NC-STOP / NC-ATTENZIONE / APPUNTO / NON VERIFICABILE]
Motivazione: [testo]
Rif. normativo: [QT.6495.023 §1.1]

CHECK-2 | CATENA MOCK-UP → WPS → WPQR
Esito: [...]
Motivazione: [...]
Rif. normativo: [QT.6495.023 §1.2 + ISO 15614-1 §8.4.3]

CHECK-3 | RIFERIMENTI DEL REPORT
Esito: [...]
Motivazione: [...]
Rif. normativo: [QT.6495.023 §1.2 + ISO 5817 / ISO 10042]

CHECK-4 | FORMATO DEL REPORT
Esito: [...]
Motivazione: [...]
Rif. normativo: [QT.6495.023 §1.3]

CHECK-5 | COERENZA MATERIALE BASE
Esito: [...]
Motivazione: [...]
Rif. normativo: [QT.6495.023 §1.4]

CHECK-6 | SPESSORE NELLE MACROGRAFIE
Esito: [...]
Motivazione: [...]
Rif. normativo: [QT.6495.023 §1.4]

CHECK-7 | MISURE GEOMETRICHE OBBLIGATORIE
Esito: [...]
Motivazione: [...]
Rif. normativo: [QT.6495.023 §1.4 + ISO 5817 / ISO 10042]

CHECK-8 | CONTENUTO FOTOGRAFICO
Esito: [...]
Motivazione: [...]
Rif. normativo: [QT.6495.023 §1.3 + ISO 17639]

CHECK-9 | CLASSIFICAZIONE INDICAZIONI
Esito: [...]
Motivazione: [...]
Rif. normativo: [ISO 6520-1 + ISO 5817 / ISO 10042]

CHECK-10 | PROVE DI DUREZZA
Esito: [...]
Motivazione: [...]
Rif. normativo: [ISO 9015-1 + QT.6495.023 §1.4]

CHECK-11 | FIRME
Esito: [...]
Motivazione: [...]
Rif. normativo: [QT.6495.023 §1.5]

RIEPILOGO MOCK-UP:
- Esiti critici (NC-STOP): [elenco o "Nessuno"]
- Esiti di attenzione (NC-ATTENZIONE): [elenco o "Nessuno"]
- Osservazioni (APPUNTO): [elenco o "Nessuno"]
- Giudizio complessivo: [GO / ATTENZIONE / STOP]
"""


# =============================================================================
# BLOCCO A — ISTRUZIONI FISSE VISUAL TEST (statiche, identiche in ogni chiamata)
#
# MODIFICATO in questa sessione: aggiunta classificazione preliminare
# obbligatoria REPORT_VT vs DOCUMENTO_SUPPORTO, in testa al prompt, prima
# di [CHECK-1]. Aggiunto FORMATO B in coda per i documenti di supporto.
# Il contenuto di CHECK-1...CHECK-7 (quando si tratta di un vero report VT)
# è rimasto IDENTICO parola per parola alla versione precedente.
# =============================================================================

ISTRUZIONI_FISSE_VT = """Sei un esperto di coordinamento della saldatura (IWE — International Welding Engineer).
Devi verificare la conformità documentale di un report di Visual Test (VT) per la commessa Deutsche Bahn,
secondo i requisiti di QT.6495.024 §9 (linea guida Geismar Italia — documento contrattuale, non norma)
e le norme ISO applicabili.

NON stai valutando la qualità produttiva delle saldature.
Stai verificando SOLO la conformità documentale del report VT.

NOTA IMPORTANTE: NON verificare la verifica annuale dell'acuità visiva dell'operatore.
Questo documento si controlla solo durante l'ispezione fisica, non nel welding book digitale.

CLASSIFICAZIONE PRELIMINARE — OBBLIGATORIA, PRIMA DI QUALSIASI ALTRO CONTROLLO:
Determina innanzitutto se il documento fornito è:
  (A) REPORT_VT — un report di ispezione visiva vero e proprio, che contiene dati di
      controllo su un manufatto specifico (identificazione del pezzo/SN, riferimento
      disegno, risultato del controllo visivo, giudizio di conformità).
  (B) DOCUMENTO_SUPPORTO — un documento che NON è un'ispezione VT su un manufatto, ma
      un documento di supporto alla qualifica dell'operatore (es. certificato di
      qualifica personale ISO 9712, patentino, attestato di formazione interna).
      Riconoscibile perché non contiene dati di controllo su un manufatto specifico:
      contiene invece dati anagrafici dell'operatore, ente certificatore, validità
      della qualifica.

Se (A): esegui TUTTI i controlli CHECK-1...CHECK-7 indicati sotto e produci l'output
nel FORMATO A (Report VT completo).

Se (B): NON eseguire i controlli CHECK-1...CHECK-7 e NON produrre un "Giudizio
complessivo" GO/ATTENZIONE/STOP — un documento di supporto non è un'ispezione fallita
o riuscita, è semplicemente un altro tipo di documento, e attribuirgli un giudizio di
conformità sarebbe fuorviante. Estrai invece solo i dati identificativi della qualifica
e produci l'output nel FORMATO B (Documento di supporto), più sotto.

Esegui i seguenti controlli (SOLO se il documento è di tipo A) e per ognuno indica:
- ESITO: OK / NC-STOP / NC-ATTENZIONE / APPUNTO / NON VERIFICABILE
- MOTIVAZIONE: spiegazione tecnica concisa
- RIFERIMENTO NORMATIVO: norma ISO/EN o documento contrattuale Geismar (citali separatamente)

FORMATO DI OUTPUT — REGOLE VINCOLANTI (leggi prima di scrivere):
- NIENTE markdown: no titoli con #, no grassetto **, no tabelle, no emoji o checkmark (✅❌⚠️ℹ️).
  Solo testo semplice riga per riga, esattamente nel formato indicato più sotto.
- MOTIVAZIONE = massimo 3-4 frasi. Non elencare ogni singolo campo verificato con esito OK —
  cita solo ciò che è assente, incoerente o rilevante per l'esito scelto. Un check con esito OK
  può avere una motivazione di una sola frase.
- Segui l'ordine e le etichette esatte del formato sotto, senza aggiungere sezioni extra.

CONTROLLI DA ESEGUIRE (solo per documenti di tipo A — REPORT_VT):

[CHECK-1] QUALIFICA OPERATORE VT
Verifica che sia presente la qualifica dell'operatore che ha eseguito il controllo VT.
Sono accettate entrambe le seguenti modalità:
  a) Certificato ISO 9712 Level 2 VT in corso di validità
  b) Qualifica interna del produttore documentata (formazione da CS responsabile)
NON verificare l'acuità visiva annuale — non controllabile nel welding book digitale.
Se nessuna qualifica identificabile → NC-ATTENZIONE.

[CHECK-2] NORMA DI RIFERIMENTO
Verifica che il report citi la norma ISO 17637
(Non-destructive testing of welds — Visual testing of fusion-welded joints).
Assenza → NC-ATTENZIONE.

[CHECK-3] FIRMA E APPROVAZIONE CS
Verifica che il documento sia firmato e datato dal CS (Coordinatore di Saldatura).
Il CS può essere identificato con firma, timbro, sigla o campo dedicato.
Assenza firma CS → NC-ATTENZIONE.

[CHECK-4] TRACCIABILITÀ — SERIAL NUMBER O LOTTO
Verifica che il report indichi esplicitamente il Serial Number (SN)
o il lotto di riferimento del/dei manufatto/i controllato/i.
La tracciabilità diretta al pezzo fisico è obbligatoria.
Assenza SN o lotto → NC-STOP.

Un report VT può coprire uno o più manufatti con un unico verdetto.
Il campo di tracciabilità può presentarsi in forme molto diverse a seconda
del fornitore: un SN semplice, un codice di lotto, o un identificativo
articolato e gerarchico (es. progetto/sottoassieme/elemento/progressivo,
con slash, punti o altri separatori).

REGOLA DI DEFAULT: tratta il valore del campo come UN SINGOLO identificativo
che copre UN SOLO manufatto, salvo che non ricorra ESPLICITAMENTE il caso
di range descritto sotto. In caso di dubbio, scegli sempre l'interpretazione
"identificativo singolo" — non dedurre un range da un codice articolato solo
perché contiene segmenti numerici in sequenza (es. un identificativo con
segmenti tipo ".../001/050" NON è un range da 1 a 50: è un codice gerarchico
a campo singolo, salvo indicazione esplicita contraria nel testo del report).

Per determinare se il report copre PIÙ manufatti, in quest'ordine:
1. Cerca PRIMA un campo esplicito che dichiari il numero di pezzi/manufatti
   controllati (es. "quantità", "n. pezzi", "lotto di N unità"). Se presente,
   usa questo numero come fonte di verità.
2. Solo in ASSENZA di tale campo, puoi interpretare un range dalla notazione
   SN, e SOLO se ricorrono TUTTE queste condizioni:
   - due identificativi COMPLETI sono scritti per esteso, separati da un
     trattino o dalla parola "a"/"to" (es. "SN01-SN05", "da SN01 a SN05")
   - i due identificativi condividono lo stesso prefisso testuale
   - cambia solo la parte numerica finale, e nient'altro nella struttura
     del codice
   Un elenco di identificativi completi separati da virgola (es. "SN01, SN03,
   SN07") indica elementi discreti singoli, non un range: somma il numero
   di elementi elencati.
3. Se nessuna delle due condizioni sopra è soddisfatta, il report copre
   1 (uno) manufatto.

Riporta sempre nel campo note il numero di manufatti coperti dal report
e il criterio applicato (campo esplicito / range esplicito / elenco discreto
/ default singolo).

[CHECK-5] RIFERIMENTO AL DISEGNO
Verifica che il report riporti il numero del disegno di riferimento,
con indice di revisione.

- Numero disegno presente E indice di revisione presente → OK, nessuna NC.
- Numero disegno presente MA indice di revisione assente o non specificato
  → APPUNTO (non NC-ATTENZIONE): il disegno è comunque identificabile,
  manca solo la puntualizzazione della revisione applicabile.
- Numero disegno assente del tutto → NC-ATTENZIONE: senza alcun riferimento
  al disegno non è possibile ricondurre il controllo alla configurazione
  geometrica di progetto.
Non abbassare la severità ipotizzando che il dato possa essere presente su una versione cartacea o fisica non fornita: la valutazione si basa esclusivamente sul documento digitale ricevuto.

[CHECK-6] CRONOLOGIA — DATA VT vs DATA PRODUZIONE
Verifica che la data di emissione del report VT sia successiva
alla data di produzione del manufatto.
Un VT datato prima della produzione è documentalmente impossibile → NC-STOP.
Se la data di produzione non è disponibile → NON VERIFICABILE con APPUNTO.

[CHECK-7] INDICAZIONI E GIUDIZIO DI CONFORMITÀ
Le "indicazioni" sono le imperfezioni/discontinuità eventualmente rilevate durante il controllo
visivo (es. cricche superficiali, porosità, spruzzi, mancanza di fusione visibile).

Verifica che il report:
- Riporti un giudizio di conformità esplicito (conforme / non conforme) — anche tramite spunta su
  modulo checklist: una casella "CONFORME" compilata è un giudizio esplicito valido, non serve
  necessariamente la frase scritta per esteso.
- Se sono presenti indicazioni, siano elencate con le relative misure.

REGOLA SULL'ASSENZA DI INDICAZIONI — quando "nessuna indicazione" è già soddisfatto:
Un giudizio di conformità esplicito (spunta "CONFORME" o dicitura equivalente) è di per sé una
dichiarazione sufficiente di assenza di indicazioni rilevanti, anche senza la frase letterale
"nessuna indicazione rilevata" — è prassi standard che un giudizio di conformità implichi l'assenza
di indicazioni non accettabili. In questo caso → OK.
Genera NC-ATTENZIONE SOLO se:
  a) manca del tutto un giudizio di conformità esplicito (nessuna spunta, nessuna dicitura), oppure
  b) il report menziona la presenza di indicazioni ma non le elenca con le misure.
Nella motivazione dichiara sempre esplicitamente quale caso si applica.

---
FORMATO A — REPORT VT (usa questo formato SOLO se il documento è di tipo REPORT_VT):

TIPO_DOCUMENTO: REPORT_VT
REPORT ANALIZZATO: [nome file]

CHECK-1 | QUALIFICA OPERATORE VT
Esito: [OK / NC-STOP / NC-ATTENZIONE / APPUNTO / NON VERIFICABILE]
Motivazione: [testo]
Rif. normativo: [ISO 9712 + QT.6495.024 §9]

CHECK-2 | NORMA DI RIFERIMENTO
Esito: [...]
Motivazione: [...]
Rif. normativo: [ISO 17637 + QT.6495.024 §9]

CHECK-3 | FIRMA CS
Esito: [...]
Motivazione: [...]
Rif. normativo: [QT.6495.024 §9]

CHECK-4 | TRACCIABILITÀ SN / LOTTO
Esito: [...]
Motivazione: [...]
Rif. normativo: [QT.6495.024 §9]

CHECK-5 | RIFERIMENTO DISEGNO
Esito: [...]
Motivazione: [...]
Rif. normativo: [QT.6495.024 §9]

CHECK-6 | CRONOLOGIA DATA VT
Esito: [...]
Motivazione: [...]
Rif. normativo: [QT.6495.024 §9]

CHECK-7 | INDICAZIONI E GIUDIZIO
Esito: [...]
Motivazione: [...]
Rif. normativo: [ISO 17637 + QT.6495.024 §9]

RIEPILOGO VISUAL TEST:
- Esiti critici (NC-STOP): [elenco o "Nessuno"]
- Esiti di attenzione (NC-ATTENZIONE): [elenco o "Nessuno"]
- Osservazioni (APPUNTO): [elenco o "Nessuno"]
- Giudizio complessivo: [GO / ATTENZIONE / STOP]

---
FORMATO B — DOCUMENTO DI SUPPORTO (usa questo formato SOLO se il documento è di tipo DOCUMENTO_SUPPORTO):

TIPO_DOCUMENTO: DOCUMENTO_SUPPORTO
REPORT ANALIZZATO: [nome file]
NOTA: Questo documento non è un report VT ma un documento di supporto alla qualifica
dell'operatore (es. certificato di qualifica personale, patentino, attestato).

Nome operatore: [nome estratto dal documento, o "non identificabile"]
Ente certificatore: [nome ente, o "non identificabile"]
Norma di riferimento qualifica: [es. ISO 9712:2022, o "non identificabile"]
Data rilascio: [data, o "non disponibile"]
Data scadenza: [data, o "non disponibile"]
Qualifica in corso di validità: [SI / NO / NON VERIFICABILE — confronta scadenza con data odierna se nota]

NOTA D'USO: questo documento può essere impiegato per verificare la corrispondenza col
nome dell'operatore dichiarato nel report VT collegato, ma NON genera un giudizio
complessivo GO/ATTENZIONE/STOP proprio.
"""


# =============================================================================
# FUNZIONE PRINCIPALE — CHECK MOCK-UP (e7 + e8)
# Catena di verifica: Mock-up → WPS citata → WPQR che qualifica la WPS
#
# NESSUNA MODIFICA in questa sessione — funzione identica alla versione
# precedente.
# =============================================================================

def check_mockup(pdf_paths_mockup: list, wps_testo: str, wpqr_testo: str = "") -> list:
    """
    Verifica la conformità dei report mock-up.

    Parametri:
    - pdf_paths_mockup : lista percorsi PDF dei report mock-up
    - wps_testo        : testo WPS del welding book (costruito da agente1.carica_documenti)
    - wpqr_testo       : digest WPQR del welding book, formattati JSON
                         (costruito da agente1.carica_wpqr_chunked — opzionale,
                         se assente i check relativi → NON VERIFICABILE)

    Ritorna lista di dizionari con i risultati per ogni report.

    NOTA CACHING: il blocco WPS/WPQR viene costruito UNA VOLTA fuori dal loop
    (e' identico per tutti i report mock-up di questa run) cosi' la seconda,
    terza, quarta chiamata di questo ciclo lo trovano gia' in cache.
    """

    risultati = []

    # Blocco B — WPS + WPQR, costruito una sola volta, uguale per tutta la run
    if wpqr_testo.strip():
        blocco_wps_wpqr = f"""TESTO DELLE WPS DEL WELDING BOOK:
{wps_testo}

---
DIGEST DELLE WPQR DEL WELDING BOOK (già estratti con chunking — per verifica catena WPS → WPQR):
{wpqr_testo}
"""
    else:
        blocco_wps_wpqr = f"""TESTO DELLE WPS DEL WELDING BOOK:
{wps_testo}

---
DIGEST DELLE WPQR DEL WELDING BOOK: non fornito.
Per i check relativi alla catena WPS → WPQR, indica NON VERIFICABILE.
"""

    for pdf_path in pdf_paths_mockup:

        print(f"\n[MOCK-UP] Analisi: {os.path.basename(pdf_path)}")
        testo = estrai_testo_pdf_semplice(pdf_path)

        # Blocco C — testo del report specifico, SEMPRE ultimo, mai cacheato
        blocco_report = f"""TESTO DEL REPORT MOCK-UP DA ANALIZZARE:
{testo}

---
Produci ora l'output secondo il formato indicato sopra, per questo specifico report.
"""

        content = _prepara_content_multi_cache(
            blocchi_cacheabili=[ISTRUZIONI_FISSE_MOCKUP, blocco_wps_wpqr],
            blocco_finale=blocco_report
        )

        risposta = client.messages.create(
            model=MODEL,
            max_tokens=8000,
            messages=[
                {"role": "user", "content": content}
            ]
        )

        _stampa_uso_cache(risposta, etichetta=f"mockup: {os.path.basename(pdf_path)}")

        risultati.append({
            "file": os.path.basename(pdf_path),
            "tipo": "mock-up",
            "analisi": risposta.content[0].text
        })

    return risultati


# =============================================================================
# FUNZIONE PRINCIPALE — CHECK VISUAL TEST (e9 + e10)
# Indipendente dal mock-up — verifica conformità report VT di produzione
#
# MODIFICATA in questa sessione: ora ritorna DUE liste invece di una.
# Instrada ogni file in base alla riga "TIPO_DOCUMENTO:" che il modello
# scrive in testa alla risposta (vedi FORMATO A / FORMATO B nel prompt).
# =============================================================================

def check_visual_test(pdf_paths_vt: list, data_produzione: str = None) -> tuple:
    """
    Verifica la conformità dei report di Visual Test.

    Parametri:
    - pdf_paths_vt   : lista percorsi PDF dei report VT (o documenti di supporto
                       come certificati di qualifica operatore, mescolati nella
                       stessa cartella)
    - data_produzione: data produzione del manufatto (es. "2025-03-15")
                       Se non fornita → check cronologico NON VERIFICABILE.

    Ritorna una TUPLA (risultati_vt, documenti_supporto):
    - risultati_vt       : report VT veri, con checklist completa e giudizio complessivo
    - documenti_supporto : documenti di supporto (es. qualifiche operatore),
                           senza checklist né giudizio complessivo

    ATTENZIONE CHIAMANTI: prima di questa modifica la funzione ritornava una
    lista sola. Se supervisor_agent.py chiama questa funzione direttamente,
    va aggiornato per spacchettare la tupla.
    """

    risultati_vt = []
    documenti_supporto = []

    # Contesto data produzione per il prompt (dinamico ma corto, va nel
    # blocco finale insieme al testo del report)
    if data_produzione:
        ctx_data = f"Data di produzione del manufatto disponibile: {data_produzione}"
    else:
        ctx_data = ("Data di produzione del manufatto NON fornita. "
                    "Il check cronologico (CHECK-6) sarà NON VERIFICABILE.")

    for pdf_path in pdf_paths_vt:

        print(f"\n[VT] Analisi: {os.path.basename(pdf_path)}")
        testo = estrai_testo_pdf_semplice(pdf_path)

        blocco_report = f"""{ctx_data}

---
TESTO DEL REPORT VISUAL TEST DA ANALIZZARE:
{testo}

---
Produci ora l'output secondo il formato indicato sopra (FORMATO A o FORMATO B a
seconda della classificazione), per questo specifico documento.
"""

        content = _prepara_content_multi_cache(
            blocchi_cacheabili=[ISTRUZIONI_FISSE_VT],
            blocco_finale=blocco_report
        )

        risposta = client.messages.create(
            model=MODEL,
            max_tokens=6000,
            messages=[
                {"role": "user", "content": content}
            ]
        )

        _stampa_uso_cache(risposta, etichetta=f"VT: {os.path.basename(pdf_path)}")

        testo_risposta = risposta.content[0].text

        # Instradamento in base alla classificazione dichiarata dal modello
        # nella prima riga della risposta. Default a REPORT_VT se la riga
        # manca o non è riconosciuta, per non perdere silenziosamente un
        # risultato in caso di formato imprevisto.
        prima_riga = testo_risposta.strip().splitlines()[0] if testo_risposta.strip() else ""

        if "DOCUMENTO_SUPPORTO" in prima_riga:
            print(f"  -> Classificato come DOCUMENTO DI SUPPORTO (non un report VT)")
            documenti_supporto.append({
                "file": os.path.basename(pdf_path),
                "tipo": "documento-supporto",
                "analisi": testo_risposta
            })
        else:
            risultati_vt.append({
                "file": os.path.basename(pdf_path),
                "tipo": "visual-test",
                "analisi": testo_risposta
            })

    return risultati_vt, documenti_supporto

# =============================================================================
# BLOCCO DI TEST — eseguito solo se lanci questo file direttamente
# =============================================================================

if __name__ == "__main__":

    # -------------------------------------------------------------------------
    # CONFIGURAZIONE CARTELLE — struttura QT.6495.022
    # WPS/WPQR NON sono più configurati qui: arrivano da agente1 (stessa cartella
    # test_docs, path condivisi con Agente 1 — vedi agente1.WPS_DIR/WPQR_DIR)
    # -------------------------------------------------------------------------
    CARTELLA_MOCKUP = str(BASE_DIR / "test_docs" / "07_MOCKUP")
    CARTELLA_VT     = str(BASE_DIR / "test_docs" / "09_VT")

    # Data produzione manufatto — inserisci se disponibile, altrimenti lascia None
    DATA_PRODUZIONE = None  # es. "2025-03-15"

    # -------------------------------------------------------------------------
    # WPS e WPQR: riuso del caricamento di Agente 1 (chunking WPQR incluso)
    # invece di rileggere/riprocessare da zero come nella versione precedente
    # -------------------------------------------------------------------------
    print("[Agent3] Caricamento WPS/WPQR tramite Agente 1 (carica_documenti / carica_wpqr_chunked)...")
    wps_docs = agente1.carica_documenti(agente1.WPS_DIR, "WPS")
    wpqr_digests = agente1.carica_wpqr_chunked(agente1.WPQR_DIR, client, agente1.MODEL)

    wps_testo_combinato = "\n\n---\n\n".join(
        [f"[WPS: {d['nome']}]\n{d['testo']}" for d in wps_docs]
    ) if wps_docs else ""

    wpqr_testo_combinato = "\n\n---\n\n".join(
        [f"[WPQR: {d.get('_nome_file', '?')}]\n{json.dumps(d, ensure_ascii=False, indent=2)}"
         for d in wpqr_digests]
    ) if wpqr_digests else ""

    # -------------------------------------------------------------------------
    # CHECK MOCK-UP
    # -------------------------------------------------------------------------
    pdf_mockup = trova_file_per_estensione(CARTELLA_MOCKUP, [".pdf"])

    print("\n" + "="*60)
    print("AGENT 3 — CHECK MOCK-UP")
    print("="*60)

    risultati_mockup = []
    if pdf_mockup:
        risultati_mockup = check_mockup(pdf_mockup, wps_testo_combinato, wpqr_testo_combinato)
        for r in risultati_mockup:
            print(f"\n{'='*60}")
            print(f"FILE: {r['file']}")
            print(f"{'='*60}")
            print(r["analisi"])
    else:
        print("Nessun PDF trovato nella cartella 07_MOCKUP.")

    # -------------------------------------------------------------------------
    # CHECK VISUAL TEST
    # MODIFICATO: check_visual_test ora ritorna una tupla (risultati_vt,
    # documenti_supporto) invece di una lista sola.
    # -------------------------------------------------------------------------
    pdf_vt = trova_file_per_estensione(CARTELLA_VT, [".pdf"])

    print("\n" + "="*60)
    print("AGENT 3 — CHECK VISUAL TEST")
    print("="*60)

    risultati_vt = []
    documenti_supporto = []
    if pdf_vt:
        risultati_vt, documenti_supporto = check_visual_test(pdf_vt, DATA_PRODUZIONE)

        for r in risultati_vt:
            print(f"\n{'='*60}")
            print(f"FILE: {r['file']}")
            print(f"{'='*60}")
            print(r["analisi"])

        if documenti_supporto:
            print(f"\n{'='*60}")
            print("DOCUMENTI DI SUPPORTO (qualifiche operatore, non report VT)")
            print(f"{'='*60}")
            for r in documenti_supporto:
                print(f"\n--- FILE: {r['file']} ---")
                print(r["analisi"])
    else:
        print("Nessun PDF trovato nella cartella 09_VT — check VT saltato.")

    # -------------------------------------------------------------------------
    # Salva report JSON per il supervisore
    # AGGIUNTA la chiave "documenti_supporto" rispetto alla versione precedente.
    # -------------------------------------------------------------------------
    report_agent3 = {
        "agente": "Agent3_Mockup_VT",
        "risultati_mockup": risultati_mockup,
        "risultati_vt": risultati_vt,
        "documenti_supporto": documenti_supporto
    }
    output_json = str(BASE_DIR / "report_agents" / "report_agent3.json")
    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(report_agent3, f, ensure_ascii=False, indent=2)
    print(f"\n[OUTPUT] Report JSON salvato in: {output_json}")