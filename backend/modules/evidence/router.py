"""可信存证路由。契约 2.6。

路由声明顺序有讲究：/evidence/verify、/evidence/chain/status、/evidence/trace/{id}
必须排在 /evidence/{evidence_id} 前面，否则会被后者吃掉。
"""
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from core.deps import current_user, get_db, pagination, require_roles
from core.middleware import Principal, audited, require_permission
from core.response import PageQuery, ok, page
from modules.evidence import service
from modules.evidence.chain import get_chain
from modules.permission.service import has_own_scope_only

router = APIRouter(tags=["可信存证"])


class EvidenceWriteRequest(BaseModel):
    category: str = Field(pattern="^(data|identity|permission|audit|algo)$")
    refId: str = Field(max_length=64)
    payload: dict
    actorDid: str | None = Field(default=None, max_length=128)


class EvidenceVerifyRequest(BaseModel):
    evidenceId: str = Field(max_length=64)
    payload: dict | None = Field(default=None,
                                 description="省略时用库内快照重算，这是篡改演示的关键")


class TamperRequest(BaseModel):
    evidenceId: str = Field(max_length=64)
    newValue: dict = Field(description="要覆盖进快照的字段，比如 {\"pvOutput\": 999.9}")


@router.post("/evidence", summary="写入存证")
@audited(module="evidence", action="evidence:write", risk="medium", resource_type="evidence")
@require_permission("evidence", "write")
def write(
    body: EvidenceWriteRequest,
    db: Session = Depends(get_db),
    principal: Principal = Depends(current_user),
):
    result = service.write_evidence(
        db, category=body.category, ref_id=body.refId, payload=body.payload,
        actor_did=body.actorDid or principal.did,
    )
    db.commit()
    return ok(result)


@router.get("/evidence", summary="存证检索")
@require_permission("evidence", "read")
def search(
    pg: PageQuery = Depends(pagination),
    category: str | None = Query(None, pattern="^(data|identity|permission|audit|algo)$"),
    did: str | None = Query(None),
    dataType: str | None = Query(None, pattern="^(pv|wind|storage|load|dispatch)$"),
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    db: Session = Depends(get_db),
    principal: Principal = Depends(current_user),
):
    # scope='own' 的角色（能源主体）只能看到 actorDid 是自己的存证
    owner_only = principal.did if has_own_scope_only(principal, "evidence", "read") else None
    items, total = service.search(db, pg.page, pg.size, category=category, did=did,
                                  data_type=dataType, from_=from_, to=to,
                                  owner_only=owner_only)
    return ok(page(items, total, pg.page, pg.size))


@router.post("/evidence/verify", summary="完整性校验")
@audited(module="evidence", action="evidence:verify", risk="low")
@require_permission("evidence", "read")
def verify(
    body: EvidenceVerifyRequest,
    db: Session = Depends(get_db),
    _p: Principal = Depends(current_user),
):
    return ok(get_chain().verify(db, body.evidenceId, body.payload))


@router.get("/evidence/chain/status", summary="链状态")
@require_permission("evidence", "read")
def chain_status(db: Session = Depends(get_db), _p: Principal = Depends(current_user)):
    return ok(get_chain().status(db))


@router.get("/evidence/trace/{trace_id}", summary="按 traceId 查完整业务链路")
@require_permission("evidence", "read")
def trace(trace_id: str, db: Session = Depends(get_db),
          _p: Principal = Depends(current_user)):
    steps = get_chain().trace(db, trace_id)
    return ok({"traceId": trace_id, "total": len(steps), "steps": steps})


@router.post("/evidence/demo/tamper", summary="【演示专用】篡改一条存证")
@audited(module="evidence", action="evidence:tamper", risk="critical",
         resource_type="evidence")
def tamper(
    body: TamperRequest,
    db: Session = Depends(get_db),
    _admin: Principal = Depends(require_roles("sys_admin")),
):
    """仅 sys_admin 可调用，用于答辩现场演示防篡改能力。

    它绕过应用层直接改数据库快照，模拟「数据库被人动了手脚」这一场景。
    """
    return ok(service.tamper(db, body.evidenceId, body.newValue))


@router.post("/evidence/demo/restore", summary="【演示专用】还原被篡改的存证")
@audited(module="evidence", action="evidence:restore", risk="high",
         resource_type="evidence")
def restore(
    body: EvidenceVerifyRequest,
    db: Session = Depends(get_db),
    _admin: Principal = Depends(require_roles("sys_admin")),
):
    """契约之外的补充接口，为了能反复彩排篡改演示。乙的前端无需调用。"""
    return ok(service.restore(db, body.evidenceId))


@router.post("/evidence/demo/restore-all", summary="【演示专用】一键还原全部被篡改的存证")
@audited(module="evidence", action="evidence:restore-all", risk="high",
         resource_type="evidence")
def restore_all(
    db: Session = Depends(get_db),
    _admin: Principal = Depends(require_roles("sys_admin")),
):
    """契约外补充接口（路径不与契约 2.6 冲突）。

    彩排收尾用：把 chain_evidence_backup 里所有原始快照一次性写回，链恢复 intact。
    """
    return ok(service.restore_all(db))


@router.get("/evidence/{evidence_id}/certificate", summary="导出存证凭证")
@audited(module="evidence", action="evidence:certificate", risk="low",
         resource_type="evidence", resource_id_arg="evidence_id")
@require_permission("evidence", "read", resource_id_arg="evidence_id")
def certificate(evidence_id: str, db: Session = Depends(get_db),
                _p: Principal = Depends(current_user)):
    return ok(service.certificate(db, evidence_id))


@router.get("/evidence/{evidence_id}", summary="存证详情")
@audited(module="evidence", action="evidence:read", risk="low",
         resource_type="evidence", resource_id_arg="evidence_id")
# resource_id_arg 一定要传：scope='own' 的角色要靠它解析出这条存证的 actorDid
# 再判属主，否则能源主体能读到别人的存证（B-002 / 用例 API-EV-17b）
@require_permission("evidence", "read", resource_id_arg="evidence_id")
def get_evidence(evidence_id: str, db: Session = Depends(get_db),
                 _p: Principal = Depends(current_user)):
    return ok(service.get_evidence(db, evidence_id))
