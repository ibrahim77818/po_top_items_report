# Part of po_top_items_report. License OPL-1.
import logging

from . import report
from . import wizard
from .tools.report_engine import SALES_INDEX_NAME

_logger = logging.getLogger(__name__)


def uninstall_hook(cr, registry):
    _logger.info("Dropping index %s", SALES_INDEX_NAME)
    cr.execute(f"DROP INDEX IF EXISTS {SALES_INDEX_NAME}")
