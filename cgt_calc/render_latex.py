#!/usr/bin/env python3

from decimal import Decimal
import os
import subprocess
import tempfile

import jinja2

from .misc import round_decimal
from .model import CalculationLog

# Latex template for calculations report
calculations_template_file = "template.tex.j2"


def render_calculations(
    calculation_log: CalculationLog, tax_year: int, date_from_index, output_file: str
) -> None:
    print("Generate calculations report")
    current_directory = os.path.abspath(".")
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
        loader=jinja2.FileSystemLoader(current_directory),
    )
    template = latex_template_env.get_template(calculations_template_file)
    output_text = template.render(
        calculation_log=calculation_log,
        tax_year=tax_year,
        date_from_index=date_from_index,
        round_decimal=round_decimal,
        Decimal=Decimal,
    )
    generated_file_fd, generated_file = tempfile.mkstemp(suffix=".tex")
    os.write(generated_file_fd, output_text.encode())
    os.close(generated_file_fd)
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
    os.remove(generated_file)
    os.remove(f"{output_filename}.log")
    os.remove(f"{output_filename}.aux")
    os.rename(output_filename + ".pdf", output_file)
