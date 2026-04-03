# Mouser BOM Tool (with Rate Limiting)

This Streamlit app prices a BOM against Mouser's Search API and includes:

- Per‑minute **rate limiting** (default ~10 calls/min)
- **Retry/backoff** for 429/5xx and Mouser's `TooManyRequests` (403) payload
- Real endpoint **health check**
- **Caching** within a run to avoid duplicate queries
- Proxy/TLS tips for corporate environments

## Quick Start

```bash
python -m venv .venv
.venv\Scripts\activate
python.exe -m pip install --upgrade pip
python -m pip install pip-system-certs
pip install -r requirements.txt
cp .env.example .env
# edit .env and set MOUSER_API_KEY
python -m pip install streamlit requests pandas python-dotenv pip-system-certs chardet
python -m streamlit
python -m streamlit run app.py

```
 api.digikey.com/CustomerResource/v1/associatedaccounts
### Environment Variables (optional)
- `MOUSER_CALLS_PER_MINUTE` — default 10
- `MAX_RETRIES`, `BACKOFF_BASE`, `BACKOFF_CAP` — tune retry behavior
- `TRUST_ENV` — set `true` to inherit corporate proxy ENV vars (HTTP(S)_PROXY)
- `REQUESTS_CA_BUNDLE` — path to combined CA bundle if TLS inspection is used
- `CONNECT_TIMEOUT`, `READ_TIMEOUT` — per‑request timeouts (seconds)

## CSV Format
```
PartNumber,Quantity,Description,Manufacturer
595-SN74HC00N,10,Quad 2-Input NAND Gate,Texas Instruments
485-ATMEGA328P-PU,5,8-bit AVR Microcontroller,Microchip
568-0010,100,LED Red 5mm,Generic
```

## Notes
- If you encounter `TooManyRequests`, the client will back off and retry, but overall throughput is capped by the rate limiter.
- For very large BOMs, consider increasing your Mouser quota or running multiple suppliers in sequence.



setx HTTP_PROXY ""
setx HTTPS_PROXY ""
