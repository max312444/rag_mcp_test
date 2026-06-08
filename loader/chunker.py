import re


def split_into_children(section: dict) -> list[dict]:
    """
    Parent 섹션을 Child 단위로 분리
    기준: 가. 나. 다. / ○ / - / 표 단위
    반환: [{"parent_section": "...", "content": "...", "page_num": 1}, ...]
    """
    content = section["content"]
    parent_section = section["section"]
    page_num = section["page_num"]

    # Child 구분 패턴 (가. 나. 다. / ○으로 시작하는 항목)
    child_pattern = re.compile(
        r'(?=(?:가\.|나\.|다\.|라\.|마\.|바\.|사\.|○))'
    )

    parts = child_pattern.split(content)
    children = []

    for part in parts:
        part = part.strip()
        if len(part) < 20:  # 너무 짧은 건 스킵
            continue

        children.append({
            "parent_section": parent_section,
            "content": part,
            "page_num": page_num
        })

    # Child가 안 나뉘면 Parent 내용 자체를 Child로
    if not children:
        children.append({
            "parent_section": parent_section,
            "content": content,
            "page_num": page_num
        })

    return children


def build_parent_child(sections: list[dict]) -> tuple[list[dict], list[dict]]:
    """
    Parent/Child 구조 생성
    반환: (parents, children)
    """
    parents = []
    children = []

    for section in sections:
        # Parent 등록
        parent = {
            "section": section["section"],
            "content": section["content"],
            "page_num": section["page_num"]
        }
        parents.append(parent)

        # Child 분리
        child_list = split_into_children(section)
        for child in child_list:
            children.append(child)

    return parents, children


if __name__ == "__main__":
    from extract import extract_text_by_page, extract_sections

    pdf_path = "/app/data/2026_해외취업연수사업.pdf"

    pages = extract_text_by_page(pdf_path)
    sections = extract_sections(pages)
    parents, children = build_parent_child(sections)

    print(f"Parent 수: {len(parents)}")
    print(f"Child 수: {len(children)}")
    print()

    for i, child in enumerate(children[:5]):
        print(f"[Child {i+1}]")
        print(f"  Parent: {child['parent_section']}")
        print(f"  내용: {child['content'][:80]}...")
        print()