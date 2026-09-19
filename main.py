"""
main.py
-------
Unified Blockchain-based VANET Simulation
==========================================

Implements the full architecture described in the research paper:

1. **BFT Consensus (PBFT-style)**
   – Leader (RSU) proposes a block.
   – Stakeholder nodes vote; faulty nodes cast invalid votes.
   – Consensus requires > 2/3 honest agreement (BFT threshold: n >= 3f+1).

2. **Pruning**
   – After the chain exceeds a configurable pruning threshold, older
     blocks are archived and removed from the active chain, reducing
     validation overhead for subsequent blocks.

3. **Smart-Contract-Based Intrusion Detection**
   – The IntrusionDetectionContract is deployed on-chain and executed
     before any transaction is accepted into the pool.

4. **Unified Architecture**
   – BSM generation, smart contracts, blockchain core, consensus,
     pruning, and visualisation are all orchestrated from this single
     entry point.

5. **RSU as a Distinct High-Trust Validator**
   – RSU nodes are always elected as the block proposer (leader) and
     are never marked faulty, reflecting their stationary, trusted
     infrastructure role in VANETs.

Usage
-----
    python main.py
"""

import hashlib
import json
import time
import random
import sys
import numpy as np
import matplotlib
if not sys.stdout.isatty():
    matplotlib.use('Agg')
import matplotlib.pyplot as plt

from bsm_data import generate_random_bsm_data, generate_batch_bsm
from smart_contract import IntrusionDetectionContract


# ====================================================================== #
#  Core Data Structures
# ====================================================================== #

class Transaction:
    """Wraps a BSM payload travelling between VANET nodes."""

    def __init__(self, sender, recipient, bsm_data, smart_contract=None):
        self.sender = sender
        self.recipient = recipient
        self.bsm_data = bsm_data
        self.smart_contract = smart_contract

    def to_dict(self):
        d = {
            "sender": self.sender,
            "recipient": self.recipient,
            "bsm_data": self.bsm_data,
        }
        if self.smart_contract:
            d["smart_contract"] = self.smart_contract.to_dict()
        return d

    def to_json(self):
        return json.dumps(self.to_dict(), sort_keys=True)


class Block:
    """A single block in the private VANET blockchain."""

    def __init__(self, transactions, previous_hash=None, timestamp=None,
                 proposer=None, nonce=0):
        self.transactions = transactions
        self.previous_hash = previous_hash
        self.timestamp = timestamp or time.time()
        self.proposer = proposer        # RSU that proposed the block
        self.nonce = nonce

    def compute_hash(self):
        block_string = json.dumps(self.to_dict(), sort_keys=True)
        return hashlib.sha256(block_string.encode()).hexdigest()

    def to_dict(self):
        return {
            "transactions": [tx.to_dict() for tx in self.transactions],
            "previous_hash": self.previous_hash,
            "timestamp": self.timestamp,
            "proposer": self.proposer,
            "nonce": self.nonce,
        }

    def to_json(self):
        return json.dumps(self.to_dict(), sort_keys=True, indent=2)


# ====================================================================== #
#  Blockchain with BFT + Pruning + Smart-Contract IDS
# ====================================================================== #

class Blockchain:
    """
    Private lightweight blockchain for VANETs.

    Key features aligned with the paper's abstract:
      • BFT consensus (PBFT-style 3-phase: propose → vote → commit)
      • Smart-contract-based intrusion detection
      • Dynamic chain pruning
      • RSU as distinguished validator
    """

    def __init__(self, pruning_threshold=10, quiet=False):
        # --- Chain state ---
        self.chain = []                 # Active chain
        self.pruned_blocks = []         # Archived (pruned) blocks
        self.transaction_pool = []

        # --- Network participants ---
        self.vehicle_nodes = []         # Regular vehicle nodes
        self.rsu_nodes = []             # Roadside units (high-trust)
        self.faulty_nodes = set()

        # --- Smart contract (IDS) ---
        self.ids_contract = IntrusionDetectionContract()

        # --- Pruning config ---
        self.pruning_threshold = pruning_threshold
        self.quiet = quiet

        # --- Metrics ---
        self.mining_times = []
        self.block_times = []
        self.pruned_mining_times = []   # Mining times for blocks mined after pruning
        self.consensus_rounds = []      # Number of rounds to reach consensus per block
        self.blocks_before_pruning = 0
        self.blocks_after_pruning = 0

    # ------------------------------------------------------------------ #
    #  Network Setup
    # ------------------------------------------------------------------ #
    def add_rsu(self, address):
        """Add a Roadside Unit (always trusted, acts as block proposer)."""
        if address not in self.rsu_nodes:
            self.rsu_nodes.append(address)

    def add_vehicle_node(self, address, is_faulty=False):
        """Add a vehicle node to the network."""
        if address not in self.vehicle_nodes:
            self.vehicle_nodes.append(address)
        if is_faulty:
            self.faulty_nodes.add(address)

    @property
    def all_validators(self):
        """All nodes that participate in BFT voting."""
        return self.rsu_nodes + self.vehicle_nodes

    # ------------------------------------------------------------------ #
    #  Smart-Contract-Based Intrusion Detection  (Improvement #3)
    # ------------------------------------------------------------------ #
    def add_transaction(self, transaction):
        """
        Submit a transaction.  The IDS smart contract is executed first;
        corrupt transactions are rejected on-chain.
        """
        tx_data = {
            "sender": transaction.sender,
            "bsm_data": transaction.bsm_data,
        }
        is_valid, reason = self.ids_contract.execute(tx_data)

        if not is_valid:
            if not self.quiet:
                print(f"  [IDS Contract] BLOCKED tx from {transaction.sender}: {reason}")
            return False

        self.transaction_pool.append(transaction)
        return True

    # ------------------------------------------------------------------ #
    #  BFT Consensus  (Improvement #1)
    # ------------------------------------------------------------------ #
    def _select_leader(self):
        """
        RSU is always the block proposer (Improvement #5).
        If multiple RSUs exist, round-robin based on block height.
        """
        if not self.rsu_nodes:
            raise RuntimeError("No RSU nodes in the network!")
        idx = len(self.chain) % len(self.rsu_nodes)
        return self.rsu_nodes[idx]

    def _generate_faulty_set(self):
        """
        Randomly designate a variable fraction (15–50 %) of vehicle nodes
        as faulty for this round.  A wider range makes it possible for BFT
        consensus to require multiple rounds when the faulty count
        approaches the n >= 3f+1 limit.
        RSU nodes are NEVER faulty (Improvement #5).
        """
        faulty_pct = random.uniform(0.15, 0.50)
        num_faulty = max(1, int(len(self.vehicle_nodes) * faulty_pct))
        self.faulty_nodes = set(random.sample(self.vehicle_nodes,
                                              min(num_faulty, len(self.vehicle_nodes))))

    def _bft_vote(self, block):
        """
        Each validator votes on the proposed block.
        Honest nodes return the correct block hash.
        Faulty nodes return a garbage hash.

        Returns
        -------
        dict[str, str]  –  {node_address: vote_hash}
        """
        votes = {}
        for node in self.all_validators:
            if node in self.faulty_nodes:
                votes[node] = hashlib.sha256(
                    f"faulty-{random.random()}".encode()
                ).hexdigest()
            else:
                votes[node] = block.compute_hash()
        return votes

    def _check_bft_consensus(self, votes, expected_hash):
        """
        BFT rule: consensus is reached when **more than 2/3** of
        validators agree on the same block hash.

        For n validators tolerating f faults: n >= 3f + 1.

        Returns
        -------
        bool
        """
        n = len(self.all_validators)
        agree_count = sum(1 for v in votes.values() if v == expected_hash)
        required = (2 * n) // 3 + 1     # strict > 2/3
        return agree_count >= required

    def run_consensus(self):
        """
        Full BFT consensus round:
        1. Leader (RSU) proposes a block.
        2. Validators cast votes (faulty ones deviate).
        3. If > 2/3 agree, the block is committed.
        4. Pruning is applied if threshold is exceeded.

        Returns
        -------
        (Block, float, float, int) | None
            (block, block_time, mining_time, consensus_rounds)
        """
        if not self.transaction_pool:
            print("  No valid transactions to mine.")
            return None

        self._generate_faulty_set()
        leader = self._select_leader()

        # --- Phase 1: Propose ---
        previous_hash = self.chain[-1].compute_hash() if self.chain else None
        block = Block(
            transactions=list(self.transaction_pool),
            previous_hash=previous_hash,
            proposer=leader,
        )

        # Computational work whose cost scales with active chain length.
        # Before pruning the chain grows → increasing validation overhead.
        # After pruning the chain is capped → overhead plateaus.
        start_time = time.time()
        # Phase 1: Chain validation (scales with active chain length)
        for existing_block in self.chain:
            for _ in range(5):
                _ = existing_block.compute_hash()
        # Phase 2: Core block mining (constant base work)
        for i in range(80):
            _ = block.compute_hash()
            block.nonce += 1
        end_time = time.time()
        mining_time = max(end_time - start_time, 0.001)

        # --- Phase 2: Vote (BFT) ---
        rounds = 0
        max_rounds = 5
        consensus_reached = False

        while rounds < max_rounds:
            rounds += 1
            votes = self._bft_vote(block)
            expected = block.compute_hash()
            if self._check_bft_consensus(votes, expected):
                consensus_reached = True
                break
            # re-shuffle faulty set and retry (simulates view-change)
            self._generate_faulty_set()

        if not consensus_reached:
            print(f"  [BFT] Consensus NOT reached after {max_rounds} rounds — block dropped.")
            return None

        # --- Phase 3: Commit ---
        self.chain.append(block)
        block_time = (time.time() - self.chain[-2].timestamp) if len(self.chain) > 1 else 0

        self.transaction_pool = []
        self.mining_times.append(mining_time)
        self.block_times.append(block_time)
        self.consensus_rounds.append(rounds)

        # --- Pruning (Improvement #2) ---
        self._apply_pruning()

        return block, block_time, mining_time, rounds

    # ------------------------------------------------------------------ #
    #  Chain Pruning  (Improvement #2)
    # ------------------------------------------------------------------ #
    def _apply_pruning(self):
        """
        If the active chain length exceeds the pruning threshold, move
        older blocks to the archive.  This reduces the validation
        overhead for subsequent operations, improving mining efficiency.
        """
        if len(self.chain) > self.pruning_threshold:
            # Keep only the last `pruning_threshold` blocks active
            blocks_to_prune = len(self.chain) - self.pruning_threshold
            pruned = self.chain[:blocks_to_prune]
            self.pruned_blocks.extend(pruned)
            self.chain = self.chain[blocks_to_prune:]

            # Track mining times post-pruning
            self.blocks_after_pruning += 1
            self.pruned_mining_times.append(self.mining_times[-1])

            if not self.quiet:
                print(f"  [Pruning] Archived {blocks_to_prune} block(s). "
                      f"Active chain: {len(self.chain)}, "
                      f"Total archived: {len(self.pruned_blocks)}")
        else:
            self.blocks_before_pruning += 1

    # ------------------------------------------------------------------ #
    #  Utility
    # ------------------------------------------------------------------ #
    def get_chain_length(self):
        return len(self.chain) + len(self.pruned_blocks)

    def print_ids_report(self):
        """Print the Intrusion Detection System alert report."""
        print("\n" + "=" * 60)
        print("  INTRUSION DETECTION SMART CONTRACT REPORT")
        print("=" * 60)
        print(f"  Contract Code  : {self.ids_contract.code}")
        print(f"  Thresholds     : {json.dumps(self.ids_contract.state, indent=2)}")
        print(f"  Total Alerts   : {len(self.ids_contract.alert_log)}")
        print(f"  Blocked Nodes  : {self.ids_contract.blocked_nodes or 'None'}")
        if self.ids_contract.alert_log:
            print("\n  Recent Alerts:")
            for alert in self.ids_contract.alert_log[-5:]:
                print(f"    • {alert['sender']}: {', '.join(alert['violations'])}")
        print("=" * 60)


# ====================================================================== #
#  Simulation Runner
# ====================================================================== #

def _run_trial_set(num_blocks, num_vehicle_nodes, num_rsu, corrupt_ratio,
                   pruning_threshold, trials, verbose=False):
    """
    Run a set of independent trials with the given pruning threshold.

    Returns
    -------
    avg_mining : np.ndarray   – Average mining time per block (seconds).
    avg_rounds : np.ndarray   – Average BFT rounds per block.
    last_blockchain : Blockchain – The blockchain from the last trial.
    """
    all_mining_times = np.zeros(num_blocks)
    all_consensus_rounds = np.zeros(num_blocks)
    last_blockchain = None

    for trial in range(trials):
        blockchain = Blockchain(pruning_threshold=pruning_threshold,
                                quiet=not verbose)

        # --- Add RSU nodes (Improvement #5) ---
        for r in range(num_rsu):
            blockchain.add_rsu(f"RSU_{r + 1}")

        # --- Add vehicle nodes ---
        for v in range(num_vehicle_nodes):
            blockchain.add_vehicle_node(f"N{v + 1}")

        block_count = 0
        while block_count < num_blocks:
            # Generate a batch of BSM transactions for this round
            num_tx = random.randint(3, 8)
            for _ in range(num_tx):
                is_corrupt = random.random() < corrupt_ratio
                bsm = generate_random_bsm_data(
                    vehicle_id=f"N{random.randint(1, num_vehicle_nodes)}",
                    corrupt=is_corrupt,
                )
                sender = f"N{random.randint(1, num_vehicle_nodes)}"
                recipient = random.choice(blockchain.rsu_nodes)
                tx = Transaction(sender=sender, recipient=recipient, bsm_data=bsm)
                blockchain.add_transaction(tx)

            result = blockchain.run_consensus()
            if result:
                block, block_time, mining_time, rounds = result
                block_count += 1
                if verbose and trial == 0:
                    print(f"  Trial 1 | Block {block_count:>3d} | "
                          f"Mining: {mining_time * 1000:>7.2f} ms | "
                          f"BFT rounds: {rounds} | "
                          f"Faulty: {len(blockchain.faulty_nodes)} | "
                          f"Proposer: {block.proposer}")

        # Accumulate mining times
        mt = np.array(blockchain.mining_times[:num_blocks])
        if len(mt) == num_blocks:
            all_mining_times += mt

        # Accumulate consensus rounds across all trials
        cr = np.array(blockchain.consensus_rounds[:num_blocks])
        if len(cr) == num_blocks:
            all_consensus_rounds += cr

        last_blockchain = blockchain

    avg_mining = all_mining_times / trials
    avg_rounds = all_consensus_rounds / trials
    return avg_mining, avg_rounds, last_blockchain


def run_simulation(num_blocks=20, num_vehicle_nodes=20, num_rsu=2,
                   corrupt_ratio=0.15, pruning_threshold=10, trials=20):
    """
    Run the full VANET blockchain simulation across multiple trials.

    Two trial sets are executed:
      1. **With pruning** – chain is pruned at the configured threshold.
      2. **Without pruning** – baseline run with pruning disabled.

    The comparison of mining times for blocks beyond the threshold
    quantifies the efficiency gain attributable to pruning.

    Parameters
    ----------
    num_blocks : int          – Blocks to mine per trial.
    num_vehicle_nodes : int   – Number of vehicle nodes in the network.
    num_rsu : int             – Number of RSU nodes.
    corrupt_ratio : float     – Fraction of transactions with corrupt BSM data.
    pruning_threshold : int   – Chain length at which pruning kicks in.
    trials : int              – Number of independent trial runs.
    """

    print(f"\n{'='*60}")
    print(f"  VANET BLOCKCHAIN SIMULATION")
    print(f"  Blocks={num_blocks}  Vehicles={num_vehicle_nodes}  RSUs={num_rsu}")
    print(f"  Corrupt ratio={corrupt_ratio}  Pruning threshold={pruning_threshold}")
    print(f"  Trials={trials}")
    print(f"{'='*60}\n")

    # ---- Run WITH pruning ----
    print("  -- Phase 1: Trials WITH pruning --\n")
    avg_mining, avg_rounds, last_blockchain = _run_trial_set(
        num_blocks, num_vehicle_nodes, num_rsu, corrupt_ratio,
        pruning_threshold, trials, verbose=True,
    )

    # ---- Run WITHOUT pruning (baseline) ----
    print("\n  -- Phase 2: Baseline trials WITHOUT pruning --\n")
    avg_mining_no_prune, _, _ = _run_trial_set(
        num_blocks, num_vehicle_nodes, num_rsu, corrupt_ratio,
        num_blocks + 1000, trials, verbose=False,
    )

    # ------------------------------------------------------------------ #
    #  Results
    # ------------------------------------------------------------------ #
    mining_ms = avg_mining * 1000
    mining_ms_no_prune = avg_mining_no_prune * 1000

    # Compare post-threshold blocks: with pruning vs without pruning
    post_with = mining_ms[pruning_threshold:]
    post_without = mining_ms_no_prune[pruning_threshold:]
    avg_post_with = np.mean(post_with) if len(post_with) else 0
    avg_post_without = np.mean(post_without) if len(post_without) else 0
    improvement = ((avg_post_without - avg_post_with) / avg_post_without * 100
                   if avg_post_without > 0 else 0)

    print(f"\n{'='*60}")
    print(f"  SIMULATION RESULTS (averaged over {trials} trials)")
    print(f"{'='*60}")
    print(f"  Avg mining time WITH    pruning (blocks {pruning_threshold+1}-{num_blocks}): "
          f"{avg_post_with:.4f} ms")
    print(f"  Avg mining time WITHOUT pruning (blocks {pruning_threshold+1}-{num_blocks}): "
          f"{avg_post_without:.4f} ms")
    print(f"  Pruning efficiency improvement : {improvement:.2f}%")
    print(f"  Total blocks mined (last trial): {last_blockchain.get_chain_length()}")
    print(f"  Active chain length            : {len(last_blockchain.chain)}")
    print(f"  Pruned/archived blocks         : {len(last_blockchain.pruned_blocks)}")

    # IDS Report
    last_blockchain.print_ids_report()

    # ------------------------------------------------------------------ #
    #  Print Data Points for All 3 Graphs
    # ------------------------------------------------------------------ #

    # --- Graph 1 Data Points ---
    print("\n" + "=" * 70)
    print("  GRAPH 1: Mining Time with Dynamic Pruning")
    print("=" * 70)
    print(f"  {'Block #':<10} {'With Pruning (ms)':<22} {'No Pruning (ms)':<22} {'Phase':<20}")
    print(f"  {'-'*10} {'-'*22} {'-'*22} {'-'*20}")
    for i in range(num_blocks):
        phase = "Before Pruning" if i < pruning_threshold else "After Pruning"
        print(f"  {i+1:<10} {mining_ms[i]:<22.4f} {mining_ms_no_prune[i]:<22.4f} {phase:<20}")
    print(f"\n  Pruning Threshold: Block {pruning_threshold}")

    # --- Graph 2 Data Points ---
    print("\n" + "=" * 70)
    print(f"  GRAPH 2: Pruning Efficiency (blocks {pruning_threshold+1}-{num_blocks})")
    print("=" * 70)
    print(f"  {'Category':<25} {'Avg Mining Time (ms)':<25}")
    print(f"  {'-'*25} {'-'*25}")
    print(f"  {'With Pruning':<25} {avg_post_with:<25.4f}")
    print(f"  {'Without Pruning':<25} {avg_post_without:<25.4f}")
    diff = avg_post_without - avg_post_with
    print(f"\n  Difference : {diff:.4f} ms")
    print(f"  Improvement: {improvement:.2f}%")

    # --- Graph 3 Data Points ---
    rounds_arr = avg_rounds
    print("\n" + "=" * 70)
    print("  GRAPH 3: Impact of Faulty Nodes on Consensus")
    print("=" * 70)
    print(f"  {'Block #':<10} {'Avg BFT Rounds':<20} {'Status':<25}")
    print(f"  {'-'*10} {'-'*20} {'-'*25}")
    for i in range(num_blocks):
        r = rounds_arr[i]
        status = "Ideal (1 round)" if r <= 1.05 else f"Avg {r:.2f} rounds"
        print(f"  {i+1:<10} {r:<20.2f} {status:<25}")
    avg_r = np.mean(rounds_arr)
    max_r = np.max(rounds_arr)
    multi = sum(1 for r in rounds_arr if r > 1.05)
    print(f"\n  Average rounds (across trials) : {avg_r:.2f}")
    print(f"  Max avg rounds for any block   : {max_r:.2f}")
    print(f"  Blocks with avg > 1 round      : {multi}/{num_blocks}")

    print("\n" + "=" * 70 + "\n")

    # ------------------------------------------------------------------ #
    #  Visualisation
    # ------------------------------------------------------------------ #
    x = np.arange(1, num_blocks + 1)
    fig, axes = plt.subplots(1, 3, figsize=(20, 7))

    # --- Plot 1: Mining Time per Block (with vs without pruning) ---
    ax1 = axes[0]
    colors = ['#2196F3' if i < pruning_threshold else '#4CAF50'
              for i in range(num_blocks)]
    bars1 = ax1.bar(x, mining_ms, width=0.6, color=colors, edgecolor='white',
                    linewidth=0.5, alpha=0.85, label='With Pruning')
    # Overlay "without pruning" as a red line for comparison
    ax1.plot(x, mining_ms_no_prune, color='#E53935', linewidth=2, linestyle='-',
             marker='s', markersize=4, label='Without Pruning', zorder=5)
    # Scatter points on the pruned bars
    ax1.scatter(x, mining_ms, color='#0D47A1', zorder=5, s=30, marker='o',
                edgecolors='white', linewidth=0.8)
    # Value labels every other bar to avoid clutter
    for i, (bar, val) in enumerate(zip(bars1, mining_ms)):
        if i % 2 == 0:
            ax1.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.02,
                     f'{val:.2f}', ha='center', va='bottom', fontsize=6.5,
                     fontweight='bold', rotation=45)
    ax1.axvline(pruning_threshold + 0.5, color='red', linestyle='--', linewidth=2,
                label=f'Pruning Threshold ({pruning_threshold})')
    ax1.set_xlabel("Block Number", fontsize=12)
    ax1.set_ylabel("Avg Mining Time (ms)", fontsize=12)
    ax1.set_title("Mining Time with Dynamic Pruning", fontsize=13, fontweight='bold')
    ax1.legend(fontsize=8, loc='upper left')
    ax1.set_xticks(x)
    # Phase labels
    ax1.text(pruning_threshold / 2, max(mining_ms) * 0.85,
             "Before\nPruning", ha='center', fontsize=9, color='#1565C0',
             fontweight='bold')
    if num_blocks > pruning_threshold:
        y_top = max(max(mining_ms), max(mining_ms_no_prune))
        ax1.text(pruning_threshold + (num_blocks - pruning_threshold) / 2,
                 y_top * 0.85,
                 "After\nPruning", ha='center', fontsize=9, color='#2E7D32',
                 fontweight='bold')

    # --- Plot 2: Pruning vs No-Pruning (post-threshold blocks) ---
    ax2 = axes[1]
    categories = ['With Pruning', 'Without Pruning']
    values = [avg_post_with, avg_post_without]
    bar_colors = ['#4CAF50', '#E53935']
    bars2 = ax2.bar(categories, values, color=bar_colors, width=0.5,
                    edgecolor='white', alpha=0.85)
    ax2.scatter([0, 1], values, color=['#1B5E20', '#B71C1C'], zorder=5, s=80,
                marker='D', edgecolors='white', linewidth=1.2, label='Data Points')
    for bar, val in zip(bars2, values):
        ax2.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.02,
                 f'{val:.4f} ms', ha='center', va='bottom', fontsize=11,
                 fontweight='bold',
                 bbox=dict(boxstyle='round,pad=0.3', facecolor='white',
                           edgecolor='gray', alpha=0.8))
    ax2.set_ylabel("Avg Mining Time (ms)", fontsize=12)
    ax2.set_title(f"Pruning Efficiency (Blocks {pruning_threshold+1}-{num_blocks})",
                  fontsize=13, fontweight='bold')
    ax2.legend(fontsize=9)
    ax2.annotate(f'Improvement: {improvement:.2f}%',
                 xy=(0, avg_post_with), xytext=(0.5, max(values) * 0.5),
                 fontsize=10, fontweight='bold', color='#2E7D32',
                 arrowprops=dict(arrowstyle='->', color='#2E7D32', lw=1.5),
                 ha='center')

    # --- Plot 3: Faulty Node Impact (BFT consensus rounds) ---
    ax3 = axes[2]
    x3 = np.arange(1, num_blocks + 1)
    bar_colors_3 = ['#FF7043' if r <= 1.05 else '#E53935' for r in rounds_arr]
    bars3 = ax3.bar(x3, rounds_arr, width=0.6, color=bar_colors_3,
                    edgecolor='white', linewidth=0.5, alpha=0.85)
    ax3.scatter(x3, rounds_arr, color='#BF360C', zorder=5, s=30,
                marker='o', edgecolors='white', linewidth=0.8, label='Avg Rounds')
    ax3.plot(x3, rounds_arr, color='#BF360C', linewidth=1.2, alpha=0.6, linestyle='--')
    for bar, val in zip(bars3, rounds_arr):
        ax3.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.02,
                 f'{val:.1f}', ha='center', va='bottom', fontsize=7,
                 fontweight='bold')
    ax3.axhline(1, color='green', linestyle='--', linewidth=1.5, label='Ideal (1 round)')
    ax3.set_xlabel("Block Number", fontsize=12)
    ax3.set_ylabel("BFT Consensus Rounds", fontsize=12)
    ax3.set_title("Impact of Faulty Nodes\non Consensus", fontsize=13, fontweight='bold')
    ax3.legend(fontsize=8)
    ax3.set_xticks(x3)
    ax3.set_ylim(0, max(max(rounds_arr) + 0.5, 2))

    plt.tight_layout()
    plt.savefig("simulation_results.png", dpi=150, bbox_inches='tight')
    print("  [Plot saved to simulation_results.png]")
    plt.show()


# ====================================================================== #
#  Entry Point
# ====================================================================== #
if __name__ == "__main__":
    run_simulation(
        num_blocks=20,
        num_vehicle_nodes=20,
        num_rsu=2,
        corrupt_ratio=0.15,
        pruning_threshold=10,
        trials=20,
    )
