"""The network tools against a fake client that answers like run_installed_query.

The printed shapes below are the ones the installed queries really return
(captured from the live sandbox), cut down to small graphs whose expected
loops, pairs, fans and networks can be read off by eye.
"""

from typing import Any

import pytest

from autosentry_agent.mcp.transport import ToolResult
from autosentry_agent.tools import build_tools
from autosentry_agent.tools.network import circular_paths, fraud_networks, network_tools
from autosentry_agent.tools.tool_risk import Risk


def hop(start: str, frm: str, tx: str, amount: float, at: str, to: str, n: int) -> dict[str, Any]:
    return {"start_tx": start, "from_account": frm, "tx": tx, "amount": amount,
            "sent_at": f"2026-08-01 {at}", "to_account": to, "hop": n}


# a -> b -> c -> a keeps 95%; b also paid d, which went nowhere. A second
# loop, x -> y -> x, brings back only 40%.
LOOP_HOPS = [
    hop("t1", "a", "t1", 1000.0, "01:00:00", "b", 1),
    hop("t1", "b", "t2", 980.0, "03:00:00", "c", 2),
    hop("t1", "b", "t9", 500.0, "04:00:00", "d", 2),
    hop("t1", "c", "t3", 950.0, "05:00:00", "a", 3),
    hop("x1", "x", "x1", 1000.0, "01:00:00", "y", 1),
    hop("x1", "y", "x2", 400.0, "02:00:00", "x", 2),
]

PRINTED: dict[str, dict[str, Any]] = {
    "find_circular_flows": {"hops": LOOP_HOPS},
    "find_repeated_counterparties": {
        "pair_transfers": [
            {"from_account": "m1", "to_party": "boss", "to_type": "Account", "tx": f"c{i}", "amount": 500.0,
             "sent_at": f"2026-08-0{i} 10:00:00"} for i in (3, 1, 2)
        ] + [
            {"from_account": "m2", "to_party": "ben-1", "to_type": "Beneficiary", "tx": f"w{i}", "amount": 100.0,
             "sent_at": f"2026-08-0{i} 10:00:00"} for i in (1, 2, 3)
        ],
        "fans": [{"party": "funnel", "party_type": "Account", "direction": "in", "counterparties": 2,
                  "window_start": "2026-07-01 00:00:00"}],
        "fan_transfers": [
            {"from_account": "v1", "to_party": "funnel", "to_type": "Account", "tx": "in1", "amount": 300.0,
             "sent_at": "2026-07-01 00:00:00"},
            {"from_account": "v2", "to_party": "funnel", "to_type": "Account", "tx": "in2", "amount": 200.0,
             "sent_at": "2026-07-02 00:00:00"},
        ],
    },
    "find_connected_accounts": {
        "accounts": [{"account": "a", "distance": 0}, {"account": "b", "distance": 1},
                     {"account": "c", "distance": 2}],
        "links": [
            {"from_account": "a", "to_account": "b", "link": "transfer", "via": "t1", "distance": 1},
            {"from_account": "a", "to_account": "b", "link": "transfer", "via": "t2", "distance": 1},
            {"from_account": "b", "to_account": "a", "link": "shared_device", "via": "dev-1", "distance": 1},
            {"from_account": "a", "to_account": "b", "link": "shared_device", "via": "dev-1", "distance": 1},
            {"from_account": "b", "to_account": "c", "link": "same_owner", "via": "person-1", "distance": 2},
            {"from_account": "c", "to_account": "beyond-the-cap", "link": "transfer", "via": "t7", "distance": 2},
        ],
    },
    "find_fraud_networks": {
        # A ring (r1 -> r2 -> r3, with a device), r2's household partner
        # attached by a home device, and a household that moved no money.
        "links": [
            {"from_account": "r1", "to_account": "r2", "link": "pass_through", "tx": "p1"},
            {"from_account": "r2", "to_account": "r3", "link": "pass_through", "tx": "p2"},
            {"from_account": "r3", "to_account": "boss", "link": "repeated_payments", "tx": "rp1"},
        ],
        "device_accounts": {"dev-ring": ["r1", "r2", "r3"], "dev-home": ["r2", "partner"],
                            "dev-quiet": ["h1", "h2"]},
        "ip_accounts": {},
        "owner_accounts": {"person-h1": ["h1", "h1-b"]},
        "beneficiary_accounts": {},
        "cases": {"r1": ["case-1"]},
    },
}


class FakeClient:
    graph_name = "AutosentrySandbox"

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def call(self, tool: str, arguments: dict[str, Any]) -> ToolResult:
        self.calls.append(arguments)
        name = arguments["query_name"]
        return ToolResult(ok=True, summary="ok", data={"result": [PRINTED[name]]}, error=None)


def tools(client: FakeClient) -> dict:
    return {t.name: t for t in network_tools(client)}  # type: ignore[arg-type]


def test_loops_are_rebuilt_in_time_order_and_dead_ends_dropped():
    loops = circular_paths(LOOP_HOPS, min_retained=0.5)
    assert len(loops) == 1
    loop = loops[0]
    assert loop["accounts"] == ["a", "b", "c"] and loop["transactions"] == ["t1", "t2", "t3"]
    assert loop["retained"] == 0.95 and loop["hours"] == 4.0
    assert [lp["origin"] for lp in circular_paths(LOOP_HOPS, min_retained=0.3)] == ["a", "x"]


def test_a_hop_sent_before_the_money_arrived_does_not_continue_a_loop():
    early = [h if h["tx"] != "t3" else {**h, "sent_at": "2026-08-01 02:00:00"} for h in LOOP_HOPS]
    assert circular_paths(early, min_retained=0.5) == []


@pytest.mark.asyncio
async def test_circular_flows_sends_only_the_query_params_and_filters_by_account():
    client = FakeClient()
    result = await tools(client)["find_circular_flows"].invoke({"account": "c", "min_retained": 0.3})
    assert client.calls[0]["params"] == {"max_hops": 6, "window_days": 7}
    assert [lp["origin"] for lp in result["data"]["loops"]] == ["a"]
    assert "a -> b -> c -> a (95% back in 4.0h)" in result["summary"]
    assert {"type": "Transaction", "id": "t3"} in result["evidence"]


@pytest.mark.asyncio
async def test_repeated_pairs_and_fans_are_grouped_with_typed_payees():
    result = await tools(FakeClient())["find_repeated_counterparties"].invoke({})
    pairs = result["data"]["repeated_pairs"]
    to_boss = next(p for p in pairs if p.get("to_account") == "boss")
    assert to_boss["transactions"] == ["c1", "c2", "c3"] and to_boss["total"] == 1500.0
    to_ben = next(p for p in pairs if "to_beneficiary" in p)
    assert to_ben["to_beneficiary"] == "ben-1" and "to_account" not in to_ben
    fan = result["data"]["fans"][0]
    assert fan["to_account"] == "funnel" and fan["senders"] == ["v1", "v2"] and fan["total"] == 500.0
    assert {"type": "Beneficiary", "id": "ben-1"} in result["evidence"]
    assert {"type": "Account", "id": "v2"} in result["evidence"]


@pytest.mark.asyncio
async def test_repeated_counterparties_filter_by_account():
    result = await tools(FakeClient())["find_repeated_counterparties"].invoke({"account": "v1"})
    assert result["data"]["repeated_pairs"] == [] and len(result["data"]["fans"]) == 1


@pytest.mark.asyncio
async def test_connected_accounts_merge_links_and_drop_ones_past_the_cap():
    result = await tools(FakeClient())["find_connected_accounts"].invoke({"account": "a"})
    data = result["data"]
    assert [(a["account"], a["distance"]) for a in data["accounts"]] == [("b", 1), ("c", 2)]
    assert data["accounts"][0]["links"] == ["same_owner", "shared_device", "transfer"]
    transfer = next(lk for lk in data["links"] if lk["link"] == "transfer")
    assert transfer["transactions"] == ["t1", "t2"]
    assert [lk["devices"] for lk in data["links"] if lk["link"] == "shared_device"] == [["dev-1"]]
    assert all("beyond-the-cap" not in (lk["from_account"], lk["to_account"]) for lk in data["links"])
    assert {"type": "Person", "id": "person-1"} in result["evidence"]


def test_networks_need_money_and_keep_attached_accounts_out_of_the_core():
    networks = fraud_networks(PRINTED["find_fraud_networks"], min_size=3)
    assert len(networks) == 1  # the quiet household moved no money
    net = networks[0]
    assert net["accounts"] == ["boss", "partner", "r1", "r2", "r3"]
    assert net["core_accounts"] == ["boss", "r1", "r2", "r3"]
    assert net["devices"] == ["dev-home", "dev-ring"]
    assert net["under_investigation"] == [{"account": "r1", "cases": ["case-1"]}]
    assert net["pass_through_transactions"] == ["p1", "p2"]


@pytest.mark.asyncio
async def test_fraud_networks_tool_passes_query_params_and_cites_cases():
    client = FakeClient()
    result = await tools(client)["find_fraud_networks"].invoke({"min_size": 2})
    assert "min_size" not in client.calls[0]["params"] and "top_k" not in client.calls[0]["params"]
    assert {"type": "InvestigationCase", "id": "case-1"} in result["evidence"]
    assert "1 include an account under investigation" in result["summary"]


@pytest.mark.asyncio
async def test_invalid_arguments_are_refused_before_tigergraph():
    client = FakeClient()
    result = await tools(client)["find_connected_accounts"].invoke({"account": "a", "max_hops": 9})
    assert result["ok"] is False and "max_hops" in result["error"] and client.calls == []


def test_every_network_tool_is_a_read_and_offered_to_the_agent():
    client = FakeClient()
    offered = {t.name for t in build_tools(client)}  # type: ignore[arg-type]
    for tool in network_tools(client):  # type: ignore[arg-type]
        assert tool.risk is Risk.READ and tool.name in offered
        assert tool.description and tool.input_schema()["type"] == "object"
