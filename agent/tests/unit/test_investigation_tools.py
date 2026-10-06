"""The investigation tools against a fake client that answers like run_installed_query.

The printed shapes below are the ones the installed queries really return
(captured from the live sandbox): tuples as objects, VERTEX fields as bare
ids, MapAccum<VERTEX, ...> as objects keyed by vertex id.
"""

from typing import Any

import pytest

from autosentry_agent.mcp.transport import GraphDataToolError, ToolResult
from autosentry_agent.tools.investigation import investigation_tools
from autosentry_agent.tools.results import collect_evidence
from autosentry_agent.tools.tool_risk import Risk, classify

PRINTED: dict[str, dict[str, Any]] = {
    "trace_fund_transfer_chain": {
        "hops": [
            {"from_account": "account-b", "tx": "tx-2", "amount": 950.0, "sent_at": "2026-08-01 11:00:00", "to_account": "account-c", "hop": 2},
            {"from_account": "account-a", "tx": "tx-1", "amount": 1000.0, "sent_at": "2026-08-01 10:00:00", "to_account": "account-b", "hop": 1},
        ],
        "cash_outs": [
            {"from_account": "account-c", "tx": "tx-3", "amount": 900.0, "sent_at": "2026-08-01 12:00:00", "beneficiary": "beneficiary-1", "hop": 3},
        ],
    },
    "find_shared_infrastructure": {
        "devices": [
            {"device": "device-own", "user_count": 1, "first_seen_at": "2024-01-01 00:00:00"},
            {"device": "device-ring", "user_count": 3, "first_seen_at": "2026-06-15 11:37:46"},
        ],
        "ips": [{"ip": "198.51.100.4", "user_count": 3, "is_vpn": True, "country_code": "NL"}],
        "accounts_by_device": {"device-ring": ["account-z", "account-y"]},
        "accounts_by_ip": {"198.51.100.4": ["account-y", "account-z"]},
    },
    "find_structuring_pattern": {
        "clusters": [
            {"account": "account-s", "max_in_window": 3, "window_start": "2026-07-18 19:49:25", "near_threshold_count": 6, "near_threshold_total": 5960.0},
            {"account": "account-t", "max_in_window": 7, "window_start": "2026-07-28 12:15:05", "near_threshold_count": 7, "near_threshold_total": 6963.0},
        ],
        "deposits_by_account": {
            "account-t": [
                {"tx": "st-2", "amount": 996.2, "made_at": "2026-07-29 03:00:37", "channel": "cash_deposit"},
                {"tx": "st-1", "amount": 991.0, "made_at": "2026-07-28 12:15:05", "channel": "cash_deposit"},
            ],
            "account-s": [{"tx": "st-9", "amount": 993.0, "made_at": "2026-07-18 19:49:25", "channel": "cash_deposit"}],
        },
    },
    "find_mule_candidates": {
        "candidates": [
            {"account": "account-m1", "score": 10, "kyc_score": 0.17, "open_date": "2026-07-01 00:00:00"},
            {"account": "account-m2", "score": 5, "kyc_score": 0.2, "open_date": "2026-07-02 00:00:00"},
        ],
        "signals": {
            "account-m1": ["rapid_forwarding", "cash_out", "shared_device"],
            "account-m2": ["shared_device", "low_kyc"],
            "account-other": ["shared_ip", "new_account", "low_kyc"],
        },
        "forwarded_transactions": {"account-m1": ["tx-f2", "tx-f1"]},
        "cash_out_transactions": {"account-m1": ["tx-c"]},
        "near_threshold_transactions": {},
        "shared_devices": {"account-m1": ["device-ring"], "account-m2": ["device-ring"]},
        "shared_ips": {},
    },
}


class FakeClient:
    graph_name = "AutosentrySandbox"

    def __init__(self, fail: str | None = None, raise_unreachable: bool = False) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail = fail
        self.raise_unreachable = raise_unreachable

    async def call(self, tool: str, arguments: dict[str, Any]) -> ToolResult:
        self.calls.append((tool, arguments))
        if self.raise_unreachable:
            raise GraphDataToolError("could not reach tigergraph-mcp")
        name = arguments["query_name"]
        if name == self.fail:
            return ToolResult(ok=False, summary="", data=None, error=f"Query {name} is not installed")
        return ToolResult(ok=True, summary="ok", data={"query_name": name, "result": [PRINTED[name]]}, error=None)


def tools(client: FakeClient) -> dict:
    return {t.name: t for t in investigation_tools(client)}  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_trace_orders_hops_and_names_the_cash_out():
    client = FakeClient()
    result = await tools(client)["trace_fund_transfer_chain"].invoke({"account": "account-a"})

    assert result["ok"] is True
    assert [h["tx"] for h in result["data"]["hops"]] == ["tx-1", "tx-2"]
    assert "2 onward transfer(s) reaching 2 account(s), 1 cash-out(s) to beneficiary-1" in result["summary"]
    assert {"type": "Beneficiary", "id": "beneficiary-1"} in result["evidence"]
    assert {"type": "Transaction", "id": "tx-3"} in result["evidence"]
    tool, args = client.calls[0]
    assert tool == "tigergraph__run_installed_query"
    assert args == {
        "graph_name": "AutosentrySandbox",
        "query_name": "trace_fund_transfer_chain",
        "params": {"account": "account-a", "max_hops": 6, "window_hours": 24, "min_forward_ratio": 0.8},
    }


@pytest.mark.asyncio
async def test_shared_infrastructure_attaches_other_accounts_and_ranks_by_sharing():
    result = await tools(FakeClient())["find_shared_infrastructure"].invoke({"account": "account-x"})
    devices = result["data"]["devices"]
    assert [d["device"] for d in devices] == ["device-ring", "device-own"]
    assert devices[0]["other_accounts"] == ["account-y", "account-z"] and devices[1]["other_accounts"] == []
    assert "shared with other accounts: device-ring, 198.51.100.4" in result["summary"]
    assert {"type": "IPAddress", "id": "198.51.100.4"} in result["evidence"]
    assert {"type": "Account", "id": "account-y"} in result["evidence"]


@pytest.mark.asyncio
async def test_structuring_joins_deposits_to_clusters_in_time_order():
    result = await tools(FakeClient())["find_structuring_pattern"].invoke({})
    clusters = result["data"]["clusters"]
    assert [c["account"] for c in clusters] == ["account-t", "account-s"]
    assert [d["tx"] for d in clusters[0]["transactions"]] == ["st-1", "st-2"]
    assert {"type": "Transaction", "id": "st-9"} in result["evidence"]


@pytest.mark.asyncio
async def test_mule_candidates_carry_their_own_signals_and_evidence_only():
    result = await tools(FakeClient())["find_mule_candidates"].invoke({"top_k": 2})
    first, second = result["data"]["candidates"]
    assert first["signals"] == ["rapid_forwarding", "cash_out", "shared_device"]
    assert first["forwarded_transactions"] == ["tx-f1", "tx-f2"]
    assert second["cash_out_transactions"] == []
    ids = {e["id"] for e in result["evidence"]}
    assert "account-other" not in ids  # scored but not in the returned top
    assert {"account-m1", "tx-f1", "tx-c", "device-ring"} <= ids
    assert "highest account-m1 (score 10: rapid_forwarding, cash_out, shared_device)" in result["summary"]


@pytest.mark.asyncio
async def test_mule_candidates_can_score_one_account_whatever_its_score():
    client = FakeClient()
    result = await tools(client)["find_mule_candidates"].invoke({"account": "account-m1"})
    params = client.calls[0][1]["params"]
    assert params["accounts"] == ["account-m1"] and params["min_score"] == 0 and "account" not in params
    assert result["summary"] == "account-m1 scores 10 of 16 on mule signals: rapid_forwarding, cash_out, shared_device"

    await tools(client)["find_mule_candidates"].invoke({})
    assert client.calls[1][1]["params"]["accounts"] == [] and client.calls[1][1]["params"]["min_score"] == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool", "arguments", "problem"),
    [
        ("trace_fund_transfer_chain", {}, "account: Field required"),
        ("trace_fund_transfer_chain", {"account": "acc'; DROP"}, "account: String should match pattern"),
        ("trace_fund_transfer_chain", {"account": "a", "max_hops": 50}, "max_hops"),
        ("find_mule_candidates", {"top_k": 0}, "top_k"),
        ("find_mule_candidates", {"surprise": 1}, "surprise: Extra inputs are not permitted"),
        ("find_structuring_pattern", {"threshold": 100, "margin": 200}, "margin must be smaller than threshold"),
    ],
)
async def test_invalid_arguments_are_refused_before_tigergraph(tool, arguments, problem):
    client = FakeClient()
    result = await tools(client)[tool].invoke(arguments)
    assert result["ok"] is False and problem in result["error"]
    assert client.calls == []


@pytest.mark.asyncio
async def test_a_query_failure_comes_back_as_not_ok():
    result = await tools(FakeClient(fail="find_structuring_pattern"))["find_structuring_pattern"].invoke({})
    assert result["ok"] is False and "not installed" in result["error"]


@pytest.mark.asyncio
async def test_an_unreachable_server_comes_back_as_not_ok():
    result = await tools(FakeClient(raise_unreachable=True))["find_mule_candidates"].invoke({})
    assert result["ok"] is False and "could not reach" in result["error"]


def test_every_investigation_tool_is_a_read_with_a_schema():
    for tool in investigation_tools(FakeClient()):  # type: ignore[arg-type]
        assert tool.risk is Risk.READ
        assert tool.description and tool.input_schema()["type"] == "object"


@pytest.mark.parametrize(
    ("tool", "risk"),
    [
        ("find_mule_candidates", Risk.READ),
        ("run_graph_query", Risk.READ),
        ("tigergraph__get_graph_schema", Risk.READ),
        ("tigergraph__run_installed_query", Risk.READ),
        ("tigergraph__run_query", Risk.WRITE),  # unguarded GSQL
        ("tigergraph__add_nodes", Risk.WRITE),
        ("tigergraph__install_query", Risk.WRITE),
        ("tigergraph__gsql", Risk.DESTRUCTIVE),
        ("tigergraph__clear_graph_data", Risk.DESTRUCTIVE),
        ("tigergraph__delete_node", Risk.DESTRUCTIVE),
        ("tigergraph__something_new", Risk.WRITE),  # unknown fails closed
        ("mystery_tool", Risk.WRITE),
    ],
)
def test_risk_table(tool, risk):
    assert classify(tool) is risk


def test_collect_evidence_reads_vertex_sets_and_mapped_fields_once():
    value = {
        "rows": [{"from_account": "a1", "tx": "t1"}, {"from_account": "a1", "tx": "t2"}],
        "set": [{"v_id": "d1", "v_type": "Device", "attributes": {}}],
        "accounts": ["a2", "a1"],
    }
    evidence = collect_evidence(value, {"from_account": "Account", "tx": "Transaction", "accounts": "Account"})
    assert evidence == [
        {"type": "Account", "id": "a1"},
        {"type": "Transaction", "id": "t1"},
        {"type": "Transaction", "id": "t2"},
        {"type": "Device", "id": "d1"},
        {"type": "Account", "id": "a2"},
    ]
