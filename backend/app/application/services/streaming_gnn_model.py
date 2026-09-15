import logging

import torch
import torch.nn as nn
import torch.nn.functional as F  # noqa: N812

logger = logging.getLogger(__name__)


class GATAttentionLayer(nn.Module):
    """Custom Graph Attention Network (GAT) layer with multi-head attention.

    Computes:
        alpha_ij = softmax_j(LeakyReLU(a^T [W h_i || W h_j]) + log(w_ij))
    Incorporates time-decayed edge weights and residual skip projections.
    """

    def __init__(self, in_dim: int, out_dim: int, num_heads: int = 2, dropout: float = 0.2) -> None:
        super().__init__()
        self.in_dim = in_dim
        self.out_dim = out_dim
        self.num_heads = num_heads
        self.dropout = dropout

        # Weight projection parameters per head
        self.W = nn.Parameter(torch.empty(num_heads, in_dim, out_dim))
        # Attention weight parameters per head
        self.a = nn.Parameter(torch.empty(num_heads, 2 * out_dim, 1))
        # Residual projection for feature preservation across message passing
        self.res_proj = nn.Linear(in_dim, num_heads * out_dim, bias=False)

        # Initialize parameters
        nn.init.xavier_uniform_(self.W.data)
        nn.init.xavier_uniform_(self.a.data)
        nn.init.xavier_uniform_(self.res_proj.weight.data)

        self.leaky_relu = nn.LeakyReLU(0.2)
        self.dropout_layer = nn.Dropout(dropout)

    def forward(
        self,
        h: torch.Tensor,
        edge_index: torch.Tensor,
        edge_weights: torch.Tensor | None = None,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        """Forward pass of GAT Layer.

        Args:
            h: Node feature tensor [N, in_dim]
            edge_index: Adjacency list tensor [2, E]
            edge_weights: Optional time-decayed edge weights [E]

        Returns:
            Tuple: (Output embeddings [N, num_heads * out_dim], Attention coefficients [E, num_heads])
        """
        N = h.size(0)
        E = edge_index.size(1)

        if N == 0:
            out = torch.zeros((0, self.num_heads * self.out_dim), device=h.device)
            att = torch.zeros((E, self.num_heads), device=h.device)
            return out, att

        if E == 0:
            # Handle empty graph edge case with residual projection
            out = self.res_proj(h)
            att = torch.zeros((0, self.num_heads), device=h.device)
            return out, att

        # h_head shape: [num_heads, N, out_dim]
        # Project node features into attention subspaces
        h_projected = torch.matmul(h.unsqueeze(0), self.W)  # [num_heads, N, out_dim]

        # Extract source and target node representations for each edge
        src_nodes = edge_index[0]
        tgt_nodes = edge_index[1]

        # Get projected features for connected nodes: shape [num_heads, E, out_dim]
        h_src = h_projected[:, src_nodes, :]
        h_tgt = h_projected[:, tgt_nodes, :]

        # Concatenate source and target representations: shape [num_heads, E, 2 * out_dim]
        h_concat = torch.cat([h_src, h_tgt], dim=-1)

        # Compute attention coefficients pre-softmax: shape [num_heads, E, 1]
        e = self.leaky_relu(torch.matmul(h_concat, self.a))

        # Modulate attention logits with temporal time-decayed edge weights if provided
        if edge_weights is not None and edge_weights.numel() == E:
            w_clamped = torch.clamp(edge_weights.view(1, E, 1), min=1e-5)
            e = e + torch.log(w_clamped)

        # Softmax over incoming neighbors for each target node
        alpha = torch.zeros_like(e)
        for i in range(N):
            mask = tgt_nodes == i
            if mask.any():
                alpha[:, mask, :] = F.softmax(e[:, mask, :], dim=1)

        alpha = self.dropout_layer(alpha).squeeze(-1)  # [num_heads, E]

        # Aggregate neighbor representations weighted by attention coefficients
        # Use index_add_ to prevent message loss on repeated target indices
        h_out = torch.zeros((N, self.num_heads, self.out_dim), device=h.device)
        for head in range(self.num_heads):
            weighted_messages = h_src[head] * alpha[head].unsqueeze(-1)
            h_out[:, head, :].index_add_(0, tgt_nodes, weighted_messages)

        # Concatenate multi-head outputs and combine with residual projection
        h_out_concat = h_out.view(N, -1) + self.res_proj(h)

        return h_out_concat, alpha.t()  # [E, num_heads]


class StreamingGATModel(nn.Module):
    """Streaming Graph Attention Network (GAT) classifier for real-time fraud detection.

    Consists of an attention layer with time-decayed edge modulation
    followed by a binary classification layer.
    """

    def __init__(self, in_dim: int, hidden_dim: int = 16, num_heads: int = 2) -> None:
        super().__init__()
        self.gat = GATAttentionLayer(in_dim, hidden_dim, num_heads=num_heads)
        self.classifier = nn.Sequential(
            nn.Linear(hidden_dim * num_heads, 16),
            nn.ReLU(),
            nn.Dropout(0.2),
            nn.Linear(16, 1),
            nn.Sigmoid(),
        )
        self.optimizer = torch.optim.Adam(self.parameters(), lr=0.01)
        self.loss_fn = nn.BCELoss()

    def forward(
        self,
        h: torch.Tensor,
        edge_index: torch.Tensor,
        edge_weights: torch.Tensor | None = None,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        """Compute predictions and return attention weights."""
        embeddings, attention_weights = self.gat(h, edge_index, edge_weights=edge_weights)
        predictions = self.classifier(embeddings).squeeze(-1)
        return predictions, attention_weights

    def online_train_step(
        self,
        h: torch.Tensor,
        edge_index: torch.Tensor,
        labels: torch.Tensor,
        edge_weights: torch.Tensor | None = None,
    ) -> float:
        """Perform one step of backpropagation on the active streaming graph."""
        if h.size(0) == 0 or edge_index.size(1) == 0:
            return 0.0

        self.train()
        self.optimizer.zero_grad()

        preds, _ = self(h, edge_index, edge_weights=edge_weights)
        loss = self.loss_fn(preds, labels)

        loss.backward()
        self.optimizer.step()

        return float(loss.item())
