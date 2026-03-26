"""
Blockchain ecosystem mapper.

Cornell paper (pages 10-11, Filter 2):
  Tokens originating from the same blockchain exhibit stronger structural
  co-movements due to shared ecosystem flows, correlated user activity, and
  common governance/macro events.  Cross-chain pairs are removed before any
  statistical testing.

Data source: manually compiled from CoinGecko for the 20-token universe
defined in CANDIDATE_TOKENS.  Extend BLOCKCHAIN_MAP to add new tokens.

A token may belong to multiple chains (e.g. USDC is on Ethereum AND BNB
Chain); we store a frozenset so two tokens are "same blockchain" if their
chain sets overlap.
"""
from __future__ import annotations

from typing import Dict, FrozenSet, Optional

# ---------------------------------------------------------------------------
# Chain definitions (canonical names)
# ---------------------------------------------------------------------------

ETH   = "Ethereum"
BNB   = "BNB Smart Chain"
SOL   = "Solana"
DOT   = "Polkadot"
AVAX  = "Avalanche"
POLY  = "Polygon POS"
ARB   = "Arbitrum One"
COSMOS = "Cosmos"

# ---------------------------------------------------------------------------
# Token → frozenset of chains
# Based on CoinGecko "Platforms" data for Binance Perpetuals universe.
# ---------------------------------------------------------------------------

BLOCKCHAIN_MAP: Dict[str, FrozenSet[str]] = {
    # ── Major L1s ──────────────────────────────────────────────────────────
    "BTC":    frozenset({"Bitcoin"}),
    "ETH":    frozenset({ETH}),
    "SOL":    frozenset({SOL}),
    "ADA":    frozenset({"Cardano"}),
    "AVAX":   frozenset({AVAX}),
    "DOT":    frozenset({DOT}),
    "ATOM":   frozenset({COSMOS}),
    "DOGE":   frozenset({"Dogecoin"}),

    # ── Ethereum / EVM ecosystem ───────────────────────────────────────────
    "LINK":   frozenset({ETH, BNB, POLY}),
    "UNI":    frozenset({ETH, POLY, ARB}),
    "CTSI":   frozenset({ETH, BNB}),
    "ENJ":    frozenset({ETH}),
    "ZRX":    frozenset({ETH}),
    "STORJ":  frozenset({ETH}),
    "BAND":   frozenset({ETH, BNB}),

    # ── BNB Chain ecosystem ────────────────────────────────────────────────
    "ANKR":   frozenset({ETH, BNB, POLY}),
    "ATA":    frozenset({BNB, ETH}),   # Automata Network: BNB + ETH
    "NKN":    frozenset({ETH}),         # NKN mainnet but Ethereum ERC-20

    # ── Multi-chain / bridge tokens ────────────────────────────────────────
    "ONT":    frozenset({"Ontology"}),

    # ── Polygon / MATIC ───────────────────────────────────────────────────
    # MATIC was delisted from Binance perps; kept for reference
    "MATIC":  frozenset({POLY, ETH}),
}


def get_chains(token: str) -> FrozenSet[str]:
    """Return the set of blockchains for *token*, or an empty set if unknown."""
    return BLOCKCHAIN_MAP.get(token.upper(), frozenset())


def are_same_blockchain(symbol_a: str, symbol_b: str) -> bool:
    """
    Return True if symbol_a and symbol_b share at least one blockchain.

    Two tokens with an empty/unknown mapping are treated as NOT same-chain
    so they are conservatively excluded.
    """
    chains_a = get_chains(symbol_a)
    chains_b = get_chains(symbol_b)
    if not chains_a or not chains_b:
        return False
    return bool(chains_a & chains_b)


def get_shared_chains(symbol_a: str, symbol_b: str) -> FrozenSet[str]:
    """Return the intersection of chains for the two tokens."""
    return get_chains(symbol_a) & get_chains(symbol_b)


def filter_pairs_same_blockchain(
    pairs: list,
    sym_a_key: str = "sym_a",
    sym_b_key: str = "sym_b",
) -> list:
    """
    Filter a list of pair dicts, keeping only same-blockchain pairs.

    Parameters
    ----------
    pairs : list of dict, each with at least sym_a_key and sym_b_key
    sym_a_key, sym_b_key : dict keys for the two symbols

    Returns
    -------
    Filtered list.
    """
    return [
        p for p in pairs
        if are_same_blockchain(p[sym_a_key], p[sym_b_key])
    ]


# ---------------------------------------------------------------------------
# CLI helper
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import itertools
    import sys

    tokens = list(BLOCKCHAIN_MAP.keys())
    pairs = list(itertools.combinations(tokens, 2))
    same = [(a, b) for a, b in pairs if are_same_blockchain(a, b)]

    print(f"Total tokens mapped : {len(tokens)}")
    print(f"Total candidate pairs : {len(pairs)}")
    print(f"Same-blockchain pairs : {len(same)}")
    print()
    for a, b in same:
        shared = get_shared_chains(a, b)
        print(f"  {a:8} – {b:8}  [{', '.join(sorted(shared))}]")
