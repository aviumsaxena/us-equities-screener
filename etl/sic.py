"""SIC code -> sector mapping.

True GICS is proprietary (S&P/MSCI licensed), so `companies.sector` is derived
from the SIC code SEC itself assigns to each filer -- free, authoritative, and
available for every one of the 8k+ filers. We use the familiar 11 GICS-style
sector *names* as labels, but the assignment is SIC-based and therefore an
approximation: SIC is an older taxonomy and disagrees with GICS on some names
(e.g. GOOGL/META are SIC 7370 "computer programming" -> Information Technology
here, where GICS says Communication Services). Swapping in a licensed GICS feed
later just repopulates these same columns -- see ARCHITECTURE.md §6.

Ranges are evaluated in order, so narrower entries must precede broader ones
(e.g. pharma 2830-2836 -> Health Care before chemicals 2800-2899 -> Materials).
"""
from __future__ import annotations

from typing import Optional

# (low, high, sector) -- inclusive, first match wins
_SIC_SECTOR_RANGES: list[tuple[int, int, str]] = [
    # --- Health Care (must precede the chemicals block) ---
    (2830, 2836, "Health Care"),               # pharma & biologics (JNJ 2834)
    (3826, 3826, "Health Care"),               # lab analytical instruments
    (3840, 3851, "Health Care"),               # medical/dental/ophthalmic devices
    (5122, 5122, "Health Care"),               # drugs wholesale
    (8000, 8099, "Health Care"),               # health services
    (8731, 8731, "Health Care"),               # commercial biological research

    # --- Consumer Staples (must precede chemicals + general retail) ---
    (2000, 2199, "Consumer Staples"),          # food, beverage, tobacco (KO 2086)
    (2840, 2844, "Consumer Staples"),          # soap, detergent, cosmetics (PG 2840)
    (5140, 5149, "Consumer Staples"),          # groceries wholesale
    (5300, 5399, "Consumer Staples"),          # general merchandise / variety (WMT 5331)
    (5400, 5499, "Consumer Staples"),          # food stores
    (5912, 5912, "Consumer Staples"),          # drug stores (precedes misc retail)

    # --- Energy ---
    (1200, 1399, "Energy"),                    # coal, oil & gas extraction
    (2911, 2911, "Energy"),                    # petroleum refining (CVX 2911)
    (4610, 4613, "Energy"),                    # pipelines
    (5171, 5172, "Energy"),                    # petroleum wholesale

    # --- Materials ---
    (1000, 1099, "Materials"),                 # metal mining
    (1400, 1499, "Materials"),                 # nonmetallic mining
    (2600, 2699, "Materials"),                 # paper & allied
    (2800, 2829, "Materials"),                 # industrial chemicals & plastics
    (2845, 2899, "Materials"),                 # paints, agricultural & misc chemicals
    (3050, 3089, "Materials"),                 # rubber & plastics
    (3200, 3299, "Materials"),                 # stone, clay, glass, concrete
    (3300, 3399, "Materials"),                 # primary metals

    # --- Information Technology (3570s precede general machinery) ---
    (3570, 3579, "Information Technology"),    # computer & office equipment (AAPL 3571)
    (3600, 3629, "Information Technology"),    # electronic & electrical equipment
    (3660, 3679, "Information Technology"),    # comms equipment, semiconductors (NVDA 3674)
    (3690, 3699, "Information Technology"),    # misc electrical
    (7370, 7379, "Information Technology"),    # software, data processing (MSFT 7372)

    # --- Communication Services ---
    (2700, 2799, "Communication Services"),    # publishing & printing
    (4800, 4899, "Communication Services"),    # telecom & broadcasting
    (7310, 7319, "Communication Services"),    # advertising
    (7810, 7841, "Communication Services"),    # motion pictures

    # --- Consumer Discretionary ---
    (2300, 2399, "Consumer Discretionary"),    # apparel
    (2500, 2599, "Consumer Discretionary"),    # furniture
    (3140, 3149, "Consumer Discretionary"),    # footwear
    (3630, 3659, "Consumer Discretionary"),    # household appliances, audio/video
    (3711, 3716, "Consumer Discretionary"),    # motor vehicles (TSLA 3711)
    (3751, 3751, "Consumer Discretionary"),    # motorcycles & bicycles
    (3942, 3949, "Consumer Discretionary"),    # toys & sporting goods
    (5200, 5299, "Consumer Discretionary"),    # building materials retail (HD 5211)
    (5500, 5599, "Consumer Discretionary"),    # auto dealers
    (5600, 5699, "Consumer Discretionary"),    # apparel retail
    (5700, 5799, "Consumer Discretionary"),    # home furnishings retail
    (5812, 5813, "Consumer Discretionary"),    # eating & drinking places
    (5900, 5999, "Consumer Discretionary"),    # misc retail (AMZN 5961)
    (7000, 7099, "Consumer Discretionary"),    # hotels & lodging
    (7900, 7999, "Consumer Discretionary"),    # amusement & recreation (DIS 7990)
    (8200, 8299, "Consumer Discretionary"),    # educational services

    # --- Financials ---
    (6000, 6199, "Financials"),                # banks & credit (JPM 6021)
    (6200, 6299, "Financials"),                # brokers & exchanges
    (6300, 6411, "Financials"),                # insurance (BRK-B 6331)
    (6712, 6770, "Financials"),                # holding & investment offices

    # --- Real Estate ---
    (6500, 6599, "Real Estate"),               # real estate
    (6798, 6798, "Real Estate"),               # REITs

    # --- Industrials (broad ranges last) ---
    (1500, 1799, "Industrials"),               # construction
    (3400, 3569, "Industrials"),               # fabricated metal, industrial machinery
    (3580, 3599, "Industrials"),               # general industrial machinery
    (3720, 3728, "Industrials"),               # aerospace & defense
    (3730, 3799, "Industrials"),               # ships, rail, misc transport equipment
    (3800, 3825, "Industrials"),               # measuring & controlling instruments
    (4000, 4099, "Industrials"),               # railroads
    (4100, 4199, "Industrials"),               # passenger transit
    (4200, 4299, "Industrials"),               # trucking
    (4400, 4499, "Industrials"),               # water transport
    (4500, 4599, "Industrials"),               # air transport
    (4700, 4799, "Industrials"),               # transportation services
    (7380, 7389, "Industrials"),               # business services (V/MA 7389; GICS: Financials)
    (8700, 8748, "Industrials"),               # engineering & management services

    # --- Utilities ---
    (4900, 4991, "Utilities"),                 # electric, gas, sanitary services
]


def sic_to_sector(sic: Optional[str]) -> Optional[str]:
    """Map a 4-digit SIC code to a sector label. Returns None for unmapped or
    malformed codes -- a NULL sector beats a wrong one."""
    if not sic:
        return None
    try:
        code = int(sic)
    except (TypeError, ValueError):
        return None
    for low, high, sector in _SIC_SECTOR_RANGES:
        if low <= code <= high:
            return sector
    return None
