"""Generates grafana/provisioning/dashboards/fedsketch.json (python grafana/make_dashboard.py)."""
import json
from pathlib import Path

DS = {"type": "influxdb", "uid": "fedsketch-influx"}


def ts_panel(pid, title, query, x, y, w=12, h=9, unit=None, by_variant=True, stack=False):
    q = {"refId": "A", "datasource": DS, "rawQuery": True, "resultFormat": "time_series",
         "query": query + (' GROUP BY "variant"' if by_variant else ""), "alias": "$tag_variant" if by_variant else None}
    q = {k: v for k, v in q.items() if v is not None}
    defaults = {"custom": {"drawStyle": "line", "lineWidth": 2, "pointSize": 4, "showPoints": "always",
                           "spanNulls": True, "stacking": {"mode": "normal" if stack else "none"}}}
    if unit:
        defaults["unit"] = unit
    return {"id": pid, "type": "timeseries", "title": title, "datasource": DS,
            "gridPos": {"x": x, "y": y, "w": w, "h": h}, "targets": [q],
            "fieldConfig": {"defaults": defaults, "overrides": []},
            "options": {"legend": {"displayMode": "list", "placement": "bottom", "calcs": []},
                        "tooltip": {"mode": "multi"}}}


def stat_panel(pid, title, query, x, y, w=4, h=4, unit=None):
    p = {"id": pid, "type": "stat", "title": title, "datasource": DS, "gridPos": {"x": x, "y": y, "w": w, "h": h},
         "targets": [{"refId": "A", "datasource": DS, "rawQuery": True, "query": query, "resultFormat": "time_series"}],
         "options": {"reduceOptions": {"calcs": ["lastNotNull"]}, "colorMode": "value", "graphMode": "area"},
         "fieldConfig": {"defaults": {"unit": unit} if unit else {}, "overrides": []}}
    return p


panels = [
    stat_panel(1, "Rounds released", 'SELECT last("round")+1 FROM "server" WHERE $timeFilter', 0, 0),
    stat_panel(2, "Events ingested / s", 'SELECT last("events_per_s") FROM "ingest" WHERE $timeFilter', 4, 0, unit="short"),
    stat_panel(3, "Round latency", 'SELECT last("round_latency_s") FROM "server" WHERE $timeFilter', 8, 0, unit="s"),
    stat_panel(4, "Upload / round (all edges)", 'SELECT last("upload_bytes") FROM "server" WHERE $timeFilter', 12, 0, unit="bytes"),
    stat_panel(5, "Users / round", 'SELECT last("users") FROM "server" WHERE $timeFilter', 16, 0),
    stat_panel(6, "SecAgg unmask throughput (sketches/s)", 'SELECT last("agg_throughput_sketch_per_s") FROM "server" WHERE $timeFilter', 20, 0),
]
panels += [
    ts_panel(10, "Top-50 F1 vs sliding-window truth (7 rounds)",
             'SELECT mean("f1_win") FROM "quality" WHERE $timeFilter GROUP BY time($__interval), "variant" fill(none)', 0, 4, by_variant=False),
    ts_panel(11, "Top-50 F1 vs current-round truth (drift tracking)",
             'SELECT mean("f1_now") FROM "quality" WHERE $timeFilter GROUP BY time($__interval), "variant" fill(none)', 12, 4, by_variant=False),
    ts_panel(12, "Top-50 F1 vs cumulative truth",
             'SELECT mean("f1_cum") FROM "quality" WHERE $timeFilter GROUP BY time($__interval), "variant" fill(none)', 0, 13, by_variant=False),
    ts_panel(13, "Privacy spent per unit (epsilon)",
             'SELECT max("eps_spent") FROM "release" WHERE $timeFilter AND "eps_spent" >= 0 GROUP BY time($__interval), "variant" fill(none)', 12, 13, by_variant=False),
    ts_panel(14, "Upload per edge per round",
             'SELECT mean("bytes_per_edge") FROM "release" WHERE $timeFilter GROUP BY time($__interval), "variant" fill(none)', 0, 22, unit="bytes", by_variant=False),
    ts_panel(15, "Sketch width chosen (adaptive)",
             'SELECT mean("width") FROM "release" WHERE $timeFilter GROUP BY time($__interval), "variant" fill(none)', 12, 22, by_variant=False),
    ts_panel(16, "Drift detections",
             'SELECT sum("drift") FROM "release" WHERE $timeFilter GROUP BY time($__interval), "variant" fill(none)', 0, 31, by_variant=False),
    ts_panel(17, "Ingest rate (events/s) and round latency",
             'SELECT mean("events_per_s") FROM "ingest" WHERE $timeFilter GROUP BY time($__interval) fill(none)', 12, 31, by_variant=False),
]
for p in panels:
    for t in p["targets"]:
        if "GROUP BY time" in t["query"] and '"variant"' in t["query"]:
            t["alias"] = "$tag_variant"

dash = {"uid": "fedsketch", "title": "Federated Sketches - live streaming analytics", "schemaVersion": 39,
        "time": {"from": "now-15m", "to": "now"}, "refresh": "5s", "panels": panels, "tags": ["fedsketch"],
        "timezone": "browser", "editable": True, "version": 1}
out = Path(__file__).parent / "provisioning" / "dashboards" / "fedsketch.json"
out.write_text(json.dumps(dash, indent=2))
print("wrote", out)
