from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import Response

from .config import settings
from .services.driver_registry_service import clear_driver_registry_cache, load_driver_registry
from .utils import now_local


ALLOWED_EXTENSIONS = {".xlsx", ".xls"}
MAX_UPLOAD_SIZE = 25 * 1024 * 1024

_BUTTON_HTML = """
<input id='roster-upload-input' type='file' accept='.xlsx,.xls' style='display:none'/>
<button id='roster-upload-btn' type='button'>Загрузить расстановку</button>
""".strip()

_SCRIPT_HTML = r"""
<script>
(function () {
  function installRosterUpload() {
    const button = document.getElementById('roster-upload-btn');
    const input = document.getElementById('roster-upload-input');
    if (!button || !input || button.dataset.bound === '1') return;
    button.dataset.bound = '1';

    button.addEventListener('click', function () {
      input.value = '';
      input.click();
    });

    input.addEventListener('change', async function () {
      const file = input.files && input.files[0];
      if (!file) return;

      const oldText = button.textContent;
      button.disabled = true;
      button.textContent = 'Загрузка расстановки...';

      try {
        const formData = new FormData();
        formData.append('file', file);
        const response = await fetch('/driver-roster/upload', {
          method: 'POST',
          body: formData,
        });
        const payload = await response.json().catch(function () { return {}; });
        if (!response.ok || !payload.ok) {
          throw new Error(payload.detail || 'Не удалось загрузить расстановку');
        }

        alert('Расстановка загружена: ' + (payload.rows || 0) + ' строк');
        window.location.reload();
      } catch (error) {
        alert(error && error.message ? error.message : 'Ошибка загрузки расстановки');
      } finally {
        button.disabled = false;
        button.textContent = oldText;
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', installRosterUpload);
  } else {
    installRosterUpload();
  }
})();
</script>
""".strip()


SHELL_UPLOAD_INPUT_HTML = "<input id='shell-upload-input' type='file' accept='.xlsx,.xls,.csv' style='display:none'/>"
SHELL_UPLOAD_BUTTON_HTML = "<button id='shell-upload-btn' type='button'>Загрузить файл Shell</button>"
HIDDEN_SHELL_UPLOAD_BUTTON_HTML = (
    "<button id='shell-upload-btn' type='button' style='display:none' aria-hidden='true' tabindex='-1'>"
    "Загрузить файл Shell</button>"
)


def _driver_input_dir() -> Path:
    configured = str(getattr(settings, "driver_input_dir", "") or "").strip()
    if not configured:
        configured = "/root/fuel_monitor_real/data/drivers"
    path = Path(configured).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_filename(original_name: str, suffix: str) -> str:
    stamp = now_local().strftime("%d.%m.%Y_%H-%M-%S")
    stem = Path(original_name).stem.strip() or "rasstanovka"
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_", " ", "."} else "_" for ch in stem)
    return f"{stamp} {cleaned}{suffix}"


def install_roster_upload(app: FastAPI) -> None:
    @app.post("/driver-roster/upload")
    async def upload_driver_roster(file: UploadFile = File(...)):
        original_name = file.filename or "rasstanovka.xlsx"
        suffix = Path(original_name).suffix.lower()
        if suffix not in ALLOWED_EXTENSIONS:
            raise HTTPException(status_code=400, detail="Разрешены только файлы Excel .xlsx и .xls")

        file.file.seek(0, 2)
        size = file.file.tell()
        file.file.seek(0)
        if size <= 0:
            raise HTTPException(status_code=400, detail="Загружен пустой файл")
        if size > MAX_UPLOAD_SIZE:
            raise HTTPException(status_code=413, detail="Файл превышает допустимый размер 25 МБ")

        target_dir = _driver_input_dir()
        target = target_dir / _safe_filename(original_name, suffix)
        with target.open("wb") as output:
            shutil.copyfileobj(file.file, output)

        clear_driver_registry_cache()
        registry = load_driver_registry()
        rows = int(len(registry.index)) if registry is not None else 0
        if rows == 0:
            try:
                target.unlink(missing_ok=True)
            finally:
                clear_driver_registry_cache()
            raise HTTPException(
                status_code=400,
                detail=(
                    "Не удалось прочитать расстановку. Проверьте листы «Список легкового автотранспорта» "
                    "или «Подменные Yedekler» и наличие столбца с госномером."
                ),
            )

        return {
            "ok": True,
            "filename": target.name,
            "rows": rows,
            "detail": "Расстановка сохранена и перечитана",
        }

    @app.middleware("http")
    async def inject_roster_upload_button(request: Request, call_next):
        response = await call_next(request)
        content_type = response.headers.get("content-type", "")
        if "text/html" not in content_type.lower():
            return response

        body = b""
        async for chunk in response.body_iterator:
            body += chunk

        html = body.decode("utf-8", errors="replace")
        if "id='roster-upload-btn'" not in html and "id='shell-upload-input'" in html:
            html = html.replace(
                "<input id='shell-upload-input'",
                _BUTTON_HTML + "\n<input id='shell-upload-input'",
                1,
            )

        # Keep the original hidden Shell controls in the DOM because the dashboard
        # JavaScript binds to them before it installs search and filter handlers.
        # Only hide the visible button instead of deleting the elements entirely.
        html = html.replace(SHELL_UPLOAD_BUTTON_HTML, HIDDEN_SHELL_UPLOAD_BUTTON_HTML, 1)

        if "id='roster-upload-btn'" in html and "installRosterUpload" not in html:
            html = html.replace("</body>", _SCRIPT_HTML + "\n</body>", 1)

        headers = dict(response.headers)
        headers.pop("content-length", None)
        return Response(
            content=html,
            status_code=response.status_code,
            headers=headers,
            media_type="text/html",
            background=response.background,
        )
