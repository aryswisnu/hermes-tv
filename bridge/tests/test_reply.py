from hermestv.reply import Candidate, parse_candidates

REPLY = """Here are three good horror films for tonight.

```json
{"films": [
   {"title": "His House", "year": 2020, "why": "Tense horror about refugees."},
   {"title": "The Conjuring", "year": 2013, "why": "A haunted-house classic."},
   {"title": "Unknown Year", "year": null, "why": "No year given."},
   {"id": "vid00000001", "title": "Cold Case", "why": "Neat."}
 ]}
```"""


def test_parse_json_block():
    assert parse_candidates(REPLY) == [
        Candidate("His House", 2020, "Tense horror about refugees."),
        Candidate("The Conjuring", 2013, "A haunted-house classic."),
        Candidate("Unknown Year", None, "No year given."),
        Candidate("Cold Case", None, "Neat.", id="vid00000001"),
    ]


def test_parse_uses_last_json_block_and_skips_bad_films():
    text = (
        "```json\n{\"films\": [{\"title\": \"Old\", \"year\": 2001}]}\n```\n"
        "```json\n{\"films\": [{\"year\": 2001}, {\"title\": \"Ok\", \"year\": \"x\"}, {\"title\": \"B\", \"year\": true}]}\n```"
    )
    assert parse_candidates(text) == [Candidate("Ok", None, ""), Candidate("B", None, "")]


def test_parse_without_json_is_empty():
    assert parse_candidates("Four. 2 + 2 = 4.") == []


def test_parse_broken_json_is_empty():
    assert parse_candidates('Here you go.\n```json\n{"films": [\n```') == []


def test_parse_films_not_a_list_is_empty():
    assert parse_candidates('```json\n{"films": {"title": "X"}}\n```') == []
    assert parse_candidates('```json\n["X"]\n```') == []
