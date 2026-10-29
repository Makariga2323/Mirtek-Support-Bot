def format_id(prefix: str, number: int) -> str:
    return f"{prefix}-{number}"


def parse_id(prefix: str, value) -> int | None:
    if not isinstance(value, str) or not value.startswith(prefix + "-"):
        return None
    tail = value[len(prefix) + 1:]
    if not tail.isascii() or not tail.isdigit() or len(tail) > 15:
        return None
    return int(tail)