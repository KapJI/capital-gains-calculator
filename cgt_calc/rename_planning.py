"""First pass: reading a day's ticker renames as one holding.

A rename is neither a disposal nor an acquisition (TCGA 1992 s127, CG51700):
the same shares carry on under another name. Input carries dates but not
times, so a date's rows say nothing about when in the day the name changed,
and a purchase or sale recorded that day means the same shares whichever of
the two tickers it states.

The pool itself still moves where the RENAME row sits, which is what a
price-sensitive corporate action reads. What is planned here is the day's
alias graph: how many units the whole holding can give up, so that a decrease
is checked against the holding rather than against the name its row happens to
use, and which name the day ends under, so that anything left behind under the
others is gathered in when it closes.

Only ordinary purchases and disposals are read this way. A holding a split
restates, a spin-off apportions cost out of, or excess reported income adds
cost to keeps the behaviour it has: those operations do not commute with a
rename, and giving them new tax semantics is not this module's business.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Final

from .const import RENAME_DESCRIPTION_PREFIX
from .exceptions import CalculationError, InvalidTransactionError, SymbolMissingError
from .model import ActionType, Position
from .stock_split_planning import signed_quantity
from .stock_splits import quantity_sign
from .transaction_log import has_key

if TYPE_CHECKING:
    from collections.abc import Iterator
    import datetime

    from .calculator_state import CalculatorState, PreparedHistory
    from .model import BrokerTransaction


RENAME_DAY_UNSUPPORTED_ACTIONS: Final = frozenset(
    {
        # Restate or apportion the pool itself, so where the pool sits when
        # the row is read decides what they do.
        ActionType.SPIN_OFF,
        ActionType.STOCK_SPLIT,
        ActionType.FEE,
        ActionType.EXCESS_REPORTED_INCOME,
        # Take a cost out of the pool, or put one in, at a figure worked out
        # from the holding as the row finds it.
        ActionType.STOCK_ACTIVITY,
        ActionType.REINVEST_SHARES,
        ActionType.TRANSFER_FROM_SPOUSE,
        ActionType.TRANSFER_TO_SPOUSE,
        ActionType.GIFT,
        ActionType.GIFT_UNCONNECTED,
        ActionType.UNCLASSIFIED_GIFT,
        ActionType.CASH_MERGER,
        ActionType.FULL_REDEMPTION,
    }
)
"""What a rename component may not contain if this planning is to read it.

Everything that moves a holding's units or its pooled cost, other than an
ordinary purchase or sale. A component containing one of these is left to the
row-position pool, unchanged, rather than given the day-wide reading below.
"""


UNNAMED_HOLDING_ACTIONS: Final = frozenset(
    {ActionType.SPIN_OFF, ActionType.EXCESS_REPORTED_INCOME}
)
"""Rows that change a pool without naming it, so no component can exclude them.

A spin-off row names only the holding it creates: its source is chosen when
the row is read, out of the portfolio as the day's earlier rows have left it.
An excess reported income row names no ticker at all, only an ISIN, which is
resolved to the holdings it adds cost to when that row is read. Neither can be
matched to a rename component by the ticker on its own row, so a day carrying
either is left to the row-position pool in its entirety.
"""


@dataclass(frozen=True)
class RenameComponent:
    """One holding the day's renames spell under more than one ticker.

    `names` is every ticker the day's renames connect, `closing_name` the one
    they all end under, and `capacity` what the whole holding can give up that
    day: what its names opened with, plus what the day's purchases add to any
    of them.
    """

    names: tuple[str, ...]
    closing_name: str
    capacity: Decimal


def rename_pair(transaction: BrokerTransaction) -> tuple[str, str]:
    """Return the old and new names one RENAME row states."""
    if transaction.symbol is None:
        raise SymbolMissingError(transaction)
    old_symbol = transaction.description[len(RENAME_DESCRIPTION_PREFIX) :]
    if (
        not transaction.description.startswith(RENAME_DESCRIPTION_PREFIX)
        or not old_symbol
    ):
        raise InvalidTransactionError(
            transaction,
            "Rename transaction does not identify the old symbol",
        )
    return old_symbol, transaction.symbol


def plan_renames(
    state: CalculatorState,
    date_index: datetime.date,
    day_transactions: list[BrokerTransaction],
) -> None:
    """Record what each of the day's renamed holdings is and what it holds.

    Runs before any of the day's rows are processed, the way reorganisations
    are planned, so the graph saying which names are one holding exists before
    a row states one of them.
    """
    pairs = [
        rename_pair(transaction)
        for transaction in day_transactions
        if transaction.action is ActionType.RENAME
    ]
    if not pairs:
        return
    # Every component is read for sense first, whatever else the day holds.
    # A holding renamed twice, or renamed to two names, ends up wherever the
    # input's order of those rows puts it, and neither this planning nor the
    # row-position pool it may fall back to can answer that. So the refusals
    # come before anything decides which reading the day gets.
    components = [
        (names, _one_hop_renames(names, pairs, date_index))
        for names in _components(pairs)
    ]
    for _, renames in components:
        _refuse_rename_chain(renames, date_index)
    # A day carrying a row that changes a pool without naming it cannot be
    # read as a whole: a decrease this planning lets through under one name
    # rather than another changes what that pool holds by the time the row
    # is read, and no component can be told to expect it.
    if any(
        transaction.action in UNNAMED_HOLDING_ACTIONS
        for transaction in day_transactions
    ):
        return
    planned: dict[str, RenameComponent] = {}
    for names, renames in components:
        rows = [
            transaction
            for transaction in day_transactions
            if transaction.symbol in names
        ]
        if any(
            transaction.action in RENAME_DAY_UNSUPPORTED_ACTIONS for transaction in rows
        ):
            continue
        component = RenameComponent(
            names=tuple(sorted(names)),
            closing_name=_closing_name(renames),
            capacity=sum(
                (state.run.portfolio.get(name, Position()).quantity for name in names),
                signed_quantity([row for row in rows if quantity_sign(row.action) > 0]),
            ),
        )
        for name in names:
            planned[name] = component
    if planned:
        state.history.rename_components[date_index] = planned


def rename_day_units(
    history: PreparedHistory, date_index: datetime.date, symbol: str
) -> Decimal | None:
    """Return what a decrease of this holding may still take today.

    The whole holding's capacity, less what the day's disposals have already
    taken out of it under any of its names. Everything disposed of on one day
    is one disposal (TCGA 1992 s105(1)), and the day's renames make these
    names one holding, so a sale written under either name draws on the same
    shares and they cannot be sold twice.

    None when the day plans no such holding here, or when it has nothing to
    give: a day that never held the shares is left to say so as it always has.
    """
    component = history.rename_components.get(date_index, {}).get(symbol)
    if component is None or component.capacity == 0:
        return None
    taken = sum(
        (
            history.disposal_list[date_index][name].quantity
            for name in component.names
            if has_key(history.disposal_list, date_index, name)
        ),
        Decimal(0),
    )
    return component.capacity - taken


def reconcile_rename_day(state: CalculatorState, date_index: datetime.date) -> None:
    """Gather what the day left under a renamed name into the closing one.

    The rename has already moved the pool, where its row sat. What is left
    under a name it moved away from is what the rows after it recorded there:
    an ordinary purchase or sale of the same holding, written under the name
    the day is retiring. Gathering it in is not a second tax event, and no
    rename is recorded for it - the units and their cost are the same units
    and cost under the name the day ends on (TCGA 1992 s127, CG51700).
    """
    for name, component in state.history.rename_components.get(date_index, {}).items():
        if name == component.closing_name:
            continue
        position = state.run.portfolio.pop(name, None)
        if position is not None:
            state.run.portfolio[component.closing_name] += position
        # The units keep the accounts that put them there; only the name
        # changes. Which holdings have emptied is settled where every earlier
        # day has closed, in ``_open_transaction_day``.
        moved = state.history.holding_sources.pop(name, set())
        if moved:
            state.history.holding_sources[component.closing_name] |= moved


def two_renames_message(
    old_symbol: str,
    date_index: datetime.date,
    first: str,
    second: str,
) -> str:
    """Explain why one ticker cannot be renamed to two names in a day."""
    return (
        f"{old_symbol} is renamed to both {first} and {second} on "
        f"{date_index}. The holding goes to one name or the other, and a "
        "date carries no order to choose by, so which one cannot be "
        "established and the pool would be replayed under a name it never "
        "reached. Leave the row that does not belong out, or work this "
        "day out by hand (consider professional advice)."
    )


def rename_chain_message(
    old_symbol: str,
    middle: str,
    new_symbol: str,
    date_index: datetime.date,
) -> str:
    """Explain why a holding renamed twice in a day cannot be worked out."""
    return (
        f"Cannot apply the renames of {old_symbol} on {date_index}: it is "
        f"renamed to {middle}, and {middle} is renamed to {new_symbol}, the "
        "same day. The renames are applied in the order the input lists them, "
        "so where the holding ends up depends on which of the two rows comes "
        "first, and a date carries no order to choose by. Put the renames on "
        "the days they happened, or work this day out by hand (consider "
        "professional advice)."
    )


def _components(pairs: list[tuple[str, str]]) -> Iterator[set[str]]:
    """Group the day's renames into the holdings they connect.

    A rename says two tickers are one security, so two holdings the day
    renames onto the same name belong together, even though no single row
    names them both. The graph is walked in both directions for that reason.
    """
    neighbours: dict[str, set[str]] = defaultdict(set)
    for old_name, new_name in pairs:
        neighbours[old_name].add(new_name)
        neighbours[new_name].add(old_name)
    seen: set[str] = set()
    for name in neighbours:
        if name in seen:
            continue
        group = {name}
        frontier = [name]
        while frontier:
            for other in neighbours[frontier.pop()]:
                if other not in group:
                    group.add(other)
                    frontier.append(other)
        seen |= group
        yield group


def _one_hop_renames(
    names: set[str],
    pairs: list[tuple[str, str]],
    date_index: datetime.date,
) -> dict[str, str]:
    """Return one component's old to new mappings, refusing a split holding."""
    renames: dict[str, str] = {}
    for old_name, new_name in pairs:
        if old_name not in names:
            continue
        existing = renames.get(old_name)
        if existing is not None and existing != new_name:
            raise CalculationError(
                two_renames_message(old_name, date_index, existing, new_name)
            )
        renames[old_name] = new_name
    return renames


def _refuse_rename_chain(renames: dict[str, str], date_index: datetime.date) -> None:
    """Refuse a holding this component's renames move twice in one day.

    A name that is renamed onward from the name it was just renamed to ends
    up wherever the input's order of those two rows puts it, and swapping the
    rows moves it somewhere else. A date carries no order, so neither reading
    is established. A cycle is the same thing joined up, and the same test
    catches it. The names are taken in order so that one day is always
    refused by naming the same pair.
    """
    for name in sorted({*renames, *renames.values()}):
        onward = renames.get(name)
        if onward is not None and onward in renames:
            raise CalculationError(
                rename_chain_message(name, onward, renames[onward], date_index)
            )


def _closing_name(renames: dict[str, str]) -> str:
    """Return the one name this holding's renames leave it under.

    One holding, one destination: a second one could only join this component
    through a name that is both renamed and renamed to, which is the chain
    ``_refuse_rename_chain`` has already ruled out.
    """
    (closing_name,) = set(renames.values())
    return closing_name
