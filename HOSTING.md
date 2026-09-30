# Host DoorBoard (judges open without Terminal)

**Why this is #1 remaining score leverage (with the video):** every judge can click one URL whether or not they read the repo.

DoorBoard is a standard Streamlit app. No API keys. Offline data in `doorboard/data/patients.json`.

## Option A — Streamlit Community Cloud (recommended)

1. Push **this `round2/` folder** as the root of a **public** GitHub repo  
   (or put `app.py` at the repo root — Cloud needs to find `app.py` + `requirements.txt`).
2. Go to [https://share.streamlit.io](https://share.streamlit.io) → **New app**.
3. Connect the repo.
4. Set:
   - **Main file path:** `app.py`
   - **Python version:** 3.11+ (3.12 fine)
5. Deploy. First boot trains/caches the risk model (~tens of seconds).
6. Copy the `*.streamlit.app` URL into:
   - [SHARE.md](SHARE.md) → replace the “Still to do → Host” line
   - [README.md](README.md) top callout
   - [PITCH.md](PITCH.md) Slide 1 / close
   - Contest submission form

### Repo root checklist

```
app.py
requirements.txt
doorboard/
.streamlit/config.toml   # optional; already present
README.md
```

`requirements.txt` already includes `streamlit`, `pandas`, `scikit-learn`, `numpy`.

## Option B — Local only (not “hosted”)

```bash
cd /path/to/round2
python3 -m pip install -r requirements.txt
python3 -m streamlit run app.py
```

http://localhost:8501 — fine for filming; **not** a judge link.

## After it is live

- Open the URL in an incognito window once (cold start).
- Sign in as charge nurse → Floor → Open spaces visible → Send works.
- Paste the URL next to the video in the submission package.

**Status:** Live at **https://doorboard-sap.streamlit.app/**
