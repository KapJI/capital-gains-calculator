"""Test Schwab RSU transaction ordering with vest-date same-day matching.

This test file covers transaction ordering scenarios where RSU sales occur
between vest date and settlement date. The key challenge is that shares vest
on date V (e.g., 08/15), settle on date S (e.g., 08/18, T+3 later), and tax
withholding sales can occur anywhere in [V, S].

Test Coverage:
1. Basic ordering: vest date < sale date < settlement date
2. Sale on vest date (V)
3. Sale between vest and settlement (V < sale < S)
4. Sale on settlement date (S)
5. Sale after settlement (sale > S)
6. Multiple sales across the vest-to-settlement window
7. Partial sales (less than vested amount)
8. Over-sales (vest + existing holdings)
9. Multiple chunks within window
10. Sales before, during, and after vest window
11. Awards.csv integration (vest_date and price extraction)
12. Edge cases (non-RSU transactions, boundary crossing)
"""

from __future__ import annotations

import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from cgt_calc.parsers.schwab import read_schwab_transactions

if TYPE_CHECKING:
    from pathlib import Path


class TestRSUTransactionOrdering:
    """Test RSU transaction ordering with vest-date same-day matching."""

    def test_basic_ordering_vest_sale_settlement(self, tmp_path: Path) -> None:
        """Test basic case: vest (08/15) < sale (08/16) < settlement (08/18).

        This is the canonical RSU tax withholding scenario:
        - Shares vest on 08/15 (FMV locked for income tax)
        - Tax sale on 08/16 (to cover withholding)
        - Settlement on 08/18 (T+3, when shares delivered)

        Expected behavior:
        - Acquisition date = 08/18 (settlement)
        - Vest date = 08/15 (stored separately)
        - Sale on 08/16 matches via vest-date same-day rule
        """
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            "Date,Action,Symbol,Description,Price,Quantity,Fees & Comm,Amount\n"
            "08/18/2023 as of 08/15/2023,Stock Plan Activity,GOOG,RSU Vest,$140.35,100,$0.00,$14035.00\n"
            "08/16/2023,Sell,GOOG,Tax Withholding,$140.50,35,$1.00,$4916.50\n"
        )

        transactions = read_schwab_transactions(csv_file, None)

        assert len(transactions) == 2
        # Reversed order (most recent first)
        vest = transactions[1]
        sell = transactions[0]

        # Vest transaction
        assert vest.date == datetime.date(2023, 8, 18)  # Settlement date
        assert vest.vest_date == datetime.date(2023, 8, 15)  # Vest date
        assert vest.quantity == Decimal(100)
        assert vest.price == Decimal("140.35")

        # Sale between vest and settlement
        assert sell.date == datetime.date(2023, 8, 16)
        assert sell.quantity == Decimal(35)

        # Verify ordering: vest_date < sale < settlement
        assert vest.vest_date < sell.date < vest.date

    def test_sale_on_vest_date(self, tmp_path: Path) -> None:
        """Test sale on the exact vest date (same day as vesting).

        Scenario: Shares vest and are immediately sold for tax withholding,
        all on the same day (08/15), but settlement is T+3 later (08/18).

        Expected: Vest-date same-day matching applies.
        """
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            "Date,Action,Symbol,Description,Price,Quantity,Fees & Comm,Amount\n"
            "08/18/2023 as of 08/15/2023,Stock Plan Activity,GOOG,RSU Vest,$140.35,100,$0.00,$14035.00\n"
            "08/15/2023,Sell,GOOG,Tax Sale on Vest,$140.35,30,$1.00,$4209.50\n"
        )

        transactions = read_schwab_transactions(csv_file, None)

        assert len(transactions) == 2
        vest = transactions[1]
        sell = transactions[0]

        assert vest.vest_date == datetime.date(2023, 8, 15)
        assert sell.date == datetime.date(2023, 8, 15)
        # Sale date equals vest date (same-day scenario)
        assert sell.date == vest.vest_date

    def test_sale_on_settlement_date(self, tmp_path: Path) -> None:
        """Test sale on settlement date (08/18).

        Even though acquisition and disposal are on the same date (08/18),
        vest-date matching should still apply since sale is within
        [vest_date, settlement_date] = [08/15, 08/18].
        """
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            "Date,Action,Symbol,Description,Price,Quantity,Fees & Comm,Amount\n"
            "08/18/2023 as of 08/15/2023,Stock Plan Activity,GOOG,RSU Vest,$140.35,100,$0.00,$14035.00\n"
            "08/18/2023,Sell,GOOG,Sale on Settlement,$141.00,20,$0.50,$2819.50\n"
        )

        transactions = read_schwab_transactions(csv_file, None)

        assert len(transactions) == 2
        vest = transactions[1]
        sell = transactions[0]

        assert vest.date == datetime.date(2023, 8, 18)
        assert vest.vest_date == datetime.date(2023, 8, 15)
        assert sell.date == datetime.date(2023, 8, 18)
        # Sale on settlement date (within [vest, settlement])
        assert vest.vest_date <= sell.date <= vest.date

    def test_sale_after_settlement(self, tmp_path: Path) -> None:
        """Test sale after settlement date (normal case).

        Sale on 08/20, after settlement on 08/18. This should use
        standard CGT matching rules (B&B or Section 104), not vest-date
        same-day matching.
        """
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            "Date,Action,Symbol,Description,Price,Quantity,Fees & Comm,Amount\n"
            "08/18/2023 as of 08/15/2023,Stock Plan Activity,GOOG,RSU Vest,$140.35,100,$0.00,$14035.00\n"
            "08/20/2023,Sell,GOOG,Normal Sale,$142.00,25,$0.50,$3549.50\n"
        )

        transactions = read_schwab_transactions(csv_file, None)

        assert len(transactions) == 2
        vest = transactions[1]
        sell = transactions[0]

        assert vest.date == datetime.date(2023, 8, 18)
        assert vest.vest_date == datetime.date(2023, 8, 15)
        assert sell.date == datetime.date(2023, 8, 20)
        # Sale after settlement (outside vest-date window)
        assert sell.date > vest.date

    def test_multiple_sales_across_window(self, tmp_path: Path) -> None:
        """Test multiple sales on different days across vest-to-settlement window.

        Sales on: 08/15 (vest), 08/16 (between), 08/18 (settlement), 08/20 (after).

        Expected:
        - First 3 sales (08/15-08/18): Vest-date same-day matching
        - Last sale (08/20): Standard CGT matching (B&B or Section 104)
        """
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            "Date,Action,Symbol,Description,Price,Quantity,Fees & Comm,Amount\n"
            "08/18/2023 as of 08/15/2023,Stock Plan Activity,GOOG,RSU Vest,$140.35,100,$0.00,$14035.00\n"
            "08/15/2023,Sell,GOOG,Sale on vest,$140.35,10,$0.25,$1403.25\n"
            "08/16/2023,Sell,GOOG,Sale between,$140.50,10,$0.25,$1404.75\n"
            "08/18/2023,Sell,GOOG,Sale on settlement,$141.00,10,$0.25,$1409.75\n"
            "08/20/2023,Sell,GOOG,Sale after,$142.00,10,$0.25,$1419.75\n"
        )

        transactions = read_schwab_transactions(csv_file, None)

        assert len(transactions) == 5
        vest = transactions[4]
        sell_vest = transactions[3]
        sell_between = transactions[2]
        sell_settlement = transactions[1]
        sell_after = transactions[0]

        # Verify all dates
        assert vest.vest_date == datetime.date(2023, 8, 15)
        assert vest.date == datetime.date(2023, 8, 18)

        assert sell_vest.date == datetime.date(2023, 8, 15)
        assert sell_between.date == datetime.date(2023, 8, 16)
        assert sell_settlement.date == datetime.date(2023, 8, 18)
        assert sell_after.date == datetime.date(2023, 8, 20)

        # First 3 sales within vest-date window
        assert vest.vest_date <= sell_vest.date <= vest.date
        assert vest.vest_date <= sell_between.date <= vest.date
        assert vest.vest_date <= sell_settlement.date <= vest.date

        # Last sale outside window
        assert sell_after.date > vest.date

    def test_partial_sale_less_than_vested(self, tmp_path: Path) -> None:
        """Test selling less than vested amount (typical ~35% tax withholding).

        Vest 100 shares, sell only 35 for taxes. Remaining 65 go to Section 104.
        """
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            "Date,Action,Symbol,Description,Price,Quantity,Fees & Comm,Amount\n"
            "08/18/2023 as of 08/15/2023,Stock Plan Activity,GOOG,RSU Vest,$140.35,100,$0.00,$14035.00\n"
            "08/15/2023,Sell,GOOG,Tax Withholding 35%,$140.35,35,$1.00,$4911.25\n"
        )

        transactions = read_schwab_transactions(csv_file, None)

        assert len(transactions) == 2
        vest = transactions[1]
        sell = transactions[0]

        assert vest.quantity == Decimal(100)
        assert sell.quantity == Decimal(35)
        # Selling 35% for taxes, keeping 65%
        assert sell.quantity < vest.quantity

    def test_over_sale_vest_plus_existing(self, tmp_path: Path) -> None:
        """Test selling more than vested amount (new vest + existing holdings).

        Already own: 50 shares (bought earlier)
        New vest: 100 shares on 08/15 → settle 08/18
        Sell: 120 shares on 08/16

        Expected:
        - First 100 shares matched via vest-date same-day
        - Next 20 shares from existing holdings (Section 104)
        """
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            "Date,Action,Symbol,Description,Price,Quantity,Fees & Comm,Amount\n"
            "08/01/2023,Buy,GOOG,Earlier Purchase,$130.00,50,$0.00,-$6500.00\n"
            "08/18/2023 as of 08/15/2023,Stock Plan Activity,GOOG,RSU Vest,$140.35,100,$0.00,$14035.00\n"
            "08/16/2023,Sell,GOOG,Large Sale,$141.00,120,$2.00,$16918.00\n"
        )

        transactions = read_schwab_transactions(csv_file, None)

        assert len(transactions) == 3
        buy = transactions[2]
        vest = transactions[1]
        sell = transactions[0]

        assert buy.quantity == Decimal(50)
        assert vest.quantity == Decimal(100)
        assert sell.quantity == Decimal(120)
        # Selling more than vested (vest + existing)
        assert sell.quantity > vest.quantity
        assert sell.quantity <= (buy.quantity + vest.quantity)

    def test_multiple_chunks_within_window(self, tmp_path: Path) -> None:
        """Test selling vested shares in multiple chunks within settlement window.

        Vest 100 shares, sell in 3 chunks: 30 + 20 + 15 = 65 total.
        All chunks within [08/15, 08/18] window should use vest-date matching.
        """
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            "Date,Action,Symbol,Description,Price,Quantity,Fees & Comm,Amount\n"
            "08/18/2023 as of 08/15/2023,Stock Plan Activity,GOOG,RSU Vest,$140.35,100,$0.00,$14035.00\n"
            "08/15/2023,Sell,GOOG,First Chunk,$140.35,30,$0.50,$4209.50\n"
            "08/16/2023,Sell,GOOG,Second Chunk,$140.50,20,$0.50,$2809.50\n"
            "08/17/2023,Sell,GOOG,Third Chunk,$140.75,15,$0.50,$2110.75\n"
        )

        transactions = read_schwab_transactions(csv_file, None)

        assert len(transactions) == 4
        vest = transactions[3]
        sell1 = transactions[2]
        sell2 = transactions[1]
        sell3 = transactions[0]

        assert vest.quantity == Decimal(100)
        assert sell1.quantity is not None
        assert sell2.quantity is not None
        assert sell3.quantity is not None
        total_sold = sell1.quantity + sell2.quantity + sell3.quantity
        assert total_sold == Decimal(65)

        # All chunks within window
        assert vest.vest_date is not None
        assert vest.vest_date <= sell1.date <= vest.date
        assert vest.vest_date <= sell2.date <= vest.date
        assert vest.vest_date <= sell3.date <= vest.date

    def test_multiple_chunks_exceeding_vested(self, tmp_path: Path) -> None:
        """Test multiple sales totaling more than vested amount.

        Earlier: 100 shares (bought 08/01)
        Vest: 100 shares on 08/15 → settle 08/18
        Sell: 60 on 08/15 + 70 on 08/16 = 130 total

        Expected matching:
        - First 60: Vest-date same-day (from 08/15 vest)
        - Next 40: Vest-date same-day (remaining from 08/15 vest)
        - Last 30: Section 104 (from earlier purchase)
        """
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            "Date,Action,Symbol,Description,Price,Quantity,Fees & Comm,Amount\n"
            "08/01/2023,Buy,GOOG,Earlier Purchase,$130.00,100,$0.00,-$13000.00\n"
            "08/18/2023 as of 08/15/2023,Stock Plan Activity,GOOG,RSU Vest,$140.35,100,$0.00,$14035.00\n"
            "08/15/2023,Sell,GOOG,First Sale,$140.50,60,$1.00,$8429.00\n"
            "08/16/2023,Sell,GOOG,Second Sale,$141.00,70,$1.00,$9869.00\n"
        )

        transactions = read_schwab_transactions(csv_file, None)

        assert len(transactions) == 4
        buy = transactions[3]
        vest = transactions[2]
        sell1 = transactions[1]
        sell2 = transactions[0]

        assert buy.quantity is not None
        assert vest.quantity is not None
        assert sell1.quantity is not None
        assert sell2.quantity is not None
        total_available = buy.quantity + vest.quantity
        total_sold = sell1.quantity + sell2.quantity

        assert total_available == Decimal(200)
        assert total_sold == Decimal(130)
        assert total_sold > vest.quantity  # Exceeds vested amount
        assert total_sold <= total_available  # But within total holdings

    def test_crossing_month_boundary(self, tmp_path: Path) -> None:
        """Test vest-to-settlement window crossing month boundary.

        Vest on 08/30, settle on 09/02 (crosses Aug/Sep boundary).
        Sale on 08/31 should still match via vest-date same-day.
        """
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            "Date,Action,Symbol,Description,Price,Quantity,Fees & Comm,Amount\n"
            "09/02/2023 as of 08/30/2023,Stock Plan Activity,GOOG,RSU Vest,$145.00,100,$0.00,$14500.00\n"
            "08/31/2023,Sell,GOOG,Cross-month Sale,$145.10,40,$1.00,$5803.00\n"
        )

        transactions = read_schwab_transactions(csv_file, None)

        assert len(transactions) == 2
        vest = transactions[1]
        sell = transactions[0]

        assert vest.vest_date == datetime.date(2023, 8, 30)
        assert vest.date == datetime.date(2023, 9, 2)
        assert sell.date == datetime.date(2023, 8, 31)

        # Crosses month boundary but still within window
        assert vest.vest_date.month == 8
        assert vest.date.month == 9
        assert sell.date.month == 8
        assert vest.vest_date <= sell.date <= vest.date

    def test_crossing_year_boundary(self, tmp_path: Path) -> None:
        """Test vest-to-settlement window crossing year boundary.

        Vest on 12/29/2023, settle on 01/02/2024 (crosses year boundary).
        Sale on 12/30/2023 should match via vest-date same-day.
        """
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            "Date,Action,Symbol,Description,Price,Quantity,Fees & Comm,Amount\n"
            "01/02/2024 as of 12/29/2023,Stock Plan Activity,GOOG,RSU Vest,$150.00,100,$0.00,$15000.00\n"
            "12/30/2023,Sell,GOOG,Cross-year Sale,$150.10,45,$1.00,$6753.50\n"
        )

        transactions = read_schwab_transactions(csv_file, None)

        assert len(transactions) == 2
        vest = transactions[1]
        sell = transactions[0]

        assert vest.vest_date == datetime.date(2023, 12, 29)
        assert vest.date == datetime.date(2024, 1, 2)
        assert sell.date == datetime.date(2023, 12, 30)

        # Crosses year boundary but still within window
        assert vest.vest_date.year == 2023
        assert vest.date.year == 2024
        assert sell.date.year == 2023
        assert vest.vest_date <= sell.date <= vest.date

    def test_sale_before_during_and_after_vest_window(self, tmp_path: Path) -> None:
        """Test sales before, during, and after the vest window.

        Sales on:
        - 08/14: Before vest (uses earlier holdings)
        - 08/16: During vest window (vest-date matching)
        - 08/20: After settlement (Section 104)

        This tests the boundaries of vest-date matching.
        """
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            "Date,Action,Symbol,Description,Price,Quantity,Fees & Comm,Amount\n"
            "08/01/2023,Buy,GOOG,Earlier Purchase,$130.00,100,$0.00,-$13000.00\n"
            "08/18/2023 as of 08/15/2023,Stock Plan Activity,GOOG,RSU Vest,$140.35,100,$0.00,$14035.00\n"
            "08/14/2023,Sell,GOOG,Before vest,$135.00,10,$0.50,$1349.50\n"
            "08/16/2023,Sell,GOOG,During vest,$140.50,20,$0.50,$2809.50\n"
            "08/20/2023,Sell,GOOG,After settlement,$142.00,15,$0.50,$2129.50\n"
        )

        transactions = read_schwab_transactions(csv_file, None)

        assert len(transactions) == 5
        buy = transactions[4]
        vest = transactions[3]
        sell_before = transactions[2]
        sell_during = transactions[1]
        sell_after = transactions[0]

        assert buy.date == datetime.date(2023, 8, 1)
        assert vest.vest_date == datetime.date(2023, 8, 15)
        assert vest.date == datetime.date(2023, 8, 18)

        # Before vest window
        assert sell_before.date == datetime.date(2023, 8, 14)
        assert sell_before.date < vest.vest_date

        # During vest window
        assert sell_during.date == datetime.date(2023, 8, 16)
        assert vest.vest_date <= sell_during.date <= vest.date

        # After vest window
        assert sell_after.date == datetime.date(2023, 8, 20)
        assert sell_after.date > vest.date

    def test_vest_date_not_set_without_as_of(self, tmp_path: Path) -> None:
        """Test that vest_date is None for transactions without 'as of' format.

        Regular buy/sell transactions should not have vest_date set.
        This ensures backward compatibility with non-RSU transactions.
        """
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            "Date,Action,Symbol,Description,Price,Quantity,Fees & Comm,Amount\n"
            "08/15/2023,Buy,GOOG,Regular Purchase,$140.00,100,$0.00,-$14000.00\n"
            "08/16/2023,Sell,GOOG,Regular Sale,$141.00,50,$0.00,$7050.00\n"
        )

        transactions = read_schwab_transactions(csv_file, None)

        assert len(transactions) == 2
        buy = transactions[1]
        sell = transactions[0]

        # No vest_date for regular transactions
        assert buy.vest_date is None
        assert sell.vest_date is None

    def test_awards_csv_integration_vest_date_and_price(self, tmp_path: Path) -> None:
        """Test that awards.csv provides vest_date and FMV price when 'as of' is missing.

        Schwab awards.csv contains:
        - Vest date (Date column - the vesting event date)
        - Fair Market Value price (FairMarketValuePrice)

        This test verifies the parser extracts both from awards.csv when
        the transaction CSV doesn't have "as of" format.

        Awards.csv has a special 2-row format where each logical row spans
        two physical rows, with data split between them.
        """
        # Create awards.csv with vest date and FMV price
        # Format: First row has Date/Symbol/Description, second row has FMV price
        awards_file = tmp_path / "awards.csv"
        awards_file.write_text(
            '"Date","Action","Symbol","Description","Quantity","FeesAndCommissions",'
            '"DisbursementElection","Amount","AwardDate","AwardId","FairMarketValuePrice",'
            '"SalePrice","SharesSoldWithheldForTaxes","NetSharesDeposited","Taxes"\n'
            # First logical row (split into 2 physical rows)
            '"08/15/2023","Lapse","GOOG","Restricted Stock Lapse","100","","","","","","","","","",""\n'
            '"","","","","","","","","03/21/2022","101883189","$140.35","","35","65","$4,912.25"\n'
        )

        # Create transactions.csv WITHOUT "as of" format
        # Settlement date is 08/18, which is T+3 after vest (08/15)
        # Price is empty (will be populated from awards.csv)
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            "Date,Action,Symbol,Description,Price,Quantity,Fees & Comm,Amount\n"
            "08/18/2023,Stock Plan Activity,GOOG,RSU Vest,,,,$0.00\n"
            "08/15/2023,Sell,GOOG,Tax Sale,$140.35,35,$0.00,$4912.25\n"
        )

        transactions = read_schwab_transactions(csv_file, awards_file)

        assert len(transactions) == 2
        vest = transactions[1]

        # Verify vest_date is populated from awards.csv
        # Awards file has Date=08/15, transaction has settlement=08/18
        # Parser searches back from 08/18 and finds 08/15 in awards (within 7 days)
        assert vest.vest_date == datetime.date(2023, 8, 15)

        # Verify price is populated from awards.csv (FairMarketValuePrice)
        assert vest.price == Decimal("140.35")

        # Settlement date remains as-is from CSV
        assert vest.date == datetime.date(2023, 8, 18)
