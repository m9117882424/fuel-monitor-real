from __future__ import annotations

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore', env_ignore_empty=True)

    app_name: str = 'Fuel Monitor Real'
    app_env: str = 'dev'
    database_url: str = 'sqlite:///./fuel_monitor.db'
    timezone: str = 'Europe/Istanbul'

    turpak_base_url: str = 'https://mersintransfer.turpakmonitor.com'
    turpak_company_name: str = ''
    turpak_password: str = ''
    turpak_group_name: str = '#TSM BINEK ARAC'
    turpak_enabled: bool = True

    shell_enabled: bool = True
    shell_input_path: str = ''
    shell_input_dir: str = ''
    shell_glob: str = '*.xlsx'

    petrol_enabled: bool = True
    petrol_base_url: str = 'https://automaticservices.petrolofisi.com.tr/AUTOMATIC_REST_SERVICES'
    petrol_user_id: int | None = None
    petrol_client_role_id: int | None = None
    petrol_user_name: str = ''
    petrol_user_password: str = ''
    petrol_fleet_list: str = ''
    petrol_fleet_id: str = ''
    petrol_holding_id: int | None = None
    petrol_viu_id: str = ''
    petrol_invoice_type: str = ''
    petrol_invoice_period: str = ''
    petrol_use_api: bool = True
    petrol_input_path: str = ''
    petrol_input_dir: str = ''
    petrol_glob: str = '*'

    report_output_dir: str = './reports'
    default_monthly_limit_liters: float = 300.0
    sync_days_back: int = 35
    regular_sync_days_back: int = 2

    alert_thresholds: str = '80,90,100'
    telegram_bot_token: str = ''
    telegram_chat_id: str = ''
    telegram_enabled: bool = False

    scheduler_enabled: bool = False
    scheduler_morning_hour: int = 7
    scheduler_evening_hour: int = 18

    driver_enabled: bool = True
    driver_input_dir: str = ''
    driver_glob: str = '*.xlsx'
    driver_sheet_name: str = 'Список легкового автотранспорта'

    api_token: str = ''
    limits_admin_password: str = ''

    @property
    def report_output_path(self) -> Path:
        path = Path(self.report_output_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def alert_threshold_values(self) -> tuple[int, ...]:
        values: list[int] = []
        for item in self.alert_thresholds.split(','):
            item = item.strip()
            if not item:
                continue
            values.append(int(item))
        return tuple(sorted(set(values)))


settings = Settings()
