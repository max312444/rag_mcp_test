import pdfplumber
import json
import re


def table_to_markdown(table: list[list]) -> str:
    if not table:
        return ""
    rows = []
    for i, row in enumerate(table):
        cleaned = [str(cell or "").strip().replace("\n", " ") for cell in row]
        rows.append("| " + " | ".join(cleaned) + " |")
        if i == 0:
            rows.append("|" + "|".join(["---"] * len(row)) + "|")
    return "\n".join(rows)


def extract_text_by_page(pdf_path: str) -> list[dict]:
    """
    PDF에서 페이지별 텍스트 추출 (표 포함)
    """
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            tables = page.extract_tables()
            if tables:
                table_texts = [table_to_markdown(t) for t in tables if t]
                text += "\n\n" + "\n\n".join(table_texts)
            if text.strip():
                pages.append({"page_num": i, "text": text.strip()})
    return pages


def extract_sections(pages: list[dict]) -> list[dict]:
    """
    페이지 텍스트를 장(Chapter) 기준으로 분리
    패턴: 줄 시작의 제1장, 제2장, ... / 별표(독립 헤더), 부칙
    """
    full_text = ""
    for p in pages:
        full_text += f"\n[PAGE:{p['page_num']}]\n{p['text']}"

    section_pattern = re.compile(
        r'^(제\d+장\s+[^\n<]+|[ⅠⅡⅢⅣⅤⅥⅦⅧ]\s+[^\n<]+)',
        re.MULTILINE
    )

    matches = list(section_pattern.finditer(full_text))
    sections = []

    for i, match in enumerate(matches):
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(full_text)

        section_text = full_text[start:end].strip()

        page_markers = list(re.finditer(r'\[PAGE:(\d+)\]', full_text[:start + 50]))
        page_num = int(page_markers[-1].group(1)) if page_markers else 1

        clean_text = re.sub(r'\[PAGE:\d+\]', '', section_text).strip()

        section_name = match.group(1).strip()
        section_name = re.sub(r'\s*<[^>]+>\s*$', '', section_name).strip()

        sections.append({
            "section": section_name,
            "content": clean_text,
            "page_num": page_num
        })

    return sections


def extract_tables_structured(pdf_path: str, sections: list[dict]) -> list[dict]:
    """
    PDF에서 표를 구조화하여 추출.
    각 표에 소속 섹션, 캡션, 헤더, 행 데이터 포함.
    """
    section_pages = {}
    for s in sections:
        section_pages[s["page_num"]] = s["section"]

    tables_out = []

    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            raw_tables = page.extract_tables()
            if not raw_tables:
                continue

            page_text = page.extract_text() or ""

            owner_section = None
            for pn in sorted(section_pages.keys(), reverse=True):
                if i >= pn:
                    owner_section = section_pages[pn]
                    break

            for table in raw_tables:
                if not table or len(table) < 2:
                    continue

                headers_row = table[0]
                headers = [str(c or "").strip().replace("\n", " ") for c in headers_row]
                rows = []
                for row in table[1:]:
                    cleaned = [str(c or "").strip().replace("\n", " ") for c in row]
                    rows.append(cleaned)

                md = table_to_markdown(table)

                caption_match = re.search(
                    r'((?:별표|표)\s*\d*[^\n]*)', page_text
                )
                caption = caption_match.group(1).strip() if caption_match else None

                tables_out.append({
                    "page_num": i,
                    "section": owner_section,
                    "caption": caption,
                    "headers": json.dumps(headers, ensure_ascii=False),
                    "rows": rows,
                    "content_markdown": md,
                })

    return tables_out


if __name__ == "__main__":
    import sys
    pdf_path = sys.argv[1] if len(sys.argv) > 1 else "/app/data/영진전문대학교 학칙.pdf"

    print("PDF 텍스트 추출 중...")
    pages = extract_text_by_page(pdf_path)
    print(f"총 {len(pages)} 페이지 추출 완료")

    print("섹션 분리 중...")
    sections = extract_sections(pages)
    print(f"총 {len(sections)}개 섹션 추출 완료")
    for s in sections:
        print(f"  [{s['page_num']}p] {s['section']} - {len(s['content'])}자")

    print("\n표 추출 중...")
    tables = extract_tables_structured(pdf_path, sections)
    print(f"총 {len(tables)}개 표 추출 완료")
    for t in tables:
        print(f"  [{t['page_num']}p] {t['section']} - {t['caption']}")
