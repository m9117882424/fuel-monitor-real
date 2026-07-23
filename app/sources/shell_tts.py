from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

import pandas as pd
import requests


SOAP_NAMESPACE = 'https://tts.turkiyeshell.com'
SOAP_ACTION = f'{SOAP_NAMESPACE}/GetCustomerSalesTransaction'
SOAP_ENVELOPE_NAMESPACE = 'http://schemas.xmlsoap.org/soap/envelope/'


@dataclass
class ShellTtsClient:
    base_url: str
    customer_code: str
    user_id: str
    password: str
    branch_code: str
    timeout: int = 120

    def _soap_body(
        self,
        start_dt: datetime,
        end_dt: datetime,
        plate_code: str = '',
        department_code: str = '',
        invoice_number: str = '',
        customer_reference: str = '',
    ) -> str:
        values = {
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
        }
        tags = '\n'.join(f'      <{key}>{escape(str(value or ""))}</{key}>' for key, value in values.items())
        return (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
            'xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
            f'xmlns:soap="{SOAP_ENVELOPE_NAMESPACE}">\n'
            '  <soap:Body>\n'
            f'    <GetCustomerSalesTransaction xmlns="{SOAP_NAMESPACE}">\n'
            f'{tags}\n'
            '    </GetCustomerSalesTransaction>\n'
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
        for node in root.iter():
            name = cls._local_name(node.tag)
            if name.upper() == 'PROCESSRESULT':
                process_result = (node.text or '').strip()
            elif name.upper() == 'SALESTRANSACTIONINFO':
                rows.append(cls._element_to_dict(node))

        if not rows:
            result_node = next(
                (node for node in root.iter() if cls._local_name(node.tag) == 'GetCustomerSalesTransactionResult'),
                None,
            )
            nested_text = (result_node.text or '').strip() if result_node is not None else ''
            if nested_text.startswith('<'):
                nested_rows, nested_result = cls._parse_response(nested_text)
                rows.extend(nested_rows)
                process_result = process_result or nested_result

        return rows, process_result

    def get_customer_sales_transactions(
        self,
        start_dt: datetime,
        end_dt: datetime,
        plate_code: str = '',
        department_code: str = '',
        invoice_number: str = '',
        customer_reference: str = '',
    ) -> tuple[list[dict[str, Any]], str]:
        response = requests.post(
            self.base_url,
            headers={
                'Content-Type': 'text/xml; charset=utf-8',
                'SOAPAction': SOAP_ACTION,
                'Accept': 'text/xml',
            },
            data=self._soap_body(
                start_dt=start_dt,
                end_dt=end_dt,
                plate_code=plate_code,
                department_code=department_code,
                invoice_number=invoice_number,
                customer_reference=customer_reference,
            ).encode('utf-8'),
            timeout=self.timeout,
        )
        response.raise_for_status()
        return self._parse_response(response.text)


SHELL_API_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    'Date': ('TRANSACTIONDATE', 'TRANSACTION_DATE', 'SALEDATE', 'PROCESSDATE', 'DATE'),
    'Номерной знак': ('PLATECODE', 'PLATE_CODE', 'PLATE', 'PLATENO'),
    'Вид топлива': ('PRODUCTNAME', 'PRODUCT_NAME', 'FUELTYPE', 'PRODUCT'),
    'Общий литр (л)': ('QUANTITY', 'LITER', 'LITRE', 'VOLUME', 'AMOUNTLT'),
    'Цена (тл)': ('UNITPRICE', 'UNIT_PRICE', 'PRICE'),
    'Стоимость (тл)': ('TOTALAMOUNT', 'TOTAL_AMOUNT', 'AMOUNT', 'SALEAMOUNT'),
    'Код станции': ('STATIONCODE', 'STATION_CODE', 'DEALERCODE'),
    'Название станции': ('STATIONNAME', 'STATION_NAME', 'DEALERNAME'),
    'Провинция': ('STATIONCITY', 'STATION_CITY', 'CITY', 'CITYNAME'),
    'Номер карты': ('CARDNUMBER', 'CARD_NUMBER', 'CARDNO'),
    'Название группы': ('DEPARTMENTNAME', 'DEPARTMENT_NAME', 'DEPARTMANADI'),
    'Departman Kodu': ('DEPARTMENTCODE', 'DEPARTMENT_CODE', 'DEPARTMANKODU'),
    'Километраж': ('ODOMETER', 'KILOMETER', 'KILOMETRE'),
    'Satış Tipi': ('SALETYPE', 'SALE_TYPE', 'TRANSACTIONTYPE'),
}


def shell_transactions_to_legacy_df(rows: list[dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()

    source = pd.DataFrame(rows)
    normalized_lookup = {str(column).replace(' ', '').replace('-', '').upper(): column for column in source.columns}
    output = pd.DataFrame(index=source.index)

    for target, aliases in SHELL_API_COLUMN_ALIASES.items():
        selected = None
        for alias in aliases:
            key = alias.replace(' ', '').replace('-', '').upper()
            if key in normalized_lookup:
                selected = normalized_lookup[key]
                break
        output[target] = source[selected] if selected is not None else ''

    # Legacy normalizer always checks this optional fallback column and expects
    # a pandas Series rather than a scalar string.
    output['#Н/Д'] = ''

    return output
