from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, Session, mapped_column
from starlette.responses import Response as StarletteResponse

from .db import Base, engine, get_db
from .utils import current_year_month, normalize_plate


class VehicleMonthComment(Base):
    __tablename__ = "vehicle_month_comments"
    __table_args__ = (
        UniqueConstraint("plate", "year_month", name="ux_vehicle_month_comments_plate_month"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plate: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    year_month: Mapped[str] = mapped_column(String(7), nullable=False, index=True)
    comment: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class CommentUpsert(BaseModel):
    comment: str = Field(default="", max_length=4000)
    year_month: str | None = None


def _validate_year_month(value: str | None) -> str:
    ym = (value or current_year_month()).strip()
    if len(ym) != 7 or ym[4] != "-" or not ym[:4].isdigit() or not ym[5:].isdigit():
        raise HTTPException(status_code=422, detail="year_month должен иметь формат YYYY-MM")
    month = int(ym[5:])
    if month < 1 or month > 12:
        raise HTTPException(status_code=422, detail="Некорректный месяц")
    return ym


def _serialize(row: VehicleMonthComment) -> dict[str, Any]:
    return {
        "plate": row.plate,
        "year_month": row.year_month,
        "comment": row.comment,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


COMMENTS_JS = r"""
(function () {
  'use strict';

  const state = { comments: {}, yearMonth: null, activePlate: null, observer: null };

  function escapeHtml(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function resolveYearMonth() {
    const params = new URLSearchParams(window.location.search);
    const value = params.get('year_month');
    if (/^\d{4}-\d{2}$/.test(value || '')) return value;
    const now = new Date();
    return now.getFullYear() + '-' + String(now.getMonth() + 1).padStart(2, '0');
  }

  function formatDate(value) {
    if (!value) return '';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '';
    return date.toLocaleString('ru-RU', {
      day: '2-digit', month: '2-digit', year: 'numeric',
      hour: '2-digit', minute: '2-digit'
    });
  }

  function ensureStyles() {
    if (document.getElementById('month-comments-style')) return;
    const style = document.createElement('style');
    style.id = 'month-comments-style';
    style.textContent = `
      .comment-cell{min-width:230px;max-width:340px}
      .comment-button{width:100%;text-align:left;padding:8px 10px;border-radius:12px;background:#f8fafc;border:1px solid #cbd5e1;cursor:pointer}
      .comment-button:hover{background:#eff6ff;border-color:#93c5fd}
      .comment-text{display:block;font-size:13px;color:#0f172a;white-space:normal;overflow-wrap:anywhere}
      .comment-placeholder{color:#64748b}
      .comment-date{display:block;margin-top:4px;font-size:11px;color:#64748b}
      .comment-modal-backdrop{position:fixed;inset:0;background:rgba(15,23,42,.48);display:none;align-items:center;justify-content:center;padding:24px;z-index:1200}
      .comment-modal-backdrop.show{display:flex}
      .comment-modal-card{width:min(620px,100%);background:#fff;border:1px solid #e2e8f0;border-radius:24px;padding:20px;box-shadow:0 20px 60px rgba(15,23,42,.22)}
      .comment-modal-head{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}
      .comment-modal-title{font-size:22px;font-weight:700}
      .comment-modal-subtitle{margin-top:4px;color:#64748b;font-size:13px}
      .comment-modal-card textarea{width:100%;min-height:150px;margin-top:16px;padding:12px;border:1px solid #cbd5e1;border-radius:16px;font:inherit;resize:vertical}
      .comment-modal-actions{display:flex;justify-content:flex-end;gap:10px;margin-top:14px}
      .comment-save{background:#111827;color:#fff;border-color:#111827}
    `;
    document.head.appendChild(style);
  }

  function ensureModal() {
    if (document.getElementById('month-comment-modal')) return;
    const modal = document.createElement('div');
    modal.id = 'month-comment-modal';
    modal.className = 'comment-modal-backdrop';
    modal.innerHTML = `
      <div class="comment-modal-card" role="dialog" aria-modal="true" aria-labelledby="month-comment-title">
        <div class="comment-modal-head">
          <div>
            <div id="month-comment-title" class="comment-modal-title">Комментарий</div>
            <div id="month-comment-subtitle" class="comment-modal-subtitle"></div>
          </div>
          <button id="month-comment-close" type="button">Закрыть</button>
        </div>
        <textarea id="month-comment-text" maxlength="4000" placeholder="Например: Добавлен лимит по согласованию"></textarea>
        <div class="comment-modal-actions">
          <button id="month-comment-clear" type="button">Очистить</button>
          <button id="month-comment-save" class="comment-save" type="button">Сохранить</button>
        </div>
      </div>`;
    document.body.appendChild(modal);

    const close = () => modal.classList.remove('show');
    document.getElementById('month-comment-close').addEventListener('click', close);
    modal.addEventListener('click', (event) => { if (event.target === modal) close(); });
    document.getElementById('month-comment-clear').addEventListener('click', () => {
      document.getElementById('month-comment-text').value = '';
    });
    document.getElementById('month-comment-save').addEventListener('click', saveActiveComment);
  }

  function openEditor(plate) {
    state.activePlate = plate;
    const item = state.comments[plate] || null;
    document.getElementById('month-comment-title').textContent = 'Комментарий · ' + plate;
    document.getElementById('month-comment-subtitle').textContent = 'Месяц: ' + state.yearMonth + '. Дата сохраняется автоматически.';
    document.getElementById('month-comment-text').value = item ? item.comment : '';
    document.getElementById('month-comment-modal').classList.add('show');
    setTimeout(() => document.getElementById('month-comment-text').focus(), 0);
  }

  async function saveActiveComment() {
    if (!state.activePlate) return;
    const button = document.getElementById('month-comment-save');
    const text = document.getElementById('month-comment-text').value.trim();
    button.disabled = true;
    button.textContent = 'Сохранение...';
    try {
      const response = await fetch('/vehicle-comments/' + encodeURIComponent(state.activePlate), {
        method: 'PUT',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({comment: text, year_month: state.yearMonth})
      });
      if (!response.ok) throw new Error(await response.text());
      const payload = await response.json();
      if (payload.deleted) delete state.comments[state.activePlate];
      else state.comments[state.activePlate] = payload;
      document.getElementById('month-comment-modal').classList.remove('show');
      applyCommentsToTable();
    } catch (error) {
      console.error(error);
      alert('Не удалось сохранить комментарий');
    } finally {
      button.disabled = false;
      button.textContent = 'Сохранить';
    }
  }

  function renderCell(cell, plate) {
    const item = state.comments[plate];
    const text = item && item.comment ? item.comment : '';
    const date = item ? formatDate(item.updated_at || item.created_at) : '';
    cell.innerHTML = `
      <button type="button" class="comment-button">
        <span class="comment-text ${text ? '' : 'comment-placeholder'}">${text ? escapeHtml(text) : 'Добавить комментарий'}</span>
        ${date ? '<span class="comment-date">' + escapeHtml(date) + '</span>' : ''}
      </button>`;
    cell.querySelector('button').addEventListener('click', (event) => {
      event.preventDefault();
      event.stopPropagation();
      openEditor(plate);
    });
  }

  function applyCommentsToTable() {
    const tbody = document.getElementById('vehicle-table');
    if (!tbody) return;
    const table = tbody.closest('table');
    const headRow = table && table.querySelector('thead tr');
    if (headRow && !headRow.querySelector('[data-month-comment-head]')) {
      const th = document.createElement('th');
      th.textContent = 'Комментарий';
      th.dataset.monthCommentHead = '1';
      headRow.appendChild(th);
    }

    tbody.querySelectorAll('tr').forEach((row) => {
      const firstCell = row.querySelector('td');
      if (!firstCell) return;
      const plate = String(firstCell.textContent || '').replace(/\s+/g, '').toUpperCase();
      if (!plate) return;
      let cell = row.querySelector('td[data-month-comment-cell]');
      if (!cell) {
        cell = document.createElement('td');
        cell.className = 'comment-cell';
        cell.dataset.monthCommentCell = '1';
        row.appendChild(cell);
      }
      renderCell(cell, plate);
    });
  }

  async function loadComments() {
    state.yearMonth = resolveYearMonth();
    const response = await fetch('/vehicle-comments?year_month=' + encodeURIComponent(state.yearMonth), {cache: 'no-store'});
    if (!response.ok) throw new Error('comments load failed');
    const payload = await response.json();
    state.comments = {};
    (payload.items || []).forEach((item) => { state.comments[item.plate] = item; });
    applyCommentsToTable();
  }

  function boot() {
    if (!document.getElementById('vehicle-table')) return;
    ensureStyles();
    ensureModal();
    const tbody = document.getElementById('vehicle-table');
    state.observer = new MutationObserver(() => applyCommentsToTable());
    state.observer.observe(tbody, {childList: true});
    loadComments().catch((error) => console.error(error));
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
"""


def install_month_comments(app: FastAPI) -> None:
    """Create storage, register API routes and inject the dashboard UI."""

    VehicleMonthComment.__table__.create(bind=engine, checkfirst=True)

    @app.get("/vehicle-comments")
    def list_vehicle_comments(year_month: str | None = None, db: Session = Depends(get_db)):
        ym = _validate_year_month(year_month)
        rows = (
            db.query(VehicleMonthComment)
            .filter(VehicleMonthComment.year_month == ym)
            .order_by(VehicleMonthComment.plate.asc())
            .all()
        )
        return {"year_month": ym, "items": [_serialize(row) for row in rows]}

    @app.put("/vehicle-comments/{plate}")
    def save_vehicle_comment(plate: str, payload: CommentUpsert, db: Session = Depends(get_db)):
        plate_norm = normalize_plate(plate)
        if not plate_norm:
            raise HTTPException(status_code=422, detail="Не указан госномер")
        ym = _validate_year_month(payload.year_month)
        text = payload.comment.strip()
        row = (
            db.query(VehicleMonthComment)
            .filter(
                VehicleMonthComment.plate == plate_norm,
                VehicleMonthComment.year_month == ym,
            )
            .one_or_none()
        )

        if not text:
            if row is not None:
                db.delete(row)
                db.commit()
            return {"plate": plate_norm, "year_month": ym, "deleted": True}

        if row is None:
            row = VehicleMonthComment(plate=plate_norm, year_month=ym, comment=text)
            db.add(row)
        else:
            row.comment = text
            row.updated_at = func.now()

        db.commit()
        db.refresh(row)
        return _serialize(row)

    @app.get("/month-comments.js")
    def month_comments_script():
        return Response(content=COMMENTS_JS, media_type="application/javascript")

    @app.middleware("http")
    async def inject_month_comments_ui(request, call_next):
        response = await call_next(request)
        content_type = response.headers.get("content-type", "")
        if "text/html" not in content_type:
            return response

        body = b""
        async for chunk in response.body_iterator:
            body += chunk
        text = body.decode("utf-8", errors="replace")
        if "id='vehicle-table'" not in text and 'id="vehicle-table"' not in text:
            return StarletteResponse(
                content=body,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type=response.media_type,
                background=response.background,
            )

        marker = '<script src="/month-comments.js"></script>'
        if marker not in text:
            text = text.replace("</body>", marker + "\n</body>")
        headers = dict(response.headers)
        headers.pop("content-length", None)
        return StarletteResponse(
            content=text,
            status_code=response.status_code,
            headers=headers,
            media_type="text/html",
            background=response.background,
        )
