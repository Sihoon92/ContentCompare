"""문서 리더 (Excel=xlwings, Word/PPT=win32com)."""

from .base import DocumentReader, get_reader
from .com_util import close_all as close_all_office
from .excel_reader import ExcelReader
from .ppt_reader import PptReader
from .word_reader import WordReader

__all__ = [
    "DocumentReader",
    "get_reader",
    "ExcelReader",
    "WordReader",
    "PptReader",
    "close_all_office",
]
