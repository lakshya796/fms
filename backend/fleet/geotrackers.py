"""Geotrackers telematics feed.

The vendor's dashboard endpoint is undocumented, so nothing here assumes a
fixed schema. Two things vary in practice and both have to be absorbed:

* **The envelope.** The vehicle array has been seen at the top level, and
  nested under `data`, `result`, `vehicleList` and one level deeper again.
  `extract_rows()` therefore walks the whole document and picks the list that
  most looks like GPS records rather than trusting any single key.
* **The field names.** `lat`/`latitude`, `lng`/`lon`/`long`, `vehicleNo`/
  `regNo`/`assetName` have all appeared. Keys are matched case-insensitively
  with separators stripped, so `Vehicle_No`, `vehicleNo` and `vehicleno` are
  one key as far as this module is concerned.

Parsing is kept free of Django and of the network so it can be tested against
recorded payloads - see `FleetGeotrackersTests`. `fetch_dashboard()` is the
only function that does I/O.
"""
import json
import re
import urllib.request
from datetime import datetime, timezone as dt_timezone

DASHBOARD_URL = "https://www.geotrackers.com/gt/track/v1/dashboard"
# base64("dataiceman:dataiceman")
BASIC_AUTH = "ZGF0YWljZW1hbjpkYXRhaWNlbWFu"
TIMEOUT_SECONDS = 15

# Mainland India plus the island territories, padded slightly. Used only to
# sanity-check a coordinate pair, never to reject a vehicle outright.
INDIA_LAT = (6.0, 37.6)
INDIA_LNG = (67.0, 97.5)

MOVING_SPEED_KPH = 3.0

_LAT_KEYS = ("lat", "latitude", "lattitude", "curlat", "currentlat", "lastlat", "gpslat", "ylat")
_LNG_KEYS = ("lng", "lon", "long", "longitude", "curlng", "curlon", "currentlng",
             "lastlng", "lastlon", "gpslng", "gpslon", "xlng")
_REG_KEYS = ("vehicleno", "vehiclenumber", "vehicleregno", "vehreg", "vehno", "vehicle",
             "regno", "registrationno", "registrationnumber", "assetname", "assetno",
             "devicename", "label", "name")
_MAKE_KEYS = ("make", "brand", "manufacturer")
_MODEL_KEYS = ("model", "variant")

# An Indian plate: two state letters, the RTO number, an optional series and
# four digits - `MH14LX8206`, `MH 04 JU 9182`, `KA-51-MN-6814`. The trailing
# four digits are what stop a model name such as "TATA 1109" matching.
_PLATE_RE = re.compile(r"\b[A-Z]{2}[\s-]?\d{1,2}[\s-]?[A-Z]{0,3}[\s-]?\d{4}\b")
_SPEED_KEYS = ("speed", "spd", "curspeed", "currentspeed", "lastspeed", "gpsspeed", "speedkph", "speedkmph")
_IGN_KEYS = ("ignition", "ign", "ignitionstatus", "acc", "accstatus")
_TIME_KEYS = ("fixtime", "gpstime", "devicetime", "servertime", "lastupdate", "lastupdated",
              "lastseen", "datetime", "timestamp", "packetdate", "tm")
_ADDR_KEYS = ("address", "addr", "location", "place", "landmark", "currentaddress", "lastaddress")
# `state` reads "Moving for 49 mins" / "Stopped for 3 hrs" and is the clearest
# statement of movement the feed makes, so it is consulted before `status`
# ("AC On, Key On, Motion"), which mixes movement in with the digital inputs.
_STATUS_KEYS = ("state", "status", "vehiclestatus", "movingstatus", "gpsstatus")
_DRIVER_KEYS = ("drivername", "driver")
_HEADING_KEYS = ("direction", "heading", "course", "bearing", "angle")

_MOVING_WORDS = ("moving", "motion", "running", "transit")
_IDLE_WORDS = ("idle", "idling")
_PARKED_WORDS = ("stopped", "stop", "parked", "parking", "halt", "stationary")
# Nested objects worth merging in: the coordinates live under `position` in some
# builds, and the registration under a whole `vehicle` master record in others.
_NESTED_KEYS = ("position", "location", "gps", "lastposition", "lastlocation", "latlng", "coordinates",
                "vehicle", "vehicledetail", "vehicledetails", "asset", "device", "deviceinfo")


def _nk(key):
    """Normalise a key: lowercase, letters and digits only."""
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def normalise_registration(value):
    """Registration numbers are written `MH04JU9182`, `MH 04 JU 9182` and
    `MH-04-JU-9182` interchangeably; compare them with the noise removed."""
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def _flatten(row):
    """A single lowercase-keyed dict, with any nested position object merged in.

    Nested keys are merged *underneath* top-level ones so a top-level `lat`
    still wins over `position.lat`.
    """
    flat = {}
    for key, value in row.items():
        nk = _nk(key)
        if nk in _NESTED_KEYS and isinstance(value, dict):
            for inner_key, inner_value in value.items():
                flat.setdefault(_nk(inner_key), inner_value)
        else:
            flat[nk] = value
    return flat


def _pick(flat, keys):
    """First usable scalar among `keys`.

    Containers are skipped rather than stringified: the feed nests a whole
    `vehicle` object under a key that also names the registration in other
    builds, and `str()` of that dict is what used to end up on screen.
    """
    for key in keys:
        if key in flat:
            value = flat[key]
            if isinstance(value, (dict, list, tuple)):
                continue
            if value is not None and str(value).strip().lower() not in ("", "null", "none", "-", "na"):
                return value
    return None


def registration_from(text):
    """The plate inside a descriptive asset name.

    Vendors label assets `32 TON BOLR MH14LX8206` or `TATA 1109 - MH43CK1211`;
    the plate is what matches an FMS vehicle, the rest is decoration.
    """
    match = _PLATE_RE.search(str(text or "").upper())
    return match.group().strip() if match else ""


def _to_float(value):
    """Coordinates and speeds arrive as floats, as strings, and as strings
    carrying a hemisphere suffix (`19.2967 N`) or thousands separators."""
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = str(value).strip().replace(",", "")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    number = float(match.group())
    if re.search(r"[SW]\s*$", text, re.IGNORECASE):
        number = -number
    return number


def _looks_like_gps_row(row):
    if not isinstance(row, dict):
        return False
    flat = _flatten(row)
    return any(k in flat for k in _LAT_KEYS) and any(k in flat for k in _LNG_KEYS)


def _looks_like_vehicle_row(row):
    if not isinstance(row, dict):
        return False
    flat = _flatten(row)
    return any(k in flat for k in _REG_KEYS)


def extract_rows(payload):
    """The most GPS-record-like list of dicts anywhere in `payload`.

    Scored rather than looked up by key, because the vendor nests the array
    differently depending on which dashboard build is answering. A list whose
    entries carry coordinates always beats one that only carries registrations,
    and among equals the longer list wins.
    """
    best, best_score = [], (0, 0, 0)

    def visit(node):
        nonlocal best, best_score
        if isinstance(node, list):
            dicts = [item for item in node if isinstance(item, dict)]
            if dicts:
                gps = sum(1 for item in dicts if _looks_like_gps_row(item))
                named = sum(1 for item in dicts if _looks_like_vehicle_row(item))
                score = (gps, named, len(dicts))
                if score > best_score and (gps or named):
                    best, best_score = dicts, score
            for item in node:
                visit(item)
        elif isinstance(node, dict):
            for value in node.values():
                visit(value)

    visit(payload)
    return best


def in_india(lat, lng):
    return (lat is not None and lng is not None
            and INDIA_LAT[0] <= lat <= INDIA_LAT[1]
            and INDIA_LNG[0] <= lng <= INDIA_LNG[1])


def _coordinates(flat):
    """Returns (lat, lng, swapped). Some feeds transpose the pair; when the
    values only make sense the other way round, use them the other way round
    rather than plotting the vehicle into the Indian Ocean.
    """
    lat = _to_float(_pick(flat, _LAT_KEYS))
    lng = _to_float(_pick(flat, _LNG_KEYS))

    # A single "19.2967,73.0631" field instead of two.
    if lat is not None and lng is None:
        parts = str(_pick(flat, _LAT_KEYS)).split(",")
        if len(parts) == 2:
            lat, lng = _to_float(parts[0]), _to_float(parts[1])

    if lat in (0.0, None) and lng in (0.0, None):
        return None, None, False          # null island - the device has no fix
    if not in_india(lat, lng) and in_india(lng, lat):
        return lng, lat, True
    return lat, lng, False


def _truthy(value):
    return str(value).strip().lower() in ("1", "true", "yes", "y", "on", "running", "ign_on")


def _analog(flat, *types):
    """A reading out of the `analogs` array, e.g. TEMPERATURE or BATTERY.

    On a reefer fleet the body temperature is the number the operator is
    actually watching, so it is lifted out of the array and onto the ping.
    """
    for entry in flat.get("analogs") or []:
        if isinstance(entry, dict) and str(entry.get("type", "")).upper() in types:
            value = _to_float(entry.get("value"))
            if value is not None:
                return value
    return None


def _digital(flat, *types):
    """A boolean input out of the `digitals` array - KEY is the ignition, AC is
    the reefer unit. The feed has no top-level `ignition` field; this is it."""
    for entry in flat.get("digitals") or []:
        if isinstance(entry, dict) and str(entry.get("type", "")).upper() in types:
            return bool(entry.get("state"))
    return None


def _fix_time(flat):
    """The feed times fixes in epoch milliseconds. Hand back ISO-8601 so the
    console can localise it, rather than printing 1786535659000 at the user."""
    raw = _pick(flat, _TIME_KEYS)
    number = _to_float(raw)
    if number and number > 1e11:                      # milliseconds
        number /= 1000
    if number and number > 1e8:                       # plausible epoch seconds
        try:
            return datetime.fromtimestamp(number, tz=dt_timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            pass
    return str(raw or "")


def _movement(text, speed, ignition):
    """Movement state from the vendor's own wording, falling back to telemetry."""
    words = str(text or "").lower()
    if any(w in words for w in _PARKED_WORDS):
        return "parked"
    if any(w in words for w in _IDLE_WORDS):
        return "idle"
    if any(w in words for w in _MOVING_WORDS):
        return "moving"
    if speed > MOVING_SPEED_KPH:
        return "moving"
    return "idle" if ignition else "parked"


def row_to_ping(row):
    """One vendor record -> the flat shape the console renders."""
    flat = _flatten(row)
    lat, lng, swapped = _coordinates(flat)
    speed = _to_float(_pick(flat, _SPEED_KEYS)) or 0.0

    # Ignition comes off the KEY digital input; only fall back to a named field
    # for feeds that do not send the digitals array.
    ignition = _digital(flat, "KEY", "IGNITION", "ACC")
    if ignition is None:
        raw_ignition = _pick(flat, _IGN_KEYS)
        ignition = _truthy(raw_ignition) if raw_ignition is not None else False

    vendor_state = _pick(flat, _STATUS_KEYS)
    gps_status = _movement(vendor_state, speed, ignition)

    # The asset name doubles as a description; keep both so the console can show
    # a clean plate as the heading and the vendor's own wording underneath.
    label = str(_pick(flat, _REG_KEYS) or "").strip()
    registration = registration_from(label) or label

    return {
        "reg": registration,
        "label": label if label != registration else "",
        "make": str(_pick(flat, _MAKE_KEYS) or "").strip(),
        "model": str(_pick(flat, _MODEL_KEYS) or "").strip(),
        "lat": lat,
        "lng": lng,
        "coords_swapped": swapped,
        "in_india": in_india(lat, lng),
        "speed_kph": round(speed, 1),
        "heading": _to_float(_pick(flat, _HEADING_KEYS)),
        "ignition": bool(ignition),
        "ac_on": _digital(flat, "AC"),
        "temperature_c": _analog(flat, "TEMPERATURE", "TEMP"),
        "battery_v": _analog(flat, "BATTERY", "VBATT"),
        "gps_status": gps_status,
        "state_text": str(vendor_state or "").strip(),
        "gps_time": _fix_time(flat),
        "driver": str(_pick(flat, _DRIVER_KEYS) or "").strip(),
        "address": str(_pick(flat, _ADDR_KEYS) or ""),
    }


def parse(payload):
    """Whole document -> list of pings, skipping rows with no identifier."""
    return [ping for ping in (row_to_ping(row) for row in extract_rows(payload)) if ping["reg"]]


def fetch_dashboard():
    """Returns (payload, error). Never raises - a telematics outage must not
    take the tracking console down with it.
    """
    request = urllib.request.Request(
        DASHBOARD_URL,
        data=b"{}",
        headers={
            "Authorization": f"Basic {BASIC_AUTH}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://www.geotrackers.com",
            "Referer": "https://www.geotrackers.com/GTWeb/TrackApp/TrackApp.jsp",
            "User-Agent": "Mozilla/5.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        # The body of an error response usually says far more than the status.
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        return None, f"HTTP {exc.code} from Geotrackers{': ' + detail if detail else ''}"
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"

    try:
        return json.loads(body), None
    except ValueError:
        return None, f"Geotrackers returned non-JSON: {body[:300].decode('utf-8', 'replace')}"
