# RAG MCP Server

PDF 문서를 기반으로 Claude가 답변할 수 있게 하는 RAG 시스템.
MCP(Model Context Protocol)를 통해 Claude Desktop에 `search_docs` 툴로 연결된다.

---

## 목적

Claude는 학습 데이터에 없는 문서(사내 공고문, 특정 PDF 등)를 모른다.
이 프로젝트는 문서를 벡터 DB에 저장해두고, Claude가 질문할 때마다 관련 내용을 검색해서 답변 근거로 활용할 수 있게 한다.

---

## 전체 구조

```
[데이터 적재 - 1회]
PDF → extract.py → chunker.py → embed_load.py → pgvector DB

[서비스 - 상시]
Claude Desktop → search_docs 툴 호출 → server.py → pgvector DB 검색 → 결과 반환 → Claude 답변
```

---

## 디렉토리 구조

```
rag_mcp_test/
├── data/
│   └── 2026_해외취업연수사업.pdf   # 검색 대상 문서
├── loader/
│   ├── extract.py                  # PDF 텍스트 추출 및 섹션 파싱
│   ├── chunker.py                  # Parent-Child 청킹
│   ├── embed_load.py               # 임베딩 생성 및 DB 적재
│   ├── requirements.txt
│   └── Dockerfile
├── mcp_server/
│   ├── server.py                   # MCP 서버 (search_docs 툴 제공)
│   ├── requirements.txt
│   └── Dockerfile
├── docker-compose.yml
└── .env
```

---

## RAG 방식: Parent-Child Retrieval

일반 RAG는 청크 크기 딜레마가 있다. 작으면 검색 정확도는 높지만 맥락이 부족하고, 크면 맥락은 풍부하지만 검색 정확도가 떨어진다.

이 프로젝트는 **두 단계 청크**로 이를 해결한다.

```
Parent (큰 단위): 로마숫자 섹션 전체  예) "Ⅱ 지원 자격 및 조건" 전체 텍스트
Child  (작은 단위): 가/나/다, ○ 항목  예) "가. 연령 만 34세 이하"
```

**검색 흐름:**
1. 질문을 벡터로 변환
2. Child 테이블에서 코사인 유사도로 상위 3개 검색 (정확한 위치 탐색)
3. 해당 Child의 Parent 전체 텍스트 반환 (풍부한 맥락 제공)
4. Claude가 Parent 내용을 바탕으로 자연어 답변 생성

---

## 환경 변수 (.env)

```env
# PostgreSQL
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_DB=rag_db
POSTGRES_USER=rag_user
POSTGRES_PASSWORD=rag_password

# 임베딩 모델 (한국어 특화)
EMBEDDING_MODEL=jhgan/ko-sroberta-multitask
EMBEDDING_DIM=768

# MCP 서버
MCP_HOST=0.0.0.0
MCP_PORT=8001
```

---

## 실행 방법

### 1. Docker 스택 실행

```bash
docker compose up -d postgres mcp-server
```

### 2. 데이터 적재 (최초 1회)

```bash
docker compose --profile load run --rm loader
```

PDF를 파싱하고 임베딩 벡터를 생성해 DB에 저장한다. 완료 후 컨테이너는 자동 종료된다.

### 3. Claude Desktop 연결

`claude_desktop_config.json`에 아래 항목 추가:

```json
"rag-mcp-test": {
  "command": "python",
  "args": ["C:/path/to/rag_mcp_test/mcp_server/server.py"],
  "env": {
    "POSTGRES_HOST": "localhost",
    "POSTGRES_PORT": "5435",
    "POSTGRES_DB": "rag_db",
    "POSTGRES_USER": "rag_user",
    "POSTGRES_PASSWORD": "rag_password",
    "EMBEDDING_MODEL": "jhgan/ko-sroberta-multitask",
    "EMBEDDING_DIM": "768"
  }
}
```

Claude Desktop을 재시작하면 커넥터 목록에 `rag-mcp-test`가 나타난다.

---

## 데이터베이스 스키마

### parent_chunks
| 컬럼 | 타입 | 설명 |
|---|---|---|
| id | SERIAL | PK |
| section | TEXT | 섹션 제목 (예: "Ⅱ 지원 자격") |
| content | TEXT | 섹션 전체 텍스트 |
| page_num | INT | PDF 페이지 번호 |

### child_chunks
| 컬럼 | 타입 | 설명 |
|---|---|---|
| id | SERIAL | PK |
| parent_id | INT | FK → parent_chunks.id |
| parent_section | TEXT | 부모 섹션 제목 |
| content | TEXT | 세부 항목 텍스트 |
| embedding | VECTOR(768) | 임베딩 벡터 |
| page_num | INT | PDF 페이지 번호 |

---

## MCP 툴

### `search_docs(query: str) -> str`

해외취업연수사업 공고 문서에서 질문과 관련된 내용을 검색한다.

**동작:**
1. query를 `jhgan/ko-sroberta-multitask` 모델로 벡터화
2. child_chunks에서 코사인 유사도 상위 3개 검색
3. 각 Child의 Parent 전체 텍스트 조회
4. 관련도 / 페이지 / 섹션 정보와 함께 반환

**사용 예:**
```
search_docs 툴로 해외취업연수사업 지원 자격을 찾아줘
```

---

## 기술 스택

| 구분 | 기술 |
|---|---|
| 벡터 DB | PostgreSQL + pgvector |
| 임베딩 모델 | jhgan/ko-sroberta-multitask (한국어 특화, 768차원) |
| PDF 파싱 | pdfplumber |
| MCP 서버 | FastMCP (mcp 패키지) |
| 컨테이너 | Docker Compose |
| LLM 클라이언트 | Claude Desktop |
