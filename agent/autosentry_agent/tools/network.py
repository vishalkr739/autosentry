"""Network-level investigation tools: loops, repeated counterparties, the
accounts around one account, and the networks across the whole graph.

The installed queries find the raw facts (every hop of a loop's search,
every transfer of a repeated pair, every link between accounts); these
tools turn them into the records an investigator reads: one loop with its
path and how much came back, one pair with its transfers, one network
with its members and what ties them together.
"""

from collections import defaultdict
from datetime import datetime
from typing import Any

from pydantic import Field

from ..mcp.transport import GraphDataMcpClient
from .base import ToolSpec
from .investigation import AccountId, _Input, _result, _run
from .results import InvestigationResult, failure

# How each kind of link is evidenced, and the vertex type of that evidence.
VIA = {
    "transfer": ("transactions", "Transaction"),
    "pass_through": ("transactions", "Transaction"),
    "repeated_payments": ("transactions", "Transaction"),
    "same_owner": ("people", "Person"),
    "shared_device": ("devices", "Device"),
    "shared_ip": ("ips", "IPAddress"),
    "same_beneficiary": ("beneficiaries", "Beneficiary"),
}
MONEY_LINKS = ("pass_through", "repeated_payments")
_EVIDENCE_FIELDS = {
    "account": "Account",
    "accounts": "Account",
    "core_accounts": "Account",
    "origin": "Account",
    "from_account": "Account",
    "to_account": "Account",
    "senders": "Account",
    "payees": "Account",
    "to_beneficiary": "Beneficiary",
    "cases": "InvestigationCase",
    "pass_through_transactions": "Transaction",
    "repeated_payment_transactions": "Transaction",
    **{key: vertex_type for key, vertex_type in VIA.values()},
}


class FindCircularFlowsInput(_Input):
    account: AccountId | None = Field(None, description="Only loops this account is part of; omit for all")
    max_hops: int = Field(6, ge=2, le=10, description="Longest loop, in transfers")
    window_days: int = Field(7, ge=1, le=30, description="The money must come back within this many days")
    min_retained: float = Field(
        0.5, ge=0.0, le=1.0, description="Minimum share of the first transfer's amount that comes back"
    )
    max_loops: int = Field(25, ge=1, le=100, description="Cap on loops returned")


class FindRepeatedCounterpartiesInput(_Input):
    account: AccountId | None = Field(None, description="Only pairs and fans involving this account; omit for all")
    min_transfers: int = Field(3, ge=2, le=20, description="Transfers to one payee that make a pair repeated")
    pair_window_days: int = Field(30, ge=1, le=90, description="...all within this many days")
    min_counterparties: int = Field(5, ge=2, le=50, description="Distinct senders (or payees) that make a fan")
    fan_window_days: int = Field(7, ge=1, le=30, description="...all within this many days")


class FindConnectedAccountsInput(_Input):
    account: AccountId
    max_hops: int = Field(2, ge=1, le=3, description="How many links away to look")
    max_shared_users: int = Field(
        10, ge=2, le=50, description="A device or IP used by more people than this is public, not a link"
    )
    max_accounts: int = Field(50, ge=2, le=200, description="Cap on accounts returned")


class FindFraudNetworksInput(_Input):
    min_size: int = Field(3, ge=2, le=50, description="Smallest network, in accounts")
    top_k: int = Field(10, ge=1, le=50, description="How many networks to return, strongest first")
    window_hours: int = Field(24, ge=1, le=168, description="How soon a pass-through must follow a receipt")
    min_forward_ratio: float = Field(0.8, ge=0.5, le=1.0, description="Share of a receipt a pass-through forwards")
    max_shared_users: int = Field(10, ge=2, le=50, description="Devices or IPs used by more people are public")
    min_transfers: int = Field(3, ge=2, le=20, description="Transfers to one payee that make payments repeated")
    pair_window_days: int = Field(30, ge=1, le=90, description="...all within this many days")


def _at(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _hours(first: str, last: str) -> float:
    return round((_at(last) - _at(first)).total_seconds() / 3600, 1)


def circular_paths(hops: list[dict[str, Any]], min_retained: float) -> list[dict[str, Any]]:
    """Rebuild each loop's time-ordered paths from the hops of its search.

    A search's hops form a tree of possible onward transfers; a loop is a
    path through them that starts with the start transfer, never revisits
    an account, and ends back at the account it started from.
    """
    by_start: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for hop in hops:
        by_start[hop["start_tx"]].append(hop)

    loops: dict[tuple[str, ...], dict[str, Any]] = {}
    for start_tx, start_hops in by_start.items():
        first = next((h for h in start_hops if h["hop"] == 1 and h["tx"] == start_tx), None)
        if first is None:
            continue
        origin = first["from_account"]

        def walk(path: list[dict[str, Any]]) -> None:
            last = path[-1]
            if last["to_account"] == origin:
                retained = last["amount"] / first["amount"] if first["amount"] else 0.0
                key = tuple(h["tx"] for h in path)
                if retained >= min_retained and key not in loops:
                    loops[key] = {
                        "origin": origin,
                        "accounts": [h["from_account"] for h in path],
                        "transactions": list(key),
                        "amount_out": first["amount"],
                        "amount_back": last["amount"],
                        "retained": round(retained, 3),
                        "started_at": first["sent_at"],
                        "hours": _hours(first["sent_at"], last["sent_at"]),
                    }
                return
            seen = {h["from_account"] for h in path}
            for nxt in start_hops:
                if (nxt["hop"] == last["hop"] + 1 and nxt["from_account"] == last["to_account"]
                        and nxt["sent_at"] >= last["sent_at"]
                        and (nxt["to_account"] == origin or nxt["to_account"] not in seen)):
                    walk([*path, nxt])

        walk([first])
    return sorted(loops.values(), key=lambda loop: (-loop["retained"], loop["hours"]))


def _payee_fields(party: str, party_type: str) -> dict[str, str]:
    return {"to_beneficiary": party} if party_type == "Beneficiary" else {"to_account": party}


def repeated_pairs(transfers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for t in transfers:
        grouped[(t["from_account"], t["to_party"])].append(t)
    pairs = []
    for (sender, payee), txs in grouped.items():
        txs.sort(key=lambda t: t["sent_at"])
        pairs.append({
            "from_account": sender,
            **_payee_fields(payee, txs[0]["to_type"]),
            "count": len(txs),
            "total": round(sum(t["amount"] for t in txs), 2),
            "first_at": txs[0]["sent_at"],
            "last_at": txs[-1]["sent_at"],
            "transactions": [t["tx"] for t in txs],
        })
    return sorted(pairs, key=lambda p: (-p["count"], -p["total"]))


def fans(fan_rows: list[dict[str, Any]], transfers: list[dict[str, Any]], window_days: int) -> list[dict[str, Any]]:
    result = []
    for fan in fan_rows:
        party, inbound = fan["party"], fan["direction"] == "in"
        start = _at(fan["window_start"])
        inside = sorted(
            (t for t in transfers
             if (t["to_party"] if inbound else t["from_account"]) == party
             and 0 <= (_at(t["sent_at"]) - start).total_seconds() <= window_days * 86400),
            key=lambda t: t["sent_at"],
        )
        record: dict[str, Any] = {
            "direction": fan["direction"],
            "counterparties": fan["counterparties"],
            "window_start": fan["window_start"],
            "total": round(sum(t["amount"] for t in inside), 2),
            "transactions": [t["tx"] for t in inside],
        }
        if inbound:
            record.update(_payee_fields(party, fan["party_type"]))
            record["senders"] = sorted({t["from_account"] for t in inside})
        else:
            record["from_account"] = party
            record["payees"] = sorted({t["to_party"] for t in inside if t["to_type"] == "Account"})
            record["beneficiaries"] = sorted({t["to_party"] for t in inside if t["to_type"] == "Beneficiary"})
        result.append(record)
    return sorted(result, key=lambda f: -f["counterparties"])


def group_links(links: list[dict[str, Any]], reached: set[str]) -> list[dict[str, Any]]:
    """One record per pair of accounts and kind of link, with everything evidencing it."""
    grouped: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for link in links:
        a, b = link["from_account"], link["to_account"]
        if a not in reached or b not in reached:
            continue
        if link["link"] != "transfer":
            a, b = sorted((a, b))
        grouped[(a, b, link["link"])].add(link["via"])
    records = []
    for (a, b, kind), via in sorted(grouped.items()):
        key, _ = VIA[kind]
        records.append({"from_account": a, "to_account": b, "link": kind, key: sorted(via)})
    return records


class _Components:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def join(self, members: list[str]) -> None:
        for other in members[1:]:
            self.parent[self.find(other)] = self.find(members[0])
        if members:
            self.find(members[0])


def fraud_networks(printed: dict[str, Any], min_size: int) -> list[dict[str, Any]]:
    """Group accounts joined by any link into networks; keep those that move money.

    Devices, IPs, people and beneficiaries alone also tie households and
    one person's own accounts together, so a network is reported only if
    money moved through it (a pass-through or repeated payments), and its
    core accounts are the ones the money moved between; the rest are
    attached to them by a device, IP, owner or beneficiary.
    """
    components = _Components()
    for link in printed.get("links") or []:
        components.join([link["from_account"], link["to_account"]])
    hubs = {
        "devices": printed.get("device_accounts") or {},
        "ips": printed.get("ip_accounts") or {},
        "people": printed.get("owner_accounts") or {},
        "beneficiaries": printed.get("beneficiary_accounts") or {},
    }
    for members_of in hubs.values():
        for members in members_of.values():
            components.join(sorted(members))

    networks: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "accounts": set(), "core": set(), "links": defaultdict(set),
        **{key: set() for key in hubs},
    })
    for account in list(components.parent):
        networks[components.find(account)]["accounts"].add(account)
    for link in printed.get("links") or []:
        net = networks[components.find(link["from_account"])]
        net["links"][link["link"]].add(link["tx"])
        net["core"].update((link["from_account"], link["to_account"]))
    for key, members_of in hubs.items():
        for hub, members in members_of.items():
            networks[components.find(sorted(members)[0])][key].add(hub)

    cases = printed.get("cases") or {}
    result: list[dict[str, Any]] = []
    for net in networks.values():
        money = sum(len(net["links"][kind]) for kind in MONEY_LINKS)
        if len(net["accounts"]) < min_size or money == 0:
            continue
        result.append({
            "size": len(net["accounts"]),
            "accounts": sorted(net["accounts"]),
            "core_accounts": sorted(net["core"]),
            "under_investigation": [
                {"account": a, "cases": sorted(cases[a])} for a in sorted(net["accounts"]) if a in cases
            ],
            "pass_through_transactions": sorted(net["links"]["pass_through"]),
            "repeated_payment_transactions": sorted(net["links"]["repeated_payments"]),
            **{key: sorted(net[key]) for key in hubs},
        })
    result.sort(key=lambda n: (
        -len(n["under_investigation"]),
        -(len(n["pass_through_transactions"]) + len(n["repeated_payment_transactions"])),
        -n["size"],
        n["accounts"][0],
    ))
    return result


def network_tools(client: GraphDataMcpClient) -> list[ToolSpec]:
    async def find_circular_flows(params: FindCircularFlowsInput) -> InvestigationResult:
        printed = await _run(client, "find_circular_flows", params.model_dump(include={"max_hops", "window_days"}))
        if isinstance(printed, str):
            return failure("find_circular_flows failed", printed)
        loops = circular_paths(printed.get("hops") or [], params.min_retained)
        if params.account:
            loops = [loop for loop in loops if params.account in loop["accounts"]]
        loops = loops[: params.max_loops]
        scope = f" through {params.account}" if params.account else ""
        summary = (
            f"{len(loops)} loop(s){scope}: money back at its origin within {params.window_days} days "
            f"and {params.max_hops} transfers, keeping {params.min_retained:.0%}+ of it"
            + (f"; e.g. {' -> '.join(loops[0]['accounts'])} -> {loops[0]['origin']} "
               f"({loops[0]['retained']:.0%} back in {loops[0]['hours']}h)" if loops else "")
        )
        return _result(summary, {"loops": loops}, _EVIDENCE_FIELDS)

    async def find_repeated_counterparties(params: FindRepeatedCounterpartiesInput) -> InvestigationResult:
        printed = await _run(client, "find_repeated_counterparties", params.model_dump(exclude={"account"}))
        if isinstance(printed, str):
            return failure("find_repeated_counterparties failed", printed)
        pairs = repeated_pairs(printed.get("pair_transfers") or [])
        fan_list = fans(printed.get("fans") or [], printed.get("fan_transfers") or [], params.fan_window_days)
        if params.account:
            pairs = [p for p in pairs if params.account in (p["from_account"], p.get("to_account"))]
            fan_list = [f for f in fan_list if params.account in (
                f.get("to_account"), f.get("from_account"), *f.get("senders", []), *f.get("payees", []))]
        summary = (
            f"{len(pairs)} repeated pair(s) ({params.min_transfers}+ transfers within "
            f"{params.pair_window_days} days) and {len(fan_list)} fan(s) "
            f"({params.min_counterparties}+ distinct counterparties within {params.fan_window_days} days)"
        )
        return _result(summary, {"repeated_pairs": pairs, "fans": fan_list}, _EVIDENCE_FIELDS)

    async def find_connected_accounts(params: FindConnectedAccountsInput) -> InvestigationResult:
        printed = await _run(client, "find_connected_accounts", params)
        if isinstance(printed, str):
            return failure("find_connected_accounts failed", printed)
        reached = {r["account"]: r["distance"] for r in printed.get("accounts") or []}
        links = group_links(printed.get("links") or [], set(reached))
        kinds_of: dict[str, set[str]] = defaultdict(set)
        for link in links:
            kinds_of[link["from_account"]].add(link["link"])
            kinds_of[link["to_account"]].add(link["link"])
        accounts = [
            {"account": a, "distance": d, "links": sorted(kinds_of[a])}
            for a, d in sorted(reached.items(), key=lambda item: (item[1], item[0]))
            if a != params.account
        ]
        counts: dict[str, int] = defaultdict(int)
        for link in links:
            counts[link["link"]] += 1
        summary = (
            f"{len(accounts)} account(s) within {params.max_hops} link(s) of {params.account}"
            + (": " + ", ".join(f"{n} {kind}" for kind, n in sorted(counts.items())) if counts else "")
            + (f" (capped at {params.max_accounts})" if len(reached) >= params.max_accounts else "")
        )
        return _result(summary, {"account": params.account, "accounts": accounts, "links": links}, _EVIDENCE_FIELDS)

    async def find_fraud_networks(params: FindFraudNetworksInput) -> InvestigationResult:
        printed = await _run(client, "find_fraud_networks", params.model_dump(exclude={"min_size", "top_k"}))
        if isinstance(printed, str):
            return failure("find_fraud_networks failed", printed)
        networks = fraud_networks(printed, params.min_size)[: params.top_k]
        summary = (
            f"{len(networks)} network(s) of {params.min_size}+ accounts that moved money between them"
            + (f"; largest {max(n['size'] for n in networks)} accounts" if networks else "")
            + (f"; {sum(1 for n in networks if n['under_investigation'])} include an account under investigation"
               if networks else "")
        )
        return _result(summary, {"networks": networks}, _EVIDENCE_FIELDS)

    return [
        ToolSpec(
            "find_circular_flows",
            "Find money that comes back to the account it left (round-tripping): loops of transfers, "
            "each made after the money arrived, back at the origin within days and keeping most of "
            "the amount. Returns each loop's path, transactions, and how much came back how fast.",
            FindCircularFlowsInput,
            find_circular_flows,
        ),
        ToolSpec(
            "find_repeated_counterparties",
            "Find accounts that keep paying the same payee (several transfers within weeks, e.g. ring "
            "members paying a controller) and fans: an account receiving from many distinct senders "
            "within days (a funnel collecting victims' money) or paying many payees (dispersal).",
            FindRepeatedCounterpartiesInput,
            find_repeated_counterparties,
        ),
        ToolSpec(
            "find_connected_accounts",
            "Map the accounts around one account, up to 3 links away, and how each is connected: "
            "transfers, the same owner, a device or IP shared by a few people, or the same external "
            "beneficiary. Use it to see who an account is tied to and through what.",
            FindConnectedAccountsInput,
            find_connected_accounts,
        ),
        ToolSpec(
            "find_fraud_networks",
            "Find networks across the whole graph: groups of accounts tied together by pass-through "
            "transfers, repeated payments, shared devices or IPs, common owners or beneficiaries, "
            "where money actually moved through the group. Returns each network's accounts, what ties "
            "them, and any open investigation cases among them.",
            FindFraudNetworksInput,
            find_fraud_networks,
        ),
    ]
