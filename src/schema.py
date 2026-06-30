from __future__ import annotations

TABLE_NAME = "gaming_mental_health"

COLUMNS: dict[str, tuple[str, str]] = {
    "age": ("INTEGER", "respondent age in years"),
    "gender": ("TEXT", "male/female/other"),
    "income": ("INTEGER", "annual income"),
    "daily_gaming_hours": ("REAL", "avg hours gaming per day"),
    "weekly_sessions": ("INTEGER", "gaming sessions per week"),
    "years_gaming": ("INTEGER", "years spent gaming"),
    "sleep_hours": ("REAL", "avg hours of sleep per night"),
    "caffeine_intake": ("REAL", "caffeine servings per day"),
    "exercise_hours": ("REAL", "exercise hours per week"),
    "stress_level": ("INTEGER", "self-reported stress, higher = worse"),
    "anxiety_score": ("REAL", "self-reported anxiety, higher = worse"),
    "depression_score": ("REAL", "self-reported depression, higher = worse"),
    "social_interaction_score": ("REAL", "amount of social interaction, higher = more"),
    "relationship_satisfaction": ("REAL", "satisfaction with relationships"),
    "academic_performance": ("REAL", "academic performance score"),
    "work_productivity": ("REAL", "work productivity score"),
    "addiction_level": ("REAL", "gaming addiction score, higher = more addicted"),
    "multiplayer_ratio": ("REAL", "fraction of gaming time that is multiplayer"),
    "toxic_exposure": ("REAL", "exposure to toxic behaviour in-game"),
    "violent_games_ratio": ("REAL", "fraction of games played that are violent"),
    "mobile_gaming_ratio": ("REAL", "fraction of gaming done on mobile"),
    "night_gaming_ratio": ("REAL", "fraction of gaming done at night"),
    "weekend_gaming_hours": ("REAL", "gaming hours on weekends"),
    "friends_gaming_count": ("INTEGER", "number of friends who also game"),
    "online_friends": ("INTEGER", "number of online-only friends"),
    "streaming_hours": ("REAL", "hours spent watching game streams"),
    "esports_interest": ("INTEGER", "interest in esports, higher = more"),
    "headset_usage": ("INTEGER", "frequency of headset usage"),
    "microtransactions_spending": ("REAL", "money spent on microtransactions"),
    "parental_supervision": ("INTEGER", "level of parental supervision"),
    "loneliness_score": ("REAL", "self-reported loneliness, higher = worse"),
    "aggression_score": ("REAL", "self-reported aggression, higher = worse"),
    "happiness_score": ("REAL", "self-reported happiness, higher = better"),
    "bmi": ("REAL", "body mass index"),
    "screen_time_total": ("REAL", "total daily screen time, all devices"),
    "eye_strain_score": ("REAL", "self-reported eye strain"),
    "back_pain_score": ("REAL", "self-reported back pain"),
    "competitive_rank": ("INTEGER", "in-game competitive rank"),
    "internet_quality": ("INTEGER", "self-reported internet quality"),
}

ALLOWED_COLUMNS = set(COLUMNS.keys())


def compact_schema_text() -> str:
    lines = [f"Table: {TABLE_NAME}"]
    for name, (sqltype, desc) in COLUMNS.items():
        lines.append(f"- {name} ({sqltype}): {desc}")
    return "\n".join(lines)
