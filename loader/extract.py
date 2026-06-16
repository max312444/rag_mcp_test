import pdfplumber
import os
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
    반환: [{"page_num": 1, "text": "..."}, ...]
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
                pages.append({
                    "page_num": i,
                    "text": text.strip()
                })

    return pages


def extract_sections(pages: list[dict]) -> list[dict]:
    """
    페이지 텍스트를 대섹션 기준으로 분리
    기준: Ⅰ, Ⅱ, Ⅲ, Ⅳ, Ⅴ, Ⅵ, Ⅶ, Ⅷ, 붙임
    반환: [{"section": "Ⅰ 사업 개요", "content": "...", "page_num": 1}, ...]
    """
    full_text = ""
    for p in pages:
        full_text += f"\n[PAGE:{p['page_num']}]\n{p['text']}"

    section_pattern = re.compile(
        r'(Ⅰ|Ⅱ|Ⅲ|Ⅳ|Ⅴ|Ⅵ|Ⅶ|Ⅷ|붙임\s*\d+)\s+[^\n]+'
    )

    matches = list(section_pattern.finditer(full_text))
    sections = []

    for i, match in enumerate(matches):
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(full_text)

        section_text = full_text[start:end].strip()

        page_match = re.search(r'\[PAGE:(\d+)\]', full_text[max(0, start-100):start+100])
        page_num = int(page_match.group(1)) if page_match else 0

        clean_text = re.sub(r'\[PAGE:\d+\]', '', section_text).strip()

        sections.append({
            "section": match.group(0).strip(),
            "content": clean_text,
            "page_num": page_num
        })

    return sections


if __name__ == "__main__":
    pdf_path = "/app/data/2026_해외취업연수사업.pdf"

    print("PDF 텍스트 추출 중...")
    pages = extract_text_by_page(pdf_path)
    print(f"총 {len(pages)} 페이지 추출 완료")

    print("섹션 분리 중...")
    sections = extract_sections(pages)
    print(f"총 {len(sections)}개 섹션 추출 완료")

    for s in sections:
        print(f"[{s['page_num']}p] {s['section']} - {len(s['content'])}자")