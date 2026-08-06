# Distributed Systems Evaluation Report — Federation Coordinator Subsystem

**Module:** `coordinator_service.py`, `servicer.py`, `client.py`, `flower_engine.py`, `region_failover.py`  
**Evaluation Date:** 2026-08-01  
**Auditor Role:** Senior Researcher in Distributed Systems & Scientific Software Verification  
**Evaluation Standard:** Peer-Reviewed Distributed Systems & FL Architecture Review  

---

## 1. Executive Summary

This report presents a rigorous distributed systems evaluation of the **Federation Coordinator** implementation in the *Privacy-Preserving Cross-Bank Fraud Detection* project. The architecture was evaluated across eight core distributed systems dimensions: synchronization correctness, orchestration logic, client lifecycle management, coordinator determinism, partial participation handling, scheduling correctness, consistency guarantees, and failure handling.

The evaluation confirms that the coordinator provides **high deterministic execution and strong single-master orchestration** under normal operating conditions. However, it explicitly delineates implemented guarantees from stronger distributed systems abstractions: the system relies on a single-master synchronous Bulk Synchronous Parallel (BSP) model rather than Raft/Paxos consensus or Byzantine Fault Tolerance (BFT).

---

## 2. Distributed Systems Evaluation Dimensions

### 2.1 Synchronization Correctness & Model

* **Synchronization Model:** Synchronous Round-Based Bulk Synchronous Parallel (BSP).
* **Evaluation:** Clients synchronize explicitly at round boundaries. A round progresses from `COLLECTING_GRADIENTS` to `AGGREGATING` only when the number of received gradients reaches minimum quorum ($k_{\text{submitted}} \ge k_{\text{min}}$).
* **Straggler Impact:** Stragglers are bounded by the 15.0-second heartbeat timeout threshold. If a client fails to submit within the timeout window, it is marked `"OFFLINE"`, allowing the remaining active nodes to meet quorum.

---

### 2.2 Orchestration Logic & State Machine Determinism

* **State Progression:** $\text{IDLE} \xrightarrow{\text{start\_round}} \text{COLLECTING\_GRADIENTS} \xrightarrow{k \ge k_{\text{min}}} \text{AGGREGATING} \xrightarrow{\text{Quality Gate}} \text{COMPLETED}$.
* **Determinism Assessment:** Empirically verified across 100 consecutive round state transitions (`federation_coordinator_ds_evaluation.py`). Under single-master execution, the state machine produces **100% deterministic round ID increments and status transitions**.

---

### 2.3 Client Lifecycle & Liveness Management

* **Handshake & Eviction:** Handshakes register bank profiles into `self.registry`. Heartbeats update `last_heartbeat = time.time()`.
* **Eviction Semantics:** Passive liveness monitoring evicts nodes to `"OFFLINE"` when $t_{\text{now}} - t_{\text{last\_heartbeat}} > 15.0\,\text{s}$.
* **Consistency:** Prevents dead or disconnected bank nodes from inflating quorum calculation or being assigned training tasks.

---

### 2.4 Partial Participation Handling

* **Quorum Formulation:**
  $$\text{QuorumMet}(S_r, k_{\text{min}}) = \mathbb{I}\left(|S_r| \ge k_{\text{min}}\right)$$
* **Evaluation:** Evaluated with $K=10$ registered banks, $k_{\text{min}}=5$, and $k_{\text{actual}}=6$ active submissions. The system correctly triggers aggregation once $k_{\text{actual}} \ge k_{\text{min}}$, discarding remaining unreceived gradients without blocking or stalling the pipeline.

---

### 2.5 Consistency Guarantees & State Storage

* **Consistency Model:** Strong Consistency under Single-Master Single-Thread Execution; Eventual/Weak Consistency across Multi-Region Failover Nodes.
* **Storage Reality:** State is maintained in local Python in-memory dictionaries (`self.registry`, `self.rounds`). Persistent state logging is performed asynchronously to the `gradient_submissions` SQL database table and `ImmutableAuditChain`.

---

## 3. Explicit Distinctions: Implemented vs. Stronger DS Guarantees

To ensure academic and technical transparency, the implemented guarantees are explicitly contrasted against stronger distributed systems primitives:

```
+-----------------------------------------------------------------------------------+
|            IMPLEMENTED COORDINATOR vs. STRONGER DISTRIBUTED SYSTEM PRIMITIVES     |
+-----------------------------------------------------------------------------------+
|                                                                                   |
|  1. Consensus Protocol (Raft / Paxos)                                            |
|     ├── Implemented: Single-Master In-Memory Status Tracking                    |
|     ├── Stronger Guarantee: Multi-Raft / Paxos Replicated State Machine           |
|     └── Distinction: Single master node maintains authoritative state. In a      |
|         network partition, regional failover nodes cannot prevent split-brain     |
|         multi-primary states without Raft consensus.                              |
|                                                                                   |
|  2. Fault Tolerance Model (Crash-Stop vs. Byzantine Fault Tolerance)              |
|     ├── Implemented: Crash-Stop Failure Model (Evicts unresponsive nodes)         |
|     ├── Stronger Guarantee: Byzantine Fault Tolerance (BFT / Krum / Trimmed-Mean)|
|     └── Distinction: Assumes client nodes either submit valid gradients or crash. |
|         No defenses exist against malicious clients submitting poisoned weights.  |
|                                                                                   |
|  3. Synchronization Primitive (BSP vs. Asynchronous FL)                           |
|     ├── Implemented: Synchronous Round-Based Bulk Synchronous Parallel (BSP)      |
|     ├── Stronger Guarantee: Asynchronous Stale-Gradient Aggregation (FedAsync)    |
|     └── Distinction: All clients in a round wait for quorum before global update; |
|         stragglers below quorum delay round completion.                           |
|                                                                                   |
+-----------------------------------------------------------------------------------+
```

---

## 4. Remaining Distributed Systems Limitations

1. **Split-Brain Risk in Multi-Region Failover:** `MultiRegionFailoverManager` relies on regional heartbeat timeouts ($>15\,\text{s}$) rather than distributed consensus (Raft/Paxos). A network partition between regions can cause both primary and standby nodes to claim active primary status simultaneously.
2. **Race Condition on Unlocked Quorum Evaluation:** `CoordinatorService.on_gradient_received` does not wrap status updates in thread mutex locks. Under concurrent multi-threaded execution, simultaneous gradient arrivals can trigger duplicate aggregation executions.
3. **In-Memory Volatility:** Round state and client registry data reside in volatile Python memory dictionaries. In the event of a coordinator process crash, in-flight round submissions must be re-initialized from database logs.
4. **Simulated Quality Gate AUC:** Production AUC evaluation defaults to a simulated round-decay formula (`0.88 - 0.01 * round_id`) rather than executing actual holdout model inference on a validation dataset.

---

*End of Distributed Systems Evaluation Report — Federation Coordinator Subsystem*
