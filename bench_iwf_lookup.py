import concurrent.futures
import statistics
import time
import uuid

import requests

IWF_API_URL = "https://www.iwofurn.com/addvityapi/api/Documents/FindDocuments"
IWF_API_EMAIL = "Testapi.Wetzel@iwofurn.com"
IWF_API_PASSWORD = "IWOfurn2025!"
IWF_MESSAGE_TYPE = "ORDERS"
IWF_SUPPLIER_GLN = "4031865000009"
IWF_BUYER_GLN = "4260129840000"
DOCUMENT_NO = "401152717"
TOTAL = 100
TIMEOUT = 60


def one_call(i: int):
    payload = {
        "RequestOID": str(uuid.uuid4()),
        "Email": IWF_API_EMAIL,
        "Password": IWF_API_PASSWORD,
        "MessageType": IWF_MESSAGE_TYPE,
        "SupplierGLN": IWF_SUPPLIER_GLN,
        "BuyerGLN": IWF_BUYER_GLN,
        "DocumentNo": DOCUMENT_NO,
    }

    started = time.perf_counter()
    r = requests.post(IWF_API_URL, json=payload, timeout=TIMEOUT)
    elapsed = time.perf_counter() - started
    r.raise_for_status()

    try:
        body = r.json()
    except Exception:
        body = {"non_json": True}

    return {
        "index": i,
        "seconds": round(elapsed, 3),
        "status_code": r.status_code,
        "keys": list(body.keys()) if isinstance(body, dict) else type(body).__name__,
    }


def main():
    started = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=TOTAL) as pool:
        results = list(pool.map(one_call, range(1, TOTAL + 1)))
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
