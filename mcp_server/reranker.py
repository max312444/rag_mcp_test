import os
import sys
from sentence_transformers import CrossEncoder

_reranker = None


def get_reranker():
    global _reranker
    if _reranker is None:
        model_name = os.getenv("RERANKER_MODEL", "Dongjin-kr/ko-reranker")
        print(f"Reranker 모델 로드 중: {model_name}", file=sys.stderr)
        _reranker = CrossEncoder(model_name)
    return _reranker


def rerank(query: str, passages: list[dict], top_k: int = 5) -> list[dict]:
    """
    Cross-encoder로 passage를 재순위화.
    passages: [{"content": str, ...}, ...]
    """
    if not passages:
        return []

    model = get_reranker()
    pairs = [(query, p["content"]) for p in passages]
    scores = model.predict(pairs)

    for i, p in enumerate(passages):
        p["rerank_score"] = float(scores[i])

    ranked = sorted(passages, key=lambda x: x["rerank_score"], reverse=True)
    return ranked[:top_k]
