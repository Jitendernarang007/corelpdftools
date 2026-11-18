# CorelPDFtools

A website that fixes PDFs CorelDRAW won't open ("The file is corrupted"). Upload a PDF, download a CorelDRAW-ready copy:

- rebuilt as PDF 1.4 (1.6 for CorelDRAW 2021+)
- text turned into curves (optional "keep text editable")
- CMYK colours kept, page set to the trim (crop) size
- real vectors — pages are never turned into images
- files deleted after 1 hour

## Run it on your computer

Needs Python 3.10+, Ghostscript, qpdf and poppler-utils.

```bash
# Ubuntu / WSL
sudo apt install ghostscript qpdf poppler-utils
pip install -r requirements.txt
uvicorn app.main:app --reload
# open http://localhost:8000
```

Or with Docker (works the same on Windows, Mac, Linux):

```bash
docker build -t corelpdftools .
docker run -p 8000:8000 corelpdftools
```

## Deploy

The app needs a host that can run a **Docker container** (or install Ghostscript). Ordinary shared/cPanel web hosting usually **cannot** run it.

**Render** (easiest): push this folder to a GitHub repo → render.com → New → Web Service → pick the repo → Runtime: Docker → Create. Render builds the Dockerfile and gives you a URL. Add your domain under Settings → Custom Domains.

**Railway**: railway.app → New Project → Deploy from GitHub repo → it detects the Dockerfile. Add a domain under Settings → Networking.

**VPS** (DigitalOcean, Hetzner, Hostinger VPS): install Docker, then

```bash
git clone <your repo> && cd corelpdftools
docker build -t corelpdftools .
docker run -d --restart unless-stopped -p 8000:8000 -v $PWD/data:/srv/data corelpdftools
```

and put Caddy or Nginx in front for HTTPS.

## Settings (environment variables)

| Variable | Default | Meaning |
| --- | --- | --- |
| `MAX_UPLOAD_MB` | 20 | Largest upload |
| `FREE_DAILY_LIMIT` | 3 | Free fixes per IP per day (0 = unlimited) |
| `FILE_TTL_SECONDS` | 3600 | Files deleted after this many seconds |
| `MAX_PARALLEL` | 2 | Fixes running at the same time (raise on bigger servers) |
| `MAX_PAGES` | 300 | Largest page count |
| `DATA_DIR` | ./data | Where `feedback.jsonl` ("Did it open?" answers) is saved |

## How the fix works

See `app/fixer.py`. In short:

```bash
qpdf --decrypt in.pdf repaired.pdf
gs -dNOPAUSE -dBATCH -dSAFER -sDEVICE=pdfwrite -dCompatibilityLevel=1.4 -dNoOutputFonts \
   -dPDFSETTINGS=/prepress -sColorConversionStrategy=LeaveColorUnchanged -dUseCropBox \
   -sOutputFile=out.pdf repaired.pdf
qpdf --object-streams=disable --force-version=1.4 out.pdf fixed.pdf
```

## Before launch

- [ ] Replace `https://example.com/` (canonical link in `index.html`) and `hello@example.com` (`privacy.html`) with your domain and email
- [ ] Review the privacy page and add terms of use
- [ ] Licence: Ghostscript is AGPL. Simplest route is to keep this repo public under AGPL-3.0; otherwise buy a commercial licence from Artifex. Not legal advice.
- [ ] Brand name: "Corel" and "CorelDRAW" are trademarks of their owner. Saying the tool works *with* CorelDRAW is generally fine; using "Corel" in the brand/domain carries more risk.

## Next features (from the plan)

Batch upload (ZIP), Canva mode, font checker, Razorpay credits/Pro, print preflight report, RGB→CMYK, bleed and crop marks, EPS/SVG output, Hindi pages.
