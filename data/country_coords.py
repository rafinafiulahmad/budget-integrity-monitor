"""
Country → (latitude, longitude) lookup for map markers.
Covers LDC / development-program countries.
"""

COUNTRY_COORDS: dict[str, tuple[float, float]] = {
    # South Asia
    "Bangladesh":   (23.685,   90.356),
    "Nepal":        (28.394,   84.124),
    "Pakistan":     (30.375,   69.345),
    "Afghanistan":  (33.939,   67.710),
    "Bhutan":       (27.515,   90.434),
    "Sri Lanka":    (7.873,    80.772),
    "India":        (20.594,   78.963),
    "Maldives":     (3.202,    73.220),

    # Southeast Asia
    "Cambodia":     (12.565,  104.991),
    "Myanmar":      (19.165,   96.871),
    "Laos":         (19.856,  102.495),
    "Vietnam":      (14.058,  108.278),
    "Timor-Leste":  (-8.874,  125.727),
    "Philippines":  (12.880,  121.774),
    "Indonesia":    (-0.790,  113.922),

    # East Africa
    "Ethiopia":     (9.145,    40.489),
    "Uganda":       (1.373,    32.290),
    "Tanzania":     (-6.369,   34.889),
    "Mozambique":   (-18.665,  35.529),
    "Zambia":       (-13.133,  27.849),
    "Malawi":       (-13.254,  34.302),
    "Rwanda":       (-1.940,   29.874),
    "Kenya":        (-0.023,   37.906),
    "Madagascar":   (-18.767,  46.869),
    "Zimbabwe":     (-19.015,  29.155),
    "Burundi":      (-3.373,   29.919),
    "Eritrea":      (15.180,   39.782),
    "Djibouti":     (11.826,   42.590),
    "Comoros":      (-11.645,  43.333),

    # West Africa
    "Mali":          (17.570,  -3.996),
    "Senegal":       (14.497, -14.452),
    "Niger":         (17.608,   8.082),
    "Chad":          (15.454,  18.732),
    "Burkina Faso":  (12.364,  -1.562),
    "Guinea":        (11.804, -15.180),
    "Sierra Leone":  (8.460,  -11.779),
    "Liberia":       (6.428,   -9.429),
    "Benin":         (9.308,    2.315),
    "Togo":          (8.620,    0.825),
    "Gambia":        (13.444, -15.310),
    "Guinea-Bissau": (11.804, -15.180),

    # Central Africa
    "DRC":                      (-4.038,  21.759),
    "Democratic Republic of Congo": (-4.038, 21.759),
    "Congo":                    (-0.228,  15.827),
    "CAR":                      (6.611,   20.939),
    "Central African Republic": (6.611,   20.939),

    # North/Horn Africa
    "Sudan":         (12.862,  30.218),
    "South Sudan":   (6.877,   31.307),
    "Somalia":       (5.152,   46.199),

    # Middle East / North Africa
    "Yemen":         (15.552,  48.516),
    "Syria":         (34.802,  38.997),
    "Iraq":          (33.224,  43.679),

    # Caribbean / Latin America
    "Haiti":         (18.971, -72.285),
    "Nicaragua":     (12.865, -85.207),
    "Honduras":      (15.200, -86.242),
    "Bolivia":       (-16.291, -63.589),

    # Pacific
    "Papua New Guinea": (-6.315, 143.956),
    "Kiribati":      (1.873,  157.363),
    "Vanuatu":       (-15.377, 166.959),
    "Solomon Islands": (-9.645, 160.156),
    "Samoa":         (-13.759, -172.105),
    "Tuvalu":        (-7.109,  177.649),
}


def get_coords(country: str) -> tuple[float, float] | None:
    """
    Return (lat, lon) for a country name, trying exact match then
    case-insensitive partial match.  Returns None if unknown.
    """
    if country in COUNTRY_COORDS:
        return COUNTRY_COORDS[country]
    lower = country.lower()
    for key, val in COUNTRY_COORDS.items():
        if key.lower() == lower or lower in key.lower():
            return val
    return None
