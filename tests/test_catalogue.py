from napari_demo_recorder.core.catalogue import Catalogue, norm, tooltip_keys


def test_norm():
    assert norm("&File") == "file"
    assert norm("Toggle 2D/3D view (Ctrl+Y) ") == "toggle 2d/3d view"
    assert norm("Brush size:") == "brush size"
    assert norm("Activate the paint brush (2 or P)") == "activate the paint brush"
    assert norm("new_points_layer") == "new points layer"
    assert norm("A\xa0 b") == "a b"


def test_tooltip_keys():
    keys = tooltip_keys("Create a new points layer.\nThis button is highlighted if ...")
    assert keys[0] == "create a new points layer"
    assert tooltip_keys("Delete selected layers (Ctrl+⌦ or Ctrl+⌫) ")[0] == "delete selected layers"
    assert tooltip_keys("") == []


def test_lookup_by_alias():
    cat = Catalogue.from_dict(
        {"parents": {"p": {"class": ["X"]}}, "elements": [{"id": "new_points_layer", "parent": "p", "aliases": ["Create Points"], "find": [{"property": {"mode": "new_points"}}]}]}
    )
    assert cat.lookup("create points")[0].id == "new_points_layer"
    assert cat.lookup("NEW_POINTS_LAYER")
    assert not cat.lookup("points")
