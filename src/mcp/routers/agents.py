"""Agent endpoints — thin wrappers over agent modules."""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from deps import get_chroma, get_neo4j, get_redis
from routers.ingestion import ingest_content

router = APIRouter()
logger = logging.getLogger("ai-companion")


class AgentQueryRequest(BaseModel):
    query: str
    domains: Optional[List[str]] = None
    top_k: int = 10
    use_reranking: bool = True


class TriageFileRequest(BaseModel):
    file_path: str
    domain: str = ""
    categorize_mode: str = ""
    tags: str = ""


class TriageBatchRequest(BaseModel):
    files: List[Dict[str, str]]
    default_mode: str = ""


class RectifyRequest(BaseModel):
    checks: Optional[List[str]] = None
    auto_fix: bool = False
    stale_days: int = 90


class AuditRequest(BaseModel):
    reports: Optional[List[str]] = None
    hours: int = 24


class MaintenanceRequest(BaseModel):
    actions: Optional[List[str]] = None
    stale_days: int = 90
    auto_purge: bool = False


@router.post("/agent/query")
async def agent_query_endpoint(req: AgentQueryRequest):
    try:
        from agents.query_agent import agent_query
        return await agent_query(
            query=req.query,
            domains=req.domains,
            top_k=req.top_k,
            use_reranking=req.use_reranking,
            chroma_client=get_chroma(),
            redis_client=get_redis(),
            neo4j_driver=get_neo4j(),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Agent query error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/agent/triage")
async def triage_file_endpoint(req: TriageFileRequest):
    try:
        from agents.triage import triage_file
        triage_result = await triage_file(
            file_path=req.file_path,
            domain=req.domain,
            categorize_mode=req.categorize_mode,
            tags=req.tags,
        )
        if triage_result.get("status") == "error":
            raise HTTPException(status_code=400, detail=triage_result.get("error", "Triage failed"))
        result = ingest_content(
            triage_result["parsed_text"],
            triage_result["domain"],
            metadata=triage_result["metadata"],
        )
        result["filename"] = triage_result["filename"]
        result["categorize_mode"] = triage_result.get("categorize_mode", "")
        result["triage_status"] = triage_result["status"]
        result["is_structured"] = triage_result.get("is_structured", False)
        return result
    except HTTPException:
        raise
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Triage error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/agent/triage/batch")
async def triage_batch_endpoint(req: TriageBatchRequest):
    try:
        from agents.triage import triage_batch
        triage_results = await triage_batch(
            files=req.files,
            default_mode=req.default_mode,
        )
        final_results = []
        for triage_result in triage_results:
            if triage_result.get("status") == "error":
                final_results.append({
                    "filename": triage_result.get("filename", ""),
                    "status": "error",
                    "error": triage_result.get("error", ""),
                })
                continue
            try:
                result = ingest_content(
                    triage_result["parsed_text"],
                    triage_result["domain"],
                    metadata=triage_result["metadata"],
                )
                result["filename"] = triage_result["filename"]
                result["triage_status"] = triage_result["status"]
                final_results.append(result)
            except Exception as e:
                final_results.append({
                    "filename": triage_result.get("filename", ""),
                    "status": "error",
                    "error": str(e),
                })
        succeeded = sum(1 for r in final_results if r.get("status") == "success")
        failed = sum(1 for r in final_results if r.get("status") == "error")
        duplicates = sum(1 for r in final_results if r.get("status") == "duplicate")
        return {
            "total": len(final_results),
            "succeeded": succeeded,
            "failed": failed,
            "duplicates": duplicates,
            "results": final_results,
        }
    except Exception as e:
        logger.error(f"Batch triage error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/agent/rectify")
async def rectify_endpoint(req: RectifyRequest):
    try:
        from agents.rectify import rectify
        return await rectify(
            neo4j_driver=get_neo4j(),
            chroma_client=get_chroma(),
            redis_client=get_redis(),
            checks=req.checks,
            auto_fix=req.auto_fix,
            stale_days=req.stale_days,
        )
    except Exception as e:
        logger.error(f"Rectify error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/agent/audit")
async def audit_endpoint(req: AuditRequest):
    try:
        from agents.audit import audit
        return await audit(
            redis_client=get_redis(),
            reports=req.reports,
            hours=req.hours,
        )
    except Exception as e:
        logger.error(f"Audit error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/agent/maintain")
async def maintain_endpoint(req: MaintenanceRequest):
    try:
        from agents.maintenance import maintain
        return await maintain(
            neo4j_driver=get_neo4j(),
            chroma_client=get_chroma(),
            redis_client=get_redis(),
            actions=req.actions,
            stale_days=req.stale_days,
            auto_purge=req.auto_purge,
        )
    except Exception as e:
        logger.error(f"Maintenance error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
