"""Complaint and appearance parsing.

Replaces substring matching. Two things went wrong before:

  "no chest pain"     scored 22 points for chest pain
  "still talking"     escalated because "still" was an escalate word

So: match on word boundaries, and check a negation window before each hit.
Negation scope stops at a clause break, which is how clinical negation
detection (NegEx-style) normally works.
"""

from __future__ import annotations

import re


NEGATORS = (
    "no", "not", "never", "without", "denies", "denied", "negative",
    "doesnt", "doesn't", "didnt", "didn't", "isnt", "isn't", "wasnt",
    "wasn't", "none", "nil", "ruled out", "free of", "resolved",
)

# a negation does not carry past these
CLAUSE_BREAK = (" , ", " ; ", " . ", " ? ", " but ", " however ", " though ",
                " although ", " or ", " vs ")

HEDGES = ("maybe", "possible", "possibly", "unclear", "unsure", "query",
          "might", "could be", " or ", " vs ")

NEG_WINDOW = 4  # tokens


def _norm(s: str) -> str:
    s = (s or "").lower().strip()
    s = s.replace("\u2014", " ").replace("\u2013", " ").replace("-", " ")
    # "not sure" is a hedge, not a negation of what follows
    s = s.replace("not sure", "unsure").replace("n't sure", " unsure")
    # pad punctuation so word-boundary matching still sees "pale," as "pale"
    for ch in ",;.:!?":
        s = s.replace(ch, " " + ch + " ")
    return re.sub(r"\s+", " ", s).strip()


def _clauses(text: str) -> list[str]:
    out = [text]
    for br in CLAUSE_BREAK:
        nxt = []
        for chunk in out:
            nxt.extend(chunk.split(br))
        out = nxt
    return [c.strip() for c in out if c.strip()]


def negated(clause: str, term: str) -> bool:
    """True if term appears inside a negation scope in this clause."""
    toks = clause.split()
    tterms = term.split()
    n = len(toks)
    k = len(tterms)
    for i in range(n - k + 1):
        if toks[i:i + k] != tterms:
            continue
        lo = max(0, i - NEG_WINDOW)
        window = " ".join(toks[lo:i])
        for neg in NEGATORS:
            if re.search(r"(?:^|\s)" + re.escape(neg) + r"(?:\s|$)", window):
                return True
    return False


def find_terms(text: str, terms) -> dict:
    """{term: 'present'|'negated'} for terms that appear at word boundaries."""
    t = _norm(text)
    found = {}
    for term in terms:
        term = _norm(term)
        pat = r"(?:^|\s)" + re.escape(term) + r"(?:\s|$)"
        if not re.search(pat, t):
            continue
        state = "negated"
        for clause in _clauses(t):
            if re.search(pat, clause) and not negated(clause, term):
                state = "present"
                break
        found[term] = state
    return found


def hedged(text: str) -> bool:
    t = _norm(text)
    for h in HEDGES:
        if h.strip() and h.strip() in t:
            return True
    return False


ESCALATE_LOOK = {
    "sweaty": 8, "diaphoretic": 8, "clammy": 8, "pale": 8, "grey": 9,
    "cyanotic": 12, "mottled": 10, "working to breathe": 10, "retractions": 10,
    "grunting": 10, "lethargic": 10, "unresponsive": 14, "confused": 9,
    "drowsy": 8, "obtunded": 12, "not playing": 7, "floppy": 10,
    "dry lips": 5, "face puffy": 7, "limp": 2,
}

# words that only mean something bad in the right phrase
CONTEXT_LOOK = {
    "quiet": 6,
    "still": 5,
}
CONTEXT_SAFE_NEXT = ("talking", "walking", "alert", "playing", "smiling", "awake")

REASSURE_LOOK = ("walking", "talking", "comfortable", "playing", "alert", "smiling", "awake")


def look_features(text: str) -> dict:
    t = _norm(text)
    hits = find_terms(t, list(ESCALATE_LOOK))
    pts = 0.0
    present = []
    for term, state in hits.items():
        if state != "present":
            continue
        pts += ESCALATE_LOOK[term]
        present.append(term)

    # "quiet"/"still" only count when not immediately qualified by a
    # reassuring word: "still talking" is fine, "quiet, pale" is not
    for term, val in CONTEXT_LOOK.items():
        st = find_terms(t, [term]).get(term)
        if st != "present":
            continue
        m = re.search(r"(?:^|\s)" + term + r"\s+(\w+)", t)
        nxt = m.group(1) if m else ""
        if nxt in CONTEXT_SAFE_NEXT:
            continue
        pts += val
        present.append(term)

    reassure = [w for w, s in find_terms(t, REASSURE_LOOK).items() if s == "present"]
    pts -= 2.0 * len(reassure)
    pts = max(0.0, min(pts, 30.0))
    return {
        "points": pts,
        "concerning": bool(present),
        "terms": present,
        "reassuring": reassure,
        "negated": [k for k, v in hits.items() if v == "negated"],
    }


COMPLAINT_WEIGHT = {
    "cardiac arrest": 30, "unresponsive": 28, "collapse": 22,
    "stroke": 24, "weakness one side": 22, "face droop": 22,
    "chest pain": 22, "chest tightness": 20, "chest pressure": 20,
    "pressure in chest": 20, "tightness in chest": 20, "pain in chest": 22,
    "shortness of breath": 20, "difficulty breathing": 20, "asthma": 16,
    "seizure": 20, "altered": 18, "anaphylaxis": 24, "lips swelling": 20,
    "allergic reaction": 16, "poor feeding": 14, "syncope": 14,
    "vomiting": 10, "abdominal pain": 10, "stomach pain": 10,
    "high sugar": 14, "low sugar": 16, "hypoglycaemia": 16, "fever": 8, "fall": 8,
    "not keeping fluids": 12, "poor perfusion": 16, "confused": 16,
    "hip pain": 8, "headache": 6, "dizzy": 8, "tired": 6, "fatigue": 6,
    "burn": 8, "sore throat": 3, "rash": 3, "cut finger": 2, "sprain": 2,
    "fine": 2, "nothing wrong": 2,
}


def complaint_features(text: str, tags=None) -> dict:
    tags = list(tags or [])
    t = _norm(text)
    hits = find_terms(t, list(COMPLAINT_WEIGHT))
    pts = 4.0
    present = []
    for term, state in hits.items():
        if state != "present":
            continue
        present.append(term)
        if COMPLAINT_WEIGHT[term] > pts:
            pts = float(COMPLAINT_WEIGHT[term])

    ambiguous = "ambiguous" in tags or hedged(t)
    under = "under_report" in tags
    if ambiguous:
        pts += 8.0
    if under:
        pts = max(pts, 6.0)
    return {
        "points": pts,
        "terms": present,
        "negated": [k for k, v in hits.items() if v == "negated"],
        "ambiguous": ambiguous,
        "under_report": under,
        "high_risk": pts >= 16,
    }


def alert_from_look(text: str) -> bool:
    """AVPU proxy for the NEWS2 consciousness row."""
    f = look_features(text)
    for w in ("unresponsive", "obtunded", "confused", "lethargic", "drowsy", "floppy"):
        if w in f["terms"]:
            return False
    return True
