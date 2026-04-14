from app.services.menu_typing import infer_menu_taxonomy


def test_infer_menu_taxonomy_marks_milk_tea_as_drink():
    item = {
        "name": "A2牛乳四季春茶",
        "description": "搭配优质 A2 牛乳，入口丝滑不腻，推荐5分糖。",
    }

    taxonomy = infer_menu_taxonomy(item)

    assert taxonomy == {"item_type": "drink", "drink_category": "milk_tea"}


def test_infer_menu_taxonomy_keeps_fresh_fruit_tea_out_of_packaged():
    item = {
        "name": "清爽芭乐提(红芭乐)",
        "description": "手摘鲜果榨汁，搭配青提果肉，清爽解腻。咖啡因：绿灯。",
    }

    taxonomy = infer_menu_taxonomy(item)

    assert taxonomy == {"item_type": "drink", "drink_category": "fruit_tea"}


def test_infer_menu_taxonomy_marks_packaged_tea_bag_as_packaged():
    item = {
        "name": "刺梨菠萝茶",
        "description": "冷泡热泡都好喝。净含量：45g（9g*5包） 保质期：12个月",
    }

    taxonomy = infer_menu_taxonomy(item)

    assert taxonomy == {"item_type": "packaged", "drink_category": None}


def test_infer_menu_taxonomy_marks_snack_as_snack_even_with_tea_word():
    item = {
        "name": "金凤茶酥",
        "description": "金凤乌龙茶风味融入传统桃酥。",
    }

    taxonomy = infer_menu_taxonomy(item)

    assert taxonomy == {"item_type": "snack", "drink_category": None}


def test_infer_menu_taxonomy_marks_addon_as_addon():
    item = {
        "name": "0脂脆波波",
        "description": "Q弹爽滑，0脂轻负担。",
    }

    taxonomy = infer_menu_taxonomy(item)

    assert taxonomy == {"item_type": "addon", "drink_category": None}


def test_infer_menu_taxonomy_treats_milkshake_as_drink():
    item = {
        "name": "草莓摇摇奶昔",
        "description": "喝前摇一摇，杯型容量 420ml。",
    }

    taxonomy = infer_menu_taxonomy(item)

    assert taxonomy == {"item_type": "drink", "drink_category": None}
