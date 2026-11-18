import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

# ===== SETTINGS =====
WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
TIMEZONE = "America/New_York"
START_HOUR = 7    # first post at 7:00 AM Eastern
END_HOUR = 19     # last post at 7:00 PM Eastern
TRANSLATION = "web"  # "web" = World English Bible, "kjv" = King James
BOT_NAME = "Scripture Reading"
EMBED_COLOR = 0x5865F2
PROGRESS_FILE = "progress.json"
EXPLANATIONS_FILE = "explanations.json"
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODELS = ["gemini-flash-lite-latest", "gemini-flash-latest", "gemini-2.5-flash-lite", "gemini-2.5-flash"]

# Genesis to Revelation: (book id, chapter count)
BOOKS = [
    ("GEN", 50), ("EXO", 40), ("LEV", 27), ("NUM", 36), ("DEU", 34),
    ("JOS", 24), ("JDG", 21), ("RUT", 4), ("1SA", 31), ("2SA", 24),
    ("1KI", 22), ("2KI", 25), ("1CH", 29), ("2CH", 36), ("EZR", 10),
    ("NEH", 13), ("EST", 10), ("JOB", 42), ("PSA", 150), ("PRO", 31),
    ("ECC", 12), ("SNG", 8), ("ISA", 66), ("JER", 52), ("LAM", 5),
    ("EZK", 48), ("DAN", 12), ("HOS", 14), ("JOL", 3), ("AMO", 9),
    ("OBA", 1), ("JON", 4), ("MIC", 7), ("NAM", 3), ("HAB", 3),
    ("ZEP", 3), ("HAG", 2), ("ZEC", 14), ("MAL", 4),
    ("MAT", 28), ("MRK", 16), ("LUK", 24), ("JHN", 21), ("ACT", 28),
    ("ROM", 16), ("1CO", 16), ("2CO", 13), ("GAL", 6), ("EPH", 6),
    ("PHP", 4), ("COL", 4), ("1TH", 5), ("2TH", 3), ("1TI", 6),
    ("2TI", 4), ("TIT", 3), ("PHM", 1), ("HEB", 13), ("JAS", 5),
    ("1PE", 5), ("2PE", 3), ("1JN", 5), ("2JN", 1), ("3JN", 1),
    ("JUD", 1), ("REV", 22),
]


def http_json(url, payload=None):
    headers = {"User-Agent": "ScriptureReadingBot/1.0", "Content-Type": "application/json"}
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode("utf-8")
        return json.loads(body) if body else None


def call_gemini(prompt):
    # Tries each model in GEMINI_MODELS until one answers. Returns the reply text or "".
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "maxOutputTokens": 16000},
    }).encode("utf-8")
    for model in GEMINI_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json", "x-goog-api-key": GEMINI_API_KEY},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            parts = data["candidates"][0]["content"]["parts"]
            return "".join(p.get("text", "") for p in parts)
        except Exception as e:
            print(f"Gemini model {model} failed: {e}")
    return ""


def clean_sentence(sentence):
    sentence = " ".join(str(sentence).split())
    for dash in (" \u2014 ", "\u2014", " \u2013 "):
        sentence = sentence.replace(dash, ", ")
    return sentence


def load_explanations():
    if os.path.exists(EXPLANATIONS_FILE):
        with open(EXPLANATIONS_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_explanations(explanations):
    with open(EXPLANATIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(explanations, f, indent=2, ensure_ascii=False)
        f.write("\n")


def explain_verse(chapter_data, verse):
    # Gets one sentence per verse for the WHOLE chapter in a single free API call,
    # saves them, and reuses them for the rest of the chapter. Returns "" if unavailable.
    if not GEMINI_API_KEY:
        return ""
    key = f"{verse['book_id']} {verse['chapter']}"
    cache = load_explanations()

    if cache.get("chapter") != key or not cache.get("verses"):
        lines = "\n".join(
            f"{v['verse']}: {' '.join(v['text'].split())}" for v in chapter_data.get("verses", [])
        )
        prompt = (
            f"Here is {verse['book']} chapter {verse['chapter']}, one verse per line:\n\n{lines}\n\n"
            "For EVERY verse, write ONE short sentence (under 35 words) explaining its significance "
            "in plain, encouraging language for a general Christian audience. Use the surrounding "
            "verses for context when a verse is part of a story, list or genealogy. No em dashes, "
            "do not quote the verse. Return ONLY a JSON object mapping each verse number (as a "
            "string) to its sentence, like {\"1\": \"...\", \"2\": \"...\"}."
        )
        reply = call_gemini(prompt)
        try:
            start = reply.find("{")
            end = reply.rfind("}")
            verses = json.loads(reply[start:end + 1])
            verses = {str(k): clean_sentence(v) for k, v in verses.items() if str(v).strip()}
        except Exception as e:
            print(f"Could not read explanations: {e}")
            return ""
        if not verses:
            return ""
        cache = {"chapter": key, "verses": verses}
        save_explanations(cache)

    return cache["verses"].get(str(verse["verse"]), "")


def load_progress():
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"book": 0, "chapter": 1, "verse": 1, "count": 0}


def save_progress(progress):
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(progress, f, indent=2)
        f.write("\n")


def next_chapter(progress):
    book_id, chapters = BOOKS[progress["book"]]
    if progress["chapter"] < chapters:
        progress["chapter"] += 1
    else:
        progress["book"] = (progress["book"] + 1) % len(BOOKS)  # after Revelation, back to Genesis
        progress["chapter"] = 1
    progress["verse"] = 1


def find_next_verse(progress):
    # Walks forward from the saved spot until it finds a verse that exists.
    for _ in range(len(BOOKS) * 160):
        book_id, _chapters = BOOKS[progress["book"]]
        url = f"https://bible-api.com/data/{TRANSLATION}/{book_id}/{progress['chapter']}"
        try:
            chapter = http_json(url)
            verses = chapter.get("verses", [])
        except urllib.error.HTTPError as e:
            if e.code == 404:
                verses = []
            else:
                raise
        match = [v for v in verses if int(v["verse"]) >= progress["verse"]]
        if match:
            verse = min(match, key=lambda v: int(v["verse"]))
            translation = chapter.get("translation", {})
            name = translation.get("name", "") if isinstance(translation, dict) else str(translation)
            return verse, name, chapter
        next_chapter(progress)
    sys.exit("Could not find the next verse.")


def main():
    if not WEBHOOK_URL:
        sys.exit("DISCORD_WEBHOOK_URL is not set.")

    now = datetime.now(ZoneInfo(TIMEZONE))
    force = os.environ.get("FORCE_POST", "").lower() == "true"

    in_hours = START_HOUR <= now.hour < END_HOUR or (now.hour == END_HOUR and now.minute < 10)
    if not force and not in_hours:
        print(f"{now:%I:%M %p} is outside posting hours. Skipping.")
        return

    progress = load_progress()
    verse, translation_name, chapter_data = find_next_verse(progress)

    reference = f"{verse['book']} {verse['chapter']}:{verse['verse']}"
    text = " ".join(verse["text"].split())
    count = progress.get("count", 0) + 1
    explanation = explain_verse(chapter_data, verse)

    description = text
    if explanation:
        description = f"{text}\n\n💡 *{explanation}*"

    message = {
        "username": BOT_NAME,
        "embeds": [{
            "title": f"📖 {reference}",
            "description": description,
            "color": EMBED_COLOR,
            "footer": {"text": f"{translation_name} • Verse #{count:,}"},
        }],
    }
    http_json(WEBHOOK_URL, message)

    progress["verse"] = int(verse["verse"]) + 1
    progress["chapter"] = int(verse["chapter"])
    progress["count"] = count
    save_progress(progress)
    print(f"Posted {reference}")


if __name__ == "__main__":
    main()
