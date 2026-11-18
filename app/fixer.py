"""The PDF fix itself: rebuild a PDF so CorelDRAW can import it.

Pipeline (each step runs as a separate process with a timeout):
  1. qpdf      - repair the file structure and check it is a real PDF
  2. Ghostscript (pdfwrite) - rewrite to an old, simple PDF version,
     optionally turn all text into curves, keep colours unchanged (CMYK stays CMYK),
     use the CropBox as the page size
  3. qpdf      - final clean-up: no object streams, fixed PDF version
  4. pdfium     - small PNG previews of page 1 (before and after)
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

# CorelDRAW version presets -> PDF version to write.
# Older CorelDRAW versions are the strictest, so they get PDF 1.4.
PRESETS: dict[str, dict] = {
    "x4-x7": {"label": "CorelDRAW X4 to X7", "pdf_version": "1.4"},
    "2017-2020": {"label": "CorelDRAW 2017 to 2020 (incl. version 20 / 2018)", "pdf_version": "1.4"},
    "2021+": {"label": "CorelDRAW 2021 or newer", "pdf_version": "1.6"},
}
DEFAULT_PRESET = "2017-2020"

STEP_TIMEOUT = 180  # seconds per external command


class FixError(Exception):
    """A problem we can explain to the user."""


@dataclass
class FixResult:
    output: Path
    preview_before: Path | None
    preview_after: Path | None
    pages: int


def _run(cmd: list[str], timeout: int = STEP_TIMEOUT) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise FixError("This PDF took too long to process. Try a smaller file.") from e


def check_tools() -> list[str]:
    """Return the names of any missing command-line tools."""
    return [t for t in ("gs", "qpdf", "pdfinfo") if shutil.which(t) is None]


def page_count(pdf: Path) -> int:
    r = _run(["pdfinfo", str(pdf)], timeout=30)
    for line in r.stdout.splitlines():
        if line.startswith("Pages:"):
            return int(line.split()[1])
    return 0


def _preview(pdf: Path, out_stem: Path) -> Path | None:
    """Render page 1 as a small PNG with pdfium (Chrome's PDF engine), which shows
    CMYK colours the same way for the original and the fixed file.
    Previews are optional, so any failure is ignored."""
    png = out_stem.with_suffix(".png")
    try:
        import pypdfium2 as pdfium
        doc = pdfium.PdfDocument(str(pdf))
        page = doc[0]
        width = page.get_width() or 1
        page.render(scale=700 / width).to_pil().convert("RGB").save(png, optimize=True)
        doc.close()
        return png
    except Exception:
        return None


def fix_pdf(src: Path, workdir: Path, preset: str = DEFAULT_PRESET,
            keep_text: bool = False, max_pages: int = 300) -> FixResult:
    if preset not in PRESETS:
        preset = DEFAULT_PRESET
    version = PRESETS[preset]["pdf_version"]

    with open(src, "rb") as f:
        if b"%PDF" not in f.read(1024):
            raise FixError("This doesn't look like a PDF file.")

    # 1. Repair structure (qpdf exit code 3 = repaired with warnings, which is fine)
    repaired = workdir / "1_repaired.pdf"
    r = _run(["qpdf", "--decrypt", str(src), str(repaired)])
    if r.returncode not in (0, 3) or not repaired.exists():
        if "password" in (r.stderr or "").lower():
            raise FixError("This PDF is password-protected. Remove the password and try again.")
        raise FixError("This PDF is too damaged to read.")

    pages = page_count(repaired)
    if pages == 0:
        raise FixError("This PDF has no pages we can read.")
    if pages > max_pages:
        raise FixError(f"This PDF has {pages} pages; the limit is {max_pages}.")

    # 2. Ghostscript rewrite
    rewritten = workdir / "2_gs.pdf"
    gs = ["gs", "-q", "-dNOPAUSE", "-dBATCH", "-dSAFER",
          "-sDEVICE=pdfwrite", f"-dCompatibilityLevel={version}",
          "-dPDFSETTINGS=/prepress",
          "-sColorConversionStrategy=LeaveColorUnchanged",
          "-dUseCropBox", "-dAutoRotatePages=/None",
          # Print files: keep every image exactly as it is - no downsampling,
          # no re-compression of lossless images into JPEG.
          "-dDownsampleColorImages=false", "-dDownsampleGrayImages=false",
          "-dDownsampleMonoImages=false",
          "-dAutoFilterColorImages=false", "-dColorImageFilter=/FlateEncode",
          "-dAutoFilterGrayImages=false", "-dGrayImageFilter=/FlateEncode",
          "-dPassThroughJPEGImages=true",
          f"-sOutputFile={rewritten}"]
    if not keep_text:
        gs.insert(5, "-dNoOutputFonts")  # all text becomes curves
    gs.append(str(repaired))
    r = _run(gs)
    if r.returncode != 0 or not rewritten.exists() or rewritten.stat().st_size == 0:
        raise FixError("We couldn't rebuild this PDF. Please send it to us so we can improve the fix.")

    # 3. Final structural clean-up
    final = workdir / "fixed.pdf"
    r = _run(["qpdf", "--object-streams=disable", f"--force-version={version}",
              str(rewritten), str(final)])
    if r.returncode not in (0, 3) or not final.exists():
        raise FixError("We couldn't finish rebuilding this PDF.")

    # 4. Previews
    before = _preview(repaired, workdir / "before")
    after = _preview(final, workdir / "after")

    for tmp in (repaired, rewritten):
        tmp.unlink(missing_ok=True)

    return FixResult(output=final, preview_before=before, preview_after=after, pages=pages)
