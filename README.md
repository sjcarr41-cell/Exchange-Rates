# Real Exchange Rate Explorer (IMF data)

## Purpose

This Streamlit app computes and plots the **real exchange rate (RER)** between any two economies, using monthly consumer price indexes and exchange rates from the IMF. You pick Country 1 and Country 2 (any IMF economy or the Euro area) and a sample period. The app shows whether Country 1's currency is strong (overvalued) or weak (undervalued) in real terms relative to its average over that period. You can view the real index, the nominal exchange rate, or both, and download every observation as a CSV.

## Installation and running

Requires **Python 3.12**.

```bash
# 1. Create and activate a virtual environment
python3.12 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run the app
streamlit run app.py
```

The app opens in your browser at http://localhost:8501.

**Deploying online (optional):** push `app.py`, `requirements.txt` and `README.md` to a GitHub repository. At [share.streamlit.io](https://share.streamlit.io), click *Create app*, choose the repository, set the main file to `app.py` and select Python 3.12 under *Advanced settings*.

## Methodology

The IMF publishes exchange rates as **national currency per U.S. dollar** (period average):

- E1(t) = Country 1 currency per USD
- E2(t) = Country 2 currency per USD

**Nominal exchange rate (USD cross rate).** S(t) is the number of units of Country 2 currency per **one** unit of Country 1 currency:

  **S(t) = E2(t) / E1(t)**

For example, with Country 1 = Euro area and Country 2 = Japan, S(t) is yen per euro: (JPY per USD) ÷ (EUR per USD).

**Real exchange rate.** With P1(t) and P2(t) the monthly CPIs of Country 1 and Country 2:

  **RER(t) = S(t) × P1(t) / P2(t)**

**Benchmark.** Over the start and end months you select:

  **RER\* = average of RER(t) over the valid months in the window**

**Real Index.**

  **Real Index(t) = 100 × RER(t) / RER\***

RER\* is recalculated every time you change the start or end month. Nominal S(t) is always plotted in actual currency units and is never normalized.

**Valid months.** A month is used only if S, P1 and P2 are all present and positive. Gaps are never interpolated or forward-filled.

**CPI base years.** Countries use different CPI base years. This doesn't matter because the Real Index divides RER(t) by RER\*, so the base-year scaling cancels out.

## Interpreting the Real Index

| Real Index | Meaning |
|---|---|
| **> 100** | Country 1's currency is **strong in real terms** relative to its sample average: Country 1 goods are relatively expensive, so its currency is **overvalued versus Country 2**. A fall in S would restore the average. |
| **= 100** | RER equals its sample average. |
| **< 100** | Country 1's currency is **weak in real terms**: it is **undervalued versus Country 2**. A rise in S would restore the average. |

For example, a Real Index of 88 means Country 1's currency is about 12% below its average real value over the chosen period. The conclusion depends on the sample period, since that period defines "fair value."

## Data sources

IMF SDMX 3.0 API: `https://api.imf.org/external/sdmx/3.0`

| Series | Dataflow | Key | Request pattern |
|---|---|---|---|
| CPI, all items, index, monthly | `IMF.STA:CPI` | `{ISO3}.CPI._T.IX.M` | `/data/dataflow/IMF.STA/CPI/~/{key}?c[TIME_PERIOD]=ge:1976-M01` |
| Exchange rate, national currency per USD, period average, monthly | `IMF.STA:ER` | `{ISO3}.XDC_USD.PA_RT.M` | `/data/dataflow/IMF.STA/ER/~/{key}?c[TIME_PERIOD]=ge:1976-M01` |

- Data are requested from **January 1976** where available. The IMF writes monthly periods as `1976-M01`, and the version segment `~` means "latest version."
- Requests send `Accept: text/csv` first, then fall back to JSON. Only successful responses are cached, so a temporary IMF outage doesn't stick.
- The Euro area uses IMF code **G163**. If a country has no series under the `CPI` index type, the app tries the Harmonised Index of Consumer Prices (`{ISO3}.HICP._T.IX.M`), which is how the IMF publishes euro-area prices.
- The country list comes from the CPI dataflow's codelist (`/structure/dataflow/IMF.STA/CPI/~?references=descendants`). If that fails, the app falls back to the ISO 3166 country list.
- Responses are cached for 24 hours, so changing dates or chart options doesn't refetch data.
- An optional IMF API key can be supplied in the environment variable `IMF_API_KEY`. It isn't needed for normal use.

### Known limitations

- **Gaps and coverage.** Many economies have short, gappy or non-monthly CPI or exchange-rate histories. Open the *Data coverage and caveats* panel to see each series' first and last month, number of observations and missing months.
- **Euro area before 1999.** The euro started in January 1999, so G163 exchange rates before then may be missing, synthetic or expressed in legacy units. Individual euro-area members' national series may also change units in 1999. The app flags one-month moves in S above 50% as possible structural breaks but does **not** adjust them.
- **United States.** The USD-per-USD rate is 1 by definition, so the app sets E = 1.0 for every month rather than requesting it from the IMF.
- **Hyperinflation economies** can produce extreme values. Use the log-scale option for the nominal axis.
- **Currency labels** come from a built-in table. Economies not in the table are labeled `<ISO3>cur`.

## CSV output

The download button exports one row per valid month **within the selected sample period**. The file is named `RER_{Country1}_{Country2}_{start}_{end}.csv`, for example `RER_G163_JPN_1999-01_2026-09.csv`.

| Column | Description |
|---|---|
| `month` | Month (YYYY-MM) |
| `P1_CPI` | Country 1 CPI |
| `P2_CPI` | Country 2 CPI |
| `E1_<CUR1>_per_USD` | Country 1 currency per USD (IMF) |
| `E2_<CUR2>_per_USD` | Country 2 currency per USD (IMF) |
| `S_<CUR2>_per_<CUR1>` | Nominal rate S(t) = E2 / E1 |
| `RER` | S(t) × P1(t) / P2(t) |
| `RER_star` | Average RER over the selected window (same value in every row) |
| `Real_Index` | 100 × RER / RER\* |

## Troubleshooting

| Problem | What to do |
|---|---|
| **"Cannot reach IMF API"** for every country | Open https://api.imf.org/external/sdmx/3.0/data/dataflow/IMF.STA/CPI/~/USA.CPI._T.IX.M?c[TIME_PERIOD]=ge:2025-M01 in your browser. If it shows data, the API works and the hosting server's connection is the problem (try again later or run the app locally). If it fails, the IMF API is down. Then click **Clear cached IMF responses and retry** in the app. |
| **"Could not retrieve ... from the IMF"** with a timeout or HTTP 5xx | The IMF API may be down or slow. The app retries automatically, so wait a few minutes and reload. |
| **HTTP 404 / empty series** for one country | The IMF doesn't publish that monthly series for that economy. Choose another country. |
| **Every request fails with 404**, including major economies such as JPN | The IMF may have renamed a dataflow or changed its version. Check the current IDs at [data.imf.org](https://data.imf.org) and update the constants `AGENCY`, `CPI_DATAFLOW`, `ER_DATAFLOW`, `DATAFLOW_VERSIONS`, `CPI_KEY_TEMPLATE` or `ER_KEY_TEMPLATE` at the top of `app.py`. |
| **HTTP 401/403** | Some IMF queries need a subscription key. Set `IMF_API_KEY` (or add it to Streamlit secrets as an environment variable) and restart. |
| **Stale data** | Data are cached for 24 hours. Use Streamlit's menu → *Clear cache*, or restart the app. |
| **Country list shows ISO 3166 names** | The IMF codelist request failed and the app fell back to a local list. Data requests still work. |
