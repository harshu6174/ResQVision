# Backend guide (for someone who has never built one)

## 1. What a backend is

Your analysis code is Python. A web page runs in the browser and cannot run Python. So you need a program that
stays running on a computer, waits for requests from the page, runs the Python, and sends answers back. That program is the **backend**.

```
 Browser (web/)                         Backend (server.py)                    Your code
 ┌─────────────────────┐   request     ┌──────────────────────┐   calls      ┌───────────────────┐
 │ index.html          │ ────────────► │ FastAPI              │ ───────────► │ damage_analyzer/  │
 │ styles.css          │               │  - checks the input  │              │  align, scoring,  │
 │ app.js              │ ◄──────────── │  - runs the job      │ ◄─────────── │  pipeline ...     │
 └─────────────────────┘   JSON/JPEG   │  - remembers results │   results    └───────────────────┘
                                        └──────────────────────┘
```

The two sides only talk through **URLs** (the "API"). That is the whole idea. Everything else is detail.

## 2. Run it

```bash
pip install -r requirements.txt
python server.py
```
Open http://127.0.0.1:8000. Press **Analyze damage** with "Demo town" selected.
Open http://127.0.0.1:8000/docs for a page FastAPI builds automatically, where you can try every URL by clicking.

The old Streamlit app is now `legacy_streamlit_app.py`. If you see the old look, you started that one by mistake. Nothing in `damage_analyzer/` was changed.

## 3. The URLs (the API)

| Method + URL | What it does |
|---|---|
| `POST /api/runs` | Start an analysis. Send the images and settings. Returns a `run_id` at once. |
| `GET /api/runs/{id}` | Ask how it is going: `running` with a message, then `done` with the summary, or `error`. |
| `GET /api/runs/{id}/sites` | The ranked list of damaged sites as JSON. |
| `GET /api/runs/{id}/images/{pre|post|overlay|heat}.jpg` | The pictures. |
| `GET /api/runs/{id}/sites/{rank}/crop/{pre|post|heat}.jpg` | A close-up of one site. |
| `GET /api/runs/{id}/route?k=8` | The field route. |
| `GET /api/runs/{id}/exports/{csv|geojson|kml|report}` | Downloads. |
| `GET /api/runs/{id}/map` | The interactive map page. |
| `GET /api/runs/{id}/evaluation`, `POST .../truth` | Accuracy numbers, and uploading labels. |

GET means "give me something". POST means "here is data, do something with it".
Try it yourself in a second terminal while the server runs:

```bash
curl -X POST -F source=demo http://127.0.0.1:8000/api/runs        # -> {"run_id":"ab12...","status":"running"}
curl http://127.0.0.1:8000/api/runs/ab12...                       # -> status, then the summary
curl http://127.0.0.1:8000/api/runs/ab12.../sites                 # -> the ranked list
```

## 4. Why the analysis runs in the background

An analysis can take many seconds on a big image. If the POST waited for it, the browser would look frozen and
could time out. So `start_run` does three things only: check the input, start a **thread** that runs `run_job`, and return a `run_id`.
`app.js` then asks `GET /api/runs/{id}` every half second and shows the `stage` text on the button ("Aligning images…").
That pattern, *start, then poll*, is how most real systems handle slow work.

## 5. A tour of `server.py` (read it in this order)

1. **`Run` and `RUNS`**: a Python dictionary that remembers each analysis by its id. The server keeps the last 8.
2. **`save_upload`**: copies an uploaded file to a temporary folder. It checks the file type and size, and never uses the
   user's file name as a path. This is where "never trust input from the browser" shows up in code.
3. **`run_job`**: runs `analyze()` from your pipeline and stores the result on the `Run`. Errors become a message the page can show.
4. **`start_run`**: the `POST /api/runs` door. The `Form(...)` parameters are the settings. FastAPI validates them for you,
   so `minor=7` is refused before your code runs.
5. **The `GET` functions**: each one finds the run, then calls a function you already had (`exporters.to_csv`, `plan_route`, `render_overlay` ...).
   This is why the backend is only about 350 lines. It is glue.
6. **The last lines**: `app.mount("/", StaticFiles(...))` serves the `web/` folder, so one server provides both the page and the API.

## 6. Add your own endpoint (a good first exercise)

Say you want the number of P1 sites. Add this to `server.py`, above the `app.mount(...)` line:

```python
@app.get("/api/runs/{run_id}/p1-count")
def p1_count(run_id: str):
    res = get_run(run_id).result
    return {"p1": int((res.df.tier == "P1").sum())}
```
Restart the server and visit `/docs`. The new URL is there. Calling it from the page is one `api.json(...)` line in `app.js`.

## 7. Things that will go wrong, and what they mean

| You see | Cause |
|---|---|
| `ModuleNotFoundError: fastapi` | `pip install -r requirements.txt` was not run in this Python. |
| Page says "Cannot reach the server" | `python server.py` is not running, or you opened `index.html` by double-clicking. Use the address http://127.0.0.1:8000. |
| "Unknown run. The server may have restarted." | Runs live in memory. Restarting the server forgets them. Analyze again. |
| Address already in use | Another program has port 8000. Run `PORT=8001 python server.py` (Windows PowerShell: `$env:PORT=8001; python server.py`). |
| Satellite map is blank | It loads Esri tiles from the internet. Check your connection. |
| Fonts look different | The page loads *Schibsted Grotesk* from Google Fonts. Offline, it falls back to your system font. |

## 8. What this version does not do (be honest about it when you present)

- **No database.** Results are in memory and disappear on restart. To keep them, save `res.df` to SQLite or Postgres and the images to disk.
- **No login.** Anyone who can reach the server can use it. It listens only on your own computer (`127.0.0.1`) by default. Do not set `HOST=0.0.0.0` on a public network without adding authentication.
- **One process.** Two analyses at a time are allowed. For many users you would use a job queue (Celery or RQ) and several workers.
- **Not deployed.** To put it online, run it on a small cloud server behind HTTPS (Render, Railway, Fly.io, or a VPS), and keep uploads limited.

## 9. Tests

```bash
python -m pytest -q tests          # the old 12 tests, plus the API tests in tests/test_api.py
```
