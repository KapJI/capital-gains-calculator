"""Render PDF report with LaTeX."""

from decimal import Decimal
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Union

import jinja2

from .const import CG_TEMPLATE_NAME, DG_TEMPLATE_NAME, PACKAGE_NAME
from .model import CapitalGainsReport, DividendsReport
from .util import round_decimal, strip_zeros


def render_calculations(
    cg_report: CapitalGainsReport,
    dg_report: DividendsReport,
    cg_output_path: Path,
    dg_output_path: Path,
    skip_pdflatex: bool = False,
) -> None:
    """Render PDF report."""
    print("Generate calculations reports")
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
    _render(
        cg_report, latex_template_env, CG_TEMPLATE_NAME, cg_output_path, skip_pdflatex
    )
    _render(
        dg_report, latex_template_env, DG_TEMPLATE_NAME, dg_output_path, skip_pdflatex
    )


def _render(
    report: Union[CapitalGainsReport, DividendsReport],
    latex_template_env: jinja2.Environment,
    template_name: str,
    output_path: Path,
    skip_pdflatex: bool,
) -> None:
    cg_template = latex_template_env.get_template(template_name)
    output_text = cg_template.render(
        report=report,
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
    subprocess.run(
        [
            "pdflatex",
            f"-output-directory={current_directory}",
            f"-jobname={output_path}",
            "-interaction=batchmode",
            generated_file,
        ],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    Path(generated_file).unlink()
    Path(f"{output_path}.log").unlink()
    Path(f"{output_path}.aux").unlink()
    Path(f"{output_path}.pdf").replace(output_path)
