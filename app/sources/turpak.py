from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests


@dataclass
class TurpakClient:
    base_url: str
    company_name: str
    password: str
    timeout: int = 60

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip('/')
        self._token: str | None = None

    def login(self) -> str:
        response = requests.post(
            f'{self.base_url}/api/Login/Login',
            headers={'accept': 'application/json', 'Content-Type': 'application/json'},
            json={'companyName': self.company_name, 'password': self.password},
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()
        if data.get('resultCode') != 0:
            raise RuntimeError(f'Turpak login failed: {data}')
        token = data.get('token')
        if not token:
            raise RuntimeError(f'Turpak token missing: {data}')
        self._token = token
        return token

    def _headers(self) -> dict[str, str]:
        if not self._token:
            self.login()
        assert self._token
        return {
            'accept': 'application/json',
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {self._token}',
        }

    def get_sales(self, start_dt: str, end_dt: str, license_plate: str | None = None, group_name: str | None = None) -> list[dict[str, Any]]:
        response = requests.post(
            f'{self.base_url}/api/Main/GetSales',
            headers=self._headers(),
            json={
                'saleBegin': start_dt,
                'saleEnd': end_dt,
                'licensePlateNr': license_plate,
                'groupName': group_name,
            },
            timeout=self.timeout,
        )
        if response.status_code == 401:
            self.login()
            response = requests.post(
                f'{self.base_url}/api/Main/GetSales',
                headers=self._headers(),
                json={
                    'saleBegin': start_dt,
                    'saleEnd': end_dt,
                    'licensePlateNr': license_plate,
                    'groupName': group_name,
                },
                timeout=self.timeout,
            )
        response.raise_for_status()
        data = response.json()
        return data.get('salesList', []) or []
