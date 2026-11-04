"""
RecipeScreen – lokaler Server

Liefert die Web-App aus (wie "python -m http.server") und bietet zusätzlich
/api/fetch?url=... an: Der Server ruft eine Rezeptseite ab und gibt die
Rezeptdaten (schema.org/Recipe), die Seitenbeschreibung und den lesbaren
Text zurück. Der Abruf passiert nur auf Wunsch (ein Link pro Rezept),
ist als RecipeScreen gekennzeichnet und folgt keinen weiteren Links.

YouTube: Keys in config.json (steht in .gitignore, landet nicht im Repo):
    {
      "youtube_api_key": "AIza...",   Titel + Beschreibung (YouTube Data API)
      "gemini_api_key":  "AIza...",   Zubereitung aus dem Video (Gemini)
      "gemini_model":    "gemini-3.6-flash"   optional
    }
Alternativ als Umgebungsvariablen YOUTUBE_API_KEY / GEMINI_API_KEY.
Beide sind optional; mit beiden bekommt Claude Beschreibung UND Videoinhalt.

Start:  python server.py          (Port 8080)
        python server.py 9000     (anderer Port)
"""

import ipaddress
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
ROOT = Path(__file__).resolve().parent

MAX_DOWNLOAD = 5 * 1024 * 1024   # höchstens 5 MB pro Seite laden
MAX_TEXT = 30000                 # höchstens so viele Zeichen Text an Claude
TIMEOUT = 15                     # Sekunden

# Ehrlich gekennzeichnet: Der Server gibt sich nicht als Browser aus.
# Seiten, die diesen Abruf sperren, werden respektiert – dann den
# Rezepttext beim Senden mit einfügen.
USER_AGENT = "RecipeScreen/1.0 (private Kuechenanzeige)"


class PageParser(HTMLParser):
    """Sammelt JSON-LD-Blöcke, Meta-Beschreibungen und sichtbaren Text."""

    SKIP = {"script", "style", "noscript", "svg", "template", "head"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.jsonld = []
        self.meta = {}
        self.text = []
        self._skip_depth = 0
        self._in_jsonld = False
        self._jsonld_buf = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "script" and (a.get("type") or "").lower() == "application/ld+json":
            self._in_jsonld = True
            self._jsonld_buf = []
        if tag == "meta":
            key = (a.get("property") or a.get("name") or "").lower()
            if key in ("og:title", "og:description", "description", "twitter:description") and a.get("content"):
                self.meta[key] = a["content"]
        if tag in self.SKIP:
            self._skip_depth += 1
        if tag in ("p", "li", "br", "h1", "h2", "h3", "h4", "div", "tr"):
            self.text.append("\n")

    def handle_endtag(self, tag):
        if tag == "script" and self._in_jsonld:
            self._in_jsonld = False
            self.jsonld.append("".join(self._jsonld_buf))
        if tag in self.SKIP and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._in_jsonld:
            self._jsonld_buf.append(data)
        elif self._skip_depth == 0:
            self.text.append(data)


def find_recipes(node):
    """Sucht in JSON-LD (auch verschachtelt / @graph) nach Objekten vom Typ Recipe."""
    found = []
    if isinstance(node, list):
        for item in node:
            found += find_recipes(item)
    elif isinstance(node, dict):
        t = node.get("@type")
        types = t if isinstance(t, list) else [t]
        if "Recipe" in types:
            found.append(node)
        for value in node.values():
            if isinstance(value, (list, dict)):
                found += find_recipes(value)
    return found


def is_public_host(host):
    """Nur öffentliche Adressen erlauben – kein Zugriff auf Router, NAS usw. im Heimnetz."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
    return True


class NoPrivateRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        host = urllib.parse.urlparse(newurl).hostname or ""
        if not is_public_host(host):
            raise urllib.error.URLError("Weiterleitung auf eine private Adresse blockiert")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


opener = urllib.request.build_opener(NoPrivateRedirects)


# ---------------------------------------------------------------------------
# YouTube über die offizielle Data API
# ---------------------------------------------------------------------------

def load_config():
    path = ROOT / "config.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"Warnung: config.json nicht lesbar ({e})")
    return {}


def youtube_api_key():
    # Bei jedem Abruf neu lesen – so wirkt ein neu eingetragener Key ohne Neustart.
    return os.environ.get("YOUTUBE_API_KEY") or load_config().get("youtube_api_key") or ""


YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}
YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def youtube_video_id(url):
    """Erkennt watch?v=, youtu.be/, /shorts/, /live/ und /embed/."""
    p = urllib.parse.urlparse(url)
    host = (p.hostname or "").lower()
    if host not in YOUTUBE_HOSTS:
        return None
    if host == "youtu.be":
        candidate = p.path.strip("/").split("/")[0]
    else:
        candidate = urllib.parse.parse_qs(p.query).get("v", [""])[0]
        if not candidate:
            parts = p.path.strip("/").split("/")
            if len(parts) >= 2 and parts[0] in ("shorts", "live", "embed"):
                candidate = parts[1]
    return candidate if YOUTUBE_ID.match(candidate or "") else None


# ---------------------------------------------------------------------------
# Gemini: wertet öffentliche YouTube-Videos aus (offizielle Google-Funktion)
# ---------------------------------------------------------------------------

GEMINI_TIMEOUT = 180  # Videoanalyse dauert länger als ein Seitenabruf
GEMINI_DEFAULT_MODEL = "gemini-3.6-flash"

GEMINI_PROMPT = (
    "Das ist ein Kochvideo. Schreib alle Zutaten mit Mengen und alle Zubereitungsschritte "
    "in der richtigen Reihenfolge auf, die im Video gesagt, eingeblendet oder gezeigt werden – "
    "auch Zeiten, Temperaturen und Tipps. Nur was im Video vorkommt, nichts erfinden. "
    "Wenn eine Menge nur zu sehen, aber nicht genannt ist, schätze sie und markiere sie mit (geschätzt). "
    "Wenn das Video kein Rezept zeigt, antworte nur mit: KEIN REZEPT"
)


def gemini_api_key():
    return os.environ.get("GEMINI_API_KEY") or load_config().get("gemini_api_key") or ""


def gemini_model():
    return load_config().get("gemini_model") or GEMINI_DEFAULT_MODEL


def collect_texts(node, out):
    """Sammelt alle Textbausteine aus der Antwort, egal wie tief verschachtelt."""
    if isinstance(node, dict):
        if node.get("type") == "text" and isinstance(node.get("text"), str):
            out.append(node["text"])
        for value in node.values():
            if isinstance(value, (dict, list)):
                collect_texts(value, out)
    elif isinstance(node, list):
        for item in node:
            collect_texts(item, out)
    return out


class GeminiBusy(Exception):
    """Gemini ist überlastet (HTTP 503) – später oder mit anderem Modell erneut versuchen."""


# Ausweichmodelle, falls das gewünschte Modell überlastet ist
GEMINI_FALLBACK_MODELS = ["gemini-3.8-flash", "gemini-3.5-flash-lite"]


def analyze_video_with_gemini(video_url, key):
    models = [gemini_model()] + [m for m in GEMINI_FALLBACK_MODELS if m != gemini_model()]
    last_error = None
    for model in models:
        for attempt in range(2):  # pro Modell: ein Versuch + eine Wiederholung
            try:
                text = gemini_request(model, video_url, key)
                if model != models[0]:
                    print(f"Gemini: Ausweichmodell {model} verwendet")
                return text
            except GeminiBusy as e:
                last_error = e
                print(f"Gemini {model} überlastet (Versuch {attempt + 1}) …")
                if attempt == 0:
                    time.sleep(4)
    raise ValueError(f"Gemini ist gerade überlastet, alle Modelle versucht ({last_error})")


def gemini_request(model, video_url, key):
    body = json.dumps({
        "model": model,
        "input": [
            {"type": "text", "text": GEMINI_PROMPT},
            {"type": "video", "uri": video_url},
        ],
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://generativelanguage.googleapis.com/v1beta/interactions",
        data=body,
        headers={"x-goog-api-key": key, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=GEMINI_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = json.loads(e.read().decode("utf-8"))["error"]["message"]
        except Exception:  # noqa: BLE001
            pass
        if e.code == 503:
            raise GeminiBusy(detail) from None
        raise ValueError(f"Gemini-Fehler {e.code} ({model}): {detail}".strip()) from None

    if isinstance(data.get("output_text"), str):
        return data["output_text"].strip()
    # Nur die Antwort auswerten, nicht die mitgeschickte Eingabe
    answer = {k: v for k, v in data.items() if k not in ("input", "model")}
    return "\n".join(collect_texts(answer, [])).strip()


def youtube_result(video_id):
    """Beschreibung (YouTube-API) und Videoinhalt (Gemini) – je nachdem, welche Keys da sind."""
    url = f"https://www.youtube.com/watch?v={video_id}"
    result = {"url": url, "source": "youtube", "recipes": [], "meta": {}, "text": ""}
    parts, notes = [], []

    # Videoinhalt zuerst – er ist am wichtigsten und soll bei langen
    # Beschreibungen nicht abgeschnitten werden.
    gem_key = gemini_api_key()
    if gem_key:
        print(f"Gemini analysiert Video {video_id} …")
        try:
            analysis = analyze_video_with_gemini(url, gem_key)
            if analysis:
                parts.append("Inhalt des Videos (von Gemini aus Bild und Ton ausgewertet):\n" + analysis)
        except Exception as e:  # noqa: BLE001 – Beschreibung trotzdem nutzen
            print(f"Gemini fehlgeschlagen: {e}")
            notes.append(f"Videoanalyse fehlgeschlagen: {e}")

    yt_key = youtube_api_key()
    if yt_key:
        try:
            info = fetch_youtube(video_id, yt_key)
            result["meta"] = info["meta"]
            parts.append(info["text"])
        except Exception as e:  # noqa: BLE001 – Videoanalyse trotzdem nutzen
            print(f"YouTube-API fehlgeschlagen: {e}")
            notes.append(f"Videobeschreibung nicht abrufbar: {e}")

    if not parts:
        raise ValueError("; ".join(notes) or "Keine Daten zum Video")

    result["text"] = "\n\n".join(parts + notes)[:MAX_TEXT]
    return result


def fetch_youtube(video_id, key):
    api = "https://www.googleapis.com/youtube/v3/videos?" + urllib.parse.urlencode(
        {"part": "snippet", "id": video_id, "key": key}
    )
    try:
        with urllib.request.urlopen(api, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = json.loads(e.read().decode("utf-8"))["error"]["message"]
        except Exception:  # noqa: BLE001
            pass
        raise ValueError(f"YouTube-API-Fehler {e.code}: {detail}".strip()) from None

    items = data.get("items") or []
    if not items:
        raise ValueError("YouTube-Video nicht gefunden (privat oder gelöscht?)")
    s = items[0]["snippet"]
    return {
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "source": "youtube",
        "recipes": [],
        "meta": {"og:title": s.get("title", ""), "channel": s.get("channelTitle", "")},
        "text": ("Videobeschreibung:\n" + s.get("description", ""))[:MAX_TEXT],
    }


# ---------------------------------------------------------------------------
# Abruf einer Seite
# ---------------------------------------------------------------------------

def fetch_page(url):
    video_id = youtube_video_id(url)
    if video_id and (youtube_api_key() or gemini_api_key()):
        return youtube_result(video_id)

    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Nur http- und https-Links sind erlaubt")
    if not is_public_host(parsed.hostname):
        raise ValueError("Adresse nicht erlaubt")

    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "de-DE,de;q=0.9,en;q=0.5",
    })
    with opener.open(req, timeout=TIMEOUT) as resp:
        raw = resp.read(MAX_DOWNLOAD)
        charset = resp.headers.get_content_charset() or "utf-8"
        final_url = resp.geturl()

    html = raw.decode(charset, errors="replace")
    parser = PageParser()
    parser.feed(html)

    recipes = []
    for block in parser.jsonld:
        try:
            recipes += find_recipes(json.loads(block))
        except json.JSONDecodeError:
            pass

    lines = [" ".join(line.split()) for line in "".join(parser.text).splitlines()]
    text = "\n".join(line for line in lines if line)

    return {
        "url": final_url,
        "source": "page",
        "recipes": recipes,
        "meta": parser.meta,
        "text": text[:MAX_TEXT],
    }


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/fetch":
            url = urllib.parse.parse_qs(parsed.query).get("url", [""])[0]
            try:
                self.send_json(200, fetch_page(url))
            except urllib.error.HTTPError as e:
                self.send_json(502, {"error": f"Seite antwortet mit HTTP {e.code}"})
            except Exception as e:  # noqa: BLE001 – Fehlertext geht an die App
                self.send_json(502, {"error": str(e)})
            return
        super().do_GET()

    def send_json(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"RecipeScreen läuft auf Port {PORT}")
    print("YouTube-API-Key: " + ("gefunden" if youtube_api_key() else "fehlt (config.json) – keine Videobeschreibung"))
    print("Gemini-API-Key:  " + (f"gefunden, Modell {gemini_model()}" if gemini_api_key() else "fehlt (config.json) – keine Videoanalyse"))
    print(f"Am Bildschirm öffnen: http://<IP-dieses-PCs>:{PORT}/   (IP mit 'ipconfig' herausfinden)")
    print("Beenden mit Strg+C")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
