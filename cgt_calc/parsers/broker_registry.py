"""Registry for all broker parsers."""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING, ClassVar

from colorama import Fore

from cgt_calc.logging import style_text
from cgt_calc.model import ActionType
from cgt_calc.parsers.eri.raw import ERIRawParser
from cgt_calc.parsers.freetrade import FreetradeParser
from cgt_calc.parsers.hl import HargreavesLansdownParser
from cgt_calc.parsers.interactive_brokers import InteractiveBrokersParser
from cgt_calc.parsers.mssb import MSSBParser
from cgt_calc.parsers.raw import RawParser
from cgt_calc.parsers.schwab import SchwabParser
from cgt_calc.parsers.schwab_equity_award_json import SchwabEquityAwardsJSONParser
from cgt_calc.parsers.sharesight import SharesightParser
from cgt_calc.parsers.trading212 import Trading212Parser
from cgt_calc.parsers.vanguard import VanguardParser

if TYPE_CHECKING:
    import argparse
    import datetime

    from cgt_calc.isin_converter import IsinConverter
    from cgt_calc.model import BrokerTransaction

    from .base_parsers import BaseParser

LOGGER = logging.getLogger(__name__)


def _transaction_sort_key(
    transaction: BrokerTransaction,
) -> tuple[datetime.date, int]:
    """Sort by date, with share vests first and transfers to a spouse last.

    A broker export can list the sale of vested shares before the vest itself
    (e.g. Schwab or Morgan Stanley equity awards). A disposal is validated
    against the current holding as soon as it is read, so the unsorted order
    fails with "Tried to sell not owned symbol". Vesting (``STOCK_ACTIVITY``)
    does not move the cash balance, so ordering it first cannot introduce a
    negative balance.

    Shares received from a spouse are written by hand too, and like a vest
    they cost nothing in cash, so they go first as well.

    Transfers to a spouse and gifts go last for the same reason from the
    other end. They are recorded by hand in a RAW file while the shares they
    move were acquired through a broker export, and parsers are merged in
    registry order, so such a row would otherwise be validated before the
    same-day acquisition that makes it possible. No consideration changes
    hands, so ordering them last cannot introduce a negative balance either.

    Everything else keeps its relative order, so the "buys last" ordering that
    some parsers rely on to avoid negative balances is preserved.
    """
    if transaction.action in (
        ActionType.STOCK_ACTIVITY,
        ActionType.TRANSFER_FROM_SPOUSE,
    ):
        order = 0
    elif transaction.action in (
        ActionType.TRANSFER_TO_SPOUSE,
        ActionType.GIFT,
        ActionType.GIFT_UNCONNECTED,
    ):
        order = 2
    else:
        order = 1
    return (transaction.date, order)


class BrokerRegistry:
    """Registry for all broker parsers."""

    # Ordered by pretty name, matching the docs, with the generic RAW format
    # last. Add new brokers in their sorted place.
    _BROKERS: ClassVar[list[type[BaseParser]]] = [
        SchwabParser,
        SchwabEquityAwardsJSONParser,
        FreetradeParser,
        HargreavesLansdownParser,
        InteractiveBrokersParser,
        MSSBParser,
        SharesightParser,
        Trading212Parser,
        VanguardParser,
        RawParser,
    ]

    @staticmethod
    def register_all_arguments(broker_group: argparse._ArgumentGroup) -> None:
        """Register arguments for all brokers."""
        for broker_class in BrokerRegistry._BROKERS:
            broker_class.register_arguments(broker_group)

        # ERI Raw is not a broker but is close enough to one to be here
        ERIRawParser.register_arguments(broker_group)

    @staticmethod
    def load_all_transactions(
        args: argparse.Namespace, isin_converter: IsinConverter
    ) -> list[BrokerTransaction]:
        """Load transactions from all brokers."""
        all_transactions: list[BrokerTransaction] = []
        for broker_class in BrokerRegistry._BROKERS:
            transactions = broker_class.load_from_args(args)
            if transactions:
                LOGGER.info(
                    "Loaded %d transactions from %s",
                    len(transactions),
                    broker_class.pretty_name,
                )
                all_transactions += transactions

        # ERI transactions are appended after this count, so scope the wording.
        msg = f"Found {len(all_transactions)} broker transactions"
        if len(all_transactions) == 0:
            LOGGER.warning(msg)
        else:
            LOGGER.info(
                "\n%s",
                style_text(msg, colour=Fore.CYAN, emoji="📄", stream=sys.stderr),
            )

        # ERI Raw is not a broker but is close enough to one to be here
        # Only add ERI for funds that show up in the portfolio
        isin_map = isin_converter.get_symbol_to_isin_map()
        isins = {trx.isin or isin_map.get(trx.symbol or "") for trx in all_transactions}
        all_transactions += [
            trx for trx in ERIRawParser.load_from_args(args) if trx.isin in isins
        ]

        all_transactions.sort(key=_transaction_sort_key)
        return all_transactions
