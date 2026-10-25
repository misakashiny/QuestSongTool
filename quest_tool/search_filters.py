"""BeatSaver browse presets and validated official filter query parameters."""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from .downloads import DownloadError
from .search_catalog import TAGS, ENVIRONMENTS

MODES = ("Search", "Curated", "Week", "Month", "Year", "Latest", "Verified", "Ranked", "Random")
FLAGS = ("curated", "verified", "fullSpread", "chroma", "noodle", "me", "cinema", "vivify")
CHARACTERISTICS = ("Standard", "OneSaber", "NoArrows", "90Degree", "360Degree", "Lightshow", "Lawless", "Legacy")
RANGES = {"Nps": 1000, "Bpm": 10000, "Duration": 86400, "Rating": 100,
          "BlStars": 100, "SsStars": 100, "Votes": 10000000}


def filter_params(values: dict) -> dict:
    """Accept only known enums and finite, ordered bounds before requesting anything."""
    try:
        if not isinstance(values, dict):
            raise ValueError("filters")
        params = {}
        known = set(FLAGS) | {"automapper", "leaderboard", "tags", "environments", "characteristics", "from", "to"}
        known |= {bound + name for name in RANGES for bound in ("min", "max")}
        if set(values) - known:
            raise ValueError("unknown filter")
        for key in FLAGS:
            if values.get(key) not in (None, ""):
                if type(values[key]) is not bool:
                    raise ValueError(key)
                params[key] = str(values[key]).lower()
        ai = values.get("automapper", "")
        if ai not in ("", "all", "human", "ai"):
            raise ValueError("automapper")
        # BeatSaver search: omitted = human; true = both; false = AI only.
        if ai in ("", "all"):
            params["automapper"] = "true"
        elif ai == "ai":
            params["automapper"] = "false"
        leaderboard = values.get("leaderboard", "All")
        if leaderboard not in ("All", "Ranked", "BeatLeader", "ScoreSaber"):
            raise ValueError("leaderboard")
        if leaderboard != "All":
            params["leaderboard"] = leaderboard
        for key, allowed in (("environments", ENVIRONMENTS), ("characteristics", CHARACTERISTICS)):
            items = values.get(key, [])
            if not isinstance(items, (list, tuple)) or any(item not in allowed for item in items):
                raise ValueError(key)
            if items:
                params[key] = ",".join(dict.fromkeys(items))
        tags = values.get("tags", {})
        if not isinstance(tags, dict) or any(key not in TAGS or type(value) is not bool for key, value in tags.items()):
            raise ValueError("tags")
        if tags:
            params["tags"] = ",".join(("" if required else "!") + tag for tag, required in sorted(tags.items()))
        for name, limit in RANGES.items():
            bounds = {}
            for prefix in ("min", "max"):
                key = prefix + name
                if values.get(key) in (None, ""):
                    continue
                value = float(values[key])
                if not math.isfinite(value) or not 0 <= value <= limit:
                    raise ValueError(key)
                if name in ("Duration", "Votes") and value != int(value):
                    raise ValueError(key)
                bounds[prefix] = value
                normalized = value / 100 if name == "Rating" else value
                params[key] = f"{normalized:g}"
            if bounds.get("min", 0) > bounds.get("max", limit):
                raise ValueError(name)
        for key in ("from", "to"):
            value = values.get(key, "")
            if value:
                day = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                if day.strftime("%Y-%m-%d") != value:
                    raise ValueError(key)
                if key == "to":
                    day += timedelta(days=1)
                params[key] = day.isoformat().replace("+00:00", "Z")
        if values.get("from") and values.get("to") and values["from"] > values["to"]:
            raise ValueError("date")
        return params
    except (ValueError, TypeError, OverflowError) as exc:
        raise DownloadError("filters", str(exc)) from exc


def browse_params(mode, filters, now=None):
    if mode not in MODES:
        raise DownloadError("filters", "browse mode")
    params = filter_params(filters)
    order = None
    if mode in ("Week", "Month", "Year"):
        now = now or datetime.now(timezone.utc)
        days = {"Week": 7, "Month": 30, "Year": 365}[mode]
        start = (now - timedelta(days=days)).isoformat().replace("+00:00", "Z")
        # Apply both recommendation interval and user's date bounds.
        if "from" not in params or params["from"] < start:
            params["from"] = start
        order = "Rating"
    elif mode == "Curated":
        if params.get("curated") == "false":
            raise DownloadError("filters", "curated preset conflicts with filter")
        params["curated"] = "true"
        order = "Curated"
    elif mode == "Verified":
        if params.get("verified") == "false":
            raise DownloadError("filters", "verified preset conflicts with filter")
        params["verified"] = "true"
        order = "Latest"
    elif mode == "Ranked":
        params.setdefault("leaderboard", "Ranked")
        order = "Rating"
    elif mode in ("Latest", "Random"):
        order = mode
    if params.get("from") and params.get("to") and params["from"] >= params["to"]:
        raise DownloadError("filters", "recommendation date conflicts with filter")
    return params, order
