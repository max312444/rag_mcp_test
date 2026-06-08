import os
import psycopg2
from mcp.server.fastmcp import FastMCP
from sentence_transformers import SentenceTransformer

mcp = FastMCP(
    "rag-study",
    host=os.getenv("MCP_HOST", "0.0.0.0"),
    port=int(os.getenv("MCP_PORT", 8000))
)
model = None


def get_model():
    global model
    if model is None:
        print(f"모델 로드 중: {os.getenv('EMBEDDING_MODEL')}")
        model = SentenceTransformer(os.getenv("EMBEDDING_MODEL"))
    return model


def get_connection():
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST"),
        port=os.getenv("POSTGRES_PORT"),
        dbname=os.getenv("POSTGRES_DB"),
        user=os.getenv("POSTGRES_USER"),
        password=os.getenv("POSTGRES_PASSWORD")
    )


@mcp.tool()
def search_docs(query: str) -> str:
    """
    해외취업연수사업 공고 문서에서 질문과 관련된 내용을 검색합니다.
    질문에 대한 답변을 찾기 위해 이 도구를 사용하세요.
    """
    try:
        # 1. 질문 임베딩
        embedding = get_model().encode(query).tolist()

        # 2. Child 유사도 검색
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    c.id,
                    c.parent_id,
                    c.parent_section,
                    c.content,
                    c.page_num,
                    1 - (c.embedding <=> %s::vector) AS similarity
                FROM child_chunks c
                ORDER BY c.embedding <=> %s::vector
                LIMIT 3;
            """, (embedding, embedding))

            child_results = cur.fetchall()

            if not child_results:
                return "관련 내용을 찾을 수 없습니다."

            # 3. Parent 텍스트 조회
            parent_ids = list(set([row[1] for row in child_results]))
            cur.execute("""
                SELECT id, section, content, page_num
                FROM parent_chunks
                WHERE id = ANY(%s);
            """, (parent_ids,))

            parents = {row[0]: row for row in cur.fetchall()}

        conn.close()

        # 4. 결과 조합
        results = []
        for child in child_results:
            child_id, parent_id, parent_section, child_content, page_num, similarity = child
            parent = parents.get(parent_id)

            results.append(f"""
[관련도: {similarity:.3f} / {page_num}페이지]
[섹션: {parent_section}]

{parent[2] if parent else child_content}
""")

        return "\n---\n".join(results)

    except Exception as e:
        return f"검색 오류: {str(e)}"


if __name__ == "__main__":
    mcp.run(transport="streamable-http")