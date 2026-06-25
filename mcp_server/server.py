import os
import sys
import psycopg2
from mcp.server.fastmcp import FastMCP
from sentence_transformers import SentenceTransformer
from reranker import rerank

mcp = FastMCP("rag-mcp-test")
model = None

SIMILARITY_THRESHOLD = float(os.getenv("SIMILARITY_THRESHOLD", "0.4"))


def get_model():
    global model
    if model is None:
        print(f"모델 로드 중: {os.getenv('EMBEDDING_MODEL')}", file=sys.stderr)
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


def _quality_indicator(best_score: float) -> str:
    if best_score >= 0.6:
        return "[검색 품질: 높음]"
    elif best_score >= SIMILARITY_THRESHOLD:
        return "[검색 품질: 보통]"
    else:
        return (
            f"[검색 품질: 낮음 - 최고 관련도 {best_score:.3f}]\n"
            "검색 결과의 관련도가 낮습니다. 다른 검색어를 시도하거나, "
            "search_keyword로 키워드 검색, 또는 list_sections로 문서 구조를 확인해보세요."
        )


def _vector_search(query_embedding: list, top_k: int = 20,
                   section_filter: str = None, content_type: str = None) -> list[dict]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            conditions = []
            params = [query_embedding, query_embedding]

            if section_filter:
                conditions.append("c.parent_section ILIKE %s")
                params.append(f"%{section_filter}%")
            if content_type:
                conditions.append("c.content_type = %s")
                params.append(content_type)

            where = ""
            if conditions:
                where = "WHERE " + " AND ".join(conditions)

            cur.execute(f"""
                SELECT
                    c.id, c.parent_id, c.parent_section, c.content,
                    c.content_type, c.page_num,
                    1 - (c.embedding <=> %s::vector) AS similarity
                FROM child_chunks c
                {where}
                ORDER BY c.embedding <=> %s::vector
                LIMIT %s;
            """, (*params, top_k))

            results = []
            for row in cur.fetchall():
                results.append({
                    "id": row[0], "parent_id": row[1],
                    "parent_section": row[2], "content": row[3],
                    "content_type": row[4], "page_num": row[5],
                    "similarity": float(row[6])
                })
            return results
    finally:
        conn.close()


def _keyword_search(query: str, top_k: int = 20) -> list[dict]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SET pg_trgm.similarity_threshold = 0.05;")
            cur.execute("""
                SELECT
                    c.id, c.parent_id, c.parent_section, c.content,
                    c.content_type, c.page_num,
                    GREATEST(
                        similarity(c.content, %s),
                        CASE WHEN c.content ILIKE %s THEN 0.5 ELSE 0.0 END
                    ) AS keyword_score
                FROM child_chunks c
                WHERE c.content %% %s OR c.content ILIKE %s
                ORDER BY keyword_score DESC
                LIMIT %s;
            """, (query, f"%{query}%", query, f"%{query}%", top_k))

            results = []
            for row in cur.fetchall():
                results.append({
                    "id": row[0], "parent_id": row[1],
                    "parent_section": row[2], "content": row[3],
                    "content_type": row[4], "page_num": row[5],
                    "keyword_score": float(row[6])
                })
            return results
    finally:
        conn.close()


def _fetch_parent(parent_id: int) -> dict | None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, section, content, page_num FROM parent_chunks WHERE id = %s;",
                (parent_id,)
            )
            row = cur.fetchone()
            if row:
                return {"id": row[0], "section": row[1],
                        "content": row[2], "page_num": row[3]}
            return None
    finally:
        conn.close()


def _rrf_fusion(semantic_results: list[dict], keyword_results: list[dict],
                semantic_weight: float = 0.7, k: int = 60) -> list[dict]:
    scores = {}
    items = {}

    for rank, item in enumerate(semantic_results):
        cid = item["id"]
        scores[cid] = scores.get(cid, 0) + semantic_weight / (k + rank + 1)
        items[cid] = item

    for rank, item in enumerate(keyword_results):
        cid = item["id"]
        scores[cid] = scores.get(cid, 0) + (1 - semantic_weight) / (k + rank + 1)
        if cid not in items:
            items[cid] = item

    for cid, item in items.items():
        item["rrf_score"] = scores[cid]

    return sorted(items.values(), key=lambda x: x["rrf_score"], reverse=True)


def _format_results(results: list[dict], score_key: str = "similarity") -> str:
    if not results:
        return (
            "관련 내용을 찾을 수 없습니다.\n\n"
            "다음을 시도해보세요:\n"
            "- 다른 검색어로 search_docs 재시도\n"
            "- search_keyword로 키워드 검색\n"
            "- list_sections로 문서 목차 확인 후 get_section으로 섹션 조회"
        )

    best_score = max(r.get(score_key, 0) for r in results)
    quality = _quality_indicator(best_score)

    seen_parents = set()
    output_parts = [quality, ""]

    for r in results:
        parent_id = r.get("parent_id")
        score = r.get(score_key, r.get("rerank_score", 0))
        content_tag = f"[{r.get('content_type', 'text')}]" if r.get('content_type') != 'text' else ""

        if parent_id and parent_id not in seen_parents:
            parent = _fetch_parent(parent_id)
            if parent:
                seen_parents.add(parent_id)
                output_parts.append(
                    f"[관련도: {score:.3f} / {r['page_num']}페이지] {content_tag}\n"
                    f"[섹션: {r['parent_section']}]\n\n"
                    f"{parent['content']}"
                )
                continue

        output_parts.append(
            f"[관련도: {score:.3f} / {r['page_num']}페이지] {content_tag}\n"
            f"[섹션: {r['parent_section']}]\n\n"
            f"{r['content']}"
        )

    return "\n\n---\n\n".join(output_parts)


# ========== MCP Tools ==========

@mcp.tool()
def search_docs(query: str, top_k: int = 5, section_filter: str = None) -> str:
    """
    문서에서 의미 기반 검색을 수행합니다. 질문과 가장 관련 있는 내용을 벡터 유사도로 찾고,
    Cross-Encoder Reranking으로 정확도를 높입니다.

    검색 결과의 관련도가 낮으면 search_keyword나 search_hybrid 도구를 사용해보세요.
    특정 섹션의 전체 내용이 필요하면 list_sections로 구조를 확인한 후 get_section을 사용하세요.

    Args:
        query: 검색 질문 (자연어)
        top_k: 반환할 결과 수 (기본값: 5)
        section_filter: 특정 섹션에서만 검색 (예: "제7장", "시험")
    """
    try:
        embedding = get_model().encode(query).tolist()
        candidates = _vector_search(embedding, top_k=20, section_filter=section_filter)

        if not candidates:
            return _format_results([])

        reranked = rerank(query, candidates, top_k=top_k)
        return _format_results(reranked, score_key="rerank_score")

    except Exception as e:
        return f"검색 오류: {str(e)}"


@mcp.tool()
def search_keyword(query: str, top_k: int = 5) -> str:
    """
    키워드 기반으로 문서를 검색합니다.
    특정 용어, 조항 번호(예: "제15조"), 숫자, 고유명사 등 정확한 단어 매칭이 필요할 때 사용하세요.
    의미 기반 검색(search_docs)이 좋은 결과를 못 찾을 때 대안으로 사용하세요.

    Args:
        query: 검색할 키워드 또는 문구
        top_k: 반환할 결과 수 (기본값: 5)
    """
    try:
        results = _keyword_search(query, top_k=top_k)
        if not results:
            return _format_results([])
        return _format_results(results, score_key="keyword_score")

    except Exception as e:
        return f"검색 오류: {str(e)}"


@mcp.tool()
def search_hybrid(query: str, top_k: int = 5, semantic_weight: float = 0.7) -> str:
    """
    의미 검색과 키워드 검색을 결합한 하이브리드 검색입니다.
    복잡하거나 애매한 질문에 가장 효과적입니다.
    두 검색 결과를 Reciprocal Rank Fusion으로 결합한 후 Cross-Encoder로 재순위화합니다.

    Args:
        query: 검색 질문
        top_k: 반환할 결과 수 (기본값: 5)
        semantic_weight: 의미 검색 비중 (0.0~1.0, 기본값: 0.7)
    """
    try:
        embedding = get_model().encode(query).tolist()
        semantic_results = _vector_search(embedding, top_k=20)
        keyword_results = _keyword_search(query, top_k=20)

        if not semantic_results and not keyword_results:
            return _format_results([])

        fused = _rrf_fusion(semantic_results, keyword_results, semantic_weight)
        reranked = rerank(query, fused[:20], top_k=top_k)
        return _format_results(reranked, score_key="rerank_score")

    except Exception as e:
        return f"검색 오류: {str(e)}"


@mcp.tool()
def list_sections() -> str:
    """
    문서의 전체 구조(목차)를 반환합니다.
    문서에 어떤 내용이 있는지 파악할 때 가장 먼저 사용하세요.
    검색 전에 문서 구조를 이해하면 더 정확한 검색이 가능합니다.
    """
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT section, page_num, LENGTH(content) AS content_length
                FROM parent_chunks
                ORDER BY id;
            """)
            rows = cur.fetchall()
        conn.close()

        if not rows:
            return "등록된 문서가 없습니다."

        lines = ["[문서 목차]\n"]
        for section, page_num, length in rows:
            lines.append(f"- {section} ({page_num}페이지, {length}자)")

        lines.append(f"\n총 {len(rows)}개 섹션")
        lines.append("\n특정 섹션의 전체 내용을 보려면 get_section을 사용하세요.")
        return "\n".join(lines)

    except Exception as e:
        return f"오류: {str(e)}"


@mcp.tool()
def get_section(section_name: str) -> str:
    """
    특정 섹션의 전체 내용을 반환합니다.
    list_sections로 확인한 섹션명을 입력하세요.
    부분 일치도 지원합니다 (예: "제7장" 또는 "시험").

    Args:
        section_name: 섹션 이름 (예: "제7장 시험, 성적 및 졸업" 또는 "제7장")
    """
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT section, content, page_num
                FROM parent_chunks
                WHERE section ILIKE %s
                ORDER BY id
                LIMIT 3;
            """, (f"%{section_name}%",))
            rows = cur.fetchall()
        conn.close()

        if not rows:
            return (
                f"'{section_name}' 섹션을 찾을 수 없습니다.\n"
                "list_sections로 정확한 섹션명을 확인해보세요."
            )

        parts = []
        for section, content, page_num in rows:
            parts.append(f"[섹션: {section} / {page_num}페이지]\n\n{content}")

        return "\n\n---\n\n".join(parts)

    except Exception as e:
        return f"오류: {str(e)}"


@mcp.tool()
def get_table(table_id: int = None, section_filter: str = None) -> str:
    """
    문서의 표(테이블) 데이터를 조회합니다.
    표 형태의 정보(학과 목록, 졸업학점, 성적 기준 등)를 찾을 때 사용하세요.
    인자 없이 호출하면 전체 표 목록을, table_id를 지정하면 해당 표의 상세 내용을 반환합니다.

    Args:
        table_id: 특정 표 ID (생략하면 전체 표 목록 반환)
        section_filter: 특정 섹션의 표만 조회 (예: "제6장", "교과")
    """
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            if table_id is not None:
                cur.execute("""
                    SELECT id, page_num, section, caption, content_markdown
                    FROM doc_tables WHERE id = %s;
                """, (table_id,))
                row = cur.fetchone()
                conn.close()

                if not row:
                    return f"표 ID {table_id}를 찾을 수 없습니다."

                return (
                    f"[표 #{row[0]} / {row[1]}페이지]\n"
                    f"[섹션: {row[2]}]\n"
                    f"[설명: {row[3] or '없음'}]\n\n"
                    f"{row[4]}"
                )

            conditions = []
            params = []
            if section_filter:
                conditions.append("section ILIKE %s")
                params.append(f"%{section_filter}%")

            where = ""
            if conditions:
                where = "WHERE " + " AND ".join(conditions)

            cur.execute(f"""
                SELECT id, page_num, section, caption,
                       LEFT(content_markdown, 100) AS preview
                FROM doc_tables
                {where}
                ORDER BY id;
            """, params)
            rows = cur.fetchall()
        conn.close()

        if not rows:
            return "해당 조건의 표를 찾을 수 없습니다."

        lines = ["[표 목록]\n"]
        for tid, page_num, section, caption, preview in rows:
            desc = caption or preview.split("\n")[0] if preview else "내용 없음"
            lines.append(f"- 표 #{tid} ({page_num}페이지, {section}): {desc}")

        lines.append(f"\n총 {len(rows)}개 표")
        lines.append("\n특정 표의 전체 내용을 보려면 get_table(table_id=번호)를 사용하세요.")
        return "\n".join(lines)

    except Exception as e:
        return f"오류: {str(e)}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
