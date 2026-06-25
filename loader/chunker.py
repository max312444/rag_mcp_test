import re

CHUNK_SIZE = 800
OVERLAP_SIZE = 150


def split_by_articles(content: str) -> list[str]:
    """
    섹션 내용을 하위 단위로 분리
    - 학칙: 제X조 패턴
    - 공고문: 가. 나. 다. / ○ 패턴
    """
    pattern_article = re.compile(r'(?=제\d+조(?:의\d+)?\s*[\(（])')
    parts = pattern_article.split(content)
    meaningful = [p.strip() for p in parts if p.strip() and len(p.strip()) >= 20]

    if len(meaningful) >= 2:
        return meaningful

    pattern_items = re.compile(r'(?=(?:가\.|나\.|다\.|라\.|마\.|바\.|사\.|○))')
    parts = pattern_items.split(content)
    meaningful = [p.strip() for p in parts if p.strip() and len(p.strip()) >= 20]

    return meaningful if len(meaningful) >= 2 else []


def sliding_window_chunks(text: str, chunk_size: int = CHUNK_SIZE,
                          overlap: int = OVERLAP_SIZE) -> list[str]:
    """
    긴 텍스트를 sliding window로 분할 (overlap 포함)
    """
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end]
        if chunk.strip():
            chunks.append(chunk.strip())
        if end >= len(text):
            break
        start = end - overlap

    return chunks


def split_into_children(section: dict) -> list[dict]:
    """
    Parent 섹션을 Child로 분리 (overlap 청킹)
    1단계: 조(Article) 단위 분리
    2단계: 앞뒤 조 context를 overlap으로 포함
    3단계: 긴 조는 sliding window로 추가 분할
    """
    content = section["content"]
    parent_section = section["section"]
    page_num = section["page_num"]

    articles = split_by_articles(content)

    if not articles:
        chunks = sliding_window_chunks(content)
        return [{
            "parent_section": parent_section,
            "content": c,
            "content_type": "text",
            "page_num": page_num
        } for c in chunks]

    children = []

    for i, article in enumerate(articles):
        prev_context = ""
        next_context = ""

        if i > 0:
            prev_text = articles[i - 1]
            prev_context = prev_text[-OVERLAP_SIZE:] + "\n\n"

        if i < len(articles) - 1:
            next_text = articles[i + 1]
            next_context = "\n\n" + next_text[:OVERLAP_SIZE]

        full_chunk = prev_context + article + next_context

        if len(full_chunk) > CHUNK_SIZE * 1.5:
            sub_chunks = sliding_window_chunks(article)
            for j, sc in enumerate(sub_chunks):
                if j == 0 and prev_context:
                    sc = prev_context + sc
                if j == len(sub_chunks) - 1 and next_context:
                    sc = sc + next_context

                children.append({
                    "parent_section": parent_section,
                    "content": sc,
                    "content_type": "text",
                    "page_num": page_num
                })
        else:
            children.append({
                "parent_section": parent_section,
                "content": full_chunk,
                "content_type": "text",
                "page_num": page_num
            })

    return children


def create_table_chunks(tables: list[dict]) -> list[dict]:
    children = []
    for t in tables:
        children.append({
            "parent_section": t.get("section", ""),
            "content": t["content_markdown"],
            "content_type": "table",
            "page_num": t["page_num"]
        })
    return children


def build_parent_child(sections: list[dict]) -> tuple[list[dict], list[dict]]:
    parents = []
    children = []

    for section in sections:
        parent = {
            "section": section["section"],
            "content": section["content"],
            "page_num": section["page_num"]
        }
        parents.append(parent)
        children.extend(split_into_children(section))

    return parents, children


if __name__ == "__main__":
    from extract import extract_text_by_page, extract_sections

    pdf_path = "/app/data/영진전문대학교 학칙.pdf"

    pages = extract_text_by_page(pdf_path)
    sections = extract_sections(pages)
    parents, children = build_parent_child(sections)

    print(f"Parent 수: {len(parents)}")
    print(f"Child 수: {len(children)}")
    print()

    for i, child in enumerate(children[:5]):
        print(f"[Child {i+1}] ({child['content_type']}) {len(child['content'])}자")
        print(f"  Parent: {child['parent_section']}")
        print(f"  내용: {child['content'][:100]}...")
        print()
