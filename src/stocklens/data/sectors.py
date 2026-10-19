"""Which sector a company is in, and which fund prices that sector.

Why a sector at all
-------------------
A single beta says how much of a stock's move is "the market" and calls the
rest idiosyncratic. For most stocks that rest is not one thing: a refiner falls
with oil whatever the S&P does, and a bank moves with the yield curve. Putting
the sector between the market and the company splits the unexplained part into
"what every company in this business is going through" and "what is specific to
this one" — the second is the only part an analysis of the company can speak to.

Why SPDR sector funds
---------------------
The eleven Select Sector SPDRs partition the S&P 500 by GICS sector, trade
daily with deep liquidity, and have a long history. Their price is a clean,
investable read on the sector that needs no constituent data — this module
knows that XLE is the energy fund, not what it holds or in what weights.

Why SIC, and how it is rolled up
--------------------------------
SEC filings carry a four-digit Standard Industrial Classification code; GICS
is proprietary and not in the filings. SIC is far finer than an allocation is
expressed in (Coca-Cola is "Beverages", Johnson & Johnson "Pharmaceutical
Preparations"), so codes are rolled up to the eleven sectors following SIC's
own structure: the two-digit major group decides, except where a major group
genuinely spans two sectors and the three-digit industry group overrides it.
Group 28 is the clearest case — 283x is pharmaceuticals (health care), the
rest of 28 is chemicals (materials). One deliberate departure from the
common SIC rollup: SIC 6798 (REITs) maps to real estate,
because GICS moved REITs out of financials in 2016 and XLRE holds them.

The rollup is an approximation: SIC predates the internet, so 7370-7379 files
Alphabet and Meta next to software houses, while GICS puts them in
communication services. That is what the override table below corrects for
the handful of names where the difference is large and well known.
"""

from __future__ import annotations

#: Sector key -> SPDR sector fund.
SECTOR_ETFS: dict[str, str] = {
    "technology": "XLK",
    "financials": "XLF",
    "energy": "XLE",
    "health_care": "XLV",
    "consumer_discretionary": "XLY",
    "consumer_staples": "XLP",
    "industrials": "XLI",
    "materials": "XLB",
    "utilities": "XLU",
    "real_estate": "XLRE",
    "communication_services": "XLC",
}

#: Fund -> sector key, the reverse of the map above.
ETF_SECTORS: dict[str, str] = {etf: sector for sector, etf in SECTOR_ETFS.items()}

SECTOR_LABELS: dict[str, str] = {
    "technology": "Technology",
    "financials": "Financials",
    "energy": "Energy",
    "health_care": "Health care",
    "consumer_discretionary": "Consumer discretionary",
    "consumer_staples": "Consumer staples",
    "industrials": "Industrials",
    "materials": "Materials",
    "utilities": "Utilities",
    "real_estate": "Real estate",
    "communication_services": "Communication services",
}

#: The fund used when no sector is known: the market itself. Regressing on the
#: market twice adds nothing, so callers should treat this as "no sector".
MARKET_ETF = "SPY"

#: Curated GICS sector for well-known mega caps. A note on why this exists at
#: all: SIC files Amazon under retail (5961 -> consumer discretionary, right by
#: luck), Alphabet and Meta under 7370 (technology, where GICS says
#: communication services) and Tesla under motor vehicles (3711 -> industrials,
#: where GICS says consumer discretionary). For the names readers look up most,
#: a wrong sector would be the most visible error, so they are pinned here.
OVERRIDES: dict[str, str] = {
    "AAPL": "technology", "MSFT": "technology", "NVDA": "technology",
    "AMZN": "consumer_discretionary", "TSLA": "consumer_discretionary",
    "GOOGL": "communication_services", "GOOG": "communication_services",
    "META": "communication_services",
    "JPM": "financials",
    "XOM": "energy",
    "KO": "consumer_staples", "PEP": "consumer_staples", "WMT": "consumer_staples",
    "PFE": "health_care", "JNJ": "health_care",
    "CAT": "industrials",
}

#: SIC major group (two digits) -> sector.
_BY_MAJOR_GROUP: dict[str, str] = {
    "01": "consumer_staples", "02": "consumer_staples", "07": "consumer_staples",
    "08": "materials", "09": "consumer_staples",
    "10": "materials", "12": "energy", "13": "energy", "14": "materials",
    "15": "industrials", "16": "industrials", "17": "industrials",
    "20": "consumer_staples", "21": "consumer_staples",
    "22": "consumer_discretionary", "23": "consumer_discretionary",
    "24": "industrials", "25": "consumer_discretionary", "26": "materials",
    "27": "communication_services",
    "28": "materials",  # 283x overridden to health care below
    "29": "energy", "30": "materials", "31": "consumer_discretionary",
    "32": "materials", "33": "materials", "34": "industrials",
    "35": "technology",  # 351x-353x overridden to industrials below
    "36": "technology",
    "37": "industrials",
    "38": "technology",  # 384x-385x overridden to health care below
    "39": "consumer_discretionary",
    "40": "industrials", "41": "industrials", "42": "industrials", "44": "industrials",
    "45": "industrials", "46": "energy", "47": "industrials",
    "48": "communication_services", "49": "utilities",
    "50": "industrials", "51": "consumer_staples",
    "52": "consumer_discretionary", "53": "consumer_discretionary",
    "54": "consumer_staples",
    "55": "consumer_discretionary", "56": "consumer_discretionary",
    "57": "consumer_discretionary", "58": "consumer_discretionary",
    "59": "consumer_discretionary",
    "60": "financials", "61": "financials", "62": "financials", "63": "financials",
    "64": "financials", "65": "real_estate", "67": "financials",
    "70": "consumer_discretionary", "72": "consumer_discretionary",
    "73": "technology", "75": "consumer_discretionary", "76": "industrials",
    "78": "communication_services", "79": "consumer_discretionary",
    "80": "health_care", "81": "industrials",
    "82": "consumer_discretionary", "83": "consumer_discretionary",
    "86": "industrials", "87": "industrials", "89": "industrials",
}

#: SIC industry group (three digits) -> sector, where a major group spans two
#: sectors. Checked before the two-digit table.
_BY_INDUSTRY_GROUP: dict[str, str] = {
    "283": "health_care",  # pharmaceutical preparations, biologicals
    "384": "health_care",  # surgical, medical and dental instruments
    "385": "health_care",  # ophthalmic goods
    "351": "industrials",  # engines and turbines
    "352": "industrials",  # farm and garden machinery
    "353": "industrials",  # construction and mining machinery
    "372": "industrials",  # aircraft and parts
    "679": "real_estate",  # 6798 real estate investment trusts (GICS 2016 split)
}


def sector_from_sic(sic: int | None) -> str | None:
    """The sector for a four-digit SIC code, or None if it carries none.

    None, not a guess, for codes outside the table (9995 "non-operating
    establishments", 9999 "nonclassifiable"): a wrong sector would put a
    confident-looking factor into the regression.
    """
    if sic is None or isinstance(sic, bool) or not 0 < int(sic) < 10000:
        return None
    padded = f"{int(sic):04d}"
    return _BY_INDUSTRY_GROUP.get(padded[:3]) or _BY_MAJOR_GROUP.get(padded[:2])


def etf_for(sector: str | None) -> str:
    """The SPDR fund for a sector key; the market fund when the sector is unknown."""
    return SECTOR_ETFS.get(sector or "", MARKET_ETF)


def sector_for_symbol(symbol: str, sic: int | None = None) -> str | None:
    """Curated override first, then the fund itself, then the filing's SIC code."""
    symbol = symbol.upper()
    return OVERRIDES.get(symbol) or ETF_SECTORS.get(symbol) or sector_from_sic(sic)


#: A representative SIC code per sector, so synthetic filings carry a code that
#: rolls back up to the sector they were generated for.
REPRESENTATIVE_SIC: dict[str, int] = {
    "technology": 7372,  # prepackaged software
    "financials": 6022,  # state commercial banks
    "energy": 1311,  # crude petroleum and natural gas
    "health_care": 2834,  # pharmaceutical preparations
    "consumer_discretionary": 5961,  # catalog and mail-order houses
    "consumer_staples": 2080,  # beverages
    "industrials": 3720,  # aircraft and parts
    "materials": 2810,  # industrial inorganic chemicals
    "utilities": 4911,  # electric services
    "real_estate": 6798,  # real estate investment trusts
    "communication_services": 4813,  # telephone communications
}
