import re

from hermestv import config
from hermestv.catalog import Film
from hermestv.slots import Row, Slot
from hermestv.youtube import Video

# Every Hermes run of the bridge is a background run: a program reads the reply, nobody waits on it.
BACKGROUND_NOTE = """\
You are answering for Hermes TV, a shared living-room TV.
A program reads your reply, not a person. You have no tools: answer from what you know.
Reply only with the ```json block that the request asks for, in exactly that shape.
"""


def _hints(titles: list[str]) -> str:
    titles = [t for t in map(_plain, titles) if t]
    return f"Recently opened on this TV (taste hints): {', '.join(titles)}.\n" if titles else ""


def picks_note(slot: Slot, row: Row, exclude: list[str], candidates: list[Film], hints: list[str] = ()) -> str:
    noun = "series" if row == "series" else "films"
    listed = "\n".join(f"- {f.title} ({f.year})" for f in candidates)
    return f"""\
Choose up to 10 {noun} to watch now: {slot.ask}.
{_hints(hints)}Choose only from this list. Every title on it is on Netflix or HBO Max in {config.load().region} now:
{listed}
When enough other titles are on the list, do not choose any of these: {", ".join(exclude) or "none"}.
Answer from what you know. Do not run code, search, or use any tool.
Copy each title and year exactly as the list gives them.
"why" is one sentence of 18 words or fewer, in English.
Reply only with this ```json block:
```json
{{"films": [{{"title": "<title>", "year": <year>, "why": "<reason>"}}]}}
```
"""


def _plain(text: str, limit: int = 100) -> str:
    # Video titles come from strangers; no brackets or fences reach the prompt.
    return re.sub(r"[`{}\[\]<>]", "", text)[:limit].strip()


def youtube_note(slot: Slot, exclude: list[str], videos: list[Video], hints: list[str] = ()) -> str:
    listed = "\n".join(f"- [{v.id}] {_plain(v.title)} · {_plain(v.channel)} · {round((v.duration_s or 0) / 60)} min"
                       for v in videos)
    exclude = [_plain(t) for t in exclude]
    return f"""\
Choose up to 10 YouTube videos to watch now on a shared living-room TV: {slot.label.lower()}.
The viewers like videos that match these searches: {", ".join(config.load().youtube_queries)}.
{_hints(hints)}Never music, news clips or pranks. No graphic gore.
Choose only from this list (the video id is in brackets):
{listed}
When enough other videos are on the list, do not choose any of these: {", ".join(exclude) or "none"}.
Answer from what you know. Do not run code, search, or use any tool.
Copy each id and title exactly as the list gives them.
"why" is one sentence of 18 words or fewer, in English.
Reply only with this ```json block:
```json
{{"films": [{{"id": "<id>", "title": "<title>", "why": "<reason>"}}]}}
```
"""
