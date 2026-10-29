class CoreError(Exception):
    """Ядро сломалось при обработки вопроса."""

class CoreTimeout(Exception):
    """Ядро не уложилось в определенное время."""