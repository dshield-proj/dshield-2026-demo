"""
Generate daily dshield demo config JSON files.

Edit the parameters at the top of this script, then run:
    python generate_configs.py

Parameters:
    SIM_START_DATE      Simulation start date in YYYYMMDD format.
    N                   Number of daily config files to generate.
    CYGNSS_LATENCY      Latency of the Level-1 CYGNSS products
    FORECAST_PERIOD     Days ahead of today for forecast outputs
                        (pre_fire_priority, active_fire_priority, orbits, planner).

Output:
    Files are written to dshield-demo-configuration/dshield-demo-config-YYYYMMDD.json
    relative to the script's directory.
"""
import json
from datetime import datetime, timedelta
from pathlib import Path

# --- Parameters ---
SIM_START_DATE = "20260527"   # YYYYMMDD
N = 25                        # number of days to generate
CYGNSS_LATENCY = 3            # days
FORECAST_PERIOD = 2           # days
# ------------------


def generate_config(sim_start: datetime, today: datetime, day_number: int,
                    cygnss_latency: int, forecast_period: int) -> dict:
    def fmt(d: datetime) -> str:
        return d.strftime("%Y%m%d")

    lagged = today - timedelta(days=cygnss_latency)
    ahead  = today + timedelta(days=forecast_period)

    return {
        "scenario_info": {
            "scenario_id": "scenario1",
            "sim_execution_date": fmt(today),
            "today": fmt(today),
            "sim_start_date": fmt(sim_start),
            "sim_day_number": day_number,
        },
        "outputs": {
            "soil_moisture":        f"/soil-moisture/output/{fmt(lagged)}/",
            "burned_area":          f"/burned-area/output/{fmt(lagged)}/",
            "fire_arrival":         f"/fire-arrival/output/{fmt(lagged)}/",
            "fire_danger":          f"/fire-danger/output/{fmt(today)}/",
            "pre_fire_priority":    f"/pre-fire-priority/output/{fmt(ahead)}/",
            "active_fire_priority": f"/active-fire-priority/output/{fmt(ahead)}/",
            "orbits":               f"/orbits/output/{fmt(ahead)}/",
            "planner":              f"/planner/output/{fmt(ahead)}/",
        },
        "fallback_outputs": {
            "soil_moisture":        "/soil-moisture/sample/output/",
            "burned_area":          "/burned-area/sample/output/",
            "fire_arrival":         "/fire-arrival/sample/output/",
            "fire_danger":          "/fire-danger/sample/output/",
            "pre_fire_priority":    "/pre-fire-priority/sample/output/",
            "active_fire_priority": "/active-fire-priority/sample/output/",
            "orbits":               "/orbits/sample/output/",
            "planner":              "/planner/sample/output/",
        },
    }


def main():
    sim_start = datetime.strptime(SIM_START_DATE, "%Y%m%d")
    out_dir = Path(__file__).parent / "dshield-demo-configuration"
    out_dir.mkdir(exist_ok=True)

    for day_number in range(N):
        today = sim_start + timedelta(days=day_number)
        cfg = generate_config(sim_start, today, day_number,
                              CYGNSS_LATENCY, FORECAST_PERIOD)
        filename = out_dir / f"dshield-demo-config-{today.strftime('%Y%m%d')}.json"
        with filename.open("w") as f:
            json.dump(cfg, f, indent=2)
        print(f"Wrote {filename.name}")

    print(f"\nGenerated {N} config file(s) in {out_dir}/")


if __name__ == "__main__":
    main()
