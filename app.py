
# ============================================================
# Ultra-Fast Mouser  BOM Tool (Streamlit)
# ============================================================

import os
import time
import json
import asyncio
import httpx
import random
import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor
import io
import chardet

# ------------------------------------------------------------
# Streamlit Config
# ------------------------------------------------------------
st.set_page_config(page_title="Mouser BOM Tool", layout="wide")
st.title("⚡ Ultra-Fast  Mouser  BOM Sourcing Dashboard")

load_dotenv()

# ------------------------------------------------------------
# Timeouts / Retries / Limits
# ------------------------------------------------------------
CONNECT_TIMEOUT = float(os.getenv("CONNECT_TIMEOUT", "5"))
READ_TIMEOUT = float(os.getenv("READ_TIMEOUT", "45"))
TIMEOUT = READ_TIMEOUT

MAX_RETRIES = int(os.getenv("MAX_RETRIES", "5"))
BACKOFF_BASE = float(os.getenv("BACKOFF_BASE", "1.2"))
mouser_semaphore = asyncio.Semaphore(3)
# ============================================================
# Async Rate Limiters + Helpers
# ============================================================

class AsyncRateLimiter:
    """
    Simple async rate limiter that ensures no more than
    N calls per minute. Each API (Mouser, Octopart) gets
    its own limiter so they can run in parallel at full speed.
    """
    def __init__(self, calls_per_minute: int):
        self.min_interval = 60.0 / max(calls_per_minute, 1)
        self.next_timestamp = 0.0

    async def wait(self):
        now = asyncio.get_event_loop().time()
        wait_for = self.next_timestamp - now
        if wait_for > 0:
            await asyncio.sleep(wait_for)
        self.next_timestamp = asyncio.get_event_loop().time() + self.min_interval


# Rate limits (per API)
mouser_limiter = AsyncRateLimiter(int(os.getenv("MOUSER_CALLS_PER_MINUTE", "10")))
##octopart_limiter = AsyncRateLimiter(100)  # Octopart free-tier default


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------
def parse_price(v):
    if v is None:
        return None
    try:
        return float(str(v).replace("$", "").replace(",", "").strip())
    except:
        return None


def compute_total(price, qty):
    p = parse_price(price)
    return round(p * qty, 4) if p is not None else None
# ============================================================
# Async Mouser Client (Ultra-fast async version)
# ============================================================

class AsyncMouserClient:
    SEARCH_URL = "https://api.mouser.com/api/v1/search/partnumber"

    def __init__(self, api_key: str):
        self.api_key = (api_key or "").strip()
        self.cache = {}
    ##
    
    async def search_part(self, client: httpx.AsyncClient, mpn: str):
        mpn = (mpn or "").strip()

        if mpn in self.cache:
            return self.cache[mpn]

        if not self.api_key:
            result = (None, [], "Missing MOUSER_API_KEY")
            self.cache[mpn] = result
            return result

        last_err = None

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                async with mouser_semaphore:        # ✅ concurrency control
                    await mouser_limiter.wait()     # ✅ rate limiter

                    resp = await client.post(
                        self.SEARCH_URL,
                        params={"apiKey": self.api_key},
                        headers={
                            "Content-Type": "application/json"   # ✅ FIX 302 redirect
                        },
                        json={
                            "SearchByPartRequest": {
                                "mouserPartNumber": mpn
                            }
                        },
                        timeout=TIMEOUT,
                        follow_redirects=True                    # ✅ STOP 302 "Object moved"
                    )

                # ---- Success ----
                if resp.status_code == 200:
                    try:
                        data = resp.json()
                    except Exception as e:
                        result = (None, [], f"Invalid JSON: {e}")
                        self.cache[mpn] = result
                        return result

                    parts = data.get("SearchResults", {}).get("Parts", []) or []
                    if not parts:
                        result = (None, [], "No results")
                        self.cache[mpn] = result
                        return result

                    main = parts[0]
                    alts = [p.get("ManufacturerPartNumber", "").strip()
                            for p in parts[1:]]

                    price_breaks = main.get("PriceBreaks", []) or []
                    unit_price = price_breaks[0].get("Price") if price_breaks else None

                    result = (
                        {
                            "price": unit_price,
                            "manufacturer": main.get("Manufacturer"),
                            "stock": main.get("Availability"),
                            "lifecycle": main.get("LifecycleStatus"),
                        },
                        alts,
                        None
                    )

                    self.cache[mpn] = result
                    return result

                # ---- Retryable server errors ----
                if resp.status_code in (429, 500, 502, 503, 504):
                    await asyncio.sleep((BACKOFF_BASE ** attempt) + random.random())
                    continue

                # ---- Permanent failure ----
                text = (resp.text or "").strip()[:200]
                result = (None, [], f"HTTP {resp.status_code}: {text}")
                self.cache[mpn] = result
                return result

            except Exception as e:
                last_err = e
                await asyncio.sleep((BACKOFF_BASE ** attempt) + random.random())

        # ---- Max retries exceeded ----
        result = (None, [], f"Mouser network error: {last_err}")
        self.cache[mpn] = result
        return result


# ============================================================
# BOM Processing
# ============================================================

mouser_client = AsyncMouserClient(os.getenv("MOUSER_API_KEY", ""))
##octopart_client = AsyncOctopartClient()

# ============================================================
# Async BOM Processing Engine (Parallel Mouser + Octopart)
# ============================================================

async def process_single_part(client, mouser_client, mpn, qty):
    """
    Process a single part by querying Mouser and Octopart concurrently.
    Mouser and Octopart calls run in parallel for maximum performance.
    """

    # Run both lookups concurrently
    mouser_task = asyncio.create_task(
        mouser_client.search_part(client, mpn)
    )
    

    mouser_data, mouser_alts, mouser_err = await mouser_task
    

    # Prepare structured output row
    r = {
        "Part Number": mpn,
        "Quantity": qty,

        # ---- Mouser ----
        "Mfr (Mouser)": None,
        "Lifecycle (Mouser)": None,
        "Stock (Mouser)": None,
        "Unit Price (Mouser)": None,
        "Total Price (Mouser)": None,
        "Alternates (Mouser)": ", ".join(mouser_alts or []),
        "Error (Mouser)": mouser_err,

        
    }

    # ---- Fill Mouser data ----
    if mouser_data and not mouser_err:
        r["Mfr (Mouser)"] = mouser_data.get("manufacturer")
        r["Lifecycle (Mouser)"] = mouser_data.get("lifecycle")
        r["Stock (Mouser)"] = mouser_data.get("stock")
        r["Unit Price (Mouser)"] = mouser_data.get("price")
        r["Total Price (Mouser)"] = compute_total(
            mouser_data.get("price"), qty
        )

   

    return r


async def process_bom_async(df):
    """
    Processes an entire BOM asynchronously.
    All parts are queried in parallel for maximum throughput.
    """

    mouser_client = AsyncMouserClient(os.getenv("MOUSER_API_KEY", ""))
    ##octo_client = AsyncOctopartClient()

    output_rows = []

    # Create a single shared httpx.AsyncClient session
    async with httpx.AsyncClient(verify=False) as client:

        tasks = []
        for _, row in df.iterrows():
            mpn = str(row["PartNumber"]).strip()
            qty = int(row["Quantity"])

            tasks.append(
                asyncio.create_task(
                    process_single_part(client, mouser_client, mpn, qty)
                )
            )

        # Run everything concurrently
        output_rows = await asyncio.gather(*tasks)

    return output_rows

   # ============================================================
# Streamlit UI — Final Section
# ============================================================

st.header("📤 Upload BOM CSV")

uploaded_file = st.file_uploader("Upload CSV file containing PartNumber, Quantity", type=["csv"])

def run_async_bom(df):
    """Runs the BOM processor inside a thread-safe executor."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    return loop.run_until_complete(process_bom_async(df))


if uploaded_file:
    raw = uploaded_file.read()
    if not raw or len(raw) < 5:
        st.error("Uploaded CSV appears empty or invalid.")
        st.stop()

    # Detect encoding for robust CSV loading
    encoding = chardet.detect(raw)["encoding"] or "latin1"

    try:
        df = pd.read_csv(io.BytesIO(raw), encoding=encoding, on_bad_lines="skip")
    except Exception as e:
        st.error(f"Error reading CSV: {e}")
        st.stop()

    # Validate required columns
    required_cols = {"PartNumber", "Quantity"}
    if not required_cols.issubset(df.columns):
        st.error("CSV must contain at least: PartNumber, Quantity")
        st.stop()

    # Clean up BOM
    df["PartNumber"] = df["PartNumber"].astype(str).str.strip()
    df["Quantity"] = pd.to_numeric(df["Quantity"], errors="coerce").fillna(0).astype(int)
    df = df[df["Quantity"] > 0]

    st.success(f"Loaded {len(df)} parts from the BOM.")
    st.write("✅ Ready to process asynchronously!")

    if st.button("🚀 Run  BOM Processing"):
        with st.spinner("Processing BOM asynchronously… This will be very fast ⚡"):
            executor = ThreadPoolExecutor(max_workers=1)
            future = executor.submit(run_async_bom, df)
            results = future.result()

        df_out = pd.DataFrame(results)

        st.subheader("📋 Per‑Part Results")
        st.dataframe(df_out, use_container_width=True)

        # Compute total Mouser cost
        total_cost_mouser = (
            df_out["Total Price (Mouser)"]
            .dropna()
            .sum()
        )
        st.metric("💰 Total Mouser BOM Cost", f"${round(total_cost_mouser, 2)}")

        st.download_button(
            "⬇️ Download Results CSV",
            df_out.to_csv(index=False),
            file_name="bom_results_async.csv",
            mime="text/csv",
        )

else:
    st.info("Upload a BOM CSV to begin.")