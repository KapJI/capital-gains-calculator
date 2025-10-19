"""Render PDF report with LaTeX."""

from decimal import Decimal
from pathlib import Path
import shutil
import subprocess
import tempfile

import jinja2

from .const import LATEX_TEMPLATE_RESOURCE, PACKAGE_NAME
from .exceptions import LatexRenderError, MissingExternalToolError
from .model import CapitalGainsReport
from .util import round_decimal, strip_zeros


def render_pdf(
    report: CapitalGainsReport,
    output_path: Path,
    skip_pdflatex: bool = False,
) -> None:
    """Render LaTeX to a PDF report."""
    print("Generating PDF report...")
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
        extensions=["jinja2.ext.loopcontrols"],
    )
    template = latex_template_env.get_template(LATEX_TEMPLATE_RESOURCE)
    output_text = template.render(
        report=report,
        round_decimal=round_decimal,
        strip_zeros=strip_zeros,
        Decimal=Decimal,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", prefix="cgt_calc_", suffix=".tex", delete=False
    ) as tmp:
        tmp_path = Path(tmp.name)
        tmp.write(output_text)

    jobname = output_path.stem
    out_dir = output_path.parent
    log_path = out_dir / f"{jobname}.latex.log"

    # Skip for integration tests when pdflatex is not available.
    if skip_pdflatex:
        return
    try:
        if shutil.which("pdflatex") is None:
            raise MissingExternalToolError("pdflatex")
        cmd = [
            "pdflatex",
            "-file-line-error",
            "-halt-on-error",
            "-interaction=nonstopmode",
            f"-output-directory={out_dir}",
            f"-jobname={jobname}",
            str(tmp_path),
        ]
        with log_path.open("w", encoding="utf-8") as log:
            subprocess.run(cmd, check=True, stdout=log, stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError as err:
        raise LatexRenderError(log_path) from err
    finally:
        # Always attempt to clean up temp and aux files.
        tmp_path.unlink(missing_ok=True)
        for ext in (".log", ".aux"):
            (out_dir / f"{jobname}{ext}").unlink(missing_ok=True)
