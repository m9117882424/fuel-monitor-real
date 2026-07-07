from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

import requests


@dataclass
class PetrolAutomaticClient:
    base_url: str
    user_id: int | None
    client_role_id: int | None
    user_name: str
    user_password: str
    timeout: int = 60
    proxy_url: str | None = None

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip('/')
        self.session = requests.Session()
        self.session.headers.update({
            'Accept': 'application/json',
            'Content-Type': 'application/json',
        })
        if self.proxy_url:
            self.session.proxies.update({
                'http': self.proxy_url,
                'https': self.proxy_url,
            })

    def _request_info(self) -> dict[str, Any]:
        data = {
            'userName': self.user_name,
            'userPassword': self.user_password,
            'transactionId': str(uuid.uuid4()).replace('-', ''),
        }
        if self.user_id is not None:
            data['userId'] = self.user_id
        if self.client_role_id is not None:
            data['clientRoleId'] = self.client_role_id
        return data

    def _request_info_pascal(self) -> dict[str, Any]:
        data = {
            'UserName': self.user_name,
            'UserPassword': self.user_password,
            'TransactionId': str(uuid.uuid4()).replace('-', ''),
        }
        if self.user_id is not None:
            data['UserId'] = self.user_id
        if self.client_role_id is not None:
            data['ClientRoleId'] = self.client_role_id
        return data

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = self.session.post(
            f'{self.base_url}{path}',
            json={
                'automaticRequestInfo': self._request_info(),
                **payload,
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()
        info = data.get('automaticResponseInfo') or {}
        response_code = str(info.get('responseCode') or '').strip()
        response_message = str(info.get('responseMessage') or '').strip().lower()
        is_success = (
            response_code == '0000'
            or 'başar' in response_message
            or 'basar' in response_message
        )
        if not is_success:
            raise RuntimeError(f'Petrol API error: {info}')
        return data

    @staticmethod
    def _decode_json_return_data(data: dict[str, Any]) -> Any:
        raw = data.get('jsoN_ReturnData')
        if raw in (None, ''):
            return {}
        if isinstance(raw, (dict, list)):
            return raw
        if not isinstance(raw, str):
            return raw

        text = raw.strip()
        if not text:
            return {}

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            repaired = text.encode('utf-8', errors='ignore').decode('unicode_escape', errors='ignore')
            return json.loads(repaired)

    def get_transaction_sales_v2(
        self,
        start_date: str,
        end_date: str,
        fleet_list: str | None = None,
        viu_id: str | None = None,
        invoice_type: str | None = None,
        invoice_period: str | None = None,
    ) -> Any:
        data = self._post(
            '/CORP_API_AUTOMATIC/TransactionBasedFuelPurchaseInfoReport_v2',
            {
                'startDate': start_date,
                'endDate': end_date,
                'fleetList': fleet_list,
                'viuId': viu_id,
                'invoicE_TYPE': invoice_type,
                'invoicePeriod': invoice_period,
            },
        )
        return self._decode_json_return_data(data)

    def get_sales_with_invoice_infos(
        self,
        start_date: str,
        end_date: str,
        fleet_id: str | None = None,
        holding_id: int | None = None,
    ) -> Any:
        response = self.session.post(
            f'{self.base_url}/CORP_API_AUTOMATIC/GET_SALES_WITH_INVOICE_INFOS',
            json={
                'HOLDING_ID': holding_id,
                'FLEET_ID': fleet_id,
                'START_DATE': start_date,
                'END_DATE': end_date,
                'AutomaticRequestInfo': self._request_info_pascal(),
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()
        info = data.get('automaticResponseInfo') or data.get('AutomaticResponseInfo') or {}
        response_code = str(info.get('responseCode') or '').strip() if isinstance(info, dict) else ''
        response_message = str(info.get('responseMessage') or '').strip().lower() if isinstance(info, dict) else ''
        is_success = (
            response_code == '0000'
            or 'başar' in response_message
            or 'basar' in response_message
        )
        if not is_success:
            raise RuntimeError(f'Petrol API error: {info}')
        return self._decode_json_return_data(data)

    def get_purchase_info_v3(
        self,
        start_date: str,
        end_date: str,
        fleet_id: str | None = None,
        plate: str | None = None,
        card_no: str | None = None,
        query_prepaid: bool | None = None,
        query_standard: bool | None = None,
        page_index: int = 1,
        page_size: int = 1000,
    ) -> Any:
        data = self._post(
            '/CORP_API_AUTOMATIC/FuelPurchaseInfoReport_v3',
            {
                'automaticPagedRequestInfo': {
                    'pageIndex': page_index,
                    'pageSize': page_size,
                },
                'fleetId': fleet_id,
                'cardNo': card_no,
                'plate': plate,
                'startDate': start_date,
                'endDate': end_date,
                'queryPrepaid': query_prepaid,
                'queryStandard': query_standard,
            },
        )
        return self._decode_json_return_data(data)
