from shared.zones import ZONES, ZONES_BY_ID, nearest_zone, zone_code_from_device_id


def test_zone_ids_are_device_id_safe():
    for z in ZONES:
        assert z.zone_id.isalnum() and z.zone_id.isupper()
        assert z.source_context and z.source_tags


def test_each_zone_resolves_from_its_own_vertices():
    for z in ZONES:
        for lat, lon in z.path:
            assert nearest_zone(lat, lon).zone_id == z.zone_id


def test_corridor_midpoint_resolves():
    z = ZONES_BY_ID["PGPT"]
    (a, b) = z.path[2], z.path[3]            # Kukatpally -> Miyapur
    mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
    assert nearest_zone(*mid).zone_id == "PGPT"


def test_far_point_has_no_zone():
    assert nearest_zone(12.9716, 77.5946) is None      # Bengaluru, not Hyderabad


def test_zone_code_from_device_id():
    assert zone_code_from_device_id("ZONE_HITEC_01") == "HITEC"
    assert zone_code_from_device_id("BUS_CHNAM_01") is None
