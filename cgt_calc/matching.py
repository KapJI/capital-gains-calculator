"""Second pass: matching disposals against acquisitions."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import datetime
from decimal import Decimal
from fractions import Fraction
import logging
from typing import TYPE_CHECKING, NamedTuple

from .const import (
    BED_AND_BREAKFAST_DAYS,
    ERI_TAX_DATE_DELTA,
    INTERNAL_START_DATE,
    MAX_CONTENDED_DATES_SHOWN,
)
from .exceptions import CalculationError
from .model import (
    CalculationEntry,
    CalculationLog,
    ExcessReportedIncome,
    ExcessReportedIncomeDistribution,
    HmrcTransactionData,
    Position,
    RuleType,
    SpinOff,
)
from .stock_splits import (
    StockSplitDetail,
    StockSplitEvent,
    UnresolvedRatio,
    scale_quantity,
    unscale_quantity,
)
from .transaction_log import add_to_list, has_key
from .util import normalize_amount, round_decimal, strip_zeros

if TYPE_CHECKING:
    from collections.abc import Callable

    from .calculator_state import CalculatorState

LOGGER = logging.getLogger(__name__)


class WalkResult(NamedTuple):
    """What one walk over the days accumulated."""

    disposal_count: int
    disposal_proceeds: Decimal
    allowable_costs: Decimal
    capital_gain: Decimal
    capital_loss: Decimal
    gift_loss: Decimal
    exempt_disposal_count: int
    exempt_disposal_proceeds: Decimal
    calculation_log: CalculationLog


@dataclass
class DisposalContext:
    """The running state of one disposal as the identification rules consume it."""

    symbol: str
    date_index: datetime.date
    no_gain_no_loss: bool
    disposal: HmrcTransactionData
    disposal_quantity: Decimal
    disposal_price: Decimal
    current_quantity: Decimal
    current_amount: Decimal
    chargeable_gain: Decimal
    calculation_entries: list[CalculationEntry]


class Matcher:
    """Matches disposals against acquisitions across the whole history."""

    def __init__(
        self,
        state: CalculatorState,
        tax_year_start_date: datetime.date,
        tax_year_end_date: datetime.date,
        interest_fund_tickers: list[str],
        date_in_tax_year: Callable[[datetime.date], bool],
        is_cgt_exempt: Callable[[str], bool],
    ):
        """Create matcher object."""
        self.history = state.history
        self.run = state.run
        self.tax_year_start_date = tax_year_start_date
        self.tax_year_end_date = tax_year_end_date
        self.interest_fund_tickers = interest_fund_tickers
        self.date_in_tax_year = date_in_tax_year
        self.is_cgt_exempt = is_cgt_exempt

    def get_eri(self, symbol: str, date: datetime.date) -> ExcessReportedIncome | None:
        """Return Excess Reported Income at specific date for the input symbol."""
        return self.history.eris.get(date, {}).get(symbol)

    def _disposal_log_prefix(self, date_index: datetime.date, symbol: str) -> str:
        """Name the kind of disposal recorded for a symbol on a day.

        A sale with a gift to someone unconnected is one ordinary disposal,
        reported as a sale.

        A disposal of an instrument the user has classified as CGT-exempt is
        named "exempt" whichever kind it is, so a gift of one is reported as an
        exempt disposal rather than as a gift.
        """
        if self.is_cgt_exempt(symbol):
            return "exempt"
        key = (date_index, symbol)
        connected = self.history.gift_disposals.get(key)
        if connected:
            return "gift"
        if connected is None or key in self.history.sale_days:
            return "sell"
        return "gift-unconnected"

    def process_acquisition(
        self,
        symbol: str,
        date_index: datetime.date,
    ) -> list[CalculationEntry]:
        """Process single acquisition."""
        acquisition = self.history.acquisition_list[date_index][symbol]
        spin_offs_here = [
            spin_off
            for spin_off in self.history.spin_offs.get(date_index, [])
            if spin_off.dest == symbol
        ]
        spin_off = spin_offs_here[0] if spin_offs_here else None
        if spin_offs_here:
            # A spin-off carries a share of the source's cost across, and the
            # original cost is apportioned between the two holdings at the
            # reorganisation itself (CG51976). The first pass could only
            # estimate the source pool, so it recorded an estimate for this
            # holding; here the pool is authoritative and in GBP. Swap exactly
            # the estimate out of the day's acquisitions, which may also hold
            # ordinary purchases of the same symbol, and take the source's
            # side at the same moment so that what one gives up is what the
            # other receives.
            carried = Decimal(0)
            # Several rows for one spin-off, as when the holding is split
            # across brokers, are one reorganisation: the split of value is by
            # the whole of what was received, and the source gives up its
            # share once. Rows from different sources stay separate events,
            # taken in the order they happened.
            events: dict[str, list[SpinOff]] = {}
            for row in spin_offs_here:
                events.setdefault(row.source, []).append(row)
            for source_symbol, rows in events.items():
                first = rows[0]
                source = self.run.portfolio[
                    self._spin_off_pool(date_index, source_symbol, first.dest)
                ]
                assert first.source_price is not None
                assert first.dest_price is not None
                received = sum((row.quantity for row in rows), Decimal(0))
                source_value = source.quantity * first.source_price
                total_value = source_value + received * first.dest_price
                proportion = source_value / total_value if total_value else Decimal(1)
                share = round_decimal((1 - proportion) * source.amount, 2)
                carried += share
                self.run.spin_off_entries[date_index][source_symbol].append(
                    CalculationEntry(
                        RuleType.SPIN_OFF,
                        quantity=source.quantity,
                        amount=-source.amount,
                        new_quantity=source.quantity,
                        gain=None,
                        # Fees, if any, are already accounted for on the
                        # acquisition of spun-off shares
                        fees=Decimal(0),
                        new_pool_cost=source.amount - share,
                        allowable_cost=source.amount - share,
                        spin_off=first,
                    )
                )
                source.amount -= share
            acquisition.amount += carried - self.history.spin_off_estimates.pop(
                (date_index, symbol), Decimal(0)
            )
        modified_amount = acquisition.amount
        position = self.run.portfolio[symbol]
        calculation_entries = []
        # Management fee transaction can have 0 quantity
        assert acquisition.quantity >= 0
        # Stock split can have 0 amount
        assert acquisition.amount >= 0

        bnb_acquisition = HmrcTransactionData()
        bed_and_breakfast_fees = Decimal(0)

        if acquisition.quantity > 0 and has_key(self.run.bnb_list, date_index, symbol):
            bnb_acquisition = self.run.bnb_list[date_index][symbol]
            assert bnb_acquisition.quantity <= acquisition.quantity
            # Multiply by the B&B quantity before dividing to avoid rounding errors from division
            bnb_cost_basis = normalize_amount(
                (bnb_acquisition.quantity * acquisition.amount) / acquisition.quantity
            )
            modified_amount -= bnb_cost_basis
            modified_amount += bnb_acquisition.amount
            assert modified_amount > 0
            bed_and_breakfast_fees = (
                acquisition.fees * bnb_acquisition.quantity / acquisition.quantity
            )
            calculation_entries.append(
                CalculationEntry(
                    rule_type=RuleType.BED_AND_BREAKFAST,
                    quantity=bnb_acquisition.quantity,
                    amount=-bnb_acquisition.amount,
                    new_quantity=position.quantity + bnb_acquisition.quantity,
                    new_pool_cost=position.amount + bnb_acquisition.amount,
                    fees=bed_and_breakfast_fees,
                    allowable_cost=acquisition.amount,
                    eris=bnb_acquisition.eris,
                )
            )
        self.run.portfolio[symbol] += Position(
            acquisition.quantity,
            modified_amount,
        )
        if (
            acquisition.quantity - bnb_acquisition.quantity > 0
            or bnb_acquisition.quantity == 0
        ):
            calculation_entries.append(
                CalculationEntry(
                    rule_type=RuleType.SECTION_104,
                    quantity=acquisition.quantity - bnb_acquisition.quantity,
                    amount=-(modified_amount - bnb_acquisition.amount),
                    new_quantity=position.quantity + acquisition.quantity,
                    new_pool_cost=position.amount + modified_amount,
                    fees=acquisition.fees - bed_and_breakfast_fees,
                    allowable_cost=acquisition.amount,
                    spin_off=spin_off,
                )
            )
        return calculation_entries

    def process_disposal(
        self,
        symbol: str,
        date_index: datetime.date,
        *,
        no_gain_no_loss: bool = False,
    ) -> tuple[Decimal, list[CalculationEntry]]:
        """Process a single disposal.

        With ``no_gain_no_loss`` the event is a transfer to a spouse/civil
        partner: it uses the same same-day/30-day/Section 104 identification as a
        disposal, but each matched tranche's deemed proceeds equal its allowable
        cost so the gain is nil (TCGA 1992 s58), and the source rows come from
        ``transfer_to_spouse_list`` (which carry no fees and no proceeds).
        """
        disposal = (
            self.history.transfer_to_spouse_list
            if no_gain_no_loss
            else self.history.disposal_list
        )[date_index][symbol]
        ctx = DisposalContext(
            symbol=symbol,
            date_index=date_index,
            no_gain_no_loss=no_gain_no_loss,
            disposal=disposal,
            disposal_quantity=disposal.quantity,
            disposal_price=disposal.amount / disposal.quantity,
            current_quantity=self.run.portfolio[symbol].quantity,
            current_amount=self.run.portfolio[symbol].amount,
            chargeable_gain=Decimal(0),
            calculation_entries=[],
        )
        assert ctx.disposal_quantity <= ctx.current_quantity
        self._match_same_day(ctx)
        self._match_bed_and_breakfast(ctx)
        self._match_section_104(ctx)

        assert round_decimal(ctx.disposal_quantity, 23) == 0, (
            f"disposal quantity {ctx.disposal_quantity}"
        )
        self.run.portfolio[ctx.symbol] = Position(
            ctx.current_quantity, normalize_amount(ctx.current_amount)
        )
        ctx.chargeable_gain = round_decimal(ctx.chargeable_gain, 2)
        return ctx.chargeable_gain, ctx.calculation_entries

    def _match_same_day(self, ctx: DisposalContext) -> None:
        """Identify the disposal against the same day's acquisitions."""
        # Same day rule is first, against the day's real purchases only. Shares
        # from a split are not an acquisition (TCGA 1992 s127, CG51805) and cost
        # nothing, so matching them would hand the disposal a nil allowable cost
        # and, on a day that also has a purchase, spread that purchase's cost
        # over the free shares as well.
        same_day_acquisition = self._matchable_acquisition(ctx.date_index, ctx.symbol)
        if same_day_acquisition.quantity > 0:
            available_quantity = min(
                ctx.disposal_quantity, same_day_acquisition.quantity
            )
            if available_quantity > 0:
                fees = ctx.disposal.fees * available_quantity / ctx.disposal.quantity

                # Multiply by available_quantity before divide to avoid rounding errors from division
                acquisition_cost = normalize_amount(
                    (available_quantity * same_day_acquisition.amount)
                    / same_day_acquisition.quantity
                )

                acquisition_price = acquisition_cost / available_quantity
                # No gain/no loss: deemed proceeds equal the allowable cost.
                same_day_amount = (
                    acquisition_cost
                    if ctx.no_gain_no_loss
                    else available_quantity * ctx.disposal_price
                )
                same_day_proceeds = same_day_amount + fees
                same_day_allowable_cost = acquisition_cost + fees
                same_day_gain = same_day_proceeds - same_day_allowable_cost
                ctx.chargeable_gain += same_day_gain
                LOGGER.debug(
                    "SAME DAY, quantity %s, gain %s, disposal price %s, "
                    "acquisition price %s",
                    available_quantity,
                    same_day_gain,
                    ctx.disposal_price,
                    acquisition_price,
                )
                ctx.disposal_quantity -= available_quantity
                ctx.current_quantity -= available_quantity
                # These shares shouldn't be added to Section 104 holding
                ctx.current_amount -= acquisition_cost
                if ctx.current_quantity == 0:
                    assert round_decimal(ctx.current_amount, 23) == 0, (
                        f"current amount {ctx.current_amount}"
                    )
                ctx.calculation_entries.append(
                    CalculationEntry(
                        rule_type=(
                            RuleType.TRANSFER_TO_SPOUSE
                            if ctx.no_gain_no_loss
                            else RuleType.SAME_DAY
                        ),
                        quantity=available_quantity,
                        amount=same_day_amount,
                        gain=same_day_gain,
                        allowable_cost=same_day_allowable_cost,
                        fees=fees,
                        new_quantity=ctx.current_quantity,
                        new_pool_cost=ctx.current_amount,
                    )
                )

    def _match_bed_and_breakfast(self, ctx: DisposalContext) -> None:
        """Identify the disposal against the next 30 days' acquisitions."""
        # Bed and breakfast rule next
        if ctx.disposal_quantity > 0:
            eris = []
            eri = self.get_eri(ctx.symbol, ctx.date_index)
            if eri:
                eris.append(eri)

            # A reorganisation between the disposal and the repurchase
            # leaves the two counted in different units. The ratios compose
            # exactly, as one fraction, and are divided by once at the end:
            # multiplying rounded decimal multipliers day by day compounds the
            # very error the exact ratio removed.
            cumulative_ratio = Fraction(1)
            unresolved_splits: list[tuple[datetime.date, StockSplitEvent]] = []
            effective_symbol = ctx.symbol

            for i in range(BED_AND_BREAKFAST_DAYS):
                search_index = ctx.date_index + datetime.timedelta(days=i + 1)
                # HMRC treats renames as the same security for B&B purposes.
                # A reorganisation is recorded under the name the holding
                # carried when it happened, which on a day the holding is also
                # renamed is the name it entered the day under. Look there
                # first and under the day's new name second, then carry on
                # under the name the day ends with. Only one can be there:
                # a day whose rename pools two holdings is refused before
                # either pass reaches it (see apply_split_openings). A
                # reorganisation of a holding renamed *into* this name is
                # deliberately not picked up: the shares disposed of here left
                # before that one arrived, so they were never its shares.
                renames = self.history.rename_list.get(search_index, {})
                day_splits = self.history.splits.get(search_index, {})
                renamed_symbol = renames.get(effective_symbol, effective_symbol)
                transformation = day_splits.get(effective_symbol) or day_splits.get(
                    renamed_symbol
                )
                effective_symbol = renamed_symbol
                if transformation is not None:
                    if transformation.ratio is None:
                        unresolved_splits.append((search_index, transformation.event))
                    else:
                        cumulative_ratio *= transformation.ratio
                    # A reorganisation this close to a disposal is worth a
                    # second look, whether or not a match follows.
                    LOGGER.warning(
                        "A split happened shortly after a disposal of %s, double check these transactions."
                        "Disposed on %s and split happened on %s",
                        ctx.symbol,
                        ctx.date_index,
                        search_index,
                    )

                # ERI is distributed annually, but when a fund closes we might have
                # multiple ERI distributions in close succession
                eri = self.get_eri(effective_symbol, search_index)
                if eri:
                    eris.append(eri)
                acquisition = self._matchable_acquisition(
                    search_index, effective_symbol
                )
                if acquisition.quantity > 0:
                    bnb_acquisition = (
                        self.run.bnb_list[search_index][effective_symbol]
                        if has_key(self.run.bnb_list, search_index, effective_symbol)
                        else HmrcTransactionData()
                    )
                    assert bnb_acquisition.quantity <= acquisition.quantity

                    same_day_disposal = (
                        self.history.disposal_list[search_index][effective_symbol]
                        if has_key(
                            self.history.disposal_list, search_index, effective_symbol
                        )
                        else HmrcTransactionData()
                    )
                    # A same-day transfer to spouse competes for the same-day
                    # acquisition like a same-day sale, so reserve its share too.
                    if has_key(
                        self.history.transfer_to_spouse_list,
                        search_index,
                        effective_symbol,
                    ):
                        same_day_disposal = (
                            same_day_disposal
                            + self.history.transfer_to_spouse_list[search_index][
                                effective_symbol
                            ]
                        )
                    if same_day_disposal.quantity > acquisition.quantity:
                        # If the number of shares disposed of exceeds the number
                        # acquired on the same day the excess shares will be identified
                        # in the normal way.
                        continue

                    # This can be some management fee entry or already used
                    # by bed and breakfast rule
                    if (
                        acquisition.quantity
                        - same_day_disposal.quantity
                        - bnb_acquisition.quantity
                        == 0
                    ):
                        continue
                    if any(
                        spin_off.dest == effective_symbol
                        for spin_off in self.history.spin_offs.get(search_index, [])
                    ):
                        # What those shares cost is a share of the source's pool
                        # on the day of the spin-off, and the walk has not
                        # reached that day. The only figure to hand is the
                        # first-pass estimate, and that is wrong after any
                        # profitable sale, so refuse rather than use it.
                        raise CalculationError(
                            f"Cannot compute the disposal of {ctx.symbol} on "
                            f"{ctx.date_index}: a spin-off added {effective_symbol} "
                            f"shares on {search_index}, within the following 30 "
                            "days, and the bed and breakfast rule would identify "
                            "this disposal against them. What they cost is a "
                            "share of the source holding's pool on the day of the "
                            "spin-off, which is not settled until that day, so "
                            "this tool cannot say what they cost here.\n"
                            "What to do: run again with this one disposal left "
                            "out. That run still applies the spin-off to both "
                            "holdings and shows what the spun-off shares cost, "
                            f"as the {effective_symbol} acquisition on "
                            f"{search_index}. Identify this disposal against them "
                            "at that cost by hand (consider professional advice). "
                            f"Any later {effective_symbol} figures in that run "
                            "still include the shares disposed of here, so check "
                            "those by hand as well. Do not leave the symbol or "
                            "the spin-off row out: the spin-off also reduces the "
                            "source holding's cost."
                        )
                    # Bed and breakfasting is a record of how the disposal was
                    # matched rather than a problem. Surface it for the computed
                    # tax year; the rest of the history walk logs it at DEBUG.
                    LOGGER.log(
                        logging.INFO
                        if self.date_in_tax_year(ctx.date_index)
                        else logging.DEBUG,
                        "Bed & breakfast match: %s %s %s, re-acquired %s",
                        ctx.symbol,
                        "transferred to spouse" if ctx.no_gain_no_loss else "disposed",
                        ctx.date_index,
                        search_index,
                    )
                    # The conversion needs the ratio, so this is where an
                    # unrecoverable one is refused. Not merely for happening
                    # inside the window: an acquisition before it, or a
                    # disposal already matched, needs no conversion at all.
                    if unresolved_splits:
                        split_date, unresolved_event = unresolved_splits[0]
                        raise CalculationError(
                            self._unresolved_bnb_message(
                                unresolved_event, ctx.symbol, ctx.date_index, split_date
                            )
                        )
                    if cumulative_ratio != 1:
                        LOGGER.warning(
                            "Bed & breakfast for %s is taking into account a %sx split "
                            "that happened shortly before the repurchase of shares",
                            ctx.symbol,
                            cumulative_ratio,
                        )
                    # Everything reserved here is counted in the acquisition's
                    # own units, so the whole remainder converts to the
                    # disposal's units once. Converting only the acquisition
                    # would subtract post-split counts from a pre-split one and
                    # can go negative.
                    available_acquisition_units = (
                        acquisition.quantity
                        - same_day_disposal.quantity
                        - bnb_acquisition.quantity
                    )
                    available_quantity, consumed_acquisition_units = (
                        self._match_across_splits(
                            ctx.disposal_quantity,
                            available_acquisition_units,
                            cumulative_ratio,
                            symbol=ctx.symbol,
                            date_index=ctx.date_index,
                            search_index=search_index,
                        )
                    )
                    fees = (
                        ctx.disposal.fees * available_quantity / ctx.disposal.quantity
                    )
                    # Multiply by the consumed quantity before dividing to
                    # avoid rounding errors from division. Both counts here
                    # are in the acquisition's own units.
                    bnb_acquisition_cost = normalize_amount(
                        (consumed_acquisition_units * acquisition.amount)
                        / acquisition.quantity
                    )
                    acquisition_price = bnb_acquisition_cost / available_quantity
                    # No gain/no loss: deemed proceeds equal the allowable cost.
                    bed_and_breakfast_amount = (
                        bnb_acquisition_cost
                        if ctx.no_gain_no_loss
                        else available_quantity * ctx.disposal_price
                    )
                    bed_and_breakfast_proceeds = bed_and_breakfast_amount + fees
                    bed_and_breakfast_allowable_cost = bnb_acquisition_cost + fees
                    # ERI needs to be reported when doing bed and breakfast as if you
                    # held the stocks at the reporting end date.
                    # https://www.rawknowledge.ltd/eri-explained-four-tricky-questions-answered/
                    total_dist_amount = Decimal(0)
                    for eri in eris:
                        eri_distribution = ExcessReportedIncomeDistribution(
                            price=eri.price,
                            amount=available_quantity * eri.price,
                            quantity=available_quantity,
                        )
                        total_dist_amount += eri_distribution.amount
                        if self.date_in_tax_year(eri.distribution_date):
                            self.run.eris_distribution[eri.distribution_date][
                                ctx.symbol
                            ] += eri_distribution

                    bed_and_breakfast_gain = (
                        bed_and_breakfast_proceeds - bed_and_breakfast_allowable_cost
                    )
                    ctx.chargeable_gain += bed_and_breakfast_gain
                    LOGGER.debug(
                        "BED & BREAKFAST, quantity %s, gain %s, disposal price %s, "
                        "acquisition price %s%s",
                        available_quantity,
                        bed_and_breakfast_gain,
                        ctx.disposal_price,
                        acquisition_price,
                        f", added_excess_income: {total_dist_amount}"
                        if total_dist_amount > 0
                        else "",
                    )
                    ctx.disposal_quantity -= available_quantity

                    # Multiply by available_quantity before divide to avoid rounding errors from division
                    amount_delta = normalize_amount(
                        (available_quantity * ctx.current_amount) / ctx.current_quantity
                    )

                    ctx.current_quantity -= available_quantity
                    ctx.current_amount -= amount_delta
                    if ctx.current_quantity == 0:
                        assert round_decimal(ctx.current_amount, 23) == 0, (
                            f"current amount {ctx.current_amount}"
                        )
                    add_to_list(
                        self.run.bnb_list,
                        search_index,
                        effective_symbol,
                        consumed_acquisition_units,
                        amount_delta + total_dist_amount,
                        Decimal(0),
                        eris,
                    )
                    ctx.calculation_entries.append(
                        CalculationEntry(
                            rule_type=(
                                RuleType.TRANSFER_TO_SPOUSE
                                if ctx.no_gain_no_loss
                                else RuleType.BED_AND_BREAKFAST
                            ),
                            quantity=available_quantity,
                            amount=bed_and_breakfast_amount,
                            gain=bed_and_breakfast_gain,
                            allowable_cost=bed_and_breakfast_allowable_cost,
                            fees=fees,
                            bed_and_breakfast_date_index=search_index,
                            new_quantity=ctx.current_quantity,
                            new_pool_cost=ctx.current_amount,
                        )
                    )
                    # If we completely matched the current disposal,
                    # there's no need to keep looking for more B&B days
                    if ctx.disposal_quantity <= 0:
                        break

    def _match_section_104(self, ctx: DisposalContext) -> None:
        """Identify what is left of the disposal against the Section 104 pool."""
        if ctx.disposal_quantity > 0:
            available_quantity = ctx.disposal_quantity
            fees = ctx.disposal.fees * available_quantity / ctx.disposal.quantity

            # Multiply by available_quantity before divide to avoid rounding errors from division
            amount_delta = normalize_amount(
                (available_quantity * ctx.current_amount) / ctx.current_quantity
            )

            # No gain/no loss: deemed proceeds equal the allowable cost.
            r104_amount = (
                amount_delta
                if ctx.no_gain_no_loss
                else available_quantity * ctx.disposal_price
            )
            r104_proceeds = r104_amount + fees
            r104_allowable_cost = amount_delta + fees
            r104_gain = r104_proceeds - r104_allowable_cost
            ctx.chargeable_gain += r104_gain
            LOGGER.debug(
                "SECTION 104, quantity %s, gain %s, proceeds amount %s, "
                "allowable cost %s",
                available_quantity,
                r104_gain,
                r104_proceeds,
                r104_allowable_cost,
            )
            ctx.disposal_quantity -= available_quantity
            ctx.current_quantity -= available_quantity
            ctx.current_amount -= amount_delta
            if ctx.current_quantity == 0:
                assert round_decimal(ctx.current_amount, 10) == 0, (
                    f"current amount {ctx.current_amount}"
                )
            ctx.calculation_entries.append(
                CalculationEntry(
                    rule_type=(
                        RuleType.TRANSFER_TO_SPOUSE
                        if ctx.no_gain_no_loss
                        else RuleType.SECTION_104
                    ),
                    quantity=available_quantity,
                    amount=r104_amount,
                    gain=r104_gain,
                    allowable_cost=r104_allowable_cost,
                    fees=fees,
                    new_quantity=ctx.current_quantity,
                    new_pool_cost=ctx.current_amount,
                )
            )
            ctx.disposal_quantity = Decimal(0)

    def process_rename(self, old: str, new: str) -> CalculationEntry:
        """Transfer pool from old ticker to new ticker (no disposal)."""
        pos = self.run.portfolio.pop(old, Position())
        self.run.portfolio[new] += pos
        return CalculationEntry(
            rule_type=RuleType.RENAME,
            quantity=pos.quantity,
            amount=Decimal(0),
            fees=Decimal(0),
            new_quantity=self.run.portfolio[new].quantity,
            new_pool_cost=self.run.portfolio[new].amount,
            allowable_cost=pos.amount,
            renamed_to=new,
        )

    def apply_split_openings(self, date_index: datetime.date) -> None:
        """Restate every holding a reorganisation touches today.

        A reorganisation restates the day's opening pool before any of the
        day's activity: the rows that preceded it are already in the units it
        leaves behind, so scaling after any of them applies the ratio twice.

        Replays what the first pass recorded rather than deciding again.
        Substituting the broker's count in one pass while scaling by the ratio
        in the other differs by a billionth of a share on the Trading 212
        exports this was built from, which is enough to leave a disposal of
        the whole holding short of zero.
        """
        renames = self.history.rename_list.get(date_index, {})
        for symbol, transformation in sorted(
            self.history.splits.get(date_index, {}).items()
        ):
            chained = self._rename_chain_at(symbol, renames)
            if chained is not None:
                raise CalculationError(
                    self._rename_chain_message(symbol, date_index, *chained)
                )
            pooled = self._rename_pooling_with(symbol, date_index, renames)
            if pooled is not None:
                raise CalculationError(
                    self._rename_and_split_message(symbol, date_index, *pooled)
                )
            position = self.run.portfolio[symbol]
            if position.quantity != transformation.day_open_quantity:
                raise CalculationError(
                    f"Cannot apply the reorganisation of {symbol} on "
                    f"{date_index}: the holding opens at "
                    f"{strip_zeros(position.quantity)} units here and at "
                    f"{strip_zeros(transformation.day_open_quantity)} where "
                    "the reorganisation was worked out. Please report this: "
                    "the two passes over the history disagree."
                )
            self.run.portfolio[symbol] = Position(
                transformation.scaled_day_open_quantity, position.amount
            )

    def _holds_units_today(self, symbol: str, date_index: datetime.date) -> bool:
        """Whether a rename today could move units of ``symbol`` anywhere.

        Called from ``apply_split_openings``, before any of the day's rows, so
        the portfolio still holds the day-opening pool; an acquisition later
        today lands before the rename, which is applied at the end of the day.
        """
        if symbol in self.run.portfolio and self.run.portfolio[symbol].quantity != 0:
            return True
        return has_key(self.history.acquisition_list, date_index, symbol)

    @staticmethod
    def _rename_chain_at(
        symbol: str, renames: dict[str, str]
    ) -> tuple[tuple[str, str], tuple[str, str]] | None:
        """Return the two renames that move this holding twice in one day.

        A day's renames are applied in the order the input lists them, so a
        holding renamed onward from the name it was just renamed to ends
        wherever that order puts it, and swapping the two rows moves it
        somewhere else. A date carries no order, so neither reading is
        established and there is nothing to pick between them. A cycle is the
        same thing joined up, and is caught by the same test.
        """
        next_name = renames.get(symbol)
        if next_name is None or next_name not in renames:
            return None
        return (symbol, next_name), (next_name, renames[next_name])

    def _rename_pooling_with(
        self,
        symbol: str,
        date_index: datetime.date,
        renames: dict[str, str],
    ) -> tuple[str, tuple[str, str]] | None:
        """Return a second holding the day's renames would pool this one with.

        A rename says the two tickers are one security, so a reorganisation of
        either restates both. The calculator restates one, and the renames
        bring whatever else they reach in afterwards, untouched. What they
        reach is not only the name at the other end of a rename this holding
        is named in: a holding renamed into the name this one is renamed to,
        or renamed along to it through names it never held, arrives just the
        same. So the whole of the day's rename graph connected to this holding
        is walked, in both directions, and any name on it that holds something
        of its own is the answer. The ordinary ticker change reaches only
        empty names, so it passes.

        Returns the holding it would be pooled with and the rename that
        reaches it, taking the renames in the order the input lists them.
        """
        reached = {symbol}
        frontier = [symbol]
        while frontier:
            name = frontier.pop(0)
            for old_name, new_name in renames.items():
                other = (
                    new_name
                    if name == old_name
                    else old_name
                    if name == new_name
                    else None
                )
                if other is None or other in reached:
                    continue
                if self._holds_units_today(other, date_index):
                    return other, (old_name, new_name)
                reached.add(other)
                frontier.append(other)
        return None

    def record_split_reconciliations(
        self,
        date_index: datetime.date,
        tax_year_start_index: datetime.date,
        calculation_log: CalculationLog,
    ) -> None:
        """Reconcile every reorganisation today to the broker's own count.

        Runs after the day's genuine acquisitions, so the correction cannot
        make a holding acquired earlier today look momentarily negative, and
        before its disposals, so a sale of the whole holding sees the count
        the broker stated.

        The delta is quantity only. It absorbs the broker's own rounding of
        the count it reported, and the difference left by restating the
        opening pool and the day's earlier rows separately rather than as one
        sum. It buys nothing and sells nothing, so it changes no cost and
        takes no part in same-day or 30-day identification.
        """
        for symbol, transformation in sorted(
            self.history.splits.get(date_index, {}).items()
        ):
            position = self.run.portfolio[symbol]
            quantity = position.quantity + transformation.reconciliation_delta
            if quantity < 0:
                raise CalculationError(
                    f"Cannot apply the reorganisation of {symbol} on "
                    f"{date_index}: reconciling to the broker's count leaves "
                    f"{strip_zeros(quantity)} units."
                )
            self.run.portfolio[symbol] = Position(quantity, position.amount)
            if date_index < tax_year_start_index:
                continue
            event = transformation.event
            calculation_log[date_index][f"split${symbol}"] = [
                CalculationEntry(
                    rule_type=RuleType.STOCK_SPLIT,
                    # A reorganisation acquires and disposes of nothing; the
                    # counts it moves between two unit systems are in the
                    # detail below.
                    quantity=Decimal(0),
                    amount=Decimal(0),
                    fees=Decimal(0),
                    gain=Decimal(0),
                    allowable_cost=Decimal(0),
                    new_quantity=quantity,
                    new_pool_cost=position.amount,
                    stock_split=StockSplitDetail(
                        ratio_description=transformation.ratio_description,
                        broker_before_quantity=event.before_quantity,
                        broker_after_quantity=event.after_quantity,
                        day_open_quantity=transformation.day_open_quantity,
                        scaled_day_open_quantity=(
                            transformation.scaled_day_open_quantity
                        ),
                        quantity_before_reconciliation=position.quantity,
                        quantity_after_reconciliation=quantity,
                        reconciliation_delta=transformation.reconciliation_delta,
                    ),
                )
            ]

    def verify_split_day_closes(self, date_index: datetime.date) -> None:
        """Check every reorganisation today left the holding where it said.

        A holding renamed today is checked under the name it was renamed to:
        the rename has moved the pool by now. That single hop is the whole
        journey, and the pool arrived alone, because a day that renames the
        holding onward again or pools it with a second one is refused before
        any of this (see ``apply_split_openings``).
        """
        renames = self.history.rename_list.get(date_index, {})
        for symbol, transformation in sorted(
            self.history.splits.get(date_index, {}).items()
        ):
            closing = self.run.portfolio[renames.get(symbol, symbol)].quantity
            if closing != transformation.expected_day_close:
                raise CalculationError(
                    f"Cannot apply the reorganisation of {symbol} on "
                    f"{date_index}: the day closes at {strip_zeros(closing)} "
                    "units where the reorganisation and the day's activity "
                    f"come to {strip_zeros(transformation.expected_day_close)}."
                    " Please report this: the two passes over the history "
                    "disagree."
                )

    @staticmethod
    def _rename_chain_message(
        symbol: str,
        date_index: datetime.date,
        first: tuple[str, str],
        second: tuple[str, str],
    ) -> str:
        """Explain why a holding renamed twice in a day cannot be restated."""
        return (
            f"Cannot apply the reorganisation of {symbol} on {date_index}: it "
            f"is renamed to {first[1]}, and {second[0]} is renamed to "
            f"{second[1]}, the same day. The renames are applied in the order "
            "the input lists them, so where the holding ends up, and what it "
            "is pooled with on the way, depends on which of the two rows "
            "comes first. A date carries no order, so neither reading is "
            "established. Put the renames on the days they happened, or work "
            "this day out by hand (consider professional advice)."
        )

    @staticmethod
    def _rename_and_split_message(
        symbol: str,
        date_index: datetime.date,
        other: str,
        rename: tuple[str, str],
    ) -> str:
        """Explain why a rename that pools two holdings cannot be applied."""
        old_name, new_name = rename
        return (
            f"Cannot apply the reorganisation of {symbol} on {date_index}: "
            f"the day's renames pool it with {other}, which holds shares of "
            f"its own, by way of the rename of {old_name} to {new_name}. A "
            "rename says the two tickers are one security, so the "
            "reorganisation restates both, but it is applied under one name "
            "and the renames bring the other holding in afterwards, "
            "untouched. Whether those shares were part of the event depends on "
            "whether the renames took effect before it or after, and the input "
            "carries dates but not that order. Work this day out by hand "
            "(consider professional advice)."
        )

    @staticmethod
    def _unresolved_bnb_message(
        event: StockSplitEvent,
        symbol: str,
        date_index: datetime.date,
        search_index: datetime.date,
    ) -> str:
        """Explain why an unrecoverable ratio blocks a bed and breakfast match."""
        assert isinstance(event.ratio, UnresolvedRatio)
        return (
            f"Cannot compute the disposal of {symbol} on {date_index}: it is "
            f"identified under the 30-day rule against the acquisition on "
            f"{search_index}, and {event.describe()} lies between them, so the "
            "two counts are in different units. Converting one to the other "
            "needs the corporate ratio, and that cannot be recovered from the "
            f"export: {event.ratio.describe()}. Work this disposal out by hand "
            "(consider professional advice)."
        )

    def _spin_off_pool(self, date_index: datetime.date, source: str, dest: str) -> str:
        """Return the symbol whose pool a spin-off from `source` draws on today.

        A rename on the day is applied after the day's acquisitions, so a
        source renamed that morning still has its pool under the old name
        here.

        Refuses when the source was also traded on the day. Whether a trade
        came before or after the reorganisation decides which shares were
        reorganised and what they cost, and the input carries dates but not
        times, so there is no right answer to give. Shares the source itself
        received by spin-off that day are not a trade; dependency order has
        already put them in its pool.
        """
        renames = self.history.rename_list.get(date_index, {})
        # Follow the day's renames back from the source, a holding renamed
        # twice in a morning being two hops from its pool. Only renames on
        # this chain matter; what happened to other symbols that day does not.
        names = [source]
        frontier = [source]
        while frontier:
            current = frontier.pop()
            for old, new in renames.items():
                if new != current:
                    continue
                if old in names:
                    raise CalculationError(
                        f"Cannot compute the spin-off of {dest} from {source} on "
                        f"{date_index}: the day's renames go round in a circle "
                        f"({' -> '.join([*names, old])}), so there is no telling "
                        f"where the pool is. Work {source} and {dest} out by "
                        "hand (consider professional advice)."
                    )
                names.append(old)
                frontier.append(old)
        # The pool sits under whichever of these names holds anything here,
        # since the day's renames are applied after its acquisitions. Cost
        # with no shares counts: a fee charged after a holding was sold out
        # leaves exactly that, and the rename merges it in all the same. Two
        # of them means two holdings became one that day, and whether the
        # spin-off applied to both or to one cannot be told from the input.
        holding = sorted(
            name
            for name in names
            if name in self.run.portfolio
            and (
                self.run.portfolio[name].quantity > 0
                or self.run.portfolio[name].amount != 0
            )
        )
        if len(holding) > 1:
            raise CalculationError(
                f"Cannot compute the spin-off of {dest} from {source} on "
                f"{date_index}: {' and '.join(holding)} were separate holdings "
                "until renamed into one that day, and whether the spin-off "
                "applied to both or just one cannot be told from the input. "
                f"Work {source} and {dest} out by hand (consider professional "
                "advice)."
            )
        pool = holding[0] if holding else source
        activity = []
        for name in names:
            spun_in = sum(
                (
                    spin_off.quantity
                    for spin_off in self.history.spin_offs.get(date_index, [])
                    if spin_off.dest == name
                ),
                Decimal(0),
            )
            if (
                has_key(self.history.acquisition_list, date_index, name)
                and self.history.acquisition_list[date_index][name].quantity > spun_in
            ):
                activity.append("bought")
            if has_key(self.history.disposal_list, date_index, name):
                activity.append("sold")
            if has_key(self.history.transfer_to_spouse_list, date_index, name):
                activity.append("transferred to a spouse")
            if (date_index, name) in self.history.fee_days:
                activity.append("charged a fee")
        if activity:
            raise CalculationError(
                f"Cannot compute the spin-off of {dest} from {source} on "
                f"{date_index}: {source} was also {' and '.join(activity)} that "
                "day. Whether that came before or after the reorganisation "
                "decides which shares were reorganised and what they cost, and "
                "the input has dates but not times, so this tool cannot tell. "
                f"Work {source} and {dest} out by hand for this period (consider "
                "professional advice). Do not change the dates."
            )
        return pool

    def _acquisition_order(self, date_index: datetime.date) -> list[str]:
        """Return the day's acquired symbols, each spun-off holding after its source.

        A spin-off takes its share of the source's pool as it stands on the
        day, and the day's purchases of the source are part of that, since
        same-day acquisitions form a single acquisition (TCGA 1992 s105). So
        every other symbol is pooled first, and a spun-off holding only after
        whatever it was spun off from, so that a holding spun off and spun off
        from on the same day is complete before it is apportioned. Holdings
        spun off from the same source take their shares one after another, so
        among themselves they keep the order the spin-offs happened in, which
        is the order the first pass worked them out in.
        """
        sources: dict[str, list[str]] = defaultdict(list)
        first_event: dict[str, int] = {}
        for index, spin_off in enumerate(self.history.spin_offs.get(date_index, [])):
            sources[spin_off.dest].append(spin_off.source)
            first_event.setdefault(spin_off.dest, index)

        def depth(symbol: str) -> int:
            # A cycle cannot get here: the first pass refuses a chain that is
            # not in order, and a cycle is never in order.
            return 1 + max((depth(source) for source in sources[symbol]), default=-1)

        return sorted(
            self.history.acquisition_list[date_index],
            key=lambda symbol: (depth(symbol), first_event.get(symbol, -1)),
        )

    def _match_across_splits(
        self,
        disposal_quantity: Decimal,
        available_acquisition_units: Decimal,
        cumulative_ratio: Fraction,
        *,
        symbol: str,
        date_index: datetime.date,
        search_index: datetime.date,
    ) -> tuple[Decimal, Decimal]:
        """Match a disposal against an acquisition counted in other units.

        Returns what the disposal takes, in its own units, and what that
        consumes of the acquisition, in the acquisition's. Everything
        reserved is counted in the acquisition's own units, so the whole
        remainder converts once: converting only the acquisition would
        subtract post-split counts from a pre-split one and can go negative.

        Both counts are kept to ten decimal places, so a large enough ratio
        can leave a real quantity with nothing to say for itself on the other
        side. That is refused rather than matched at nil cost against an
        acquisition it then fails to reserve.
        """
        available_disposal_units = unscale_quantity(
            available_acquisition_units, cumulative_ratio
        )
        if available_disposal_units <= 0:
            raise CalculationError(
                self._unrepresentable_match_message(
                    symbol, date_index, search_index, cumulative_ratio
                )
            )
        matched = min(disposal_quantity, available_disposal_units)
        # Taking the whole remainder outright, rather than converting it back,
        # keeps a ten-decimal round trip from leaving or over-consuming a
        # residual share.
        if matched == available_disposal_units:
            consumed = available_acquisition_units
        else:
            consumed = min(
                available_acquisition_units,
                scale_quantity(matched, cumulative_ratio),
            )
        if consumed <= 0:
            raise CalculationError(
                self._unrepresentable_match_message(
                    symbol, date_index, search_index, cumulative_ratio
                )
            )
        assert consumed <= available_acquisition_units
        return matched, consumed

    @staticmethod
    def _unrepresentable_match_message(
        symbol: str,
        date_index: datetime.date,
        search_index: datetime.date,
        cumulative_ratio: Fraction,
    ) -> str:
        """Explain a match the two unit systems cannot both express."""
        return (
            f"Cannot compute the disposal of {symbol} on {date_index}: it is "
            f"identified under the 30-day rule against the acquisition on "
            f"{search_index}, and the reorganisations in between restate the "
            f"count by {cumulative_ratio}. One of the two quantities comes to "
            "less than the ten decimal places this keeps, so the match cannot "
            "be expressed in both unit systems at once. Work this disposal out "
            "by hand (consider professional advice)."
        )

    def _matchable_acquisition(
        self,
        date_index: datetime.date,
        symbol: str,
    ) -> HmrcTransactionData:
        """Return the acquisitions a disposal could be identified with.

        Units a reorganisation restated are not here to be taken out: they
        were never acquired (TCGA 1992 s127, CG51805), so they never enter
        ``acquisition_list`` in the first place.
        """
        if not has_key(self.history.acquisition_list, date_index, symbol):
            return HmrcTransactionData()
        return self.history.acquisition_list[date_index][symbol]

    def _contending_acquisitions(
        self,
        symbol: str,
        date_index: datetime.date,
        bnb_claimed_before_today: dict[tuple[datetime.date, str], Decimal],
    ) -> list[datetime.date]:
        """Dates a same-day sale and transfer of ``symbol`` would both claim.

        Left to the Section 104 pool alone the two draw at the same average
        cost, so the order between them cannot change either result. They only
        contend over acquisitions both could be identified against under the
        same-day or 30-day rules, and then HMRC does not say which one gets
        them. Returns those acquisition dates, earliest first, or an empty list
        when there is nothing to argue over.
        """
        dates = []
        effective_symbol = symbol

        def has_shares_going_spare(
            day: datetime.date, ticker: str, *, is_disposal_day: bool
        ) -> bool:
            # Only what is left over is worth arguing about. Shares from a
            # split are already excluded, and a management fee is recorded as
            # cost with no shares at all.
            acquisition = self._matchable_acquisition(day, ticker)
            if acquisition.quantity <= 0 or acquisition.amount == 0:
                return False
            # Claims an earlier disposal already made are settled and leave
            # that much less to argue over. What the sale we are contending
            # with took today is deliberately not deducted: those are the very
            # shares in dispute, and it only got them by going first.
            taken = bnb_claimed_before_today.get((day, ticker), Decimal(0))
            if not is_disposal_day:
                # Whatever that later day disposes of under the same-day rule
                # is spoken for before either of ours can reach it. On the
                # disposal day itself the sale and the transfer are the two
                # laying claim, so subtracting them would hide the clash.
                for log in (
                    self.history.disposal_list,
                    self.history.transfer_to_spouse_list,
                ):
                    if has_key(log, day, ticker):
                        taken += log[day][ticker].quantity
            return acquisition.quantity - taken > 0

        if has_shares_going_spare(date_index, symbol, is_disposal_day=True):
            dates.append(date_index)
        for i in range(BED_AND_BREAKFAST_DAYS):
            search_index = date_index + datetime.timedelta(days=i + 1)
            # HMRC treats renames as the same security for B&B purposes.
            effective_symbol = self.history.rename_list.get(search_index, {}).get(
                effective_symbol, effective_symbol
            )
            if has_shares_going_spare(
                search_index, effective_symbol, is_disposal_day=False
            ):
                dates.append(search_index)
        return dates

    def _same_day_sale_and_transfer_message(
        self,
        symbol: str,
        date_index: datetime.date,
        contended: list[datetime.date],
    ) -> str:
        """Explain why a same-day sale and transfer cannot be calculated."""
        sold = self.history.disposal_list[date_index][symbol].quantity
        transferred = self.history.transfer_to_spouse_list[date_index][symbol].quantity
        shown = ", ".join(str(date) for date in contended[:MAX_CONTENDED_DATES_SHOWN])
        if len(contended) > MAX_CONTENDED_DATES_SHOWN:
            shown += f" and {len(contended) - MAX_CONTENDED_DATES_SHOWN} more"
        has_gift = (date_index, symbol) in self.history.gift_disposals
        has_sale = (date_index, symbol) in self.history.sale_days
        if has_gift and has_sale:
            verb, noun = ("sold or gave away", "disposal")
        elif has_gift:
            verb, noun = ("gave away", "gift")
        else:
            verb, noun = ("sold", "sale")
        return (
            f"On {date_index} you {verb} {strip_zeros(sold)} units of {symbol} "
            f"and transferred {strip_zeros(transferred)} to a spouse, and you "
            f"also acquired {symbol} on {shown}.\n"
            f"Both the {noun} and the transfer have to be identified against those "
            "acquisitions under the same-day and 30-day rules, but they cannot "
            "share them: TCGA 1992 s105(1) treats everything disposed of on one "
            "day as a single transaction, and here one part is chargeable while "
            "the other is no gain/no loss. HMRC does not say how to split the "
            "acquisitions between the two, so this tool will not guess.\n"
            "What to do: work this disposal out by hand and consider taking "
            "professional advice, then leave the affected symbol out of the "
            "input and add its figures to your return yourself. Everything else "
            "in your history still calculates normally.\n"
            "Do not move the real transaction dates to get past this: the dates "
            "are what the identification rules run on, so changing them changes "
            "the tax."
        )

    def _record_transfers_to_spouse(
        self,
        date_index: datetime.date,
        tax_year_start_index: datetime.date,
        calculation_log: CalculationLog,
        bnb_claimed_before_today: dict[tuple[datetime.date, str], Decimal],
    ) -> None:
        """Process and log no gain/no loss transfers to spouse for a single day.

        Each transfer is identified against the transferor's own acquisitions
        exactly like a disposal (see ``process_disposal``), but with a nil gain,
        so it is kept out of the taxable disposal totals.
        """
        if date_index not in self.history.transfer_to_spouse_list:
            return
        for symbol in self.history.transfer_to_spouse_list[date_index]:
            # HMRC does not define how to identify a taxable sale and a no gain/no
            # loss transfer of the same shares on the same day, so refuse the
            # cases where that identification actually changes the answer.
            if has_key(self.history.disposal_list, date_index, symbol) and (
                contended := self._contending_acquisitions(
                    symbol, date_index, bnb_claimed_before_today
                )
            ):
                raise CalculationError(
                    self._same_day_sale_and_transfer_message(
                        symbol, date_index, contended
                    )
                )
            gain, entries = self.process_disposal(
                symbol, date_index, no_gain_no_loss=True
            )
            assert gain == 0, gain
            base_cost = sum((entry.allowable_cost for entry in entries), Decimal(0))
            # Surface transfers made in the reported period; the rest of the
            # history walk logs them at DEBUG.
            LOGGER.log(
                logging.INFO if self.date_in_tax_year(date_index) else logging.DEBUG,
                "Transferred %s units of %s to spouse on %s "
                "(no gain/no loss, base cost £%s)",
                self.history.transfer_to_spouse_list[date_index][symbol].quantity,
                symbol,
                date_index,
                round_decimal(base_cost, 2),
            )
            if date_index >= tax_year_start_index:
                calculation_log[date_index][f"transfer-to-spouse${symbol}"] = entries

    def process_eri(
        self,
        symbol: str,
        date_index: datetime.date,
    ) -> CalculationEntry | None:
        """Process single excess reported income."""
        eri = self.get_eri(symbol, date_index)
        assert eri is not None
        amount = self.run.portfolio[eri.symbol].amount
        quantity = self.run.portfolio[eri.symbol].quantity

        if quantity == 0:
            return None

        allowable_cost = quantity * eri.price

        if allowable_cost == 0:
            return None

        new_amount = amount + allowable_cost
        LOGGER.debug(
            "Detected excess reported income of %s on %s, "
            "modyfing the cost amount from %s to %s",
            eri.symbol,
            eri.date,
            amount,
            new_amount,
        )
        self.run.portfolio[eri.symbol].amount = new_amount

        if self.date_in_tax_year(eri.distribution_date):
            self.run.eris_distribution[eri.distribution_date][symbol] += (
                ExcessReportedIncomeDistribution(
                    price=eri.price,
                    amount=allowable_cost,
                    quantity=quantity,
                )
            )

        return CalculationEntry(
            RuleType.EXCESS_REPORTED_INCOME,
            quantity=quantity,
            amount=-amount,
            new_quantity=quantity,
            gain=None,
            fees=Decimal(0),
            new_pool_cost=new_amount,
            allowable_cost=allowable_cost,
            eris=[eri],
        )

    def _record_option_disposals(
        self,
        date_index: datetime.date,
        calculation_log: CalculationLog,
    ) -> tuple[int, Decimal, Decimal, Decimal, Decimal]:
        """Add one day's written-option grants to the report totals and log."""
        count = 0
        proceeds = Decimal(0)
        costs = Decimal(0)
        gains = Decimal(0)
        losses = Decimal(0)
        for option_name, option in self.history.option_disposal_list.get(
            date_index, {}
        ).items():
            raw_gain = option.proceeds - option.allowable_cost
            gain = round_decimal(raw_gain, 2)
            count += 1
            proceeds += option.proceeds
            costs += option.proceeds - gain
            calculation_log[date_index][f"option${option_name}"] = [
                CalculationEntry(
                    rule_type=RuleType.OPTION,
                    quantity=option.quantity,
                    amount=option.proceeds,
                    fees=Decimal(0),
                    gain=raw_gain,
                    allowable_cost=option.allowable_cost,
                    new_quantity=Decimal(0),
                    new_pool_cost=Decimal(0),
                )
            ]
            if gain > 0:
                gains += gain
            else:
                losses += gain
        return count, proceeds, costs, gains, losses

    def walk(self) -> WalkResult:
        """Replay every day from the start of history to the tax year end."""
        begin_index = INTERNAL_START_DATE
        tax_year_start_index = self.tax_year_start_date
        end_index = self.tax_year_end_date
        disposal_count = 0
        disposal_proceeds = Decimal(0)
        allowable_costs = Decimal(0)
        capital_gain = Decimal(0)
        capital_loss = Decimal(0)
        gift_loss = Decimal(0)
        exempt_disposal_count = 0
        exempt_disposal_proceeds = Decimal(0)

        # The first pass left its estimates in the portfolio; the walk rebuilds
        # it from nothing.
        self.run.portfolio.clear()
        self.run.spin_off_entries.clear()
        calculation_log: CalculationLog = defaultdict(dict)

        for date_index in (
            begin_index + datetime.timedelta(days=x)
            for x in range((end_index - begin_index).days + 1)
        ):
            # What earlier disposals have already bed-and-breakfasted, captured
            # before today's own disposals add to it. Only those earlier claims
            # settle whether a later acquisition is still up for grabs.
            bnb_claimed_before_today = (
                {
                    (day, ticker): data.quantity
                    for day, tickers in self.run.bnb_list.items()
                    for ticker, data in tickers.items()
                }
                if date_index in self.history.transfer_to_spouse_list
                else {}
            )
            self.apply_split_openings(date_index)
            if date_index in self.history.acquisition_list:
                for symbol in self._acquisition_order(date_index):
                    calculation_entries = self.process_acquisition(
                        symbol,
                        date_index,
                    )
                    if date_index >= tax_year_start_index:
                        calculation_log[date_index][f"buy${symbol}"] = (
                            calculation_entries
                        )
            for source, entries in self.run.spin_off_entries.pop(
                date_index, {}
            ).items():
                if date_index >= tax_year_start_index:
                    calculation_log[date_index][f"spin-off${source}"] = entries
            self.record_split_reconciliations(
                date_index, tax_year_start_index, calculation_log
            )
            if date_index in self.history.disposal_list:
                for symbol in self.history.disposal_list[date_index]:
                    transaction_capital_gain, calculation_entries = (
                        self.process_disposal(symbol, date_index)
                    )
                    if date_index >= tax_year_start_index:
                        transaction_amount = self.history.disposal_list[date_index][
                            symbol
                        ].amount
                        transaction_fees = self.history.disposal_list[date_index][
                            symbol
                        ].fees
                        transaction_disposal_proceeds = (
                            transaction_amount + transaction_fees
                        )
                        transaction_quantity = self.history.disposal_list[date_index][
                            symbol
                        ].quantity
                        calculated_quantity = Decimal(0)
                        calculated_proceeds = Decimal(0)
                        calculated_gain = Decimal(0)
                        for entry in calculation_entries:
                            calculated_quantity += entry.quantity
                            calculated_proceeds += entry.amount + entry.fees
                            calculated_gain += entry.gain
                        assert transaction_quantity == calculated_quantity
                        assert round_decimal(
                            transaction_disposal_proceeds, 10
                        ) == round_decimal(calculated_proceeds, 10), (
                            f"{transaction_disposal_proceeds} != {calculated_proceeds}"
                        )
                        assert transaction_capital_gain == round_decimal(
                            calculated_gain, 2
                        )
                        prefix = self._disposal_log_prefix(date_index, symbol)
                        calculation_log[date_index][f"{prefix}${symbol}"] = (
                            calculation_entries
                        )
                        if prefix == "exempt":
                            exempt_disposal_count += 1
                            exempt_disposal_proceeds += transaction_disposal_proceeds
                            LOGGER.debug(
                                "EXEMPT DISPOSAL on %s of %s, quantity %s, proceeds £%s",
                                date_index,
                                symbol,
                                transaction_quantity,
                                round_decimal(transaction_disposal_proceeds, 2),
                            )
                        else:
                            disposal_count += 1
                            disposal_proceeds += transaction_disposal_proceeds
                            allowable_costs += (
                                transaction_disposal_proceeds - transaction_capital_gain
                            )
                            LOGGER.debug(
                                "DISPOSAL on %s of %s, quantity %s, capital gain $%s",
                                date_index,
                                symbol,
                                transaction_quantity,
                                round_decimal(transaction_capital_gain, 2),
                            )
                            if transaction_capital_gain > 0:
                                capital_gain += transaction_capital_gain
                            elif prefix == "gift":
                                # A clogged loss: usable only against gains on
                                # disposals to the same connected person while
                                # still connected (TCGA 1992 s18(3)). Kept out of
                                # the loss total and reported as its own.
                                gift_loss += transaction_capital_gain
                            else:
                                capital_loss += transaction_capital_gain

            if date_index >= tax_year_start_index:
                (
                    option_count,
                    option_proceeds,
                    option_costs,
                    option_gains,
                    option_losses,
                ) = self._record_option_disposals(date_index, calculation_log)
                disposal_count += option_count
                disposal_proceeds += option_proceeds
                allowable_costs += option_costs
                capital_gain += option_gains
                capital_loss += option_losses

            if date_index in self.history.rename_list:
                for old, new in self.history.rename_list[date_index].items():
                    entry = self.process_rename(old, new)
                    if date_index >= tax_year_start_index:
                        calculation_log[date_index][f"rename${old}"] = [entry]

            self._record_transfers_to_spouse(
                date_index,
                tax_year_start_index,
                calculation_log,
                bnb_claimed_before_today,
            )

            self.verify_split_day_closes(date_index)

            # Excess Reported incomes should be reported at the end of the day
            if date_index in self.history.eris:
                for symbol in self.history.eris[date_index]:
                    maybe_entry = self.process_eri(symbol, date_index)
                    if not maybe_entry:
                        continue

                    if date_index >= tax_year_start_index:
                        eris = maybe_entry.eris
                        assert eris
                        calculation_log[date_index][
                            f"excess-reported-income${symbol}"
                        ] = [maybe_entry]

            # Lastly all the ERI distribution events
            if date_index in self.run.eris_distribution:
                for symbol in self.run.eris_distribution[date_index]:
                    data = self.run.eris_distribution[date_index][symbol]
                    is_interest = symbol in self.interest_fund_tickers
                    if is_interest:
                        self.run.total_foreign_interest += data.amount
                    self.run.calculation_log_yields[date_index][
                        f"excess-reported-income-distribution${symbol}"
                    ] = [
                        CalculationEntry(
                            RuleType.EXCESS_REPORTED_INCOME_DISTRIBUTION,
                            quantity=data.quantity,
                            amount=data.amount,
                            new_quantity=data.quantity,
                            gain=None,
                            fees=Decimal(0),
                            new_pool_cost=data.amount,
                            allowable_cost=None,
                            eris=[
                                ExcessReportedIncome(
                                    price=data.price,
                                    symbol=symbol,
                                    date=date_index - ERI_TAX_DATE_DELTA,
                                    distribution_date=date_index,
                                    is_interest=is_interest,
                                ),
                            ],
                        )
                    ]
        return WalkResult(
            disposal_count,
            disposal_proceeds,
            allowable_costs,
            capital_gain,
            capital_loss,
            gift_loss,
            exempt_disposal_count,
            exempt_disposal_proceeds,
            calculation_log,
        )
