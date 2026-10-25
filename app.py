"""
Real Exchange Rate Explorer (IMF data)

Streamlit app that computes and plots the real exchange rate (RER) between two
economies using monthly IMF CPI and exchange-rate data (SDMX 3.0 API).

Definitions
-----------
E1(t), E2(t) : national currency per USD for Country 1 and Country 2 (IMF, period average)
S(t)         : units of Country 2 currency per ONE unit of Country 1 currency = E2(t) / E1(t)
P1(t), P2(t) : monthly CPI of Country 1 and Country 2
RER(t)       = S(t) * P1(t) / P2(t)
RER*         = mean of RER(t) over the valid months in the selected window
Real Index(t)= 100 * RER(t) / RER*

Run with:  streamlit run app.py
"""

from __future__ import annotations

import io
import json
import os
import re
from dataclasses import dataclass

import pandas as pd
import plotly.graph_objects as go
import pycountry
import requests
import streamlit as st
from plotly.subplots import make_subplots
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ---------------------------------------------------------------------------
# Configuration (change these if the IMF renames dataflows or versions)
# ---------------------------------------------------------------------------
BASE_URL: str = "https://api.imf.org/external/sdmx/3.0"
AGENCY: str = "IMF.STA"
CPI_DATAFLOW: str = "CPI"
ER_DATAFLOW: str = "ER"
# SDMX 3.0 version wildcards tried in order: "~" = latest (known to work on the IMF API), "+" = latest stable.
DATAFLOW_VERSIONS: tuple[str, ...] = ("~", "+")
CPI_KEY_TEMPLATE: str = "{iso3}.CPI._T.IX.M"
ER_KEY_TEMPLATE: str = "{iso3}.XDC_USD.PA_RT.M"
START_PERIOD: str = "1976-01"
# The IMF writes monthly periods as "1976-M01"; this is the format its time filter expects.
START_FILTER: str = "1976-M01"
EURO_AREA_CODE: str = "G163"
REQUEST_TIMEOUT: int = 45  # seconds per request
CACHE_TTL: int = 24 * 60 * 60  # 24 hours
BREAK_THRESHOLD: float = 0.50  # flag one-month moves in S larger than 50%

# Response formats tried in order. Plain "text/csv" is what the IMF API documents for CSV.
ACCEPT_FORMATS: tuple[str, ...] = (
    "text/csv",
    "application/vnd.sdmx.data+csv;version=2.0.0",
    "application/json",
    "application/vnd.sdmx.data+json;version=2.0.0",
)

# Optional IMF API subscription key (only needed for some restricted queries).
IMF_API_KEY: str | None = os.environ.get("IMF_API_KEY")

# Euro-area members (for currency labels only).
EURO_MEMBERS: set[str] = {
    "AUT", "BEL", "CYP", "DEU", "EST", "ESP", "FIN", "FRA", "GRC", "HRV", "IRL",
    "ITA", "LTU", "LUX", "LVA", "MLT", "NLD", "PRT", "SVK", "SVN", "BGR",
}

# Currency codes for readable axis labels. Missing entries fall back to "<ISO3> currency".
CURRENCY_CODES: dict[str, str] = {
    "USA": "USD", EURO_AREA_CODE: "EUR", "GBR": "GBP", "JPN": "JPY", "CHN": "CNY",
    "CAN": "CAD", "CHE": "CHF", "AUS": "AUD", "NZL": "NZD", "MEX": "MXN",
    "BRA": "BRL", "IND": "INR", "KOR": "KRW", "RUS": "RUB", "ZAF": "ZAR",
    "TUR": "TRY", "ARG": "ARS", "CHL": "CLP", "COL": "COP", "PER": "PEN",
    "SWE": "SEK", "NOR": "NOK", "DNK": "DKK", "POL": "PLN", "CZE": "CZK",
    "HUN": "HUF", "ISR": "ILS", "SAU": "SAR", "ARE": "AED", "EGY": "EGP",
    "NGA": "NGN", "KEN": "KES", "IDN": "IDR", "MYS": "MYR", "THA": "THB",
    "PHL": "PHP", "SGP": "SGD", "HKG": "HKD", "VNM": "VND", "PAK": "PKR",
    "BGD": "BDT", "ISL": "ISK", "ROU": "RON", "UKR": "UAH", "VEN": "VES",
}

# Fallback list if the IMF codelist cannot be retrieved.
FALLBACK_COUNTRIES: dict[str, str] = {
    "USA": "United States", EURO_AREA_CODE: "Euro area", "GBR": "United Kingdom",
    "JPN": "Japan", "CHN": "China", "CAN": "Canada", "CHE": "Switzerland",
    "AUS": "Australia", "MEX": "Mexico", "BRA": "Brazil", "IND": "India",
    "KOR": "Korea", "DEU": "Germany", "FRA": "France", "ITA": "Italy",
    "ESP": "Spain", "SWE": "Sweden", "NOR": "Norway", "DNK": "Denmark",
    "NZL": "New Zealand", "ZAF": "South Africa", "TUR": "Turkiye",
    "ARG": "Argentina", "CHL": "Chile", "COL": "Colombia", "IDN": "Indonesia",
    "THA": "Thailand", "MYS": "Malaysia", "PHL": "Philippines", "SGP": "Singapore",
    "POL": "Poland", "HUN": "Hungary", "CZE": "Czech Republic", "ISR": "Israel",
    "SAU": "Saudi Arabia", "EGY": "Egypt", "NGA": "Nigeria", "PAK": "Pakistan",
}


@dataclass
class FetchResult:
    """Outcome of one IMF series request."""

    series: pd.Series | None
    error: str | None = None
    url: str | None = None


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------
def _session() -> requests.Session:
    """Return a requests session with retries on transient errors."""
    retry = Retry(
        total=2,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
        raise_on_status=False,
    )
    sess = requests.Session()
    sess.mount("https://", HTTPAdapter(max_retries=retry))
    sess.headers.update({"User-Agent": "rer-explorer/1.0 (Streamlit; educational use)"})
    if IMF_API_KEY:
        sess.headers.update({"Ocp-Apim-Subscription-Key": IMF_API_KEY})
    return sess


# ---------------------------------------------------------------------------
# Country list
# ---------------------------------------------------------------------------
def _codes_from_structure_json(payload: dict) -> dict[str, str]:
    """Extract {code: name} from any codelist in an SDMX-JSON structure message
    whose id mentions COUNTRY or AREA."""
    out: dict[str, str] = {}
    codelists = payload.get("data", {}).get("codelists", []) or payload.get("codelists", [])
    for cl in codelists:
        cl_id = str(cl.get("id", "")).upper()
        if "COUNTRY" not in cl_id and "AREA" not in cl_id:
            continue
        for code in cl.get("codes", []):
            cid = code.get("id")
            name = code.get("name")
            if isinstance(name, dict):
                name = name.get("en") or next(iter(name.values()), None)
            if not name:
                names = code.get("names", {})
                name = names.get("en") if isinstance(names, dict) else None
            if cid:
                out[str(cid)] = str(name or cid)
    return out


@st.cache_data(ttl=CACHE_TTL, show_spinner="Loading IMF country list...")
def get_country_list() -> tuple[dict[str, str], str]:
    """Return ({ISO3 code: name}, source description).

    Tries the CPI dataflow structure (with referenced codelists) from the IMF API.
    Falls back to pycountry plus the Euro area, then to a hard-coded list.
    """
    sess = _session()
    for ver in DATAFLOW_VERSIONS:
        url = f"{BASE_URL}/structure/dataflow/{AGENCY}/{CPI_DATAFLOW}/{ver}"
        try:
            r = sess.get(
                url,
                params={"references": "descendants", "detail": "referencepartial"},
                headers={"Accept": "application/json"},
                timeout=REQUEST_TIMEOUT,
            )
            if r.ok:
                codes = _codes_from_structure_json(r.json())
                # Keep 3-letter economies and the Euro area aggregate.
                codes = {k: v for k, v in codes.items() if re.fullmatch(r"[A-Z]{3}", k) or k == EURO_AREA_CODE}
                if len(codes) > 50:
                    codes.setdefault(EURO_AREA_CODE, "Euro area")
                    return codes, "IMF country codelist (API)"
        except (requests.RequestException, ValueError):
            continue

    # Fallback 1: ISO 3166 list from pycountry (IMF uses ISO3 codes for countries).
    try:
        codes = {c.alpha_3: getattr(c, "common_name", c.name) for c in pycountry.countries}
        codes[EURO_AREA_CODE] = "Euro area"
        return codes, "ISO 3166 country list (IMF codelist unavailable)"
    except Exception:  # pragma: no cover - pycountry ships its own data
        return dict(FALLBACK_COUNTRIES), "Built-in list of major economies (IMF codelist unavailable)"


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
_PERIOD_RE = re.compile(r"^(\d{4})[-/]?M?(\d{1,2})")


def parse_period(value: object) -> pd.Timestamp | None:
    """Convert IMF period strings ('2020-M01', '2020M1', '2020-01') to month-start Timestamps."""
    m = _PERIOD_RE.match(str(value).strip())
    if not m:
        return None
    year, month = int(m.group(1)), int(m.group(2))
    if not 1 <= month <= 12:
        return None
    return pd.Timestamp(year=year, month=month, day=1)


def _to_series(periods: list, values: list) -> pd.Series:
    """Build a clean monthly Series from parallel period/value lists."""
    idx = [parse_period(p) for p in periods]
    s = pd.Series(pd.to_numeric(pd.Series(values), errors="coerce").to_numpy(), index=idx)
    s = s[[i is not None for i in idx]]
    s.index = pd.DatetimeIndex(s.index)
    s = s[~s.index.duplicated(keep="last")].sort_index().dropna()
    return s.astype(float)


def parse_sdmx_csv(text: str) -> pd.Series:
    """Parse an SDMX-CSV response into a monthly Series."""
    df = pd.read_csv(io.StringIO(text), dtype=str)
    cols = {c.upper(): c for c in df.columns}
    time_col = cols.get("TIME_PERIOD")
    val_col = cols.get("OBS_VALUE")
    if time_col is None or val_col is None:
        raise ValueError("CSV response lacks TIME_PERIOD / OBS_VALUE columns")
    freq_col = cols.get("FREQUENCY") or cols.get("FREQ")
    if freq_col is not None:
        is_monthly = df[freq_col].astype(str).str.upper().isin(["M", "MONTHLY"])
        if is_monthly.any():
            df = df[is_monthly]
    return _to_series(df[time_col].tolist(), df[val_col].tolist())


def parse_sdmx_json(payload: dict) -> pd.Series:
    """Parse an SDMX-JSON (1.0 or 2.0) data message into a monthly Series.

    Handles both 'AllDimensions' (flat observations) and series-level layouts.
    """
    data = payload.get("data", payload)
    structure = (data.get("structures") or [data.get("structure")])[0] or {}
    datasets = data.get("dataSets") or []
    if not datasets:
        raise ValueError("JSON response contains no dataSets")
    dims = structure.get("dimensions", {})
    obs_dims = dims.get("observation", [])

    def time_values(dim_list: list) -> tuple[int, list[str]] | None:
        for pos, d in enumerate(dim_list):
            if str(d.get("id", "")).upper() in {"TIME_PERIOD", "TIME"}:
                return pos, [v.get("id") or v.get("value") or v.get("name") for v in d.get("values", [])]
        return None

    periods: list[str] = []
    values: list = []
    t = time_values(obs_dims)
    if t is None:
        raise ValueError("JSON response has no TIME_PERIOD dimension")
    t_pos, t_vals = t
    for ds in datasets:
        if "observations" in ds:  # flat layout
            for key, obs in ds["observations"].items():
                i = int(str(key).split(":")[t_pos])
                periods.append(t_vals[i])
                values.append(obs[0] if isinstance(obs, list) else obs)
        for ser in (ds.get("series") or {}).values():  # series layout
            for key, obs in (ser.get("observations") or {}).items():
                i = int(str(key).split(":")[0])
                periods.append(t_vals[i])
                values.append(obs[0] if isinstance(obs, list) else obs)
    return _to_series(periods, values)


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------
def _parse_response(r: requests.Response) -> pd.Series:
    """Parse a CSV or JSON IMF response, choosing by content type and content."""
    ctype = r.headers.get("Content-Type", "").lower()
    text = r.text.lstrip()
    if "json" in ctype or text.startswith("{"):
        return parse_sdmx_json(json.loads(text))
    return parse_sdmx_csv(text)


def imf_data_url(dataflow: str, key: str, version: str = DATAFLOW_VERSIONS[0]) -> str:
    """Build an IMF SDMX 3.0 data URL, e.g. .../data/dataflow/IMF.STA/CPI/~/USA.CPI._T.IX.M"""
    return f"{BASE_URL}/data/dataflow/{AGENCY}/{dataflow}/{version}/{key}"


class IMFFetchError(RuntimeError):
    """Raised when an IMF series cannot be retrieved (failures are never cached)."""


def fetch_imf_series(dataflow: str, key: str) -> FetchResult:
    """Fetch one monthly IMF series. Successful results are cached for 24 hours;
    failures are NOT cached, so a temporary outage does not stick."""
    try:
        series, url = _fetch_imf_series_cached(dataflow, key)
        return FetchResult(series=series, url=url)
    except IMFFetchError as exc:
        return FetchResult(series=None, error=str(exc))


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def _fetch_imf_series_cached(dataflow: str, key: str) -> tuple[pd.Series, str]:
    """Fetch one monthly IMF series from START_PERIOD to the latest month.

    Tries each dataflow version, response format and time-filter variant until one
    returns data. Raises IMFFetchError (which Streamlit does not cache) on failure.
    """
    sess = _session()
    # First try with the IMF time filter; as a last resort fetch the whole series
    # and cut it at START_PERIOD locally.
    param_variants: tuple[dict[str, str], ...] = ({"c[TIME_PERIOD]": f"ge:{START_FILTER}"}, {})
    errors: list[str] = []
    for params in param_variants:
        for ver in DATAFLOW_VERSIONS:
            url = imf_data_url(dataflow, key, ver)
            for accept in ACCEPT_FORMATS:
                try:
                    r = sess.get(url, params=params, headers={"Accept": accept}, timeout=REQUEST_TIMEOUT)
                except requests.RequestException as exc:
                    errors.append(f"cannot reach IMF API ({type(exc).__name__}) at {url}")
                    break  # network problem: other formats will fail the same way
                if r.status_code == 404:
                    errors.append(f"HTTP 404 (no data) at {r.url}")
                    break
                if not r.ok:
                    errors.append(f"HTTP {r.status_code} at {r.url}")
                    continue
                try:
                    s = _parse_response(r)
                except (ValueError, KeyError, IndexError, TypeError, pd.errors.ParserError) as exc:
                    errors.append(f"could not parse {accept} response from {r.url}: {exc}")
                    continue
                s = s[s.index >= pd.Timestamp(START_PERIOD + "-01")]
                if s.empty:
                    # The IMF returns HTTP 200 with zero rows for unknown codes or unpublished series.
                    errors.append(f"no observations returned at {r.url}")
                    break
                return s, r.url
    # Show the most informative messages (unique, last few).
    uniq = list(dict.fromkeys(errors))
    raise IMFFetchError(" | ".join(uniq[-3:]) or "Unknown error")


def get_cpi(iso3: str) -> FetchResult:
    """Monthly CPI index for a country."""
    return fetch_imf_series(CPI_DATAFLOW, CPI_KEY_TEMPLATE.format(iso3=iso3))


def get_usd_rate(iso3: str, reference_index: pd.DatetimeIndex | None = None) -> FetchResult:
    """National currency per USD (period average). USA is 1.0 by definition."""
    if iso3 == "USA":
        if reference_index is None or len(reference_index) == 0:
            reference_index = pd.date_range(START_PERIOD + "-01", pd.Timestamp.today().normalize(), freq="MS")
        return FetchResult(series=pd.Series(1.0, index=reference_index), url="USD per USD = 1 (by definition)")
    return fetch_imf_series(ER_DATAFLOW, ER_KEY_TEMPLATE.format(iso3=iso3))


# ---------------------------------------------------------------------------
# Calculations
# ---------------------------------------------------------------------------
def align_data(e1: pd.Series, e2: pd.Series, p1: pd.Series, p2: pd.Series) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Align series by month and keep only valid months (no gap filling).

    Returns (all_months_frame, valid_frame). valid_frame has S and RER columns.
    """
    df = pd.concat({"E1": e1, "E2": e2, "P1": p1, "P2": p2}, axis=1, sort=True)
    df["S"] = df["E2"] / df["E1"]  # Country-2 currency per 1 unit of Country-1 currency
    valid = df[["S", "P1", "P2"]].notna().all(axis=1) & (df[["S", "P1", "P2"]] > 0).all(axis=1)
    out = df[valid].copy()
    out["RER"] = out["S"] * out["P1"] / out["P2"]
    return df, out


def compute_window(valid: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> tuple[pd.DataFrame, float]:
    """Restrict to [start, end], compute RER* and Real Index. RER* is recomputed every call."""
    w = valid.loc[(valid.index >= start) & (valid.index <= end)].copy()
    rer_star = float(w["RER"].mean()) if not w.empty else float("nan")
    w["RER_star"] = rer_star
    w["Real_Index"] = 100.0 * w["RER"] / rer_star
    return w, rer_star


def detect_breaks(s: pd.Series, threshold: float = BREAK_THRESHOLD) -> pd.Series:
    """Return months where S moves more than `threshold` (in absolute % terms) from the prior valid month."""
    chg = s.pct_change()
    return chg[chg.abs() > threshold]


def coverage_table(series: dict[str, pd.Series | None]) -> pd.DataFrame:
    """Summarize first/last month, observation count and gaps for each series."""
    rows = []
    for name, s in series.items():
        if s is None or s.dropna().empty:
            rows.append({"Series": name, "First month": "n/a", "Last month": "n/a", "Observations": "0", "Missing months inside span": "n/a"})
            continue
        s = s.dropna()
        span = pd.date_range(s.index.min(), s.index.max(), freq="MS")
        rows.append({
            "Series": name,
            "First month": s.index.min().strftime("%Y-%m"),
            "Last month": s.index.max().strftime("%Y-%m"),
            "Observations": f"{len(s):,}",
            "Missing months inside span": f"{len(span) - len(s):,}",
        })
    return pd.DataFrame(rows)


def currency_code(iso3: str) -> str:
    """Readable currency code for labels."""
    if iso3 in CURRENCY_CODES:
        return CURRENCY_CODES[iso3]
    if iso3 in EURO_MEMBERS:
        return "EUR"
    return f"{iso3}cur"


def build_export(w: pd.DataFrame, cur1: str, cur2: str) -> pd.DataFrame:
    """Build the CSV export with the required column names."""
    out = pd.DataFrame({
        "month": w.index.strftime("%Y-%m"),
        "P1_CPI": w["P1"].to_numpy(),
        "P2_CPI": w["P2"].to_numpy(),
        f"E1_{cur1}_per_USD": w["E1"].to_numpy(),
        f"E2_{cur2}_per_USD": w["E2"].to_numpy(),
        f"S_{cur2}_per_{cur1}": w["S"].to_numpy(),
        "RER": w["RER"].to_numpy(),
        "RER_star": w["RER_star"].to_numpy(),
        "Real_Index": w["Real_Index"].to_numpy(),
    })
    return out


# ---------------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------------
def make_chart(w: pd.DataFrame, mode: str, s_label: str, title: str, log_nominal: bool) -> go.Figure:
    """Plotly chart: Real Index, nominal S, or both on separate y-axes."""
    real_color, nom_color = "#1f4e79", "#c0392b"
    if mode == "Both":
        fig = make_subplots(specs=[[{"secondary_y": True}]])
        fig.add_trace(go.Scatter(x=w.index, y=w["Real_Index"], name="Real Index (RER* = 100)",
                                 line=dict(color=real_color, width=2),
                                 hovertemplate="%{x|%Y-%m}<br>Real Index: %{y:.2f}<extra></extra>"),
                      secondary_y=False)
        fig.add_trace(go.Scatter(x=w.index, y=w["S"], name=f"Nominal S ({s_label})",
                                 line=dict(color=nom_color, width=1.6),
                                 hovertemplate="%{x|%Y-%m}<br>S: %{y:.4f}<extra></extra>"),
                      secondary_y=True)
        fig.add_hline(y=100, line_dash="dash", line_color="gray", secondary_y=False)
        fig.update_yaxes(title_text="Real Index (RER* = 100)", secondary_y=False)
        fig.update_yaxes(title_text=s_label, type="log" if log_nominal else "linear", secondary_y=True)
    elif mode == "Real":
        fig = go.Figure(go.Scatter(x=w.index, y=w["Real_Index"], name="Real Index",
                                   line=dict(color=real_color, width=2),
                                   hovertemplate="%{x|%Y-%m}<br>Real Index: %{y:.2f}<extra></extra>"))
        fig.add_hline(y=100, line_dash="dash", line_color="gray", annotation_text="RER* = 100")
        fig.update_yaxes(title_text="Real Index (RER* = 100)")
    else:
        fig = go.Figure(go.Scatter(x=w.index, y=w["S"], name="Nominal S",
                                   line=dict(color=nom_color, width=2),
                                   hovertemplate="%{x|%Y-%m}<br>S: %{y:.4f}<extra></extra>"))
        fig.update_yaxes(title_text=s_label, type="log" if log_nominal else "linear")
    fig.update_layout(title=title, hovermode="x unified", height=520,
                      legend=dict(orientation="h", y=-0.15), margin=dict(l=60, r=60, t=60, b=40))
    fig.update_xaxes(title_text="Month")
    return fig


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------
def main() -> None:
    """Streamlit user interface."""
    st.set_page_config(page_title="Real Exchange Rate Explorer", layout="wide")
    st.title("Real Exchange Rate Explorer")
    st.caption("Monthly IMF CPI and exchange-rate data (SDMX 3.0 API). "
               "RER(t) = S(t) × P1(t) / P2(t), with S = Country 2 currency per 1 unit of Country 1 currency.")

    countries, source = get_country_list()
    options = sorted(countries, key=lambda c: (c != "USA", c != EURO_AREA_CODE, countries[c]))
    fmt = lambda c: f"{countries.get(c, c)} ({c})"

    with st.sidebar:
        st.header("Countries")
        c1 = st.selectbox("Country 1", options, index=options.index(EURO_AREA_CODE) if EURO_AREA_CODE in options else 0, format_func=fmt)
        c2 = st.selectbox("Country 2", options, index=options.index("USA") if "USA" in options else 1, format_func=fmt)
        st.caption(f"Country list source: {source}")

    if c1 == c2:
        st.warning("Country 1 and Country 2 are the same. Please choose two different economies.")
        st.stop()

    cur1, cur2 = currency_code(c1), currency_code(c2)
    name1, name2 = countries.get(c1, c1), countries.get(c2, c2)

    with st.spinner("Fetching IMF data..."):
        p1_res, p2_res = get_cpi(c1), get_cpi(c2)
        ref_idx = None
        for r in (p1_res, p2_res):
            if r.series is not None:
                ref_idx = r.series.index if ref_idx is None else ref_idx.union(r.series.index)
        e1_res, e2_res = get_usd_rate(c1, ref_idx), get_usd_rate(c2, ref_idx)

    failures = [(lbl, res) for lbl, res in [
        (f"CPI for {name1} ({c1})", p1_res), (f"CPI for {name2} ({c2})", p2_res),
        (f"Exchange rate ({cur1} per USD) for {name1} ({c1})", e1_res),
        (f"Exchange rate ({cur2} per USD) for {name2} ({c2})", e2_res)] if res.series is None]
    if failures:
        for lbl, res in failures:
            st.error(f"Could not retrieve **{lbl}** from the IMF. Details: {res.error}")
        test_url = imf_data_url(CPI_DATAFLOW, CPI_KEY_TEMPLATE.format(iso3="USA")) + f"?c[TIME_PERIOD]=ge:2025-M01"
        st.info("The IMF may not publish this series monthly for this economy, or the API may be temporarily "
                "unavailable. Try another country or try again later.\n\n"
                f"**Quick test:** open [this IMF link]({test_url}) in your browser. If it shows U.S. CPI data, "
                "the API works and the problem is this server's connection to the IMF; if it fails too, the IMF "
                "API is down or its addresses have changed (see README → Troubleshooting).")
        if st.button("Clear cached IMF responses and retry"):
            st.cache_data.clear()
            st.rerun()
        st.stop()

    all_df, valid = align_data(e1_res.series, e2_res.series, p1_res.series, p2_res.series)
    if len(valid) < 2:
        st.error("Fewer than 2 months have CPI and exchange-rate data for both countries. Choose another pair.")
        st.stop()

    # ---- Sample-period selectors -------------------------------------------
    months = [d.strftime("%Y-%m") for d in valid.index]
    with st.sidebar:
        st.header("Sample period")
        start_lbl = st.selectbox("Start month", months, index=0)
        end_lbl = st.selectbox("End month", months, index=len(months) - 1)
        st.header("Chart")
        mode = st.radio("Show", ["Real", "Nominal", "Both"], index=2, horizontal=True)
        log_nominal = st.checkbox("Log scale for nominal S", value=False)

    start, end = pd.Timestamp(start_lbl + "-01"), pd.Timestamp(end_lbl + "-01")
    if start > end:
        st.error("Start month must be on or before the end month.")
        st.stop()
    w, rer_star = compute_window(valid, start, end)  # RER* recomputed for the current window
    if len(w) < 2:
        st.error("The selected window has fewer than 2 valid months. Widen the sample period.")
        st.stop()

    # ---- Metrics ------------------------------------------------------------
    s_label = f"{cur2} per 1 {cur1}"
    latest = w.iloc[-1]
    dev = latest["Real_Index"] - 100.0
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("RER* (window average)", f"{rer_star:,.4g}")
    m2.metric(f"Latest Real Index ({w.index[-1]:%Y-%m})", f"{latest['Real_Index']:.1f}")
    m3.metric(f"Latest S ({s_label})", f"{latest['S']:,.4f}")
    m4.metric("Deviation from 100", f"{dev:+.1f}%")
    m5.metric("Valid months used", f"{len(w):,}")

    if dev > 0:
        st.info(f"Real Index above 100: the {cur1} ({name1}) is **strong in real terms** relative to its "
                f"{start_lbl}–{end_lbl} average, i.e. **overvalued versus {name2}** by about {dev:.1f}%.")
    elif dev < 0:
        st.info(f"Real Index below 100: the {cur1} ({name1}) is **weak in real terms** relative to its "
                f"{start_lbl}–{end_lbl} average, i.e. **undervalued versus {name2}** by about {abs(dev):.1f}%.")
    else:
        st.info("Real Index equals 100: the latest RER equals its sample average.")

    # ---- Chart --------------------------------------------------------------
    title = f"{name1} vs {name2}: {'Real Index and nominal S' if mode == 'Both' else ('Real Index' if mode == 'Real' else 'Nominal S')} ({start_lbl} to {end_lbl})"
    st.plotly_chart(make_chart(w, mode, s_label, title, log_nominal), width="stretch")

    # ---- Download -----------------------------------------------------------
    export = build_export(w, cur1, cur2)
    st.download_button(
        "Download CSV (selected sample period)",
        data=export.to_csv(index=False).encode("utf-8"),
        file_name=f"RER_{c1}_{c2}_{start_lbl}_{end_lbl}.csv",
        mime="text/csv",
    )
    with st.expander("Show data table"):
        st.dataframe(export, width="stretch", hide_index=True)

    # ---- Data quality -------------------------------------------------------
    with st.expander("Data coverage and caveats", expanded=False):
        st.markdown("**Coverage of each IMF series (from January 1976 where available):**")
        st.dataframe(coverage_table({
            f"P1: CPI {c1}": p1_res.series, f"P2: CPI {c2}": p2_res.series,
            f"E1: {cur1} per USD" + (" (set to 1)" if c1 == "USA" else ""): None if c1 == "USA" else e1_res.series,
            f"E2: {cur2} per USD" + (" (set to 1)" if c2 == "USA" else ""): None if c2 == "USA" else e2_res.series,
        }), width="stretch", hide_index=True)
        span = pd.date_range(all_df.index.min(), all_df.index.max(), freq="MS")
        st.markdown(f"- Valid months (S, P1, P2 all present and positive): **{len(valid):,}** from "
                    f"{valid.index.min():%Y-%m} to {valid.index.max():%Y-%m}. "
                    f"Months dropped within the combined span: **{len(span) - len(valid):,}**. No gaps were filled.")
        if "USA" in (c1, c2):
            st.markdown("- U.S. exchange rate set to 1.0 USD per USD for every month (by definition).")
        if EURO_AREA_CODE in (c1, c2) or c1 in EURO_MEMBERS or c2 in EURO_MEMBERS:
            st.markdown("- The euro has existed only since January 1999. Euro-area exchange rates before 1999 "
                        "may be missing, synthetic, or expressed in legacy national currency units.")
        breaks = detect_breaks(w["S"])
        if not breaks.empty:
            st.warning("Possible structural breaks in S (one-month moves over 50%), not adjusted: "
                       + ", ".join(f"{d:%Y-%m} ({v:+.0%})" for d, v in breaks.items()))
        st.markdown("- CPI base years differ across countries. The Real Index divides by RER*, so base years cancel out.")
        for lbl, res in [("P1", p1_res), ("P2", p2_res), ("E1", e1_res), ("E2", e2_res)]:
            st.caption(f"{lbl} source: {res.url}")


if __name__ == "__main__":
    main()
