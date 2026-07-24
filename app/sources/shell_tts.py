from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

import pandas as pd
import requests


SOAP_NAMESPACE = 'https://tts.turkiyeshell.com'
SOAP_ENVELOPE_NAMESPACE = 'http://schemas.xmlsoap.org/soap/envelope/'
SALES_METHOD = 'GetCustomerSalesTransaction'
SALES_WITH_RID_METHOD = 'GetCustomerSalesTransaction_with_rid'
ONLINE_METHOD = 'GetOnlineTransaction'


@dataclass
class ShellTtsClient:
    base_url: str
    customer_code: str
    user_id: str
    password: str
    branch_code: str
    timeout: int = 120

    def _soap_body(self, method: str, values: dict[str, Any]) -> str:
        tags = '\n'.join(
            f'      <{key}>{escape(str(value or ""))}</{key}>'
            for key, value in values.items()
        )
        return (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
            'xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
            f'xmlns:soap="{SOAP_ENVELOPE_NAMESPACE}">\n'
            '  <soap:Body>\n'
            f'    <{method} xmlns="{SOAP_NAMESPACE}">\n'
            f'{tags}\n'
            f'    </{method}>\n'
            '  </soap:Body>\n'
            '</soap:Envelope>'
        )

    @staticmethod
    def _local_name(tag: str) -> str:
        return tag.rsplit('}', 1)[-1]

    @classmethod
    def _element_to_dict(cls, element: ET.Element) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for child in list(element):
            key = cls._local_name(child.tag)
            value = (child.text or '').strip()
            if key in result:
                existing = result[key]
                result[key] = existing + [value] if isinstance(existing, list) else [existing, value]
            else:
                result[key] = value
        return result

    @classmethod
    def _parse_response(cls, xml_text: str) -> tuple[list[dict[str, Any]], str]:
        root = ET.fromstring(xml_text)

        fault = next((node for node in root.iter() if cls._local_name(node.tag) == 'Fault'), None)
        if fault is not None:
            fault_data = cls._element_to_dict(fault)
            raise RuntimeError(f'Shell SOAP fault: {fault_data}')

        process_result = ''
        rows: list[dict[str, Any]] = []
        row_names = {
            'SALESTRANSACTIONINFO',
            'SALESTRANSACTIONINFO_WITH_RID',
            'SHELL2CUSTRESPONSEOFONLINETRN',
        }

        for node in root.iter():
            name = cls._local_name(node.tag)
            name_upper = name.upper()
            if name_upper == 'PROCESSRESULT':
                process_result = (node.text or '').strip()
            elif name_upper in row_names:
                rows.append(cls._element_to_dict(node))

        if not rows:
            result_node = next(
                (
                    node
                    for node in root.iter()
                    if cls._local_name(node.tag).lower().endswith('result')
                ),
                None,
            )
            nested_text = (result_node.text or '').strip() if result_node is not None else ''
            if nested_text.startswith('<'):
                nested_rows, nested_result = cls._parse_response(nested_text)
                rows.extend(nested_rows)
                process_result = process_result or nested_result

        return rows, process_result

    def _post(self, method: str, values: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
        response = requests.post(
            self.base_url,
            headers={
                'Content-Type': 'text/xml; charset=utf-8',
                'SOAPAction': f'{SOAP_NAMESPACE}/{method}',
                'Accept': 'text/xml',
            },
            data=self._soap_body(method, values).encode('utf-8'),
            timeout=self.timeout,
        )
        response.raise_for_status()
        return self._parse_response(response.text)

    def get_customer_sales_transactions(
        self,
        start_dt: datetime,
        end_dt: datetime,
        plate_code: str = '',
        department_code: str = '',
        invoice_number: str = '',
        customer_reference: str = '',
        with_rid: bool = True,
    ) -> tuple[list[dict[str, Any]], str]:
        method = SALES_WITH_RID_METHOD if with_rid else SALES_METHOD
        return self._post(
            method,
            {
                'cust_code': self.customer_code,
                'user_id': self.user_id,
                'password': self.password,
                'branch_code': self.branch_code,
                'report_start_dt': start_dt.strftime('%Y-%m-%dT%H:%M:%S'),
                'report_end_dt': end_dt.strftime('%Y-%m-%dT%H:%M:%S'),
                'plate_code': plate_code,
                'department_code': department_code,
                'invoice_number': invoice_number,
                'customer_reference': customer_reference,
            },
        )

    def get_online_transactions(
        self,
        start_dt: datetime,
        end_dt: datetime,
        plate_code: str = '',
        customer_reference: str = '',
    ) -> tuple[list[dict[str, Any]], str]:
        return self._post(
            ONLINE_METHOD,
            {
                'cust_code': self.customer_code,
                'user_id': self.user_id,
                'password': self.password,
                'branch_code': self.branch_code,
                'report_start_dt': start_dt.strftime('%Y-%m-%dT%H:%M:%S'),
                'report_end_dt': end_dt.strftime('%Y-%m-%dT%H:%M:%S'),
                'plate_code': plate_code,
                'customer_reference': customer_reference,
            },
        )


def _normalize_column_name(value: Any) -> str:
    return ''.join(ch for ch in str(value).upper() if ch.isalnum())


SHELL_API_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    'Date': ('TRANSACTION_DATE', 'TRANSACTIONDATE', 'SALE_DATE', 'PROCESS_DATE', 'DATE'),
    'Номерной знак': ('PLATE_CD', 'PLATE_CODE', 'PLATECODE', 'PLATE_NO', 'UTTS_PLATE_NO'),
    'Вид топлива': ('FUEL_NAME', 'PRODUCT_NAME', 'FUEL_TYPE', 'PRODUCT'),
    'Общий литр (л)': ('VOLUME', 'QUANTITY', 'LITER', 'LITRE', 'AMOUNT_LT'),
    'Цена (тл)': ('UNIT_PRICE', 'UNITPRICE', 'PRICE'),
    'Стоимость (тл)': ('SALES_TOTAL_AMOUNT', 'TOTAL_AMOUNT', 'TOTALAMOUNT', 'SALE_AMOUNT'),
    'Код станции': ('RETAIL_OUTLET_CODE', 'STATION_CODE', 'DEALER_CODE'),
    'Название станции': ('RETAIL_OUTLET_NAME', 'STATION_NAME', 'DEALER_NAME'),
    'Провинция': ('RTL_OTLT_PROVINCE', 'RETAIL_OUTLET_PROVIENCE', 'STATION_CITY', 'CITY_NAME'),
    'Номер карты': ('CARD_NO', 'CARDNO', 'CARD_NUMBER'),
    'Название группы': ('DEPARTMENT_NAME', 'DEPT_NAME', 'DEPARTMAN_ADI'),
    'Departman Kodu': ('DEPARTMENT_CODE', 'DEPT_CODE', 'DEPARTMAN_KODU'),
    'Километраж': ('VEHICLE_KM', 'ODOMETER', 'KILOMETER', 'KILOMETRE'),
    'Satış Tipi': ('SALES_TYPE', 'SALE_TYPE', 'TRANSACTION_TYPE'),
    'External ID': ('RID', 'TRANSACTION_ID', 'SHELL_REFERENCE'),
    'Номер счета': ('INVOICE_NO',),
}


def shell_transactions_to_legacy_df(rows: list[dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()

    source = pd.DataFrame(rows)
    normalized_lookup = {
        _normalize_column_name(column): column
        for column in source.columns
    }
    output = pd.DataFrame(index=source.index)

    for target, aliases in SHELL_API_COLUMN_ALIASES.items():
        selected = None
        for alias in aliases:
            key = _normalize_column_name(alias)
            if key in normalized_lookup:
                selected = normalized_lookup[key]
                break
        output[target] = source[selected] if selected is not None else ''

    # Legacy normalizer always checks this optional fallback column and expects
    # a pandas Series rather than a scalar string.
    output['#Н/Д'] = ''

    return output
