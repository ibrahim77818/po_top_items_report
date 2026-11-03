# Part of po_top_items_report. License OPL-1.
import logging

from . import report
from . import wizard
from .tools.report_engine import SALES_INDEX_NAME

_logger = logging.getLogger(__name__)


def uninstall_hook(env):
    """The v16 version created a partial index on account_move_line; Odoo 17+
    indexes product_id itself, so 17.0+ no longer creates it. Drop it if a
    database migrated from 16.0 still has it."""
    _logger.info("Dropping index %s", SALES_INDEX_NAME)
    env.cr.execute(f"DROP INDEX IF EXISTS {SALES_INDEX_NAME}")
