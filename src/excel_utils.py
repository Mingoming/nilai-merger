from pathlib import Path
import csv

import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter


def format_excel(ws):
    HEADER_FILL = PatternFill("solid", fgColor="1F4E79")
    HEADER_FONT = Font(name="Calibri", bold=True, color="FFFFFF", size=10)
    DATA_FONT = Font(name="Calibri", size=10)
    ALT_FILL = PatternFill("solid", fgColor="D6E4F0")
    WHITE_FILL = PatternFill("solid", fgColor="FFFFFF")
    CENTER_ALIGN = Alignment(horizontal="center", vertical="center", wrap_text=True)
    LEFT_ALIGN = Alignment(horizontal="left", vertical="center", wrap_text=True)
    thin = Side(style="thin", color="B0C4DE")
    BORDER = Border(left=thin, right=thin, top=thin, bottom=thin)

    n_rows = ws.max_row
    n_cols = ws.max_column

    for col in range(1, n_cols + 1):
        cell = ws.cell(row=1, column=col)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = CENTER_ALIGN
        cell.border = BORDER

    for row in range(2, n_rows + 1):
        fill = ALT_FILL if row % 2 == 0 else WHITE_FILL
        for col in range(1, n_cols + 1):
            cell = ws.cell(row=row, column=col)
            cell.fill = fill
            cell.font = DATA_FONT
            cell.border = BORDER
            cell.alignment = LEFT_ALIGN if col <= 2 else CENTER_ALIGN

    for col in range(1, n_cols + 1):
        max_len = 0
        col_letter = get_column_letter(col)
        for row in range(1, n_rows + 1):
            val = ws.cell(row=row, column=col).value
            if val:
                max_len = min(max(max_len, len(str(val))), 40)
        ws.column_dimensions[col_letter].width = max_len + 3

    ws.freeze_panes = "A2"


def _read_delimited_file(csv_path):
    """Baca export nilai yang bisa berupa CSV koma atau TSV meski ekstensi .csv."""
    last_error = None
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            # sep=None + python engine melakukan sniff delimiter (comma/tab/semicolon).
            df = pd.read_csv(csv_path, encoding=encoding, sep=None, engine="python")
            # Record the same first nonblank-line sniff used by pandas' Python
            # reader, without changing its delimiter or dtype inference.
            with open(csv_path, encoding=encoding, newline="") as source:
                first_line = next((line for line in source if line.strip()), "")
            try:
                df.attrs["delimiter"] = csv.Sniffer().sniff(first_line).delimiter
            except csv.Error:
                df.attrs["delimiter"] = None
            return df
        except (UnicodeDecodeError, pd.errors.ParserError) as exc:
            last_error = exc
    if last_error:
        raise last_error
    raise ValueError(f"File tidak dapat dibaca: {csv_path}")


def csv_to_df(csv_path):
    # Fungsi tetap berperan sebagai CSV/TSV -> DataFrame sebelum Excel.
    # Bedanya: delimiter dideteksi otomatis dan footer hanya dihapus jika memang
    # benar-benar baris 'Overall average', sehingga siswa terakhir tidak ikut terhapus.
    df = _read_delimited_file(csv_path)

    if len(df) > 0 and len(df.columns) > 0:
        first_col = df.columns[0]
        last_value = df.iloc[-1][first_col]
        if str(last_value).strip().casefold() == "overall average":
            df = df.iloc[:-1]

    if len(df.columns) == 0:
        raise ValueError("CSV tidak memiliki kolom.")

    col_a = df.columns[0]
    df = df.sort_values(by=col_a, kind="stable").reset_index(drop=True)
    return df


def df_to_excel(df, xlsx_path):
    Path(xlsx_path).parent.mkdir(parents=True, exist_ok=True)
    df.to_excel(xlsx_path, index=False, engine="openpyxl")
    wb = load_workbook(xlsx_path)
    ws = wb.active
    format_excel(ws)
    wb.save(xlsx_path)
    wb.close()
