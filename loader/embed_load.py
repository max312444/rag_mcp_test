import os
import psycopg2
from sentence_transformers import SentenceTransformer
from extract import extract_text_by_page, extract_sections
from chunker import build_parent_child


def get_connection():
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST"),
        port=os.getenv("POSTGRES_PORT"),
        dbname=os.getenv("POSTGRES_DB"),
        user=os.getenv("POSTGRES_USER"),
        password=os.getenv("POSTGRES_PASSWORD")
    )


def init_db(conn):
    """
    테이블 초기화
    """
    with conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS parent_chunks (
                id          SERIAL PRIMARY KEY,
                section     TEXT,
                content     TEXT,
                page_num    INT
            );
        """)

        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS child_chunks (
                id              SERIAL PRIMARY KEY,
                parent_id       INT REFERENCES parent_chunks(id),
                parent_section  TEXT,
                content         TEXT,
                embedding       VECTOR({os.getenv("EMBEDDING_DIM", 768)}),
                page_num        INT
            );
        """)

        conn.commit()
    print("DB 초기화 완료")


def insert_parents(conn, parents: list[dict]) -> list[int]:
    """
    Parent 삽입 후 ID 목록 반환
    """
    ids = []
    with conn.cursor() as cur:
        for p in parents:
            cur.execute("""
                INSERT INTO parent_chunks (section, content, page_num)
                VALUES (%s, %s, %s)
                RETURNING id;
            """, (p["section"], p["content"], p["page_num"]))
            ids.append(cur.fetchone()[0])
        conn.commit()
    print(f"Parent {len(ids)}개 삽입 완료")
    return ids


def insert_children(conn, children: list[dict], model: SentenceTransformer):
    """
    Child 임베딩 생성 후 삽입
    """
    print("임베딩 생성 중...")
    texts = [c["content"] for c in children]
    embeddings = model.encode(texts, batch_size=32, show_progress_bar=True)

    # Parent 섹션명 → ID 매핑
    section_to_id = {}
    with conn.cursor() as cur:
        cur.execute("SELECT id, section FROM parent_chunks;")
        for row in cur.fetchall():
            section_to_id[row[1]] = row[0]

    with conn.cursor() as cur:
        for i, child in enumerate(children):
            parent_id = section_to_id.get(child["parent_section"])
            embedding = embeddings[i].tolist()

            cur.execute("""
                INSERT INTO child_chunks (parent_id, parent_section, content, embedding, page_num)
                VALUES (%s, %s, %s, %s::vector, %s);
            """, (
                parent_id,
                child["parent_section"],
                child["content"],
                embedding,
                child["page_num"]
            ))
        conn.commit()
    print(f"Child {len(children)}개 삽입 완료")


def main():
    pdf_path = "/app/data/2026_해외취업연수사업.pdf"

    # 1. PDF 추출
    print("PDF 추출 중...")
    pages = extract_text_by_page(pdf_path)
    sections = extract_sections(pages)

    # 2. Chunking
    print("Parent-Child Chunking 중...")
    parents, children = build_parent_child(sections)
    print(f"Parent: {len(parents)}개 / Child: {len(children)}개")

    # 3. 모델 로드
    print(f"임베딩 모델 로드 중: {os.getenv('EMBEDDING_MODEL')}")
    model = SentenceTransformer(os.getenv("EMBEDDING_MODEL"))

    # 4. DB 연결 및 삽입
    conn = get_connection()
    init_db(conn)
    insert_parents(conn, parents)
    insert_children(conn, children, model)
    conn.close()

    print("완료")


if __name__ == "__main__":
    main()