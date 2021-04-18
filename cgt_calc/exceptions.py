class ParsingError(Exception):
    def __init__(self, file, message):
        self.message = f"While parsing {file}, {message}"
        super().__init__(self.message)


class InvalidTransactionError(Exception):
    def __init__(self, transaction, message):
        self.transaction = transaction
        self.message = f"{message} for the following transaction:\n{transaction}"
        super().__init__(self.message)


class AmountMissingError(InvalidTransactionError):
    def __init__(self, transaction):
        super().__init__(transaction, "Amount missing")


class SymbolMissingError(InvalidTransactionError):
    def __init__(self, transaction):
        super().__init__(transaction, "Symbol missing")


class PriceMissingError(InvalidTransactionError):
    def __init__(self, transaction):
        super().__init__(transaction, "Price missing")


class QuantityNotPositiveError(InvalidTransactionError):
    def __init__(self, transaction):
        super().__init__(transaction, "Positive quantity required")


class UnexpectedColumnCountError(ParsingError):
    def __init__(self, count, row, file):
        super().__init__(
            file, f"The following row doesn't have {count} columns:\n{row}"
        )


class CalculatedAmountDiscrepancy(InvalidTransactionError):
    def __init__(self, transaction, calculated_amount):
        super().__init__(
            transaction,
            f"Calculated amount({calculated_amount}) differs from supplied amount ({transaction.amount})",
        )


class CalculationError(Exception):
    pass


class ExchangeRateMissingError(CalculationError):
    def __init__(self, symbol, date):
        self.message = f"No GBP/{symbol} price for {date}"
        super().__init__(self.message)
