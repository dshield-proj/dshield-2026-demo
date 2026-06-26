"""
Generate daily dshield demo config JSON files.

Edit the parameters at the top of this script, then run:
    python generate_configs.py

Parameters:
    SIM_START_DATE           Simulation start date in YYYYMMDD format.
    N                        Number of daily config files to generate.
    CYGNSS_LATENCY           Latency of the Level-1 CYGNSS products
    FIRE_FORECAST_PERIOD     Days ahead of today for fire-forecast outputs
                             (fire_danger, pre_fire_priority).
    PLANNER_FORECAST_PERIOD  Days ahead of today for tasking outputs
                             (orbits, planner) and the planner's
                             orbits / pre_fire_priority inputs.

Output:
    Files are written relative to the script's directory:
      dshield-demo-configuration/dshield-demo-config-YYYYMMDD.json
      dshield-demo-configuration/soil-moisture-config/YYYYMMDD/sm_areas.json
      dshield-demo-configuration/planner_config/planner-config-YYYYMMDD.json
"""
import json
from datetime import datetime, timedelta
from pathlib import Path

# --- Parameters ---
SIM_START_DATE = "20260625"   # YYYYMMDD
N = 20                        # number of days to generate
CYGNSS_LATENCY = 5            # days
FIRE_FORECAST_PERIOD = 2      # days ahead for fire_danger / pre_fire_priority
PLANNER_FORECAST_PERIOD = 1   # days ahead for orbits / planner
# ------------------

# Static per-area definitions for sm_areas.json. The date-dependent fields
# (prod_start_date / prod_end_date) are filled in per day in generate_sm_areas().
SM_AREAS = [
    {
        "name": "NM",
        "area_id": 1,
        "lat_upper": 32.7526999999999973,
        "lat_lower": 32.2224999999999966,
        "lon_upper": -106.0737000000000023,
        "lon_lower": -107.1580000000000013,
        "output_filename": "soil_moisture_area_id_1.tif",
    },
    {
        "name": "TX_panhandle",
        "area_id": 2,
        "lat_upper": 36.5923980000000029,
        "lat_lower": 35.1442180000000022,
        "lon_upper": -99.3226980000000026,
        "lon_lower": -102.7631460000000061,
        "output_filename": "soil_moisture_area_id_2.tif",
    },
]


def generate_config(sim_start: datetime, today: datetime, day_number: int,
                    cygnss_latency: int, fire_forecast_period: int,
                    planner_forecast_period: int) -> dict:
    def fmt(d: datetime) -> str:
        return d.strftime("%Y%m%d")

    lagged     = today - timedelta(days=cygnss_latency)
    fire_ahead = today + timedelta(days=fire_forecast_period)
    plan_ahead = today + timedelta(days=planner_forecast_period)

    return {
        "scenario_info": {
            "scenario_id": "scenario1",
            "sim_execution_date": fmt(today),
            "today": fmt(today),
            "sim_start_date": fmt(sim_start),
            "sim_day_number": day_number,
        },
        "outputs": {
            "soil_moisture_config": f"/dshield-demo-configuration/soil-moisture-config/{fmt(today)}/",
            "soil_moisture":        f"/soil-moisture/output/{fmt(today)}/",
            "burned_area_config":   f"/dshield-demo-configuration/burned-area-config/{fmt(today)}/",
            "burned_area":          f"/burned-area/output/{fmt(today)}/",
            "fire_arrival":         f"/fire-arrival/output/{fmt(today)}/",
            "fire_danger":          f"/fire-danger/output/{fmt(today)}/",
            "pre_fire_priority":    f"/pre-fire-priority/output/{fmt(fire_ahead)}/",
            "active_fire_priority": f"/active-fire-priority/output/{fmt(today)}/",
            "orbits":               f"/orbits/output/{fmt(plan_ahead)}/",
            "planner":              f"/planner/output/{fmt(plan_ahead)}/",
        }
    }


def generate_sm_areas(today: datetime, cygnss_latency: int) -> list:
    """Build the sm_areas.json payload for a given day.

    prod_start_date is lagged (today - cygnss_latency) at 00:00:00 and
    prod_end_date is today at 23:59:59.
    """
    lagged = today - timedelta(days=cygnss_latency)
    prod_start = lagged.strftime("%Y-%m-%dT00:00:00")
    prod_end = today.strftime("%Y-%m-%dT23:59:59")
    return [
        {**area, "prod_start_date": prod_start, "prod_end_date": prod_end}
        for area in SM_AREAS
    ]


def generate_planner_config(today: datetime, planner_forecast_period: int) -> dict:
    """Build the planner config payload for a given day.

    inputs point at the folders where the orbits, active-fire-priority and
    pre-fire-priority products are written; outputs is the folder where the
    planner writes its results. All inputs and the planner output use the
    planner forecast horizon (today + period); note this pre_fire_priority
    input horizon differs from the pre_fire_priority *output* in
    generate_config(), which uses the fire forecast horizon.
    """
    def fmt(d: datetime) -> str:
        return d.strftime("%Y%m%d")

    plan_ahead = today + timedelta(days=planner_forecast_period)

    return {
        "inputs": {
            "orbits":               f"/orbits/output/{fmt(plan_ahead)}/",
            "active_fire_priority": f"/dshield-demo-configuration/active-fire-priority-proxy/{fmt(today)}/",
            "pre_fire_priority":    f"/pre-fire-priority/output/{fmt(plan_ahead)}/",
        },
        "outputs": {
            "planner": f"/planner/output/{fmt(plan_ahead)}/",
        },
    }


def main():
    sim_start = datetime.strptime(SIM_START_DATE, "%Y%m%d")
    out_dir = Path(__file__).parent / ".." / "dshield-demo-configuration"
    out_dir.mkdir(exist_ok=True)
    planner_dir = out_dir / "planner_config"
    planner_dir.mkdir(parents=True, exist_ok=True)

    for day_number in range(N):
        today = sim_start + timedelta(days=day_number)
        cfg = generate_config(sim_start, today, day_number, CYGNSS_LATENCY,
                              FIRE_FORECAST_PERIOD, PLANNER_FORECAST_PERIOD)
        filename = out_dir / f"dshield-demo-config-{today.strftime('%Y%m%d')}.json"
        with filename.open("w") as f:
            json.dump(cfg, f, indent=2)
        print(f"Wrote {filename.name}")

        # Per-day soil-moisture area config.
        sm_dir = out_dir / "soil-moisture-config" / today.strftime("%Y%m%d")
        sm_dir.mkdir(parents=True, exist_ok=True)
        sm_file = sm_dir / "sm_areas.json"
        with sm_file.open("w") as f:
            json.dump(generate_sm_areas(today, CYGNSS_LATENCY), f, indent=2)
        print(f"Wrote {sm_file.relative_to(out_dir)}")

        # Per-day planner config.
        planner_file = planner_dir / f"planner-config-{today.strftime('%Y%m%d')}.json"
        with planner_file.open("w") as f:
            json.dump(generate_planner_config(today, PLANNER_FORECAST_PERIOD), f, indent=2)
        print(f"Wrote {planner_file.relative_to(out_dir)}")

    print(f"\nGenerated {N} config file(s) in {out_dir}/")


if __name__ == "__main__":
    main()