"""Model classes for ERI."""

from dataclasses import dataclass
import datetime
from decimal import Decimal
from pathlib import Path

from cgt_calc.model import ActionType, BrokerTransaction


class EriTransaction(BrokerTransaction):
    """Eri transaction data."""

    def __init__(
        self,
        date: datetime.date,
        isin: str,
        price: Decimal,
        currency: str,
    ) -> None:
        """Create an Eri transaction."""

        super().__init__(
            date=date,
            action=ActionType.EXCESS_REPORTED_INCOME,
            symbol=None,
            description="",
            quantity=None,
            price=price,
            fees=Decimal(0),
            amount=None,
            currency=currency,
            broker="N/A",
            isin=isin,
        )


@dataclass
class EriParserOutput:
    """Output of an ERI parser."""

    transactions: list[EriTransaction]
    output_file_name: str


class EriParser:
    """Base class for all ERI parsers."""

    def __init__(self, name: str):
        """Create a new instance with the given name."""
        self.name = name

    def parse(self, file: Path) -> EriParserOutput | None:
        """Parse the input file.

        Return None when the file is not accepted by the parser.
        """
        raise NotImplementedError
