"""Function to work with dates."""
import datetime

from .const import INTERNAL_START_DATE
from .model import DateIndex


def is_date(date: datetime.date) -> bool:
    """Check if date has only date but not time."""
    if not isinstance(date, datetime.date) or isinstance(date, datetime.datetime):
        raise Exception(f'should be datetime.date: {type(date)} "{date}"')
    return True


def date_to_index(date: datetime.date) -> DateIndex:
    """Convert datetime to DateIndex."""
    assert is_date(date)
    return (date - INTERNAL_START_DATE).days


def date_from_index(date_index: int) -> datetime.date:
    """Convert DateIndex to datetime."""
    return INTERNAL_START_DATE + datetime.timedelta(days=date_index)


def get_tax_year_start(tax_year: int) -> datetime.date:
    """Return tax year start date."""
    # 6 April
    return datetime.date(tax_year, 4, 6)


def get_tax_year_end(tax_year: int) -> datetime.date:
    """Return tax year end date."""
    # 5 April
    return datetime.date(tax_year + 1, 4, 5)
