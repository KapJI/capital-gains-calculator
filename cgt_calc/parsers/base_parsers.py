"""Base parser from which all brokers are derived."""

from abc import ABC, abstractmethod
import argparse
import logging
from pathlib import Path
from typing import TextIO

from cgt_calc.args_validators import existing_directory_type, existing_file_type
from cgt_calc.model import BrokerTransaction

LOGGER = logging.getLogger(__name__)


class BaseParser(ABC):
    """Base parser from which all brokers are derived."""

    @classmethod
    @abstractmethod
    def register_arguments(cls, arg_group: argparse._ArgumentGroup) -> None:
        """Register argparse arguments for this broker."""

    @classmethod
    @abstractmethod
    def load_from_args(cls, args: argparse.Namespace) -> list[BrokerTransaction]:
        """Load broker data from parsed arguments."""


class BaseSingleFileParser(BaseParser):
    """Parser for single transaction file."""

    arg_name: str
    pretty_name: str
    format_name: str
    full_arg: str

    def __init_subclass__(cls, **kwargs: object) -> None:
        """Compute full arg."""
        super().__init_subclass__(**kwargs)
        # compute full_arg once per subclass at class creation time
        suffix = "json" if getattr(cls, "format_name", None) == "JSON" else "file"
        cls.full_arg = f"{getattr(cls, 'arg_name', None)}-{suffix}"

    @classmethod
    def register_arguments(cls, arg_group: argparse._ArgumentGroup) -> None:
        """Register argparse arguments for this broker."""
        arg_group.add_argument(
            f"--{cls.full_arg}",
            type=existing_file_type,
            default=None,
            metavar="PATH",
            help=f"{cls.pretty_name} transaction history in {cls.format_name} format",
        )

    @classmethod
    def load_from_args(cls, args: argparse.Namespace) -> list[BrokerTransaction]:
        """Load broker data from parsed arguments."""
        file_path = getattr(args, cls.full_arg.replace("-", "_"))
        if file_path:
            return cls.load_from_file(file_path)
        return []

    @classmethod
    def load_from_file(
        cls, file_path: Path, warn_on_empty: bool = True, show_parsing_msg: bool = True
    ) -> list[BrokerTransaction]:
        """Load broker data from file path."""
        with file_path.open(encoding="utf-8") as file:
            if show_parsing_msg:
                print(f"Parsing {file_path}...")
            transactions = cls.read_transactions(file, file_path)
            if not transactions and warn_on_empty:
                LOGGER.warning("No transactions detected in file %s", file_path)
            return transactions

    @classmethod
    @abstractmethod
    def read_transactions(
        cls, file: TextIO, file_path: Path
    ) -> list[BrokerTransaction]:
        """Parse broker transactions from open file."""


class BaseDirParser(BaseSingleFileParser):
    """Parser for loading all files within a directory."""

    glob_dir: str

    def __init_subclass__(cls, **kwargs: object) -> None:
        """Compute full arg."""
        suffix = "dir"
        cls.full_arg = f"{getattr(cls, 'arg_name', None)}-{suffix}"

    @classmethod
    def register_arguments(cls, arg_group: argparse._ArgumentGroup) -> None:
        """Register argparse arguments for this broker."""
        arg_group.add_argument(
            f"--{cls.full_arg}",
            type=existing_directory_type,
            default=None,
            metavar="DIR",
            help=f"directory with {cls.pretty_name} reports in {cls.format_name} format",
        )

    @classmethod
    def load_from_args(cls, args: argparse.Namespace) -> list[BrokerTransaction]:
        """Load broker data from parsed arguments."""
        dir_path = getattr(args, cls.full_arg.replace("-", "_"))
        if dir_path:
            return cls.load_from_dir(dir_path)
        return []

    @classmethod
    def load_from_dir(cls, dir_path: Path) -> list[BrokerTransaction]:
        """Load broker data from dir path."""
        transactions: list[BrokerTransaction] = []
        for file_path in sorted(dir_path.glob(cls.glob_dir)):
            if cls.file_path_filter(file_path):
                transactions += cls.load_from_file(file_path, warn_on_empty=False)
        if not transactions:
            LOGGER.warning(
                "No transactions detected in directory %s for broker %s",
                dir_path,
                cls.pretty_name,
            )
        return cls.post_process_transactions(transactions)

    @classmethod
    def file_path_filter(cls, file_path: Path) -> bool:
        """Choose which files to parse."""
        return True

    @classmethod
    def post_process_transactions(
        cls, transactions: list[BrokerTransaction]
    ) -> list[BrokerTransaction]:
        """Do any required post processing after loading all the transactions in the dir."""
        return transactions
