from __future__ import annotations

from pathlib import Path


TARGET = Path(__file__).resolve().parent / "wialon_fuel_tank_sync.py"


HELPER_BLOCK = r'''
EMPTY_METADATA_VALUES = {
    "",
    "-",
    "--",
    "---",
    "-----",
    "нет данных",
    "Нет данных",
    "N/A",
    "n/a",
    "None",
    "none",
}


def clean_metadata_value(value: Any) -> str:
    text = str(value or "").strip()
    if text in EMPTY_METADATA_VALUES:
        return ""
    return text


def get_profile_field(item: Dict[str, Any], field_name: str) -> str:
    for block_name in ("pflds", "profile", "flds", "aflds"):
        block = item.get(block_name)
        if not isinstance(block, dict):
            continue

        for field in block.values():
            if not isinstance(field, dict):
                continue
            if str(field.get("n") or "").strip() == field_name:
                return clean_metadata_value(field.get("v"))

    return ""


def build_unit_properties_lookup(client: WialonClient) -> Dict[str, Dict[str, str]]:
    """
    Основной справочник оргструктуры из свойств объектов Wialon:
    Фирма    = pflds.brand
    Дирекция = pflds.vehicle_type
    Госномер = pflds.registration_plate или имя объекта
    """
    data = client.call(
        "core/search_items",
        {
            "spec": {
                "itemsType": "avl_unit",
                "propName": "sys_name",
                "propValueMask": "*",
                "sortType": "sys_name",
            },
            "force": 1,
            "flags": 4611686018427387903,
            "from": 0,
            "to": 0,
        },
    )

    items = data.get("items", []) if isinstance(data, dict) else []
    lookup: Dict[str, Dict[str, str]] = {}

    for item in items:
        unit_name = clean_metadata_value(item.get("nm"))
        registration_plate = get_profile_field(item, "registration_plate")
        gos_number = registration_plate or split_grouping(unit_name)[0] or unit_name
        gos_number = clean_metadata_value(gos_number)

        if not gos_number:
            continue

        firm = get_profile_field(item, "brand")
        department = get_profile_field(item, "vehicle_type")

        lookup[gos_number] = {
            "firm": firm,
            "department": department,
            "metadata_source": "wialon_unit_pflds",
        }

    print(
        "OK: справочник из свойств объектов Wialon: "
        f"объектов={len(items)}, госномеров={len(lookup)}"
    )
    return lookup


def find_unit_properties(
    unit_lookup: Dict[str, Dict[str, str]],
    gos_number: str,
) -> Dict[str, str]:
    if not unit_lookup:
        return {}

    if gos_number in unit_lookup:
        return unit_lookup[gos_number]

    target = re.sub(r"[^A-ZА-ЯЁ0-9]", "", str(gos_number or "").upper())

    for key, value in unit_lookup.items():
        key_norm = re.sub(r"[^A-ZА-ЯЁ0-9]", "", str(key or "").upper())
        if key_norm == target:
            return value

    return {}

'''


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count == 0:
        raise RuntimeError(f"Не найден блок для замены: {label}")
    if count > 1:
        raise RuntimeError(f"Блок найден несколько раз: {label}, count={count}")
    return text.replace(old, new, 1)


def main() -> int:
    text = TARGET.read_text(encoding="utf-8")

    if "def build_unit_properties_lookup(" in text:
        print("Патч уже применён.")
        return 0

    backup = TARGET.with_suffix(TARGET.suffix + ".bak_unit_props")
    backup.write_text(text, encoding="utf-8")

    text = replace_once(
        text,
        "\n\ndef find_vehicle_metadata(\n",
        "\n\n" + HELPER_BLOCK + "def find_vehicle_metadata(\n",
        "insert unit properties helper",
    )

    text = replace_once(
        text,
        '''def parse_dashboard_rows(
    raw_rows: List[Dict[str, Any]],
    headers: List[str],
    table_name: str,
    table_index: int,
    metadata_lookup: Optional[Dict[str, Dict[Any, Dict[str, str]]]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:''',
        '''def parse_dashboard_rows(
    raw_rows: List[Dict[str, Any]],
    headers: List[str],
    table_name: str,
    table_index: int,
    metadata_lookup: Optional[Dict[str, Dict[Any, Dict[str, str]]]] = None,
    unit_properties_lookup: Optional[Dict[str, Dict[str, str]]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:''',
        "parse_dashboard_rows signature",
    )

    text = replace_once(
        text,
        '''        metadata = find_vehicle_metadata(metadata_lookup or {}, gos_number, event_datetime)

        firm = str(row.get("Фирма") or metadata.get("firm") or "").strip()
        department = str(row.get("Дирекция") or metadata.get("department") or "").strip()
''',
        '''        unit_properties = find_unit_properties(unit_properties_lookup or {}, gos_number)
        metadata = find_vehicle_metadata(metadata_lookup or {}, gos_number, event_datetime)

        firm = (
            clean_metadata_value(unit_properties.get("firm"))
            or clean_metadata_value(metadata.get("firm"))
            or clean_metadata_value(row.get("Фирма"))
            or "-----"
        )
        department = (
            clean_metadata_value(unit_properties.get("department"))
            or clean_metadata_value(metadata.get("department"))
            or clean_metadata_value(row.get("Дирекция"))
            or "-----"
        )
''',
        "firm/department resolution",
    )

    text = replace_once(
        text,
        '''        metadata_lookup = build_vehicle_metadata_lookup(
            client=client,
            report_result=report_result,
        )

        dashboard_rows, normalized_rows = parse_dashboard_rows(
            raw_rows=raw_rows,
            headers=headers,
            table_name=table_name,
            table_index=table_index,
            metadata_lookup=metadata_lookup,
        )
''',
        '''        metadata_lookup = build_vehicle_metadata_lookup(
            client=client,
            report_result=report_result,
        )
        unit_properties_lookup = build_unit_properties_lookup(client)

        dashboard_rows, normalized_rows = parse_dashboard_rows(
            raw_rows=raw_rows,
            headers=headers,
            table_name=table_name,
            table_index=table_index,
            metadata_lookup=metadata_lookup,
            unit_properties_lookup=unit_properties_lookup,
        )
''',
        "main unit properties lookup",
    )

    TARGET.write_text(text, encoding="utf-8")
    print(f"OK: patched {TARGET}")
    print(f"Backup: {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
