from __future__ import annotations

import importlib.util
import json
from pathlib import Path

try:
    from scripts.extract_menu_capture import extract_items_from_payload
except ModuleNotFoundError:
    from backend.scripts.extract_menu_capture import extract_items_from_payload


def _repo_root() -> Path:
    current = Path(__file__).resolve()
    candidates = [current.parents[2], current.parents[1]]
    for candidate in candidates:
        if (candidate / "configs").exists() or (candidate / "scripts").exists():
            return candidate
    return current.parents[1]


def _load_import_menu_module():
    root = _repo_root()
    candidate_paths = [
        root / "backend/scripts/import_menu_json.py",
        root / "scripts/import_menu_json.py",
    ]
    path = next((item for item in candidate_paths if item.exists()), candidate_paths[0])
    spec = importlib.util.spec_from_file_location("import_menu_json_module", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_extract_menu_capture_preserves_description_for_heytea_static_menu():
    root = _repo_root()
    cfg_path = root / "configs/menu_capture/heytea.static_menu.json"
    payload_path = root / "heytea_static_menu.json"

    if cfg_path.exists() and payload_path.exists():
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
    else:
        cfg = {
            "brand": "喜茶",
            "menu_list_path": "data.items[]",
            "field_map": {
                "name": ["title"],
                "price": ["price"],
                "description": ["description"],
                "size": ["size"],
                "sugar_opts": ["sugar_opts"],
                "ice_opts": ["ice_opts"],
                "is_active": ["is_active"],
            },
            "defaults": {
                "is_active": True,
                "sugar_opts": ["标准糖"],
                "ice_opts": ["标准冰"],
            },
        }
        payload = {
            "data": {
                "items": [
                    {
                        "title": "多肉葡萄",
                        "price": 19,
                        "description": "清爽葡萄果香",
                        "size": "大杯",
                        "sugar_opts": ["少糖"],
                        "ice_opts": ["少冰"],
                        "is_active": True,
                    }
                ]
            }
        }

    items = extract_items_from_payload(payload, cfg)

    assert items
    assert "description" in items[0]
    assert items[0]["description"]


def test_import_menu_json_load_items_keeps_description(tmp_path: Path):
    module = _load_import_menu_module()

    source = {
        "brand": "喜茶",
        "items": [
            {
                "brand": "喜茶",
                "name": "多肉葡萄",
                "size": "大杯",
                "price": 19,
                "description": "清爽葡萄果香",
                "sugar_opts": ["少糖"],
                "ice_opts": ["少冰"],
                "is_active": True,
            }
        ],
    }
    input_path = tmp_path / "menu.json"
    input_path.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")

    items = module.load_items(input_path, brand_override="")

    assert len(items) == 1
    assert items[0]["description"] == "清爽葡萄果香"
