def valuta_severita(giorni_scaduto):
    """
    Valuta la severità di una qualifica scaduta.
    Regola: se scaduta da più di 90 giorni -> STOP
            altrimenti -> ATTENZIONE
    """
    if giorni_scaduto > 90:
        return "STOP"
    else:
        return "ATTENZIONE"