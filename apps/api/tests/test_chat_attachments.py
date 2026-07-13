"""M85: bounded, grounded previews of data files attached to advisor chat."""

import io

from family_cfo_api.chat_attachments import build_data_file_preview


def test_csv_preview_summarizes_columns_and_rows() -> None:
    csv_bytes = (
        b"Date,Merchant,Amount\n"
        b"2026-01-05,Whole Foods,84.20\n"
        b"2026-01-06,Shell,52.00\n"
        b"2026-01-07,Netflix,15.49\n"
    )

    preview = build_data_file_preview("spending.csv", csv_bytes)

    assert "spending.csv" in preview
    assert "3 data rows" in preview
    assert "Date, Merchant, Amount" in preview
    # The Amount column is recognized as numeric with a real sum (grounded).
    assert "sum 151.69" in preview
    assert "Merchant: text" in preview
    assert "Whole Foods" in preview  # first rows shown


def test_csv_preview_bounds_a_huge_file() -> None:
    rows = "\n".join(f"2026-01-01,item{i},{i}.00" for i in range(10_000))
    csv_bytes = ("a,b,c\n" + rows).encode()

    preview = build_data_file_preview("big.csv", csv_bytes)

    assert len(preview) <= 4_000
    assert "truncated" in preview


def test_xlsx_preview_reads_the_first_sheet() -> None:
    import openpyxl

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Budget"
    sheet.append(["Category", "Limit"])
    sheet.append(["Groceries", 600])
    sheet.append(["Dining", 250])
    buffer = io.BytesIO()
    workbook.save(buffer)

    preview = build_data_file_preview("budget.xlsx", buffer.getvalue())

    assert "Budget" in preview  # sheet name
    assert "Category, Limit" in preview
    assert "sum 850.00" in preview


def test_text_preview_shows_head_and_counts() -> None:
    text = ("line one\nline two\nline three\n").encode()

    preview = build_data_file_preview("notes.txt", text)

    assert "3 lines" in preview
    assert "line one" in preview


def test_corrupt_spreadsheet_degrades_gracefully() -> None:
    preview = build_data_file_preview("broken.xlsx", b"not really a spreadsheet")

    assert "broken.xlsx" in preview
    assert "could not" in preview.lower()
