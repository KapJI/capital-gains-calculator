"""Render PDF report with LaTeX."""
from decimal import Decimal
import os
from pathlib import Path
import subprocess
import tempfile

import jinja2

from .const import PACKAGE_NAME, TEMPLATE_NAME
from .dates import date_from_index
from .model import CapitalGainsReport
from .util import round_decimal, strip_zeros


def render_calculations(
    report: CapitalGainsReport,
    output_path: Path,
    skip_pdflatex: bool = False,
) -> None:
    """Render PDF report."""
    print("Generate calculations report")
    latex_template_env = jinja2.Environment(
        block_start_string="\\BLOCK{",
        block_end_string="}",
        variable_start_string="\\VAR{",
        variable_end_string="}",
        comment_start_string="\\#{",
        comment_end_string="}",
        line_statement_prefix="%%",
        line_comment_prefix="%#",
        trim_blocks=True,
        autoescape=False,
        loader=jinja2.PackageLoader(PACKAGE_NAME, "resources"),
    )
    template = latex_template_env.get_template(TEMPLATE_NAME)
    output_text = template.render(
        report=report,
        date_from_index=date_from_index,
        round_decimal=round_decimal,
        strip_zeros=strip_zeros,
        Decimal=Decimal,
    )
    generated_file_fd, generated_file = tempfile.mkstemp(suffix=".tex")
    os.write(generated_file_fd, output_text.encode())
    os.close(generated_file_fd)

    # In case of testing
    if skip_pdflatex:
        return
    current_directory = Path.cwd()
    output_filename = "calculations"
    subprocess.run(
        [
            "pdflatex",
            f"-output-directory={current_directory}",
            f"-jobname={output_filename}",
            "-interaction=batchmode",
            generated_file,
        ],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    Path(generated_file).unlink()
    Path(f"{output_filename}.log").unlink()
    Path(f"{output_filename}.aux").unlink()
    Path(f"{output_filename}.pdf").replace(output_path)
