"""The installed investigation queries as typed tools (spec section 4.1.3).

Each tool validates its input, runs its installed query through
tigergraph-mcp's run_installed_query, reshapes TigerGraph's printed output
(maps keyed by vertex id) into one record per entity, and lists the
vertices the result rests on as evidence.
"""

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator
from tigergraph_mcp.tool_names import TigerGraphToolName as Tool

from ..mcp.transport import GraphDataMcpClient
from .base import ToolSpec
from .results import InvestigationResult, collect_evidence, failure

AccountId = Annotated[
    str, Field(pattern=r"^[A-Za-z0-9_.:-]{1,128}$", description="An Account vertex id, e.g. account-000127")
]


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FindMuleCandidatesInput(_Input):
    account: AccountId | None = Field(
        None, description="Score just this account (whatever its score); omit to rank the whole graph"
    )
    top_k: int = Field(25, ge=1, le=100, description="How many accounts to return, highest score first")
    min_score: int = Field(3, ge=1, le=16, description="Minimum combined signal score to be listed")
    window_hours: int = Field(24, ge=1, le=168, description="How soon forwarding must follow a receipt")


class TraceFundTransferChainInput(_Input):
    account: AccountId
    max_hops: int = Field(6, ge=1, le=10, description="How many transfers onward to follow")
    window_hours: int = Field(24, ge=1, le=168, description="Max delay between receiving and forwarding")
    min_forward_ratio: float = Field(
        0.8, ge=0.5, le=1.0, description="Minimum share of a receipt the next transfer must forward"
    )


class FindSharedInfrastructureInput(_Input):
    account: AccountId
    max_accounts_listed: int = Field(25, ge=1, le=100, description="Cap on accounts listed per device or IP")


class FindStructuringPatternInput(_Input):
    threshold: float = Field(1000.0, gt=0, description="The reporting threshold amounts stay under")
    margin: float = Field(50.0, gt=0, description="How far under the threshold counts as just under")
    min_count: int = Field(3, ge=2, le=50, description="Minimum near-threshold transactions per account")
    window_days: int = Field(7, ge=1, le=90, description="All of them within this many days")

    @model_validator(mode="after")
    def _margin_below_threshold(self) -> "FindStructuringPatternInput":
        if self.margin >= self.threshold:
            raise ValueError("margin must be smaller than threshold")
        return self


def _printed(data: Any) -> dict[str, Any]:
    """run_installed_query's result: a list of PRINT blocks, merged into one dict."""
    merged: dict[str, Any] = {}
    for block in (data or {}).get("result") or []:
        if isinstance(block, dict):
            merged.update(block)
    return merged


async def _run(
    client: GraphDataMcpClient, query: str, params: BaseModel | dict[str, Any]
) -> dict[str, Any] | str:
    """Run an installed query; `params` is its input model, or just the fields the query takes."""
    arguments = params.model_dump() if isinstance(params, BaseModel) else params
    result = await client.call(
        Tool.RUN_INSTALLED_QUERY.value,
        {"graph_name": client.graph_name, "query_name": query, "params": arguments},
    )
    if not result["ok"]:
        return result["error"] or f"{query} failed"
    return _printed(result["data"])


def _result(summary: str, data: Any, fields: dict[str, str]) -> InvestigationResult:
    return InvestigationResult(
        ok=True, summary=summary, data=data, error=None,
        evidence=collect_evidence(data, fields), citations=[],
    )


def investigation_tools(client: GraphDataMcpClient) -> list[ToolSpec]:
    async def find_mule_candidates(params: FindMuleCandidatesInput) -> InvestigationResult:
        query_params = params.model_dump(exclude={"account"})
        query_params["accounts"] = [params.account] if params.account else []
        if params.account:
            query_params["min_score"] = 0
        printed = await _run(client, "find_mule_candidates", query_params)
        if isinstance(printed, str):
            return failure("find_mule_candidates failed", printed)

        def per_account(key: str) -> dict[str, list[str]]:
            return {k: sorted(v) for k, v in (printed.get(key) or {}).items()}

        signals = printed.get("signals") or {}
        evidence_maps = {
            "forwarded_transactions": per_account("forwarded_transactions"),
            "cash_out_transactions": per_account("cash_out_transactions"),
            "near_threshold_transactions": per_account("near_threshold_transactions"),
            "shared_devices": per_account("shared_devices"),
            "shared_ips": per_account("shared_ips"),
        }
        candidates = []
        for row in printed.get("candidates") or []:
            account = row["account"]
            candidates.append({
                "account": account,
                "score": row["score"],
                "kyc_score": row["kyc_score"],
                "open_date": row["open_date"],
                "signals": list(signals.get(account, [])),
                **{name: values.get(account, []) for name, values in evidence_maps.items()},
            })
        if params.account:
            found = candidates[0] if candidates else None
            summary = (
                f"{params.account} scores {found['score']} of 16 on mule signals"
                + (f": {', '.join(found['signals'])}" if found["signals"] else ": none")
                if found else f"{params.account} was not found"
            )
        elif not candidates:
            summary = f"no account scored {params.min_score}+ on mule signals"
        else:
            top = candidates[0]
            summary = (
                f"{len(candidates)} account(s) scored {params.min_score}+ on mule signals; "
                f"highest {top['account']} (score {top['score']}: {', '.join(top['signals'])})"
            )
        return _result(summary, {"candidates": candidates}, {
            "account": "Account",
            "forwarded_transactions": "Transaction",
            "cash_out_transactions": "Transaction",
            "near_threshold_transactions": "Transaction",
            "shared_devices": "Device",
            "shared_ips": "IPAddress",
        })

    async def trace_fund_transfer_chain(params: TraceFundTransferChainInput) -> InvestigationResult:
        printed = await _run(client, "trace_fund_transfer_chain", params)
        if isinstance(printed, str):
            return failure("trace_fund_transfer_chain failed", printed)
        hops = sorted(printed.get("hops") or [], key=lambda h: (h["hop"], h["sent_at"]))
        cash_outs = sorted(printed.get("cash_outs") or [], key=lambda c: (c["hop"], c["sent_at"]))
        reached = {h["to_account"] for h in hops}
        summary = (
            f"from {params.account}: {len(hops)} onward transfer(s) reaching {len(reached)} account(s)"
            + (f", {len(cash_outs)} cash-out(s) to {', '.join(sorted({c['beneficiary'] for c in cash_outs}))}"
               if cash_outs else ", no cash-out to a beneficiary")
        )
        return _result(summary, {"account": params.account, "hops": hops, "cash_outs": cash_outs}, {
            "account": "Account",
            "from_account": "Account",
            "to_account": "Account",
            "tx": "Transaction",
            "beneficiary": "Beneficiary",
        })

    async def find_shared_infrastructure(params: FindSharedInfrastructureInput) -> InvestigationResult:
        printed = await _run(client, "find_shared_infrastructure", params)
        if isinstance(printed, str):
            return failure("find_shared_infrastructure failed", printed)
        by_device = printed.get("accounts_by_device") or {}
        by_ip = printed.get("accounts_by_ip") or {}
        devices = [
            {**d, "other_accounts": sorted(by_device.get(d["device"], []))}
            for d in sorted(printed.get("devices") or [], key=lambda d: -d["user_count"])
        ]
        ips = [
            {**i, "other_accounts": sorted(by_ip.get(i["ip"], []))}
            for i in sorted(printed.get("ips") or [], key=lambda i: -i["user_count"])
        ]
        shared = [d["device"] for d in devices if d["user_count"] > 1] + [i["ip"] for i in ips if i["user_count"] > 1]
        summary = (
            f"{params.account} used {len(devices)} device(s) and {len(ips)} IP(s); "
            + (f"shared with other accounts: {', '.join(shared)}" if shared else "none shared with other accounts")
        )
        return _result(summary, {"account": params.account, "devices": devices, "ips": ips}, {
            "account": "Account",
            "device": "Device",
            "ip": "IPAddress",
            "other_accounts": "Account",
        })

    async def find_structuring_pattern(params: FindStructuringPatternInput) -> InvestigationResult:
        printed = await _run(client, "find_structuring_pattern", params)
        if isinstance(printed, str):
            return failure("find_structuring_pattern failed", printed)
        deposits = printed.get("deposits_by_account") or {}
        clusters = [
            {**c, "transactions": sorted(deposits.get(c["account"], []), key=lambda d: d["made_at"])}
            for c in sorted(printed.get("clusters") or [], key=lambda c: -c["max_in_window"])
        ]
        summary = (
            f"{len(clusters)} account(s) made {params.min_count}+ transactions within "
            f"{params.margin:g} under {params.threshold:g} inside some {params.window_days}-day window"
        )
        return _result(summary, {"clusters": clusters}, {"account": "Account", "tx": "Transaction"})

    return [
        ToolSpec(
            "find_mule_candidates",
            "Rank accounts by money-mule signals (rapid forwarding of received funds, cash-out to an "
            "external beneficiary, devices or IPs shared with a few other accounts, structuring, a new "
            "account, low KYC). Use it to find which accounts look like mules; each result lists its "
            "signals and the transactions, devices and IPs behind them.",
            FindMuleCandidatesInput,
            find_mule_candidates,
        ),
        ToolSpec(
            "trace_fund_transfer_chain",
            "Follow money out of one account, transfer by transfer, keeping only onward transfers made "
            "soon after the money arrived and forwarding most of it (layering). Returns each hop and "
            "any cash-out to an external beneficiary.",
            TraceFundTransferChainInput,
            trace_fund_transfer_chain,
        ),
        ToolSpec(
            "find_shared_infrastructure",
            "List the devices and IPs an account's transactions came from and the other accounts that "
            "used the same ones, with how many accounts share each (a few suggests a ring; many "
            "suggests public Wi-Fi).",
            FindSharedInfrastructureInput,
            find_shared_infrastructure,
        ),
        ToolSpec(
            "find_structuring_pattern",
            "Find accounts that repeatedly transacted just under a reporting threshold within a short "
            "window (structuring), with the transactions.",
            FindStructuringPatternInput,
            find_structuring_pattern,
        ),
    ]
