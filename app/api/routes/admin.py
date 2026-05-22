"""
app/api/routes/admin.py
Endpoints de administração — protegidos por INTERNAL_API_KEY.

GET  /admin/screenshots         — lista screenshots de falhas
GET  /admin/screenshots/{id}    — baixa o arquivo de um invoice específico
GET  /admin/invoices            — últimas N notas (todas as situações)
"""
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select

from app.api.dependencies import DbSession, require_internal_auth
from app.models.invoice import Invoice

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_internal_auth)],
)


@router.get("/screenshots")
async def list_screenshots(
    session: DbSession,
) -> list[dict]:
    """Lista invoices com screenshot de erro."""
    result = await session.execute(
        select(Invoice)
        .where(Invoice.screenshot_path.isnot(None))
        .order_by(Invoice.created_at.desc())
        .limit(20)
    )
    invoices = result.scalars().all()
    return [
        {
            "invoice_id": inv.id,
            "user_id": inv.user_id,
            "status": inv.status,
            "failed_stage": inv.failed_stage,
            "error_message": inv.error_message,
            "screenshot_url": f"/admin/screenshots/{inv.id}",
            "created_at": inv.created_at.isoformat() if inv.created_at else None,
        }
        for inv in invoices
    ]


@router.get("/screenshots/{invoice_id}")
async def get_screenshot(
    invoice_id: int,
    session: DbSession,
) -> FileResponse:
    """Baixa o screenshot de um invoice com falha."""
    result = await session.execute(
        select(Invoice).where(Invoice.id == invoice_id)
    )
    invoice = result.scalar_one_or_none()

    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice não encontrado.")
    if not invoice.screenshot_path:
        raise HTTPException(status_code=404, detail="Este invoice não tem screenshot.")

    path = Path(invoice.screenshot_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Arquivo não encontrado no disco: {path}")

    return FileResponse(path, media_type="image/png", filename=path.name)


@router.get("/invoices")
async def list_invoices(
    session: DbSession,
    limit: int = 20,
) -> list[dict]:
    """Últimas N notas fiscais de todos os usuários."""
    result = await session.execute(
        select(Invoice).order_by(Invoice.created_at.desc()).limit(limit)
    )
    invoices = result.scalars().all()
    return [
        {
            "id": inv.id,
            "user_id": inv.user_id,
            "invoice_number": inv.invoice_number,
            "value": float(inv.value),
            "period": inv.period,
            "status": inv.status,
            "failed_stage": inv.failed_stage,
            "error_message": inv.error_message,
            "has_screenshot": inv.screenshot_path is not None,
            "screenshot_url": f"/admin/screenshots/{inv.id}" if inv.screenshot_path else None,
            "created_at": inv.created_at.isoformat() if inv.created_at else None,
        }
        for inv in invoices
    ]
