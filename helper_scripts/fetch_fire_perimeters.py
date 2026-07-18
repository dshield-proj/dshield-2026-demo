#!/usr/bin/env python3
"""Fetch daily NIFC WFIGS perimeters for the burned-area-config fires.

Rebuilds daily_fire_perimeters/YYYYMMDD/<Fire>.geojson for the analysis
window. For each date the perimeter is the *latest* record whose
poly_DateCurrent falls at or before the end of that UTC day; when no new
record exists for a date the previous one is carried forward and tagged
(properties.carried_forward / days_stale). Fires with no polygon in the
perimeter service (Rookery) get their WFIGS incident point instead.

Source: WFIGS_Daily_Perimeters_Public (services3.arcgis.com/T4QMspbfLg3qTGWY),
matched by IRWIN id. Stdlib only.
"""
import csv
import datetime as dt
import glob
import json
import os
import sys
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_GLOB = os.path.join(ROOT, "dshield-demo-configuration", "burned-area-config", "*", "fires.json")
OUT_DIR = os.path.join(ROOT, "daily_fire_perimeters")

ARCGIS = "https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services"
PERIM_SERVICE = "WFIGS_Daily_Perimeters_Public"
POINT_SERVICES = ["WFIGS_Incident_Locations", "WFIGS_Incident_Locations_YearToDate"]

DAY0 = dt.date(2026, 6, 29)
DAY1 = dt.date(2026, 7, 10)


def arcgis_query(service, params):
    url = f"{ARCGIS}/{service}/FeatureServer/0/query?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=60) as r:
        data = json.load(r)
    if "error" in data:
        raise RuntimeError(f"{service}: {data['error']}")
    return data


def load_fires():
    fires = {}
    for path in sorted(glob.glob(CONFIG_GLOB)):
        for f in json.load(open(path)):
            fires.setdefault(f["irwin_id"], f["name"])
    return fires


def fetch_perimeters(irwin):
    """All perimeter records for one IRWIN id, as GeoJSON features sorted by poly_DateCurrent."""
    where = f"UPPER(attr_IrwinID)='{irwin.upper()}' OR UPPER(poly_IRWINID)='{irwin.upper()}'"
    data = arcgis_query(PERIM_SERVICE, {
        "where": where,
        "outFields": "poly_IncidentName,poly_DateCurrent,poly_PolygonDateTime,"
                     "poly_GISAcres,attr_IrwinID,poly_IRWINID",
        "returnGeometry": "true",
        "outSR": 4326,
        "geometryPrecision": 6,
        "f": "geojson",
        "resultRecordCount": 2000,
    })
    feats = [f for f in data.get("features", []) if f["properties"].get("poly_DateCurrent")]
    feats.sort(key=lambda f: f["properties"]["poly_DateCurrent"])
    return feats


def fetch_incident_point(irwin):
    for service in POINT_SERVICES:
        for field in ("attr_IrwinID", "IrwinID"):
            try:
                data = arcgis_query(service, {
                    "where": f"UPPER({field})='{irwin.upper()}'",
                    "outFields": "*",
                    "returnGeometry": "true",
                    "outSR": 4326,
                    "f": "geojson",
                })
            except Exception:
                continue
            if data.get("features"):
                return data["features"][0], service
    return None, None


def ms_to_iso(ms):
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).isoformat()


def main():
    fires = load_fires()
    dates = [DAY0 + dt.timedelta(days=i) for i in range((DAY1 - DAY0).days + 1)]
    manifest = []

    for irwin, name in fires.items():
        feats = fetch_perimeters(irwin)
        point = None
        if not feats:
            point, pt_service = fetch_incident_point(irwin)
            print(f"{name}: no perimeter records; incident point "
                  f"{'found in ' + pt_service if point else 'NOT FOUND'}", file=sys.stderr)
        else:
            print(f"{name}: {len(feats)} perimeter records "
                  f"({ms_to_iso(feats[0]['properties']['poly_DateCurrent'])[:10]} .. "
                  f"{ms_to_iso(feats[-1]['properties']['poly_DateCurrent'])[:10]})", file=sys.stderr)

        for date in dates:
            day_dir = os.path.join(OUT_DIR, date.strftime("%Y%m%d"))
            os.makedirs(day_dir, exist_ok=True)
            end_of_day = dt.datetime.combine(date, dt.time(23, 59, 59, 999000), dt.timezone.utc)
            end_ms = end_of_day.timestamp() * 1000

            if point is not None:
                out = dict(point)
                props = dict(out.get("properties") or {})
                props.update({"fire": name, "irwin_id": irwin, "point_only": True,
                              "source_service": pt_service})
                out["properties"] = props
                path = os.path.join(day_dir, f"{name}_incident_point.geojson")
                json.dump({"type": "FeatureCollection", "features": [out]}, open(path, "w"))
                manifest.append([date.strftime("%Y%m%d"), name, "point_only", "", "", "",
                                 os.path.basename(path)])
                continue

            chosen = None
            for f in feats:
                if f["properties"]["poly_DateCurrent"] <= end_ms:
                    chosen = f
                else:
                    break
            if chosen is None:
                manifest.append([date.strftime("%Y%m%d"), name, "no_perimeter_yet", "", "", "", ""])
                continue

            cur_ms = chosen["properties"]["poly_DateCurrent"]
            cur_date = dt.datetime.fromtimestamp(cur_ms / 1000, dt.timezone.utc).date()
            days_stale = (date - cur_date).days
            carried = days_stale > 0
            out = {"type": "Feature", "geometry": chosen["geometry"], "properties": {
                "fire": name,
                "irwin_id": irwin,
                "poly_IncidentName": chosen["properties"].get("poly_IncidentName"),
                "poly_DateCurrent": ms_to_iso(cur_ms),
                "poly_GISAcres": chosen["properties"].get("poly_GISAcres"),
                "carried_forward": carried,
                "days_stale": days_stale,
                "source_service": PERIM_SERVICE,
            }}
            path = os.path.join(day_dir, f"{name}.geojson")
            json.dump({"type": "FeatureCollection", "features": [out]}, open(path, "w"))
            manifest.append([date.strftime("%Y%m%d"), name,
                             "carried_forward" if carried else "current",
                             ms_to_iso(cur_ms), days_stale,
                             round(chosen["properties"].get("poly_GISAcres") or 0, 1),
                             os.path.basename(path)])

    manifest.sort()
    with open(os.path.join(OUT_DIR, "manifest.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "fire", "status", "perimeter_date_current", "days_stale",
                    "gis_acres", "file"])
        w.writerows(manifest)
    print(f"wrote {len(manifest)} manifest rows -> {OUT_DIR}", file=sys.stderr)


if __name__ == "__main__":
    main()
