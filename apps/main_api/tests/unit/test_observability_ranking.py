"""O ranking Redis compara chamadas e tokens sem esconder valores desconhecidos."""

from scripts.observability.rank_usage_24h import _rank_users


def test_ranking_orders_users_by_calls_and_tokens():
    users = [
        {"user_id": "user-a", "calls": 2, "tokens": 400},
        {"user_id": "user-b", "calls": 5, "tokens": 100},
        {"user_id": "user-c", "calls": 1, "tokens": 900},
    ]

    rankings = _rank_users(users)

    assert [row["user_id"] for row in rankings["by_calls"]] == [
        "user-b",
        "user-a",
        "user-c",
    ]
    assert [row["user_id"] for row in rankings["by_tokens"]] == [
        "user-c",
        "user-a",
        "user-b",
    ]
