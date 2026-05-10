"""V7 Contract Store - cross-package contract index."""
from __future__ import annotations

from typing import Any


def build_contract_index(packages: list[dict[str, Any]], architecture: dict[str, Any]) -> dict[str, Any]:
    contracts = []
    for contract in architecture.get("integration_contracts") or []:
        contracts.append({
            "source_package": contract.get("source_package", ""),
            "target_package": contract.get("target_package", ""),
            "interface_type": contract.get("interface_type", ""),
            "specification": contract.get("specification", ""),
            "owner_package": contract.get("source_package", ""),
            "consumer_packages": [contract.get("target_package", "")],
        })
    return {"schema_version": "7.0", "contracts": contracts, "missing_consumers": [], "index_hash": ""}
