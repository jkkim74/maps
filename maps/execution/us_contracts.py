"""US quote evidence and account identity shared by paper adapters and service."""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import hashlib

@dataclass(frozen=True)
class USQuote:
    """Timestamp and delayed flag are evidence, never invented from poll time."""
    ticker: str
    exchange: str
    price: Decimal
    as_of: datetime | None
    delayed: bool | None = None

def us_account_key(settings) -> str:
    """Namespace US accounts independently of domestic account locks and ledgers."""
    return hashlib.sha256(f'US:{settings.maps_us_broker_mode}:{settings.maps_us_account_no}:{settings.maps_us_account_product_code}'.encode()).hexdigest()
