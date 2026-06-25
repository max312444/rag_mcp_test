import os
import json
import psycopg2
from sentence_transformers import SentenceTransformer
from extract import extract_text_by_page, extract_sections, extract_tables_structured
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
    dim = os.getenv("EMBEDDING_DIM", 768)
    with conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
        cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id          SERIAL PRIMARY KEY,
                name        TEXT NOT NULL,
                file_path   TEXT,
                loaded_at   TIMESTAMP DEFAULT NOW(),
                page_count  INT
            );
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS parent_chunks (
                id          SERIAL PRIMARY KEY,
                doc_id      INT REFERENCES documents(id),
                section     TEXT,
                content     TEXT,
                page_num    INT
            );
        """)

        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS child_chunks (
                id              SERIAL PRIMARY KEY,
                doc_id          INT REFERENCES documents(id),
                parent_id       INT REFERENCES parent_chunks(id),
                parent_section  TEXT,
                content         TEXT,
                content_type    TEXT DEFAULT 'text',
                embedding       VECTOR({dim}),
                page_num        INT
            );
        """)

        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS doc_tables (
                id              SERIAL PRIMARY KEY,
                doc_id          INT REFERENCES documents(id),
                parent_id       INT REFERENCES parent_chunks(id),
                page_num        INT,
                section         TEXT,
                caption         TEXT,
                headers         TEXT,
                content_markdown TEXT,
                content_json    JSONB,
                embedding       VECTOR({dim})
            );
        """)

        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_child_content_trgm
                ON child_chunks USING GIN (content gin_trgm_ops);
        """)

        conn.commit()
    print("DB 초기화 완료")


def register_document(conn, name: str, file_path: str, page_count: int) -> int:
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO documents (name, file_path, page_count)
            VALUES (%s, %s, %s)
            RETURNING id;
        """, (name, file_path, page_count))
        doc_id = cur.fetchone()[0]
        conn.commit()
    print(f"문서 등록 완료: {name} (doc_id={doc_id})")
    return doc_id


def insert_parents(conn, parents: list[dict], doc_id: int) -> dict:
    section_to_id = {}
    with conn.cursor() as cur:
        for p in parents:
            cur.execute("""
                INSERT INTO parent_chunks (doc_id, section, content, page_num)
                VALUES (%s, %s, %s, %s)
                RETURNING id;
            """, (doc_id, p["section"], p["content"], p["page_num"]))
            pid = cur.fetchone()[0]
            section_to_id[p["section"]] = pid
        conn.commit()
    print(f"Parent {len(parents)}개 삽입 완료")
    return section_to_id


def insert_children(conn, children: list[dict], doc_id: int,
                    section_to_id: dict, model: SentenceTransformer):
    print("Child 임베딩 생성 중...")
    texts = [c["content"] for c in children]
    embeddings = model.encode(texts, batch_size=32, show_progress_bar=True)

    with conn.cursor() as cur:
        for i, child in enumerate(children):
            parent_id = section_to_id.get(child["parent_section"])
            embedding = embeddings[i].tolist()
            content_type = child.get("content_type", "text")

            cur.execute("""
                INSERT INTO child_chunks
                    (doc_id, parent_id, parent_section, content, content_type, embedding, page_num)
                VALUES (%s, %s, %s, %s, %s, %s::vector, %s);
            """, (
                doc_id, parent_id, child["parent_section"],
                child["content"], content_type, embedding, child["page_num"]
            ))
        conn.commit()
    print(f"Child {len(children)}개 삽입 완료")


def insert_tables(conn, tables: list[dict], doc_id: int,
                  section_to_id: dict, model: SentenceTransformer):
    if not tables:
        return
    print("표 임베딩 생성 중...")
    texts = [t["content_markdown"] for t in tables]
    embeddings = model.encode(texts, batch_size=32, show_progress_bar=True)

    with conn.cursor() as cur:
        for i, t in enumerate(tables):
            parent_id = section_to_id.get(t.get("section"))
            embedding = embeddings[i].tolist()
            content_json = json.dumps(t.get("rows", []), ensure_ascii=False)

            cur.execute("""
                INSERT INTO doc_tables
                    (doc_id, parent_id, page_num, section, caption,
                     headers, content_markdown, content_json, embedding)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::vector);
            """, (
                doc_id, parent_id, t["page_num"], t.get("section"),
                t.get("caption"), t.get("headers"),
                t["content_markdown"], content_json, embedding
            ))
        conn.commit()
    print(f"표 {len(tables)}개 삽입 완료")


def load_single_pdf(pdf_path: str, conn, model: SentenceTransformer):
    doc_name = os.path.basename(pdf_path).replace(".pdf", "")
    print(f"\n{'='*50}")
    print(f"문서 로드: {doc_name}")
    print(f"{'='*50}")

    pages = extract_text_by_page(pdf_path)
    sections = extract_sections(pages)
    tables = extract_tables_structured(pdf_path, sections)

    parents, children = build_parent_child(sections)
    print(f"Parent: {len(parents)}개 / Child: {len(children)}개 / 표: {len(tables)}개")

    doc_id = register_document(conn, doc_name, pdf_path, len(pages))
    section_to_id = insert_parents(conn, parents, doc_id)
    insert_children(conn, children, doc_id, section_to_id, model)
    insert_tables(conn, tables, doc_id, section_to_id, model)

    print(f"{doc_name} 로드 완료")


def main():
    import glob

    data_dir = os.getenv("DATA_DIR", "/app/data")
    pdf_files = sorted(glob.glob(os.path.join(data_dir, "*.pdf")))

    if not pdf_files:
        print(f"오류: {data_dir}에 PDF 파일이 없습니다.")
        return

    print(f"발견된 PDF: {len(pdf_files)}개")
    for f in pdf_files:
        print(f"  - {os.path.basename(f)}")

    print(f"\n임베딩 모델 로드 중: {os.getenv('EMBEDDING_MODEL')}")
    model = SentenceTransformer(os.getenv("EMBEDDING_MODEL"))

    conn = get_connection()
    init_db(conn)

    for pdf_path in pdf_files:
        load_single_pdf(pdf_path, conn, model)

    conn.close()
    print(f"\n전체 완료: {len(pdf_files)}개 문서 로드됨")


if __name__ == "__main__":
    main()
