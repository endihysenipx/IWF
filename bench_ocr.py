import concurrent.futures
import statistics
import time
from pathlib import Path

from app.ocr_extractor import extract_data_from_scanned_pdf

PDF_PATH = Path("temp_incoming_ab.pdf")
TOTAL = 50


def one_run(i: int):
    pdf_bytes = PDF_PATH.read_bytes()
    started = time.perf_counter()
    result = extract_data_from_scanned_pdf(pdf_bytes)
    elapsed = time.perf_counter() - started

    return {
        "index": i,
        "seconds": round(elapsed, 3),
        "ok": not bool(result.get("error")) if isinstance(result, dict) else True,
        "error": result.get("error") if isinstance(result, dict) else None,
    }


def main():
    started = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=TOTAL) as pool:
        results = list(pool.map(one_run, range(1, TOTAL + 1)))
    wall = time.perf_counter() - started

    times = [r["seconds"] for r in results]
    print(f"total wall clock: {wall:.3f}s")
    print(f"min: {min(times):.3f}s")
    print(f"max: {max(times):.3f}s")
    print(f"avg: {statistics.mean(times):.3f}s")
    print()

    for r in results:
        print(r)


if __name__ == "__main__":
    main()
