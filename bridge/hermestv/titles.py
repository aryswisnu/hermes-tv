import re


def _norm(s: str) -> str:
    return re.sub(r"\W+", "", s.casefold())
