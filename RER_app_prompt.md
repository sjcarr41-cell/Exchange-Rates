# LLM Prompt: IMF Real Exchange Rate App

| Field | Value |
|---|---|
| Prompt written by | Claude Opus 5.5 (Anthropic), model ID `claude-opus-5-5` |
| Date written | October 25, 2026 |
| Model the prompt was run on | ____________________ (fill in, e.g. ChatGPT / Claude / Gemini + version) |
| Date run | ____________________ |

---

## Prompt (copy everything below this line)

You are an expert Python developer and international economist. Build a complete, working **Streamlit** web app that computes and plots the real exchange rate (RER) between any two economies using IMF data. Deliver **exactly three complete files**: `app.py`, `requirements.txt`, and `README.md`. All code must run on **Python 3.12**. Do not leave placeholders, "TODO"s, or "add your code here" sections. Every function must be fully implemented.

### 1. Country selection
- Let the user choose **Country 1** and **Country 2** from dropdowns containing all IMF countries **plus the Euro area** (IMF code **G163**).
- Build the list from the IMF country codelist through the API if possible. If that call fails, fall back to a hard-coded list of at least the major economies (USA, G163 Euro area, GBR, JPN, CHN, CAN, CHE, AUS, MEX, BRA, IND, KOR, etc.) so the app still works.
- Show readable names (e.g. "Japan (JPN)") but use ISO3 codes internally.
- If the user picks the same country twice, show a warning and stop.

### 2. Data retrieval (IMF SDMX 3.0 API)
- Base URL: `https://api.imf.org/external/sdmx/3.0`
- Monthly CPI series key: `{ISO3}.CPI._T.IX.M`
- Monthly exchange-rate series key: `{ISO3}.XDC_USD.PA_RT.M` (national currency units per U.S. dollar, period average)
- The CPI and exchange-rate data likely sit in dataflows with agency `IMF.STA` and IDs `CPI` and `ER`. Verify this through the API's structure/dataflow endpoints, and write the request-building code so the agency, dataflow IDs, and version are constants that are easy to change.
- Request data starting **January 1976** (`1976-01`) where available, up to the latest month available.
- Request a machine-readable format (SDMX-JSON or SDMX-CSV via the `Accept` header or `format` parameter) and write a robust parser that returns a pandas Series indexed by month-start dates.
- Cache API responses with `st.cache_data` (e.g. a 24-hour TTL) so changing dates or chart options does not refetch.
- Use timeouts, retries, and clear error messages. If a series is missing or empty, tell the user which country and which series failed instead of crashing.
- **United States:** the USD-per-USD rate is 1 by definition, and the IMF may not publish it. If Country 1 or 2 is USA, set its exchange rate to 1.0 for every month instead of calling the API.

### 3. Economics (follow these definitions exactly)
- The IMF rates are **local currency per USD**. Let E1(t) = Country 1 currency per USD and E2(t) = Country 2 currency per USD.
- Define **S(t) = units of Country 2 currency per one unit of Country 1 currency**, so **S(t) = E2(t) / E1(t)**.
- Let P1(t) and P2(t) be the monthly CPIs of Country 1 and Country 2.
- **RER(t) = S(t) × P1(t) / P2(t)**
- Align all series by month and keep only **valid months**, meaning months where S, P1, and P2 are all present and positive. Never fill gaps by interpolation or forward-filling. Report how many months were dropped and the first and last valid month of each series.

### 4. Sample period and normalization
- Provide **start month** and **end month** selectors (year + month), limited to the valid data range. Default to the full valid range.
- Compute **RER\* = the average of RER(t) over valid months in the selected window**.
- Plot **Real Index(t) = 100 × RER(t) / RER\***.
- **Recalculate RER\* whenever the start or end date changes.** It must always reflect the current window.
- Plot **nominal S(t) in actual currency units** (e.g. "JPY per EUR"). **Do not normalize or index S(t).**
- Validate that start ≤ end and that the window has at least 2 valid months, and show a message if not.

### 5. Charts and output
- Use **Plotly** for interactive charts (hover, zoom, pan).
- Provide a radio button: **Real**, **Nominal**, or **Both**.
  - Real: Real Index(t), with a dashed horizontal line at 100 (= RER\*).
  - Nominal: S(t), y-axis labeled with the actual units, e.g. "JPY per 1 EUR".
  - Both: both series on one chart with **separate y-axes** (Real Index on the left, nominal S on the right), each clearly labeled, with a legend.
- Show key metrics above the chart: RER\*, latest Real Index, latest S, the % deviation of the latest Real Index from 100, and the number of valid months used.
- Add a short interpretation line. Real Index > 100 means Country 1's currency is strong in real terms relative to its sample average (overvalued versus Country 2). Real Index < 100 means it is weak (undervalued).
- Provide a **CSV download button** (`st.download_button`) for all observations and calculations **within the selected sample period**, one row per valid month, with columns: `month, P1_CPI, P2_CPI, E1_<CUR1>_per_USD, E2_<CUR2>_per_USD, S_<CUR2>_per_<CUR1>, RER, RER_star, Real_Index`. Name the file after the countries and dates, e.g. `RER_JPN_G163_1999-01_2026-09.csv`.
- Optionally show the same table in an expandable `st.dataframe`.

### 6. Data caveats to handle and show in the app
- IMF coverage varies, and some countries have short or gappy CPI or exchange-rate histories. Show coverage for each series.
- The Euro area (G163) has no euro exchange rate before January 1999, and some euro-area members' national series change units in 1999. Note any structural breaks you detect, such as a jump of more than 50% in one month in S, rather than silently "fixing" them.
- CPI base years differ across countries. This is fine because the Real Index is normalized by RER\*, and the README should say so.
- Hyperinflation economies may have extreme values, so the app should still plot them. Offer a log-scale toggle for the nominal axis.

### 7. Code quality
- Organize `app.py` into clear functions: country list, API fetch, parsing, alignment, calculations, plotting, and the UI. Add docstrings and concise comments.
- Include type hints. Use only well-maintained libraries.
- `requirements.txt` must list pinned or minimum versions compatible with Python 3.12 (e.g. `streamlit`, `pandas`, `plotly`, `requests`).
- The app must start with `streamlit run app.py` and no other setup.

### 8. README.md must include
1. Purpose of the app.
2. Installation (virtual environment, `pip install -r requirements.txt`) and the run command.
3. Methodology, with the formulas for S(t), RER(t), RER\*, and Real Index(t), and how the USD cross-rate conversion works.
4. How to interpret the Real Index (above or below 100).
5. Data sources (IMF SDMX 3.0 endpoints and series keys) and known limitations: gaps, coverage, Euro area pre-1999, USA handling.
6. A description of the CSV output columns.
7. A troubleshooting section (API down, empty series, changed dataflow IDs).

### 9. Output format
Return the three files in full, each in its own fenced code block with the file name as a heading. After the files, list in a few bullet points any assumptions you made about the IMF API (dataflow IDs, response format) that I should verify.
