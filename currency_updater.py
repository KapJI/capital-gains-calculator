#!/usr/bin/env python3
"""Currency updater main module."""
import argparse
import calendar
from collections import OrderedDict
import csv
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
import time
from typing import Any, Callable, TextIO, Tuple, Type, TypeVar, cast
import urllib.request
from xml.etree import ElementTree

_BaseDecoratedFunc = Callable[..., Any]  # type: ignore
DecoratedFunc = TypeVar("DecoratedFunc", bound=_BaseDecoratedFunc)


def retry(
    times: int, exceptions: Tuple[Type[Exception]]
) -> Callable[[DecoratedFunc], DecoratedFunc]:
    """Retry Decorator."""

    def decorator(func: DecoratedFunc) -> DecoratedFunc:
        def newfn(*args: Any, **kwargs: Any) -> Any:  # type: ignore
            attempt = 0
            while attempt < times:
                try:
                    return func(*args, **kwargs)
                except exceptions:
                    print(
                        f"Exception thrown when attempting to run {func}, attempt "
                        f"{attempt} of {times}"
                    )
                    attempt += 1
                time.sleep(0.1)
            return func(*args, **kwargs)

        return cast(DecoratedFunc, newfn)

    return decorator


@retry(3, (Exception,))
def hrmc_urlopen(date: datetime) -> TextIO:
    """Create a request for the currency exchange rates of the input date."""
    date_str = date.strftime("%m%y")

    http_headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_6_8) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/49.0.2623.112 Safari/537.3"
    }

    try:
        return cast(
            TextIO,
            urllib.request.urlopen(
                urllib.request.Request(
                    "http://www.hmrc.gov.uk/softwaredevelopers/rates/"
                    f"exrates-monthly-{date_str}.xml",
                    headers=http_headers,
                )
            ),
        )
    except urllib.error.HTTPError:
        return cast(
            TextIO,
            urllib.request.urlopen(
                urllib.request.Request(
                    "http://www.hmrc.gov.uk/softwaredevelopers/rates/"
                    f"exrates-monthly-{date_str}.XML",
                    headers=http_headers,
                )
            ),
        )


def hrmc_get_currency_conversion(date: datetime) -> Decimal:
    """Return the currency conversion rate for the input date."""
    with hrmc_urlopen(date) as request:
        tree = ElementTree.parse(request)
        root = tree.getroot()
        for currency in root:
            code = currency.find("currencyCode")
            rate = currency.find("rateNew")
            if (
                code is not None
                and rate is not None
                and rate.text
                and code.text
                and code.text.lower() == "usd"
            ):
                return Decimal(rate.text)

    raise RuntimeError(f"Currency value not found for {date}")


def main() -> None:
    """Program main."""
    parser = argparse.ArgumentParser(
        description="Download HMRC monthly GBP/USD exchange rates"
    )
    parser.add_argument(
        "start",
        help="date start in YYYY-MM format",
    )
    parser.add_argument(
        "--end",
        help="date end in YYYY-MM format (default to now)",
        default=datetime.now().strftime("%Y-%m"),
    )

    args = parser.parse_args()
    from_date = datetime.strptime(args.start, "%Y-%m")
    to_date = datetime.strptime(args.end, "%Y-%m")
    assert from_date <= to_date
    currency_data = OrderedDict()
    while from_date <= to_date:
        currency_data[from_date] = hrmc_get_currency_conversion(from_date)
        days_in_month = calendar.monthrange(from_date.year, from_date.month)[1]
        from_date = from_date + timedelta(days=days_in_month)

    csv_path = (
        Path(__file__).parent / "cgt_calc" / "resources" / "GBP_USD_monthly_history.csv"
    ).absolute()

    with Path(csv_path).open("w", newline="", encoding="utf-8") as csvfile:
        csvwriter = csv.writer(
            csvfile, delimiter=",", dialect="unix", quoting=csv.QUOTE_NONE
        )
        csvwriter.writerow(["month", "price"])
        for date, value in currency_data.items():
            date_str = date.strftime("%m/%Y")
            csvwriter.writerow([date_str, value])


if __name__ == "__main__":
    main()
