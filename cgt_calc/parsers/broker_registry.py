"""Registry for all broker parsers."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, ClassVar

from cgt_calc.parsers.eri.raw import ERIRawParser
from cgt_calc.parsers.freetrade import FreetradeParser
from cgt_calc.parsers.mssb import MSSBParser
from cgt_calc.parsers.raw import RawParser
from cgt_calc.parsers.schwab import SchwabParser
from cgt_calc.parsers.schwab_equity_award_json import SchwabEquityAwardsJSONParser
from cgt_calc.parsers.sharesight import SharesightParser
from cgt_calc.parsers.trading212 import Trading212Parser
from cgt_calc.parsers.vanguard import VanguardParser

if TYPE_CHECKING:
    import argparse

    from cgt_calc.model import BrokerTransaction

    from .base_parsers import BaseParser

LOGGER = logging.getLogger(__name__)


class BrokerRegistry:
    """Registry for all broker parsers."""

    _BROKERS: ClassVar[list[type[BaseParser]]] = [
        FreetradeParser,
        RawParser,
        SchwabParser,
        SchwabEquityAwardsJSONParser,
        SharesightParser,
        Trading212Parser,
        MSSBParser,
        VanguardParser,
        # Add new brokers here
    ]

    @staticmethod
    def register_all_arguments(broker_group: argparse._ArgumentGroup) -> None:
        """Register arguments for all brokers."""
        for broker_class in BrokerRegistry._BROKERS:
            broker_class.register_arguments(broker_group)

        # ERI Raw is not a broker but is close enough to one to be here
        ERIRawParser.register_arguments(broker_group)

    @staticmethod
    def load_all_transactions(args: argparse.Namespace) -> list[BrokerTransaction]:
        """Load transactions from all brokers."""
        all_transactions: list[BrokerTransaction] = []
        for broker_class in BrokerRegistry._BROKERS:
            transactions = broker_class.load_from_args(args)
            if transactions:
                LOGGER.info(
                    "Loaded %d transactions from %s",
                    len(transactions),
                    broker_class.__name__,
                )
                all_transactions += transactions

        if len(all_transactions) == 0:
            LOGGER.warning("Found 0 broker transactions")
        else:
            print(f"Found {len(all_transactions)} broker transactions")

        # ERI Raw is not a broker but is close enough to one to be here
        all_transactions += ERIRawParser.load_from_args(args)

        all_transactions.sort(key=lambda k: k.date)
        return all_transactions
