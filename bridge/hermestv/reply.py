import json
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Candidate:
    title: str
    year: int | None
    why: str
    id: str | None = None  # a YouTube video id, when Hermes gives one


JSON_BLOCK = re.compile(r"```json\s*(.*?)```", re.DOTALL)


def _text(value) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _candidate(f) -> Candidate | None:
    if not isinstance(f, dict) or not (_text(f.get("title")) or _text(f.get("id"))):
        return None
    year = f.get("year")
    if not isinstance(year, int) or isinstance(year, bool):
        year = None
    return Candidate(title=_text(f.get("title")) or "", year=year, why=_text(f.get("why")) or "",
                     id=_text(f.get("id")))


def parse_candidates(text: str) -> list[Candidate]:
    """The "films" of the last ```json block in a Hermes reply; [] when there is none."""
    blocks = JSON_BLOCK.findall(text)
    if not blocks:
        return []
    try:
        data = json.loads(blocks[-1])
    except ValueError:
        return []
    if not isinstance(data, dict) or not isinstance(data.get("films"), list):
        return []
    return [c for c in map(_candidate, data["films"]) if c is not None]
