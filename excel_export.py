import re
from functools import lru_cache
from itertools import chain
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile


COLUMNS = (
    ("device", "裝置名稱", 25),
    ("door", "門禁點", 25),
    ("department", "部門", 24),
    ("name", "姓名", 16),
    ("employeeId", "工號", 16),
    ("card", "卡號", 22),
    ("datetime", "日期時間", 24),
    ("event", "事件說明", 26),
)
MAX_SHEET_ROWS = 1_048_576
SPREADSHEET = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
DOCUMENT_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
XML = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
INVALID_XML = re.compile(r"[^\u0009\u000A\u000D\u0020-\uD7FF\uE000-\uFFFD\U00010000-\U0010FFFF]")
EXCEL_ESCAPE = re.compile(r"_x[0-9a-fA-F]{4}_")


@lru_cache(maxsize=4096)
def text_cell(value):
    # Store every column as text. Access card numbers and employee IDs may have leading zeros.
    cleaned = INVALID_XML.sub("\ufffd", str(value)[:32767])
    cleaned = EXCEL_ESCAPE.sub(lambda match: "_x005F_" + match.group()[1:], cleaned)
    return f'<c t="inlineStr"><is><t xml:space="preserve">{escape(cleaned)}</t></is></c>'


def add_worksheet(workbook, sheet_no, rows):
    with workbook.open(f"xl/worksheets/sheet{sheet_no}.xml", "w") as sheet:
        widths = "".join(
            f'<col min="{i}" max="{i}" width="{width}" customWidth="1"/>'
            for i, (_, _, width) in enumerate(COLUMNS, 1)
        )
        header = "".join(text_cell(label) for _, label, _ in COLUMNS)
        sheet.write((
            f'{XML}<worksheet xmlns="{SPREADSHEET}"><sheetViews><sheetView workbookViewId="0">'
            '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
            f'</sheetView></sheetViews><cols>{widths}</cols><sheetData><row>{header}</row>'
        ).encode())
        next_row = next(rows, None)
        buffer = bytearray()
        for _ in range(MAX_SHEET_ROWS - 1):
            if next_row is None:
                break
            values = []
            for key, _, _ in COLUMNS:
                value = next_row.get(key)
                if key == "datetime" and isinstance(value, str):
                    value = value.replace("T", " ", 1)
                values.append(text_cell("" if value is None else value))
            cells = "".join(values)
            buffer.extend(f"<row>{cells}</row>".encode())
            if len(buffer) >= 131_072:
                sheet.write(buffer)
                buffer.clear()
            next_row = next(rows, None)
        if buffer:
            sheet.write(buffer)
        sheet.write(b"</sheetData></worksheet>")
    return next_row


def write_workbook(output, rows):
    counter = 1
    rows = iter(rows)
    with ZipFile(output, "w", compression=ZIP_DEFLATED, compresslevel=3) as workbook:
        next_row = add_worksheet(workbook, counter, rows)
        while next_row is not None:
            counter += 1
            next_row = add_worksheet(workbook, counter, chain((next_row,), rows))

        sheet_ids = range(1, counter + 1)
        entries = "".join(
            f'<sheet name="歷史資料 {i}" sheetId="{i}" r:id="rId{i}"/>' for i in sheet_ids
        )
        workbook.writestr("xl/workbook.xml", (
            f'{XML}<workbook xmlns="{SPREADSHEET}" xmlns:r="{DOCUMENT_REL}">'
            f'<bookViews><workbookView activeTab="0"/></bookViews><sheets>{entries}</sheets></workbook>'
        ))
        relationships = "".join(
            f'<Relationship Id="rId{i}" Type="{DOCUMENT_REL}/worksheet" Target="worksheets/sheet{i}.xml"/>'
            for i in sheet_ids
        )
        workbook.writestr("xl/_rels/workbook.xml.rels", (
            f'{XML}<Relationships xmlns="{PACKAGE_REL}">{relationships}</Relationships>'
        ))
        workbook.writestr("_rels/.rels", (
            f'{XML}<Relationships xmlns="{PACKAGE_REL}"><Relationship Id="rId1" '
            f'Type="{DOCUMENT_REL}/officeDocument" Target="xl/workbook.xml"/></Relationships>'
        ))
        types = "".join(
            f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            for i in sheet_ids
        )
        workbook.writestr("[Content_Types].xml", (
            f'{XML}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            f'{types}</Types>'
        ))
